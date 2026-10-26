"""시연 대시보드 읽기와 중앙 구성 저장. 실행은 기존 명시적 게시 승인 경로를 사용한다."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from contracts.show_dashboard import ShowPlan, ShowPlanUpdate
from db import jobs
from db.show_dashboard import ShowConfiguration

from .api import SessionDep
from .feedback_api import current
from .infra_api import operator

router = APIRouter(prefix="/infra/show-dashboard", tags=["show-dashboard"], dependencies=[Depends(operator)])


def show_nodes(request):
    return {node: value for node, value in json.loads(request.app.state.settings.agent_keys_json).items()
            if value.get("role") == "show" and value.get("enabled", True) and "ue_publish" in value.get("job_kinds", [])}


def candidate(session, row):
    if not row or row.kind != "ue_verify" or row.status != "succeeded":
        return None
    result = row.result or {}
    review = result.get("human_review", {}).get("request", {}).get("decision")
    if review == "reject" or not (result.get("verdict") == "pass" or
                                  (result.get("verdict") == "review" and review == "approve")):
        return None
    if row.payload.get("_approval", {}).get("scope") == "display_preview_only":
        return None
    runtime = row.payload.get("runtime") or {}
    source = session.get(jobs.Job, runtime.get("source_job_id", ""))
    if not source or source.kind != "ue_assemble" or source.status != "succeeded":
        return None
    assembly = source.payload.get("assembly") or {}
    if any(assembly.get(k) != runtime.get(k) or not runtime.get(k)
           for k in ("artwork_id", "version", "catalog_version")):
        return None
    image = next((f for f in source.result.get("files", [])
                  if f.get("name", "").lower().endswith(".png") and f.get("size", 0) <= 16 * 1024 * 1024), None)
    return {"verification_job_id": row.id, "source_job_id": source.id,
            "preview": {"job_id": source.id, **image} if image else None,
            **{k: runtime[k] for k in ("artwork_id", "version", "catalog_version")},
            "title": str(source.result.get("title") or assembly.get("spec", {}).get("content", {}).get("theme")
                         or runtime["artwork_id"]), "verdict": result["verdict"], "created_at": row.created_at}


def configuration(session, node_id):
    row = session.get(ShowConfiguration, node_id)
    node = session.get(jobs.Node, node_id)
    return {"node_id": node_id, "revision": row.revision if row else 0,
            "plan": ShowPlan.model_validate(row.plan if row else {}).model_dump(),
            "updated_at": row.updated_at if row else None,
            "automatic_playback_connected": bool(node and (node.show_report or {}).get("playback"))}


def library_page(session, offset, search, limit=50):
    result, matched = [], 0
    # 승인되지 않은 작업이 많아도 과거 승인 작품을 조회할 수 있게 DB 페이지를 계속 탐색한다.
    position = 0
    while True:
        rows = session.exec(select(jobs.Job).where(jobs.Job.kind == "ue_verify", jobs.Job.status == "succeeded")
            .order_by(jobs.Job.created_at.desc(), jobs.Job.id).offset(position).limit(200)).all()
        for row in rows:
            item = candidate(session, row)
            if not item or search.casefold() not in (item["title"] + " " + item["artwork_id"]).casefold():
                continue
            if matched >= offset:
                result.append(item)
                if len(result) > limit:
                    return result[:limit], True
            matched += 1
        if len(rows) < 200:
            return result, False
        position += 200


@router.get("")
def snapshot(request: Request, session: SessionDep, node_id: str | None = None,
             library_offset: int = Query(default=0, ge=0), search: str = Query(default="", max_length=100)):
    registry = show_nodes(request)
    if node_id and node_id not in registry:
        raise HTTPException(404, "등록된 시연 노드가 아닙니다")
    selected = node_id or next(iter(registry), None)
    observed = current(request, session)
    reports = {row["node_id"]: row for row in observed["shows"]}
    nodes = [{"node_id": key, "catalog_version": value.get("catalog_version"),
              **reports.get(key, {"online": False, "can_review": False, "reason": "시연 노드 보고 대기",
                                 "artwork": None, "phase": "Unknown", "heartbeat_at": None, "report_at": None})}
             for key, value in registry.items()]
    for node in nodes:
        stored = session.get(jobs.Node, node["node_id"])
        report = stored.show_report if stored else {}
        node["playback"] = (report or {}).get("playback")
        node["active_version"] = (report or {}).get("active_version")
        for key in ("desired_version", "runtime_pid", "runtime_session", "error"):
            node[key] = (report or {}).get(key)
    library, more = library_page(session, library_offset, search)
    config = configuration(session, selected) if selected else None
    plan_artworks = [item for row in (config["plan"]["items"] if config else [])
                    if (item := candidate(session, session.get(jobs.Job, row["verification_job_id"])))]
    current_artworks = [item for node in nodes
                       if (key := (node.get("playback") or {}).get("current_verification_job_id"))
                       if (item := candidate(session, session.get(jobs.Job, key)))]
    return {"observed_at": observed["observed_at"], "nodes": nodes, "library": library,
            "library_offset": library_offset, "library_has_more": more,
            "plan_artworks": plan_artworks, "current_artworks": current_artworks, "configuration": config}


@router.put("/{node_id}")
def save(node_id: str, body: ShowPlanUpdate, request: Request, session: SessionDep):
    registry = show_nodes(request)
    if node_id not in registry:
        raise HTTPException(404, "등록된 시연 노드가 아닙니다")
    row = session.get(ShowConfiguration, node_id)
    previous_ids = {item["verification_job_id"] for item in row.plan["items"]} if row else set()
    for item in body.plan.items:
        artwork = candidate(session, session.get(jobs.Job, item.verification_job_id))
        if not artwork or artwork["catalog_version"] != registry[node_id].get("catalog_version"):
            if item.verification_job_id in previous_ids and (not body.plan.autoplay or not item.enabled):
                continue  # 승인 취소 작품이 남아 있어도 안전한 정지·비활성 설정은 저장할 수 있다.
            raise jobs.Conflict("검수·원본·카탈로그를 확인할 수 없는 작품은 구성에 저장할 수 없습니다")
    if (row.revision if row else 0) != body.expected_revision:
        raise jobs.Conflict("다른 운영자가 구성을 변경했습니다. 서버 구성을 다시 불러오세요")
    values = {"plan": body.plan.model_dump(mode="json"), "updated_at": jobs.now(session)}
    if row is None:
        session.add(ShowConfiguration(node_id=node_id, **values))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise jobs.Conflict("다른 운영자가 먼저 구성을 저장했습니다") from None
    else:
        changed = session.execute(update(ShowConfiguration).where(ShowConfiguration.node_id == node_id,
            ShowConfiguration.revision == body.expected_revision).values(**values, revision=body.expected_revision + 1))
        if changed.rowcount != 1:
            session.rollback()
            raise jobs.Conflict("다른 운영자가 구성을 변경했습니다")
        session.commit()
        session.expire_all()
    return configuration(session, node_id)
