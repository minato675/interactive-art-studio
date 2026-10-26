"""운영 앱 원문을 비동기 Head 접수 작업으로 등록한다."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from contracts.head import OperatorHeadIntake
from db import jobs
from studio.head import intake

from .api import SessionDep
from .infra_api import operator

router = APIRouter(prefix="/infra/head/intakes", tags=["head"], dependencies=[Depends(operator)])


@router.post("", status_code=202)
def submit(body: OperatorHeadIntake, session: SessionDep):
    try:
        row = intake.submit(session, body)
    except IntegrityError:
        session.rollback()
        row = intake.submit(session, body)
    return jobs.public_job(row)


@router.get("")
def listing(session: SessionDep, limit: int = Query(default=30, ge=1, le=100)):
    rows = session.exec(
        select(jobs.Job).where(jobs.Job.kind == "head_intake").order_by(jobs.Job.created_at.desc()).limit(limit)
    ).all()
    return [jobs.public_job(row) for row in rows]
