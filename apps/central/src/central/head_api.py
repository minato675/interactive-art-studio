"""인증된 운영자만 Head 흐름을 시작하거나 배경 파일을 전송한다."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from contracts.head import BackgroundInput, HeadRequest
from db.head import HeadWorkflow
from studio.head import dispatch, ue_support

from .api import SessionDep
from .infra_api import operator

router = APIRouter(prefix="/infra/head", tags=["head"], dependencies=[Depends(operator)])


@router.post("/preflight")
def preflight(body: HeadRequest, request: Request, session: SessionDep):
    result = ue_support.preflight(session, body, json.loads(request.app.state.settings.agent_keys_json))
    selected = result["selection"]
    return {**result, "background": ue_support.conditions(selected, "background").model_dump(mode="json"),
            "assets": [ue_support.conditions(selected, "object", i).model_dump(mode="json")
                       for i in range(1 if body.asset_count is None else body.asset_count)], "runtime_verified": False}


@router.post("/workflows", status_code=202)
def submit(body: HeadRequest, request: Request, session: SessionDep):
    try:
        connection = request.app.state.settings.head_connection(body.profile)
    except ValueError:
        raise HTTPException(503, "Head API 공급자·키 설정이 필요합니다") from None
    try:
        row = dispatch.submit(session, body, connection, json.loads(request.app.state.settings.agent_keys_json))
    except IntegrityError:
        session.rollback()
        row = dispatch.submit(session, body, connection, json.loads(request.app.state.settings.agent_keys_json))
    return dispatch.public(session, row)


@router.get("/templates")
def templates(request: Request, session: SessionDep):
    return ue_support.overview(session, json.loads(request.app.state.settings.agent_keys_json))


@router.get("/workflows/{workflow_id}")
def status(workflow_id: str, session: SessionDep):
    row = session.get(HeadWorkflow, workflow_id)
    if not row:
        raise HTTPException(404, "없는 Head 흐름")
    return dispatch.public(session, row)


@router.post("/workflows/{workflow_id}/background-input", status_code=202)
def background(workflow_id: str, body: BackgroundInput, request: Request, session: SessionDep):
    row = dispatch.attach_background(session, workflow_id, body, request.app.state.settings.data_dir)
    return dispatch.public(session, row)
