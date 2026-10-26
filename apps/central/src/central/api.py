"""운영 API. 상태를 바꾸는 요청은 OPERATOR_TOKEN이 설정되어 있으면 X-Operator-Token 헤더가 맞아야 한다."""
import random
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from contracts.families import FAMILIES
from contracts.schemas import ArtworkOut, CreateRequest, DraftEdit, ReviewDecision, Step
from db.models import Artwork, Asset

from .account_auth import identity
from .pipeline import (
    GATE_STATUS,
    DraftConflict,
    DraftNotFound,
    confirm,
    draft,
    draft_step,
    gate_files,
    gate_step,
    review,
    run,
)

router = APIRouter(prefix="/api")


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def require_operator(request: Request) -> None:
    identity(request, allow_local=True)


def _out(artwork: Artwork) -> ArtworkOut:
    out = ArtworkOut(id=artwork.id, status=artwork.status, title=artwork.title,
                     steps=[Step(**s) for s in artwork.steps], scene_path=artwork.scene_path)
    out.preparation = next((s["detail"] for s in reversed(artwork.steps)
                            if s["name"] == "파일 준비" and isinstance(s["detail"], dict)), None)
    saved = draft_step(artwork) if artwork.status == "awaiting" else None
    if saved:
        out.draft = {**saved["values"], "palette_names": saved["palette_names"]}
        out.review = [name for name, origin in saved["origins"].items() if origin != "user_explicit"]
    gate = gate_step(artwork) if artwork.status in GATE_STATUS.values() else None
    if gate:
        out.gate = gate["gate"]
        out.gate_files = sorted(gate_files(artwork))
        if gate["gate"] == "final":
            out.gate_targets = {name: gate["context"]["version"] for name in gate["context"]["reviewed"]}
    return out


def _rng(request: Request) -> random.Random | None:
    seed = request.app.state.seed
    return random.Random(seed) if seed is not None else None


@router.get("/families")
def families() -> list[dict]:
    """8가지 표현 방식(운영 화면 선택지)."""
    return [{"key": key.value, "label": spec.label, "needs": spec.needs, "optional": spec.optional,
             "reactions": spec.reactions, "status": spec.status} for key, spec in FAMILIES.items()]


@router.post("/artworks", dependencies=[Depends(require_operator)])
def create_artwork(body: CreateRequest, request: Request, session: SessionDep) -> ArtworkOut:
    state = request.app.state
    return _out(run(session, body, state.settings, state.planner, state.images, state.outlines, _rng(request),
                    state.input_agent, state.director))


@router.post("/drafts", dependencies=[Depends(require_operator)])
def create_draft(body: CreateRequest, request: Request, session: SessionDep) -> ArtworkOut:
    """편집 모드: AI가 의도·구성을 채운 초안을 만들고 멈춘다(status=awaiting)."""
    state = request.app.state
    try:
        return _out(draft(session, body, state.settings, state.planner, _rng(request), state.input_agent))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/artworks/{artwork_id}/confirm", dependencies=[Depends(require_operator)])
def confirm_draft(artwork_id: int, body: DraftEdit, request: Request, session: SessionDep) -> ArtworkOut:
    """편집 모드: 바꾼 칸만 보내 확정한다. 맞지 않는 값이면 422이고 초안은 계속 기다린다."""
    state = request.app.state
    try:
        return _out(confirm(session, artwork_id, body, state.settings, state.planner, state.images, state.outlines,
                            _rng(request)))
    except DraftNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DraftConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/artworks/{artwork_id}/review", dependencies=[Depends(require_operator)])
def review_artwork(artwork_id: int, body: ReviewDecision, request: Request, session: SessionDep) -> ArtworkOut:
    """검수 대기 작품(asset_review·final_review) 승인·반려. 승인이면 남은 제작이 끝날 때까지 기다린다."""
    try:
        return _out(review(session, artwork_id, body, request.app.state.settings))
    except DraftNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DraftConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/artworks/{artwork_id}/review/files/{name}")
def review_file(artwork_id: int, name: str, session: SessionDep) -> FileResponse:
    """소재 검수 대기 작품의 소재 파일. 그 작품 검수 기록에 있는 이름만 내보낸다."""
    artwork = session.get(Artwork, artwork_id)
    path = gate_files(artwork).get(name) if artwork else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="검수 파일이 아님")
    return FileResponse(path)


@router.get("/artworks")
def list_artworks(session: SessionDep, limit: int = 20) -> list[ArtworkOut]:
    rows = session.exec(select(Artwork).order_by(Artwork.id.desc()).limit(min(limit, 100))).all()
    return [_out(row) for row in rows]


@router.get("/artworks/{artwork_id}")
def get_artwork(artwork_id: int, session: SessionDep) -> ArtworkOut:
    artwork = session.get(Artwork, artwork_id)
    if not artwork:
        raise HTTPException(status_code=404, detail="없는 작품")
    return _out(artwork)


@router.get("/pool")
def pool(session: SessionDep, kind: str | None = None) -> list[Asset]:
    query = select(Asset).order_by(Asset.id.desc())
    if kind:
        query = query.where(Asset.kind == kind)
    return list(session.exec(query.limit(200)).all())


@router.post("/pool/{asset_id}/reject", dependencies=[Depends(require_operator)])
def reject(asset_id: int, session: SessionDep) -> Asset:
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="없는 소재")
    asset.status = "rejected"
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset
