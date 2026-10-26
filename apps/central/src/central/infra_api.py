"""분산 준비용 API. 기존 동기식 작품 API와 별도 입구를 유지한다."""
import json
import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from contracts.infra import Completion, JobRequest, LeaseUpdate, ShowReport
from contracts.ue import TemplateReport
from db import jobs
from db.ue import UECapability

from .account_auth import identity as operator_identity
from .api import SessionDep
from .artifacts import require_uploaded

router = APIRouter(prefix="/infra", tags=["infrastructure"])


def audit(event, row):
    public = jobs.public_job(row)
    logging.getLogger("ranel.jobs").warning(json.dumps({
        "event": event, "job_id": row.id, "node_id": row.node_id, "kind": row.kind,
        "attempt": row.attempt, "artwork_id": public["artwork_id"], "version": public["artwork_version"]}))


def operator(request: Request):
    operator_identity(request)


def agent(request: Request, x_agent_token: str = Header(default="")) -> dict:
    registry = json.loads(request.app.state.settings.agent_keys_json)
    for node_id, config in registry.items():
        token = config.get("token", "")
        if config.get("enabled", True) and token and secrets.compare_digest(token.encode(), x_agent_token.encode()):
            return {"node_id": node_id, **{k: v for k, v in config.items() if k != "token"}}
    raise HTTPException(401, "등록된 Agent 토큰이 필요합니다")


AgentDep = Annotated[dict, Depends(agent)]


@router.get("/agent/identity")
def identity(identity: AgentDep):
    """접속 검사만으로 실제 장비의 heartbeat 상태를 바꾸지 않는다."""
    return identity


@router.post("/jobs", status_code=202, dependencies=[Depends(operator)])
def submit(body: JobRequest, request: Request, session: SessionDep):
    if body.kind == "ue_publish":
        raise HTTPException(403, "게시 작업은 검증 결과의 승인 API로만 등록합니다")
    try:
        row = jobs.enqueue(session, body.model_dump(mode="json"), request.app.state.settings.max_attempts)
    except IntegrityError:  # 동시에 들어온 같은 request_id
        session.rollback()
        row = jobs.enqueue(session, body.model_dump(mode="json"), request.app.state.settings.max_attempts)
    audit("job_submitted", row)
    return jobs.public_job(row)


@router.get("/jobs/{job_id}", dependencies=[Depends(operator)])
def job_status(job_id: str, session: SessionDep):
    row = session.get(jobs.Job, job_id)
    if not row:
        raise HTTPException(404, "없는 작업")
    result = jobs.public_job(row)
    result["attempts"] = [a.model_dump() for a in session.exec(
        select(jobs.Attempt).where(jobs.Attempt.job_id == job_id).order_by(jobs.Attempt.number)).all()]
    result["dependencies"] = [{"id": parent_id, "status": session.get(jobs.Job, parent_id).status}
                              for parent_id in row.payload.get("depends_on", [])]
    return result


@router.get("/jobs", dependencies=[Depends(operator)])
def job_list(session: SessionDep, limit: int = Query(default=50, ge=1, le=200),
             offset: int = Query(default=0, ge=0)):
    rows = session.exec(select(jobs.Job).order_by(jobs.Job.created_at.desc()).offset(offset).limit(limit)).all()
    return [jobs.public_job(row) for row in rows]


@router.post("/jobs/{job_id}/retry", dependencies=[Depends(operator)])
def retry_job(job_id: str, session: SessionDep):
    row = jobs.retry(session, job_id)
    audit("job_retry_queued", row)
    return jobs.public_job(row)


@router.post("/agent/heartbeat")
def node_heartbeat(identity: AgentDep, session: SessionDep, report: ShowReport | None = None):
    node = session.get(jobs.Node, identity["node_id"])
    if node is None:
        node = jobs.Node(id=identity["node_id"], role=identity["role"], catalog_version=identity["catalog_version"])
    if node.role != identity["role"] or node.catalog_version != identity["catalog_version"]:
        raise HTTPException(409, "등록된 노드 역할·카탈로그가 변경되었습니다. 운영자 확인이 필요합니다")
    node.heartbeat_at = jobs.now(session)
    if report:
        if node.role != "show":
            raise HTTPException(403, "시연 노드만 전시 상태를 보고합니다")
        node.show_report = {**report.model_dump(), "_received_at": node.heartbeat_at}
    session.add(node)
    session.commit()
    return {"node_id": node.id, "role": node.role}


@router.get("/nodes", dependencies=[Depends(operator)])
def nodes(session: SessionDep):
    timestamp = jobs.now(session)
    result = []
    for row in session.exec(select(jobs.Node)).all():
        job = session.get(jobs.Job, row.active_job) if row.active_job else None
        active = job and job.status == "running" and job.node_id == row.id and job.lease_until > timestamp
        result.append({**row.model_dump(), "online": timestamp - row.heartbeat_at < 90,
                       "active_job": row.active_job if active else None})
    return result


@router.post("/agent/claim")
def claim(identity: AgentDep, request: Request, session: SessionDep):
    node = session.get(jobs.Node, identity["node_id"])
    if not node:
        raise HTTPException(409, "heartbeat로 먼저 등록해야 합니다")
    if node.role != identity["role"] or node.catalog_version != identity["catalog_version"]:
        raise HTTPException(409, "등록된 노드 역할·카탈로그가 다릅니다")
    row = jobs.claim_node(session, node.id, request.app.state.settings.lease_seconds, identity.get("job_kinds"))
    if row is None:
        return None
    audit("job_claimed", row)
    return assignment(row, request, session)


