"""Head의 영속 상태. 이미지 바이트는 파일 저장소에 두고 이 테이블에는 해시만 둔다."""
import uuid

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class HeadWorkflow(SQLModel, table=True):
    __tablename__ = "infra_head_workflow"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    request_id: str = Field(unique=True, index=True)
    fingerprint: str
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    provider: str
    model: str
    status: str = "planning_background"
    decision_job: str = ""
    background_job: str = ""
    asset_jobs: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    decisions: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    image: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: float
