"""운영 앱 계정 인증. 비밀번호와 세션 원문은 저장 계약에 포함하지 않는다."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: SecretStr = Field(min_length=1, max_length=128)


class AccountCreate(LoginRequest):
    password: SecretStr = Field(min_length=12, max_length=128)
    role: Literal["operator", "admin"] = "operator"


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr | None = Field(default=None, min_length=12, max_length=128)
    enabled: bool | None = None


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)
