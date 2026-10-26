"""시연 작품에 대한 운영자 의견. 원문과 대상은 불변, 참고 여부는 revision으로 관리한다."""
import uuid

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class ArtworkFeedback(SQLModel, table=True):
    __tablename__ = "infra_artwork_feedback"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    request_id: str = Field(unique=True, index=True)
    fingerprint: str
    target: dict = Field(sa_column=Column(JSON, nullable=False))
    artwork_id: str = Field(index=True)
    version: str = Field(index=True)
    artwork_title: str
    preview_only: bool = False
    category: str
    intention: str
    comment: str
    enabled: bool = Field(default=True, index=True)
    revision: int = 1
    created_at: float = Field(index=True)
    updated_at: float
