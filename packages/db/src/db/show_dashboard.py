"""중앙 노드별 시연 구성. PC2의 실제 재생 상태와 구분한다."""
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class ShowConfiguration(SQLModel, table=True):
    __tablename__ = "infra_show_configuration"
    node_id: str = Field(primary_key=True)
    revision: int = 1
    plan: dict = Field(sa_column=Column(JSON, nullable=False))
    updated_at: float
