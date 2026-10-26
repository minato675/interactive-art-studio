"""최근 아카이브의 고정 native 작품 ID에 대한 계정 운영 요청."""

from pydantic import BaseModel, ConfigDict, Field


class NativeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    expected_revision: int = Field(ge=1)


class NativePlay(NativeCommand):
    artwork_id: str = Field(min_length=1, max_length=96, pattern=r"^[a-zA-Z0-9._-]+$")
    duration_seconds: int = Field(default=120, ge=5, le=3600, strict=True)
    loop: bool = True
