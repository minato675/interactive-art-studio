"""조립→검증의 영속 연결과 검증 결과 기반의 명시적 게시 승인."""
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import select

from contracts.infra import JobRequest, PublishApproval
from contracts.runtime import HumanReview
from db import jobs

from .api import SessionDep
from .artifacts import checked_path
from .infra_api import operator

router = APIRouter(prefix="/infra", tags=["runtime"])


def queue_verification(session, assembly):
    spec = assembly.payload["assembly"]
    request = JobRequest(request_id=f"verify-{assembly.id}", kind="ue_verify", file_transport="http",
                         depends_on=[assembly.id], runtime={
                             **{k: spec[k] for k in ("artwork_id", "version", "catalog_version")},
                             "source_job_id": assembly.id, "target_node": "ue-review-1"})
    return jobs.enqueue(session, request.model_dump(mode="json"), commit=False)


def bundle_identity(source):
    files = {f["name"]: f["sha256"] for f in source.result["files"]}
    return hashlib.sha256("\n".join(f"{k}:{files[k]}" for k in sorted(files)).encode()).hexdigest()


def finish_runtime(session, settings, row, manifest, result):
    name = "runtime-report.json" if row.kind == "ue_verify" else "applied-report.json"
    ref = next((f for f in manifest.files if f.name == name), None)
    if ref is None or ref.size > 2 * 1024 * 1024:
        raise HTTPException(422, "실행 보고서가 없거나 2MiB를 넘습니다")
    try:
        report = json.loads(checked_path(settings, ref).read_text("utf-8"))
    except (ValueError, UnicodeError):
        raise HTTPException(422, "실행 보고서는 JSON이어야 합니다") from None
    runtime = row.payload["runtime"]
    release = "ue-" + runtime["source_job_id"]
    if not isinstance(report, dict) or report.get("version") != release:
        raise HTTPException(422, "실행 보고서의 버전이 다릅니다")
    source = session.get(jobs.Job, runtime["source_job_id"])
    if report.get("source_job_id") != source.id or report.get("source_bundle_sha256") != bundle_identity(source):
        raise HTTPException(422, "실행 보고서와 조립 번들이 다릅니다")
    if row.kind == "ue_verify":
        verdict = report.get("verdict")
        if verdict not in ("pass", "review", "fail"):
            raise HTTPException(422, "verdict가 필요합니다")
        passed, needs_review = report.get("passed"), report.get("needs_review")
        if (type(passed) is not bool or type(needs_review) is not bool
                or verdict != ("fail" if not passed else "review" if needs_review else "pass")):
            raise HTTPException(422, "passed·needs_review·verdict가 일치하지 않습니다")
        checks = report.get("checks")
        if verdict != "fail" and (report.get("passed") is not True or not isinstance(checks, list) or not checks
                                  or any(c.get("passed") is not True for c in checks if isinstance(c, dict))
                                  or any(not isinstance(c, dict) for c in checks)
                                  or (verdict == "pass" and report.get("needs_review") is not False)):
            raise HTTPException(422, "판정과 검증 항목이 모순됩니다")
        names = {f.name for f in manifest.files}
        screenshots = report.get("screenshots", {})
        if not isinstance(screenshots, dict) or any(not isinstance(v, str) or v not in names for v in screenshots.values()):
            raise HTTPException(422, "캡처 파일이 결과 목록에 없습니다")
        if verdict != "fail" and (not screenshots or report.get("bundle", {}).get("sha256") != bundle_identity(source)):
            raise HTTPException(422, "통과·검수 보고에는 번들 해시와 캡처가 필요합니다")
        return {**result, "verdict": verdict, "runtime_verified": verdict == "pass",
                "review_state": "awaiting_human_review" if verdict == "review" else "not_required",
                "source_job_id": source.id, "source_bundle_sha256": bundle_identity(source)}
    status = report.get("status", {})
    if (status.get("phase") != "Applied" or status.get("desired_version") != release
            or status.get("active_version") != release or not status.get("runtime_session")
            or not isinstance(status.get("runtime_pid"), int) or status["runtime_pid"] <= 0):
        raise HTTPException(422, "실제 Applied 상태가 확인되지 않았습니다")
    node = session.get(jobs.Node, row.node_id)
    node.show_report = {**status, "_received_at": jobs.now(session)}
    session.add(node)
    return {**result, "phase": "Applied", "source_job_id": source.id, "runtime_status": status}


@router.post("/jobs/{job_id}/approve-publication", status_code=202, dependencies=[Depends(operator)])
def approve(job_id: str, body: PublishApproval, session: SessionDep, request: Request):
    from db.head import HeadWorkflow
    from studio.head.publication import queue_publication

    verification = session.exec(select(jobs.Job).where(jobs.Job.id == job_id).with_for_update()
                                .execution_options(populate_existing=True)).first()
    if verification and verification.result.get("verdict") == "review":
        from .runtime_api import record_review

        previous = verification.result.get("human_review")
        if previous and previous["request"]["decision"] != "approve":
            raise HTTPException(409, "사람 검수에서 반려된 작품은 게시할 수 없습니다")
        if not previous:
            record_review(session, verification, HumanReview(decision="approve", reviewer=body.reviewer,
                reason=body.reason, expected_attempt=verification.attempt))
    workflow = session.exec(select(HeadWorkflow).where(
        HeadWorkflow.payload["_flow"]["verification_job"].as_string() == job_id).with_for_update()).first()
    if workflow and workflow.status == "planning_review":
        raise HTTPException(409, "Head AI 검수가 끝난 뒤 수동 게시 승인하세요")
    row = queue_publication(session, verification, request_id=body.request_id, target_node=body.target_node,
                            reviewer=body.reviewer, reason=body.reason,
                            registry=json.loads(request.app.state.settings.agent_keys_json))
    # 사람이 승인한 흐름도 최종 Applied까지 추적한다.
    if workflow and workflow.status in ("awaiting_publication_approval", "needs_attention"):
        workflow.payload = {**workflow.payload, "_flow": {**workflow.payload["_flow"], "publication_job": row.id}}
        workflow.status = "waiting_publication"
        session.add(workflow)
    session.commit()
    return jobs.public_job(row)
