"""verdict=review의 사람 검수 대기열과 승인·반려 이력."""
from fastapi import APIRouter, Depends
from sqlmodel import select

from contracts.runtime import HumanReview
from db import jobs

from .api import SessionDep
from .infra_api import operator

router = APIRouter(prefix="/infra/runtime", tags=["runtime"], dependencies=[Depends(operator)])


def record_review(session, row, body):
    if (not row or row.kind != "ue_verify" or row.status != "succeeded"
            or row.result.get("verdict") != "review" or row.attempt != body.expected_attempt):
        raise jobs.Conflict("현재 시도의 review 판정만 사람 검수할 수 있습니다")
    decision = body.model_dump()
    previous = row.result.get("human_review")
    if previous:
        if previous["request"] != decision:
            raise jobs.Conflict("이미 확정된 사람 검수입니다")
    else:
        row.result = {**row.result,
            "review_state": "human_review_approved" if body.decision == "approve" else "human_review_rejected",
            "human_review": {"request": decision, "at": jobs.now(session), "authority": "operator_token"}}
        session.add(row)


@router.get("/reviews")
def reviews(session: SessionDep):
    rows = session.exec(select(jobs.Job).where(jobs.Job.kind == "ue_verify", jobs.Job.status == "succeeded",
        jobs.Job.result["verdict"].as_string() == "review").order_by(jobs.Job.created_at)).all()
    return [jobs.public_job(row) for row in rows if not row.result.get("human_review")]


@router.post("/jobs/{job_id}/review")
def review(job_id: str, body: HumanReview, session: SessionDep):
    row = session.exec(select(jobs.Job).where(jobs.Job.id == job_id).with_for_update()
                       .execution_options(populate_existing=True)).first()
    record_review(session, row, body)
    session.commit()
    return jobs.public_job(row)
