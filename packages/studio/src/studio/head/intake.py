"""원문 접수 → 명세 해석 → 설치 확인 → 분산 Head. 원문과 준비된 명세를 DB에 보존한다."""

import json
from types import SimpleNamespace

from sqlmodel import Session, select

from contracts.head import HeadRequest, OperatorHeadIntake
from db import jobs
from db.head import HeadWorkflow

from . import dispatch, ue_support
from .spec_matching import LLMSpecMatcher


def submit(session, body):
    return jobs.enqueue(
        session, {"request_id": body.request_id, "kind": "head_intake", "request": body.request.model_dump(mode="json")}
    )


def ready(session, settings, prepared=None):
    registry = json.loads(settings.agent_keys_json)
    if prepared:
        request = HeadRequest.model_validate(prepared)
        selected = ue_support.select_template(request)
        try:
            ue_support.ready_nodes(session, request, selected, registry)
            return True
        except jobs.Conflict:
            return False
    table = ue_support.catalog()
    for entry in table["templates"]:
        try:
            ue_support.ready_nodes(
                session,
                SimpleNamespace(catalog_version="v1"),
                {"template": entry["template"], "fingerprint": entry["fingerprint"]},
                registry,
            )
            return True
        except jobs.Conflict:
            continue
    return False


def defer(session, row):
    jobs.attempt_end(session, row, "waiting_ue", jobs.now(session))
    row.status, row.token, row.lease_until = "waiting_ue", None, 0
    row.dispatch_after = jobs.now(session) + 30
    row.result = {
        "stage": "waiting_ue",
        "source_artwork_id": row.payload.get("source_artwork_id"),
        "reason": "온라인 UE 제작 노드의 카탈로그·템플릿 지문 확인 대기",
    }
    session.add(row)
    session.commit()
    return {"_deferred": True}


def wake(session, settings):
    timestamp = jobs.now(session)
    rows = session.exec(
        select(jobs.Job)
        .where(jobs.Job.kind == "head_intake", jobs.Job.status == "waiting_ue", jobs.Job.dispatch_after <= timestamp)
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        if ready(session, settings, row.payload.get("prepared")):
            row.status, row.dispatch_after = "queued", 0
        else:
            row.dispatch_after = timestamp + 30
        session.add(row)
    session.commit()


def process(engine, settings, job_id, owner, token):
    with Session(engine) as session:
        task = jobs.owned(session, job_id, owner, token)
        existing = session.exec(select(HeadWorkflow).where(HeadWorkflow.request_id == task.request_id)).first()
        if existing:
            return {"workflow_id": existing.id, "stage": "head_submitted"}
        payload = dict(task.payload)
        if not ready(session, settings, payload.get("prepared")):
            return defer(session, task)
    connection = settings.head_connection(payload.get("prepared", {}).get("profile", "production"))
    if not payload.get("prepared"):
        request = OperatorHeadIntake(request_id=payload["request_id"], request=payload["request"])
        llm = SimpleNamespace(name=connection.provider, model=connection.model, complete_json=connection.complete)
        matcher = LLMSpecMatcher(llm, fail_on_unavailable=True)
        raw = request.request.theme.strip()
        match = matcher.match(raw, "scene_family")
        if match.holds:
            raise ValueError("원문 명세에 보류 사유가 있습니다")
        spec = match.spec.model_dump(mode="json")
        spec["raw_prompt"] = raw
        prepared = HeadRequest(
            request_id=payload["request_id"],
            artwork_id="operator-" + job_id,
            version="v1",
            prompt=raw,
            spec=spec,
            auto_publish=not request.request.review_final,
        )
        selection = ue_support.select_template(prepared)
        families = request.request.families
        allowed = {
            "object": {"gpu_particles", "image_stir"},
            "paint": {"paint"},
            "particles": {"gpu_particles"},
            "fluid": {"fluid"},
            "deformation": {"deformation"},
        }
        if families and selection["template"] not in allowed.get(str(families[0]), set()):
            raise ValueError("선택한 표현 방식과 원문으로 고른 UE 템플릿이 다릅니다")
        payload["prepared"] = prepared.model_dump(mode="json")
        payload["matching_sources"] = match.sources
    prepared = HeadRequest.model_validate(payload["prepared"])
    # 외부 호출이 끝난 뒤 소유권을 다시 잠그고 실제 접수까지 유지한다.
    with Session(engine) as session:
        task = jobs.owned(session, job_id, owner, token)
        task.payload = payload
        session.add(task)
        if not ready(session, settings, payload["prepared"]):
            return defer(session, task)
        workflow = dispatch.submit(session, prepared, connection, json.loads(settings.agent_keys_json))
        return {
            "workflow_id": workflow.id,
            "stage": "head_submitted",
            "source_artwork_id": payload.get("source_artwork_id"),
        }
