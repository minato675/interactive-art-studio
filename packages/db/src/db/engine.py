from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from . import accounts, exhibition_feed, feedback, files, head, jobs, models, show_dashboard, ue  # noqa: F401


def make_engine(url: str):
    """url은 central 설정의 db_url. db 패키지는 앱 설정을 import하지 않는다."""
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


def create_tables(engine) -> None:
    """골격 단계에서는 시작할 때 테이블을 만든다. 스키마가 굳으면 Alembic 마이그레이션으로 바꾼다."""
    SQLModel.metadata.create_all(engine)


def session_scope(engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
