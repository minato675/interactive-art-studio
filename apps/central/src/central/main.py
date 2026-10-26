"""uv run uvicorn central.main:app  (기본 127.0.0.1:8000 — 운영 화면은 /, API 문서는 /docs)"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db.engine import create_tables, make_engine
from db.jobs import Conflict
from studio.generation.images import LocalImage
from studio.generation.subjects import LocalShapes
from studio.head.exhibition_director import ExhibitionDirector
from studio.head.llm import make_llm, with_fallback
from studio.head.planning import LLMPlanner, RulePlanner
from studio.head.spec_matching import LLMSpecMatcher

from .account_api import router as account_router
from .api import require_operator, router
from .config import Settings
from .exhibition_feed_api import router as exhibition_feed_router
from .feedback_api import router as feedback_router
from .file_api import router as file_router
from .head_api import router as head_router
from .head_intake_api import router as head_intake_router
from .infra_api import router as infra_router
from .native_artworks_api import router as native_artworks_router
from .runtime_api import router as runtime_review_router
from .runtime_flow import router as runtime_router
from .show_dashboard_api import router as show_dashboard_router
from .show_playback_api import router as show_playback_router

WEB_DIR = Path(__file__).resolve().parent / "web"


def create_app(settings: Settings | None = None, planner=None, images=None, seed: int | None = None,
               input_agent=None, director=None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.db_url)
    if planner is None:
        backup = make_llm(settings.llm_fallback, *settings.llm_args(settings.llm_fallback))
        llm = with_fallback(make_llm(settings.llm_provider, *settings.llm_args(settings.llm_provider)), backup)
        planner = LLMPlanner(llm) if llm else RulePlanner()
        # 사용자 제작의 주인공 판단(pipeline._input_agent). LLM이 없으면 규칙 기획만 쓴다.
        input_agent = input_agent or (LLMSpecMatcher(llm) if llm else None)
        # 전시 모드의 작품 방향(pipeline._direct). GMS 모델만 DIRECTOR_MODEL로 따로 둔다. LLM이 없으면 프리셋 규칙만 쓴다.
        director_llm = with_fallback(
            make_llm(settings.llm_provider, *settings.llm_args(settings.llm_provider, director=True)), backup)
        director = director or (ExhibitionDirector(director_llm) if director_llm else None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        create_tables(engine)
        yield
        engine.dispose()

    app = FastAPI(title="Studio — 인터랙티브 미디어아트 생성", lifespan=lifespan)
    app.state.settings, app.state.engine = settings, engine
    app.state.planner, app.state.seed, app.state.input_agent = planner, seed, input_agent
    app.state.director = director
    app.state.images = images if images is not None else LocalImage()
    app.state.outlines = LocalShapes()
    app.include_router(account_router)
    app.include_router(router, dependencies=[Depends(require_operator)])
    app.include_router(infra_router)
    app.include_router(native_artworks_router)
    app.include_router(file_router)
    app.include_router(feedback_router)
    app.include_router(show_dashboard_router)
    app.include_router(show_playback_router)
    app.include_router(head_router)
    app.include_router(head_intake_router)
    app.include_router(exhibition_feed_router)
    app.include_router(runtime_router)
    app.include_router(runtime_review_router)

    @app.middleware("http")
    async def protect_auth_responses(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        if request.url.path.startswith("/auth/"):
            # 기본 422의 input 값에 비밀번호가 포함되지 않게 한다.
            return JSONResponse(status_code=422, content={"detail": "ID 형식과 비밀번호 길이를 확인하세요"})
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/ready")
    def ready():
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"ok": False, "database": "unavailable"})
        return {"ok": True, "database": engine.dialect.name}

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")   # 운영 화면(web/)
    return app


app = create_app()
