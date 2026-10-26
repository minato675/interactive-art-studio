"""전시 자동 순환 켜기·끄기·상태. 순환 자체는 Dispatcher·Worker(worker.py)가 돌린다."""

from fastapi import APIRouter, Depends, Request

from contracts.head import ExhibitionFeedToggle
from studio.head import exhibition_feed

from .api import SessionDep
from .infra_api import operator

router = APIRouter(prefix="/infra/exhibition/feed", tags=["exhibition"], dependencies=[Depends(operator)])


@router.get("")
def status(request: Request, session: SessionDep):
    return exhibition_feed.status(session, request.app.state.settings)


@router.put("")
def toggle(body: ExhibitionFeedToggle, request: Request, session: SessionDep):
    return exhibition_feed.toggle(session, request.app.state.settings, body.enabled)
