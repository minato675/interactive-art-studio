"""중앙 파일 업로드 영수증. 파일의 바이트는 DB 밖에 보관한다."""
from sqlmodel import Field, SQLModel


class UploadedFile(SQLModel, table=True):
    __tablename__ = "infra_uploaded_file"
    job_id: str = Field(foreign_key="infra_job.id", primary_key=True)
    attempt: int = Field(primary_key=True)
    name: str = Field(primary_key=True)
    sha256: str
    size: int
