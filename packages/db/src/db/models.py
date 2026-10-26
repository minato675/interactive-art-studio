"""DB 테이블. SQLite 파일 하나(기본)이며 DATABASE_URL로 PostgreSQL 등으로 바꿀 수 있다.
이미지·영상 파일 자체는 디스크에 두고, DB에는 경로·해시·상태만 둔다."""
from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _now():
    return datetime.now(UTC)


class Artwork(SQLModel, table=True):
    """작품 한 번의 생성 기록(요청·단계·결과)."""
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now, index=True)
    mode: str
    theme: str = Field(index=True)
    families: list = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(index=True)            # prepared / published(과거 기록) / held / failed
    title: str = ""
    request: dict = Field(default_factory=dict, sa_column=Column(JSON))
    steps: list = Field(default_factory=list, sa_column=Column(JSON))
    scene_path: str | None = None


class Asset(SQLModel, table=True):
    """소재 풀. 종류(kind)·테마별로 따로 저장하고, 세트 번호로 같은 작품의 소재를 묶는다."""
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    kind: str = Field(index=True)              # background / panel / fluid_image / skin / loop / sound / reference
    theme: str = Field(index=True)
    path: str = Field(unique=True)
    sha256: str | None = None
    status: str = Field(default="approved", index=True)   # pending / approved / rejected
    uses: int = 0
    set_id: str | None = None
