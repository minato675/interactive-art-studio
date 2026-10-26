"""PostgreSQL을 기준으로 한 영속 작업함. 앱/계약 패키지를 import하지 않는다."""
import hashlib
import json
import secrets
import time
import uuid

from sqlalchemy import JSON, Column, func
from sqlalchemy import select as sa_select
from sqlalchemy.orm import aliased
from sqlmodel import Field, Session, SQLModel, select


class Job(SQLModel, table=True):
    __tablename__ = "infra_job"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    request_id: str = Field(unique=True, index=True)
    fingerprint: str
    kind: str
    queue: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    result: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    error: str = ""
    progress: int = 0
    created_at: float
    dispatch_after: float = 0
    attempt: int = 0
    max_attempts: int = 3
    node_id: str | None = None
    token: str | None = None
    lease_until: float = 0
    catalog_version: str = "v1"


class Attempt(SQLModel, table=True):
    __tablename__ = "infra_attempt"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    job_id: str = Field(index=True)
    number: int
    node_id: str
    started_at: float
    ended_at: float | None = None
    status: str = "running"


class Node(SQLModel, table=True):
    __tablename__ = "infra_node"
    id: str = Field(primary_key=True)
    role: str
    catalog_version: str
    heartbeat_at: float = 0
    active_job: str | None = None
    show_report: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class Dependency(SQLModel, table=True):
    __tablename__ = "infra_dependency"
    job_id: str = Field(foreign_key="infra_job.id", primary_key=True)
    prerequisite_id: str = Field(foreign_key="infra_job.id", primary_key=True)


class Conflict(ValueError):
    pass


def now(session: Session) -> float:
    if session.bind.dialect.name == "postgresql":
        return float(session.scalar(sa_select(func.extract("epoch", func.clock_timestamp()))))
    return time.time()  # 기존 SQLite 단위 테스트 전용. 분산 실행은 PostgreSQL.


def enqueue(session: Session, request: dict, max_attempts: int = 3, *, commit: bool = True) -> Job:
    if request.get("generation") and request["generation"].get("conditions") is None:
        request = {**request, "generation": {k: v for k, v in request["generation"].items() if k != "conditions"}}
    if request.get("assembly") and request["assembly"].get("target_node") is None:
        request = {**request, "assembly": {k: v for k, v in request["assembly"].items() if k != "target_node"}}
    request = {k: v for k, v in request.items() if k not in ("generation", "assembly", "runtime", "depends_on") or v}
    if request.get("file_transport") == "shared":
        request.pop("file_transport")  # 기존 요청의 fingerprint 호환
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    existing = session.exec(select(Job).where(Job.request_id == request["request_id"])).first()
    if existing:
        if existing.fingerprint != fingerprint:
            raise Conflict("같은 request_id에 다른 요청을 사용할 수 없습니다")
        return existing
    dependencies = request.get("depends_on", [])
    parents = [session.get(Job, parent_id) for parent_id in dependencies]
    spec = request.get("generation") or request.get("assembly") or request.get("runtime") or {}
    for parent in parents:
        if parent is None:
            raise Conflict("선행 작업이 없습니다")
        parent_spec = parent.payload.get("generation") or parent.payload.get("assembly") or parent.payload.get("runtime") or {}
        if not spec or any(spec.get(key) != parent_spec.get(key) for key in ("artwork_id", "version", "catalog_version")):
            raise Conflict("선행 작업은 같은 작품·버전·카탈로그의 생성 작업이어야 합니다")
    if request["kind"] == "assets_generate" and not any(p.kind == "background_generate" for p in parents):
        raise Conflict("에셋 생성에는 배경 생성 작업이 선행해야 합니다")
    if request["kind"] == "ue_assemble" and any(p.kind not in ("background_generate", "assets_generate") for p in parents):
        raise Conflict("UE 조립의 선행 작업은 생성 작업이어야 합니다")
    if request["kind"] in ("ue_verify", "ue_publish"):
        source = session.get(Job, spec["source_job_id"])
        if source is None or source.kind != "ue_assemble":
            raise Conflict("실행 입력은 UE 조립 결과여야 합니다")
        if request["kind"] == "ue_verify" and len(parents) != 1:
            raise Conflict("검증에는 조립 작업 하나만 선행합니다")
    queues = {"bundle_verify": "assembly", "ue_assemble": "assembly", "ue_verify": "verification",
              "ue_publish": "exhibition",
              "background_generate": "background", "assets_generate": "assets"}
    row = Job(request_id=request["request_id"], fingerprint=fingerprint, kind=request["kind"],
              queue=queues.get(request["kind"], "planning"),
              payload=request, created_at=now(session), max_attempts=max_attempts,
              catalog_version=(request.get("bundle") or spec).get("catalog_version", "v1"))
    session.add(row)
    session.flush()
    for parent_id in dependencies:
        session.add(Dependency(job_id=row.id, prerequisite_id=parent_id))
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(row)
    return row


