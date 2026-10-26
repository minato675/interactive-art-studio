"""Linux Celery Worker. RabbitMQ는 작업 ID만 전달하고 DB가 실행 상태를 결정한다."""
import json
import logging
import os
import socket
import threading
import time

from celery import Celery
from sqlmodel import Session, select

from contracts.schemas import CreateRequest
from db import jobs
from db.engine import make_engine
from studio.head import dispatch as head_dispatch
from studio.head import exhibition_feed
from studio.head import intake as head_intake
from studio.head.llm import make_llm, with_fallback
from studio.head.planning import LLMPlanner, RulePlanner
from studio.head.responses import HeadAPIUnavailable
from studio.head.spec_matching import LLMSpecMatcher

from .config import Settings
from .pipeline import draft

settings = Settings()
celery = Celery("ranel", broker=settings.broker_url or "memory://")
celery.conf.update(
    task_serializer="json", accept_content=["json"], result_serializer="json", task_ignore_result=True,
    task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True, broker_connection_timeout=5,
    task_default_queue=settings.planning_queue, task_default_delivery_mode="persistent",
    broker_transport_options={"confirm_publish": True},
    task_publish_retry=False, worker_hijack_root_logger=False,
)
engine = make_engine(settings.db_url)
log = logging.getLogger("ranel.jobs")


def event(name: str, job: jobs.Job, session: Session):
    attempt = session.exec(select(jobs.Attempt).where(
        jobs.Attempt.job_id == job.id, jobs.Attempt.number == job.attempt)).one()
    log.warning(json.dumps({"event": name, "job_id": job.id, "attempt": job.attempt,
                            "attempt_id": attempt.id, "node_id": job.node_id, "kind": job.kind,
                            "artwork_id": job.result.get("artwork_id")}, ensure_ascii=False))


def feed_llm(settings: Settings):
    """전시 자동 순환의 AI 디렉터 · Input Agent 연결. LLM_FALLBACK을 쓰지 않아 연결 실패가 곧 GMS 장애가 된다."""
    return make_llm("gms", *settings.llm_args("gms", director=True))


@celery.task(name="ranel.execute")
def execute(job_id: str):
    owner = f"celery-{socket.gethostname()}-{os.getpid()}"
    with Session(engine) as session:
        row = session.exec(select(jobs.Job).where(jobs.Job.id == job_id).with_for_update()).first()
        if not row or row.queue != "planning":
            return
        try:
            row = jobs.start(session, row, owner, settings.lease_seconds)
        except jobs.Conflict:
            session.commit()  # 만료 후 최대 시도에 도달한 상태도 저장
            return
        token, kind, payload = row.token, row.kind, row.payload
        event("started", row, session)
    stop = threading.Event()

    def renew():
        while not stop.wait(settings.lease_seconds / 3):
            try:
                with Session(engine) as session:
                    jobs.heartbeat(session, job_id, owner, token, settings.lease_seconds, 10)
            except Exception:  # noqa: BLE001 — 작업 경계에서 소유권 갱신 중단
                # 결과 확정 시에도 소유권을 검사하므로 갱신 실패 뒤 늦은 완료는 반영되지 않는다.
                log.warning(json.dumps({"event": "heartbeat_failed", "job_id": job_id}))
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    result, error = {}, ""
    try:
        if kind == "diagnostic":
            result = {"worker": "celery", "transport": "rabbitmq", "database": engine.dialect.name}
        elif kind == "draft":
            llm = with_fallback(make_llm(settings.llm_provider, *settings.llm_args(settings.llm_provider)),
                                make_llm(settings.llm_fallback, *settings.llm_args(settings.llm_fallback)))
            planner = LLMPlanner(llm) if llm else RulePlanner()
            input_agent = LLMSpecMatcher(llm) if llm else None
            with Session(engine) as session:
                artwork = draft(session, CreateRequest.model_validate(payload["draft"]), settings, planner,
                                input_agent=input_agent)
                result = {"artwork_id": artwork.id, "status": artwork.status}
                if artwork.status == "failed":
                    error = "draft_failed"
        elif kind == "head_intake":
            result = head_intake.process(engine, settings, job_id, owner, token)
            if result.pop('_deferred', False):
                return
        elif kind == "head_decide":
            result = head_dispatch.decide(engine, settings, job_id, owner, token)
        elif kind == exhibition_feed.KIND:
            result = exhibition_feed.run(engine, settings, job_id, owner, token, feed_llm(settings))
            if result.get("outcome") == "exhausted":
                error = exhibition_feed.EXHAUSTED
        else:
            raise ValueError("지원하지 않는 작업")
    except Exception as exc:  # noqa: BLE001 — 외부 작업 실패를 영속 상태로 기록
        error = type(exc).__name__  # 키/URL/프롬프트가 예외 문자열로 로그에 유출되지 않도록 제한
        if isinstance(exc, HeadAPIUnavailable):
            error = "LLMUnavailable"
            result = {"error_detail": dict(exc.diagnostic)}
            log.warning(json.dumps({"event": "head_api_failed", "job_id": job_id, **exc.diagnostic}))
    finally:
        stop.set()
        thread.join(timeout=5)
    with Session(engine) as session:
        try:
            jobs.finish(session, job_id, owner, token, result, error)
            if result.get("error_detail"):
                # retry는 현재 result를 비우므로 시도별 진단은 데이터 볼륨에도 별도로 보존한다.
                row = session.get(jobs.Job, job_id)
                folder = settings.data_dir / "head-errors" / job_id
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / f"attempt-{row.attempt}.json").write_text(json.dumps(
                        {"job_id": job_id, "attempt": row.attempt, "error_detail": result["error_detail"]}),
                        encoding="utf-8")
                except OSError:
                    log.warning(json.dumps({"event": "head_diagnostic_file_unavailable", "job_id": job_id}))
            event("failed" if error else "succeeded", session.get(jobs.Job, job_id), session)
        except jobs.Conflict:
            log.warning(json.dumps({"event": "stale_result_rejected", "job_id": job_id}))


def dispatch_once():
    """DB 작업함 자체가 outbox. publish 직후 죽어도 중복 전달을 DB 임대로 걸러낸다."""
    with Session(engine) as session:
        head_intake.wake(session, settings)
        head_dispatch.advance(session, settings)
        exhibition_feed.tick(session, settings)   # DB만 보고 디렉터 작업을 넣는다. LLM은 Worker가 부른다
        timestamp = jobs.now(session)
        rows = session.exec(select(jobs.Job).where(
            ((jobs.Job.status == "queued") & (jobs.Job.queue == "planning")) |
            ((jobs.Job.status == "running") & (jobs.Job.lease_until <= timestamp)),
            jobs.Job.dispatch_after <= timestamp
        ).order_by(jobs.Job.created_at).limit(100).with_for_update(skip_locked=True)).all()
        for row in rows:
            jobs.recover(session, row, timestamp)
            if row.status == "queued" and row.queue == "planning":
                celery.send_task("ranel.execute", args=[row.id], queue=settings.planning_queue)
                row.dispatch_after = timestamp + 30
                session.add(row)
        session.commit()


if __name__ == "__main__":
    if not settings.broker_url:
        raise SystemExit("Dispatcher에는 BROKER_URL이 필요합니다")
    while True:
        try:
            dispatch_once()
        except Exception as exc:  # noqa: BLE001 — DB/브로커 복구 뒤 동일 outbox 재시도
            log.warning(json.dumps({"event": "dispatch_retry", "error": type(exc).__name__}))
        time.sleep(2)
