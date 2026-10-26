"""운영자 계정·만료 세션·로그인 시도 제한. 기존 테이블은 변경하지 않는다."""
from sqlmodel import Field, SQLModel


class OperatorAccount(SQLModel, table=True):
    __tablename__ = "infra_operator_account"
    username: str = Field(primary_key=True, max_length=64)
    password_hash: str
    role: str = "operator"
    enabled: bool = True


class OperatorSession(SQLModel, table=True):
    __tablename__ = "infra_operator_session"
    digest: str = Field(primary_key=True, max_length=64)
    username: str = Field(index=True, foreign_key="infra_operator_account.username")
    expires_at: float = Field(index=True)


class LoginLimit(SQLModel, table=True):
    __tablename__ = "infra_operator_login_limit"
    key: str = Field(primary_key=True, max_length=64)
    failures: int = 0
    window_start: float = 0
    locked_until: float = 0
