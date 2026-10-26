"""전시 자동 순환의 상태 한 줄. API 서버·Dispatcher·Worker가 같은 상태를 보고 재시작 뒤에도 켜짐이 남는다."""
from sqlmodel import Field, SQLModel


class ExhibitionFeed(SQLModel, table=True):
    __tablename__ = "exhibition_feed"
    id: int = Field(default=1, primary_key=True)   # 한 줄만 쓴다
    enabled: bool = False
    paused_until: float = 0                        # 이 시각까지 새 디렉터 작업을 넣지 않는다
    pause_reason: str = ""
    last_job: str = ""                             # 마지막으로 넣은 디렉터 작업 ID
    updated_at: float = 0