def attempt_end(session: Session, job: Job, state: str, timestamp: float):
    attempt = session.exec(select(Attempt).where(Attempt.job_id == job.id, Attempt.number == job.attempt)).one()
    attempt.status, attempt.ended_at = state, timestamp
    session.add(attempt)


def recover(session: Session, job: Job, timestamp: float):
    if job.status == "running" and job.lease_until <= timestamp:
        attempt_end(session, job, "expired", timestamp)
        job.status = "queued" if job.attempt < job.max_attempts else "failed"
        job.error, job.token, job.lease_until = "lease expired", None, 0
        job.dispatch_after = 0
        session.add(job)


def start(session: Session, job: Job, owner: str, seconds: int) -> Job:
    timestamp = now(session)
    recover(session, job, timestamp)
    if job.status != "queued":
        raise Conflict("대기 중인 작업이 아닙니다")
    job.status, job.node_id, job.token = "running", owner, secrets.token_urlsafe(32)
    job.attempt += 1
    job.lease_until, job.progress, job.error = timestamp + seconds, 0, ""
    session.add(job)
    session.add(Attempt(job_id=job.id, number=job.attempt, node_id=owner, started_at=timestamp))
    session.commit()
    session.refresh(job)
    return job


def claim_node(session: Session, node_id: str, seconds: int, kinds: list[str] | None = None) -> Job | None:
    node = session.exec(select(Node).where(Node.id == node_id).with_for_update()
                        .execution_options(populate_existing=True)).one()
    queues = {"production": ["assembly", "verification"], "generation": ["background", "assets"], "show": ["exhibition"]}.get(node.role)
    if node.role == "show" and (kinds is None or "ue_publish" not in kinds):
        raise Conflict("시연 작업 임대에는 명시적인 ue_publish 등록이 필요합니다")
    if queues is None:
        raise Conflict("이 역할은 제작 작업을 임대할 수 없습니다")
    kinds = kinds if kinds is not None else (["bundle_verify"] if node.role == "production"
                                            else ["ue_publish"] if node.role == "show" else ["background_generate", "assets_generate"])
    timestamp = now(session)
    if node.active_job:
        active = session.exec(select(Job).where(Job.id == node.active_job).with_for_update()
                              .execution_options(populate_existing=True)).one()
        recover(session, active, timestamp)
        if active.status == "running" and active.node_id == node.id:
            raise Conflict("이 노드는 이미 작업 중입니다")
    # 만료 작업을 먼저 반환하고, 동일 카탈로그의 공통 대기열에서 하나만 선택한다.
    prerequisite = aliased(Job)
    blocked = select(Dependency.job_id).join(prerequisite, Dependency.prerequisite_id == prerequisite.id).where(
        prerequisite.status != "succeeded")
    candidates = session.exec(select(Job).where(
        ((Job.payload["runtime"]["target_node"].as_string().is_(None)) |
         (Job.payload["runtime"]["target_node"].as_string() == node_id)),
        ((Job.payload["assembly"]["target_node"].as_string().is_(None)) |
         (Job.payload["assembly"]["target_node"].as_string() == node_id)),
        Job.queue.in_(queues), Job.kind.in_(kinds), Job.catalog_version == node.catalog_version, Job.id.not_in(blocked),
        (Job.status == "queued") | ((Job.status == "running") & (Job.lease_until <= timestamp))
    ).order_by(Job.created_at).limit(1).with_for_update(skip_locked=True)
      .execution_options(populate_existing=True)).all()
    for job in candidates:
        recover(session, job, timestamp)
        if job.status == "queued":
            node.active_job = job.id
            session.add(node)
            return start(session, job, node_id, seconds)
    node.active_job = None
    session.add(node)
    session.commit()
    return None


