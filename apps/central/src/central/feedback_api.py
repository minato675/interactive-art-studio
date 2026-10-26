"""실제 시연 버전의 운영자 피드백. 파일을 읽거나 현재 시연을 변경하지 않는다."""
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from contracts.feedback import FeedbackArtwork, FeedbackChange, FeedbackCreate, FeedbackCurrent, FeedbackRecord
from db import jobs
from db.feedback import ArtworkFeedback

from .api import SessionDep
from .infra_api import operator

router = APIRouter(prefix="/infra/feedback", tags=["artwork-feedback"], dependencies=[Depends(operator)])


def artwork_for(session, publication):
    if not publication or publication.kind != "ue_publish" or publication.status != "succeeded":
        return None
    runtime = publication.payload.get("runtime") or {}
    status = publication.result.get("runtime_status") or {}
    source_id = runtime.get("source_job_id")
    version = "ue-" + source_id if source_id else ""
    source = session.get(jobs.Job, source_id) if source_id else None
    if (publication.result.get("phase") != "Applied" or status.get("phase") != "Applied"
            or status.get("active_version") != version or status.get("desired_version") != version
            or not status.get("runtime_session") or not status.get("runtime_pid")
            or publication.result.get("source_job_id") != source_id
            or not source or source.kind != "ue_assemble" or source.status != "succeeded"
            or not publication.node_id or runtime.get("target_node") != publication.node_id):
        return None
    assembly = source.payload.get("assembly") or {}
    if any(runtime.get(k) != assembly.get(k) for k in ("artwork_id", "version", "catalog_version")):
        return None
    spec, assets = assembly.get("spec") or {}, assembly.get("assets") or {}
    content = spec.get("content") or {}
    target = {"publication_job_id": publication.id, "node_id": publication.node_id,
              "artwork_id": runtime["artwork_id"], "version": runtime["version"], "runtime_version": version}
    return FeedbackArtwork(target=target, title=str(source.result.get("title") or content.get("theme") or
                           runtime["artwork_id"]), summary=str(spec.get("raw_prompt") or "작품 설명 미등록")[:600],
                           template=str(source.payload.get("_head", {}).get("template", {}).get("template") or "미확인"),
                           object_count=len(assets.get("objects") or []), layer_count=len(assets.get("layers") or []),
                           interactions=[" · ".join(str(r.get(k) or "미확인") for k in ("input", "reaction", "return"))
                                         for r in spec.get("interactions", [])],
                           preview_only=publication.payload.get("_approval", {}).get("scope") == "display_preview_only")


@router.get("/current", response_model=FeedbackCurrent)
def current(request: Request, session: SessionDep):
    timestamp = jobs.now(session)
    registry = json.loads(request.app.state.settings.agent_keys_json)
    shows = []
    for node in session.exec(select(jobs.Node).where(jobs.Node.role == "show").order_by(jobs.Node.id)).all():
        identity = registry.get(node.id, {})
        if not identity.get("enabled", True) or identity.get("role") != "show":
            continue
        report = node.show_report or {}
        online = 0 <= timestamp - node.heartbeat_at < 90
        report_at = report.get("_received_at")
        fresh = isinstance(report_at, (int, float)) and 0 <= timestamp - report_at < 90
        active = report.get("active_version")
        publication = session.exec(select(jobs.Job).where(
            jobs.Job.kind == "ue_publish", jobs.Job.status == "succeeded", jobs.Job.node_id == node.id,
            jobs.Job.result["runtime_status"]["active_version"].as_string() == active
        ).order_by(jobs.Job.created_at.desc())).first() if active else None
        artwork = artwork_for(session, publication)
        live = (online and fresh and bool(report.get("runtime_pid")) and bool(report.get("runtime_session"))
                and report.get("phase") in ("Applied", "Ready", "Staged"))
        reason = ("시연 노드 오프라인" if not online else "시연 보고 시각 미확인·오래된 보고" if not fresh else
                  "현재 활성 작품 미확인" if not live else "활성 버전과 일치하는 Applied 게시 근거 없음" if not artwork else
                  "실행기 활성 보고 확인 · 실제 LED 품질 별도 확인")
        shows.append({"node_id": node.id, "online": online, "phase": report.get("phase", "Unknown"),
                      "heartbeat_at": node.heartbeat_at, "report_at": report_at, "can_review": bool(live and artwork),
                      "reason": reason, "artwork": artwork})
    return {"observed_at": timestamp, "shows": shows}


def public(row):
    return row.model_dump(exclude={"fingerprint", "artwork_id", "version"})


@router.get("", response_model=list[FeedbackRecord])
def history(session: SessionDep, artwork_id: str | None = None, version: str | None = None,
            enabled: bool | None = None, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    query = select(ArtworkFeedback)
    for field, value in (("artwork_id", artwork_id), ("version", version), ("enabled", enabled)):
        if value is not None:
            query = query.where(getattr(ArtworkFeedback, field) == value)
    return [public(row) for row in session.exec(query.order_by(ArtworkFeedback.created_at.desc(), ArtworkFeedback.id)
                                               .offset(offset).limit(limit)).all()]


def save(session, body):
    fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    previous = session.exec(select(ArtworkFeedback).where(ArtworkFeedback.request_id == body.request_id)).first()
    if previous:
        if previous.fingerprint != fingerprint:
            raise jobs.Conflict("같은 request_id에 다른 피드백을 저장할 수 없습니다")
        return previous
    artwork = artwork_for(session, session.get(jobs.Job, body.target.publication_job_id))
    if not artwork or artwork.target != body.target:
        raise jobs.Conflict("피드백 대상의 작품·버전·시연 Applied 근거가 다릅니다")
    timestamp = jobs.now(session)
    values = body.model_dump(mode="json")
    row = ArtworkFeedback(**values, fingerprint=fingerprint, artwork_id=body.target.artwork_id,
                          version=body.target.version, artwork_title=artwork.title, preview_only=artwork.preview_only,
                          created_at=timestamp, updated_at=timestamp)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.post("", response_model=FeedbackRecord)
def submit(body: FeedbackCreate, session: SessionDep):
    try:
        return public(save(session, body))
    except IntegrityError:
        session.rollback()
        return public(save(session, body))


@router.patch("/{feedback_id}", response_model=FeedbackRecord)
def change(feedback_id: str, body: FeedbackChange, session: SessionDep):
    row = session.exec(select(ArtworkFeedback).where(ArtworkFeedback.id == feedback_id).with_for_update()
                       .execution_options(populate_existing=True)).first()
    if row is None:
        raise HTTPException(404, "없는 피드백")
    if row.revision != body.expected_revision:
        raise jobs.Conflict("피드백이 다른 요청에서 변경되었습니다. 다시 조회하세요")
    changed = session.execute(update(ArtworkFeedback).where(ArtworkFeedback.id == feedback_id,
                              ArtworkFeedback.revision == body.expected_revision).values(
                                  enabled=body.enabled, revision=body.expected_revision + 1,
                                  updated_at=jobs.now(session)))
    if changed.rowcount != 1:
        session.rollback()
        raise jobs.Conflict("피드백이 다른 요청에서 변경되었습니다. 다시 조회하세요")
    session.commit()
    session.refresh(row)
    return public(row)
