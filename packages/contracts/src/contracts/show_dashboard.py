"""중앙에 저장하고 PC2에서 실제 실행하는 재생 구성."""
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .infra import Identifier


class ShowItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verification_job_id: Identifier
    repeat: int = Field(default=1, ge=1, le=100, strict=True)
    enabled: bool = Field(default=True, strict=True)


class ShowPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autoplay: bool = Field(default=False, strict=True)
    duration_seconds: int = Field(default=120, ge=5, le=3600, strict=True)
    items: list[ShowItem] = Field(default_factory=list, max_length=200)
    loop: bool = Field(default=True, strict=True)

    @field_validator("items")
    @classmethod
    def unique(cls, items):
        ids = [item.verification_job_id for item in items]
        if len(set(ids)) != len(ids):
            raise ValueError("같은 검증 작품은 한 행만 추가하고 반복 횟수를 조절하세요")
        return items


class ShowPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    plan: ShowPlan