def owned(session: Session, job_id: str, node_id: str, token: str) -> Job:
    job = session.exec(select(Job).where(Job.id == job_id).with_for_update()
                       .execution_options(populate_existing=True)).first()
    if (job is None or job.status != "running" or job.node_id != node_id or
            not secrets.compare_digest(job.token or "", token) or job.lease_until <= now(session)):
        raise Conflict("임대가 만료되었거나 작업 소유권이 다릅니다")
    return job


def heartbeat(session: Session, job_id: str, node_id: str, token: str, seconds: int, progress: int):
    job = owned(session, job_id, node_id, token)
    job.lease_until = now(session) + seconds
    job.progress = max(job.progress, min(progress, 99))
    session.add(job)
    session.commit()


def finish(session: Session, job_id: str, node_id: str, token: str, result: dict, error: str = "", *, commit: bool = True):
    job = owned(session, job_id, node_id, token)
    job.status = "failed" if error else "succeeded"
    job.result, job.error, job.progress = result, error, 100
    attempt_end(session, job, job.status, now(session))
    job.token, job.lease_until = None, 0
    session.add(job)
    if commit:
        session.commit()
    else:
        session.flush()


def public_job(job: Job) -> dict:
    bundle = job.payload.get("bundle") or job.payload.get("generation") or job.payload.get("assembly") or job.payload.get("runtime") or {}
    result = {k: v for k, v in job.result.items() if not k.startswith("_")}
    return {**job.model_dump(exclude={"token", "fingerprint", "payload"}), "result": result,
            "artwork_id": bundle.get("artwork_id") or job.result.get("artwork_id"),
            "artwork_version": bundle.get("version"), "depends_on": job.payload.get("depends_on", []),
            **({"approval": job.payload["_approval"]} if "_approval" in job.payload else {})}


def retry(session: Session, job_id: str):
    job = session.exec(select(Job).where(Job.id == job_id).with_for_update()
                       .execution_options(populate_existing=True)).first()
    if not job or job.status != "failed" or job.attempt >= job.max_attempts:
        raise Conflict("시도 한도가 남은 실패 작업만 재시도할 수 있습니다")
    job.status, job.progress, job.dispatch_after = "queued", 0, 0
    job.result, job.error, job.token, job.node_id, job.lease_until = {}, "", None, None, 0
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def complete_generation(session: Session, job_id: str, node_id: str, token: str, result: dict, error: str, *, commit: bool = True):
    # 응답 유실 후 동일 완료 재전송만 허용한다. 이전 시도나 변경된 결과는 거부한다.
    digest = hashlib.sha256(token.encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps([result, error], sort_keys=True).encode()).hexdigest()
    row = session.exec(select(Job).where(Job.id == job_id).with_for_update()
                       .execution_options(populate_existing=True)).first()
    if (row and row.node_id == node_id and row.status in ("succeeded", "failed")
            and secrets.compare_digest(row.result.get("_receipt", ""), digest)
            and row.result.get("_fingerprint") == fingerprint):
        return
    owned(session, job_id, node_id, token)
    finish(session, job_id, node_id, token,
           {**result, "_receipt": digest, "_fingerprint": fingerprint,
            "verification": {"ue_assemble": "assembly_report_only", "ue_verify": "runtime_report",
                             "ue_publish": "runtime_applied_report"}.get(row.kind, "node_report_only"),
            "publish_approved": row.kind == "ue_publish" and not error}, error, commit=commit)
