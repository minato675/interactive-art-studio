"""운영자 의견과 시연 작품 식별. 모델 재학습·게시 승인 계약과 구분한다."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .infra import Identifier


class FeedbackTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publication_job_id: Identifier
    node_id: Identifier
    artwork_id: Identifier
    version: Identifier
    runtime_version: str = Field(min_length=1, max_length=128)


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Identifier
    target: FeedbackTarget
    category: Literal["overall", "look", "composition", "interaction", "performance"] = "overall"
    intention: Literal["keep", "improve"] = "improve"
    comment: str = Field(min_length=1, max_length=1000)
    enabled: bool = Field(default=True, strict=True)

    @field_validator("comment")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("피드백 내용을 입력하세요")
        return value.strip()


class FeedbackChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)
    expected_revision: int = Field(ge=1)


class FeedbackRecord(FeedbackCreate):
    id: str
    artwork_title: str
    preview_only: bool
    revision: int
    created_at: float
    updated_at: float


class FeedbackArtwork(BaseModel):
    target: FeedbackTarget
    title: str
    summary: str
    template: str
    object_count: int
    layer_count: int
    interactions: list[str]
    preview_only: bool


class FeedbackShow(BaseModel):
    node_id: str
    online: bool
    phase: str
    heartbeat_at: float
    report_at: float | None
    can_review: bool
    reason: str
    artwork: FeedbackArtwork | None


class FeedbackCurrent(BaseModel):
    observed_at: float
    shows: list[FeedbackShow]
