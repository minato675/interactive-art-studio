"""제작 노드의 리소스 보고. 기존 infra_node 열을 변경하지 않는다."""
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class UECapability(SQLModel, table=True):
    __tablename__ = "infra_ue_capability"
    node_id: str = Field(foreign_key="infra_node.id", primary_key=True)
    report: dict = Field(sa_column=Column(JSON, nullable=False))
    reported_at: float