def assignment(row, request, session):
    attempt = session.exec(select(jobs.Attempt).where(
        jobs.Attempt.job_id == row.id, jobs.Attempt.number == row.attempt)).one()
    return {
        **jobs.public_job(row), "payload": row.payload, "token": row.token,
        "inputs": [jobs.public_job(session.get(jobs.Job, parent))
                   for parent in row.payload.get("depends_on", [])],
        "attempt_id": attempt.id,
        "heartbeat_seconds": max(1, request.app.state.settings.lease_seconds // 3)}


@router.get("/agent/current")
def current_assignment(identity: AgentDep, request: Request, session: SessionDep):
    if identity["role"] not in ("generation", "production", "show"):
        raise HTTPException(403, "작업 임대 역할이 아닙니다")
    node = session.get(jobs.Node, identity["node_id"])
    row = session.get(jobs.Job, node.active_job) if node and node.active_job else None
    if node and (node.role != identity["role"] or node.catalog_version != identity["catalog_version"]):
        raise HTTPException(409, "등록된 노드 역할·카탈로그가 다릅니다")
    if not row or row.node_id != identity["node_id"] or row.status != "running" or row.lease_until <= jobs.now(session):
        return None
    if "job_kinds" in identity and row.kind not in identity["job_kinds"]:
        raise HTTPException(409, "진행 중 작업과 노드 전담 종류가 다릅니다. 운영자 확인이 필요합니다")
    if row.queue not in ({"generation": ("background", "assets"), "production": ("assembly", "verification"), "show": ("exhibition",)}[identity["role"]]):
        raise HTTPException(409, "노드의 작업 역할이 다릅니다")
    return assignment(row, request, session)


@router.post("/agent/jobs/{job_id}/heartbeat")
def renew(job_id: str, body: LeaseUpdate, identity: AgentDep, request: Request, session: SessionDep):
    jobs.heartbeat(session, job_id, identity["node_id"], body.token,
                   request.app.state.settings.lease_seconds, body.progress)
    return {"ok": True}


@router.post("/agent/ue-capabilities")
def ue_capabilities(body: TemplateReport, identity: AgentDep, session: SessionDep):
    if identity["role"] != "production" or "ue_assemble" not in identity.get("job_kinds", []):
        raise HTTPException(403, "UE 조립 역할의 제작 노드만 리소스를 보고합니다")
    node_heartbeat(identity, session)
    row = session.get(UECapability, identity["node_id"])
    if row is None:
        row = UECapability(node_id=identity["node_id"], report={}, reported_at=0)
    row.report, row.reported_at = body.model_dump(mode="json"), jobs.now(session)
    session.add(row)
    session.commit()
    return {"ok": True, "templates": len(body.templates), "runtime_verified": False}


@router.post("/agent/jobs/{job_id}/complete")
def complete(job_id: str, body: Completion, identity: AgentDep, session: SessionDep, request: Request):
    row = session.get(jobs.Job, job_id)
    if row and row.kind in ("background_generate", "assets_generate", "ue_assemble", "ue_verify", "ue_publish"):
        expected_role = "show" if row.kind == "ue_publish" else (
            "production" if row.kind in ("ue_assemble", "ue_verify") else "generation")
        if identity["role"] != expected_role:
            raise HTTPException(403, "작업 역할과 결과 보고 역할이 다릅니다")
        spec = row.payload.get("generation") or row.payload.get("assembly") or row.payload["runtime"]
        if not body.error and body.result is None:
            raise HTTPException(422, "생성 성공에는 결과 파일 목록이 필요합니다")
        if body.error and body.result is not None:
            raise HTTPException(422, "실패 보고에는 결과 파일 목록을 넣지 않습니다")
        if body.result and (body.result.artwork_id != spec["artwork_id"] or body.result.version != spec["version"]):
            raise HTTPException(422, "작업과 결과의 작품·버전이 다릅니다")
        if body.result:
            conditions = spec.get("conditions")
            if conditions and conditions["output_name"] not in {f.name for f in body.result.files}:
                raise HTTPException(422, "생성 조건에서 지정한 PNG 파일 이름이 필요합니다")
            require_uploaded(session, request.app.state.settings, row, body.result)
            if row.kind == "ue_assemble" and not {"scene.json", "assembly-report.json"}.issubset(
                    {f.name for f in body.result.files}):
                raise HTTPException(422, "조립 성공에는 scene.json과 assembly-report.json이 필요합니다")
        result = body.result.model_dump(mode="json") if body.result else {}
        if len(json.dumps(result).encode()) > 64 * 1024:
            raise HTTPException(422, "결과 메타데이터는 64 KiB 이하여야 합니다")
        from .runtime_flow import finish_runtime, queue_verification

        if row.kind in ("ue_verify", "ue_publish") and body.result:
            result = finish_runtime(session, request.app.state.settings, row, body.result, result)
        jobs.complete_generation(session, job_id, identity["node_id"], body.token, result, body.error, commit=False)
        if row.kind == "ue_assemble" and not body.error:
            queue_verification(session, row)
        session.commit()
        audit("generation_failed" if body.error else "generation_completed", session.get(jobs.Job, job_id))
        return {"ok": True, "verification": {"ue_assemble": "assembly_report_only", "ue_verify": "runtime_report",
                                 "ue_publish": "runtime_applied_report"}.get(row.kind, "node_report_only"),
                "publish_approved": row.kind == "ue_publish" and not body.error}
    row = jobs.owned(session, job_id, identity["node_id"], body.token)
    if row.kind != "bundle_verify":
        raise HTTPException(403, "이 Agent가 실행할 수 없는 작업")
    bundle = row.payload["bundle"]
    jobs.finish(session, job_id, identity["node_id"], body.token,
                {"artwork_id": bundle["artwork_id"], "version": bundle["version"],
                 "check": "file_integrity_only", "publish_approved": False}, body.error)
    return {"ok": True}
