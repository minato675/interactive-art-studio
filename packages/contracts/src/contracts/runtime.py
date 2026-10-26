"""사람 검수 요청. 게시 승인은 기존 infra 계약을 사용한다."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HumanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)
    expected_attempt: int = Field(ge=1)
