"""요청·계획·응답 모델. 같은 모델이 API 검사, 문서(/docs), LLM 출력 JSON 스키마를 함께 맡는다."""
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Mode(StrEnum):
    exhibition = "exhibition"   # 자동 요청 + 소재 매번 새로 생성
    manual = "manual"


class Family(StrEnum):
    object = "object"
    paint = "paint"
    particles = "particles"
    media = "media"
    deformation = "deformation"
    fluid = "fluid"
    flock = "flock"
    audio = "audio"


class InputSource(StrEnum):
    hand = "hand"
    body = "body"     # 포즈 — UE 연결 예정
    sound = "sound"   # 소리 세기 — UE 연결 예정
    touch = "touch"


class Direction(StrEnum):
    none = "none"     # 표현 방식만
    mood = "mood"     # 분위기·색 (기본)
    story = "story"   # 제목·컨셉·단계별 이야기


class Timeline(StrEnum):
    loop = "loop"               # 반복 (기본)
    stages = "stages"           # N단계로 바뀜 — UE 시간 조건 필요
    accumulate = "accumulate"   # 반응이 쌓이면 바뀜 — UE 연결 예정


class AssetChoice(StrEnum):
    auto = "auto"
    pool = "pool"
    new = "new"


class Reveal(StrEnum):
    """보이지 않는 주인공을 드러내는 방식. 주인공 자체는 화면에 그리지 않는다."""
    trace = "trace"           # 흔적: 지나간 자리에 자국이 남고 번지고 사라짐 (예: 사슴의 발자취)
    formation = "formation"   # 형상: 작은 것들이 모여 잠깐 주인공 모양이 됨 (예: 물고기 떼 → 고래)
    none = "none"             # 주인공 없이 장(field)만


class Renderer(StrEnum):
    td = "td"     # TouchDesigner (기본)
    ue = "ue"     # Unreal Engine 라넬 런타임


PRESETS = ("ocean", "forest", "space", "flowers")  # 내부 기본 테마. 테마 문장이 빌 때 규칙이 고른다(요청 칸 아님)
CHIPS = {
    "mood": ("calm", "dreamy", "mysterious", "joyful"),
    "composition": ("center", "horizon", "diagonal", "scattered"),
    "focus": ("near", "far", "whole"),
    "space": ("open_center", "full"),
    "palette": ("cool", "warm", "night", "pastel"),
    "intensity": ("gentle", "normal", "strong"),
}
MAX_ELEMENTS = 3  # 현재 프로토타입 조립 어댑터의 범위. 회사 UE 전체의 요소 수 제한이 아니다.


class Reference(BaseModel):
    path: str
    note: str = ""


class CreateRequest(BaseModel):
    """입력은 맨 처음 한 번. 비운 항목은 AI(골격에서는 규칙)가 정한다."""
    mode: Mode = Mode.manual
    theme: str = Field("", max_length=2000, description="자유 테마 문장")
    chips: dict[str, str] = Field(default_factory=dict)
    families: list[Family] = Field(default_factory=list, max_length=MAX_ELEMENTS)
    inputs: list[InputSource] = Field(default_factory=lambda: [InputSource.hand], min_length=1)
    direction: Direction = Direction.mood
    timeline: Timeline = Timeline.loop
    stages: int = Field(3, ge=2, le=5, description="timeline=stages일 때 단계 수")
    assets: AssetChoice = AssetChoice.auto
    reference: Reference | None = None
    subject: str = Field("", max_length=40, description="주인공. 비우면 전시는 자율 선택, 사용자 제작은 원문에서 추출")
    reveal: Reveal | None = Field(None, description="드러내는 방식. 비우면 AI")
    review_assets: bool = Field(False, description="소재를 만든 뒤 멈추고 사람이 확인 (전시 모드는 무시)")
    review_final: bool = Field(False, description="조립 뒤 검수 폴더로만 보내고 사람이 확인 (전시 모드는 무시)")

    @model_validator(mode="after")
    def _check(self):
        from .families import CANVAS_FAMILIES, REVEAL_FAMILIES

        for name, value in self.chips.items():
            if value not in CHIPS.get(name, ()):
                raise ValueError(f"칩 {name}={value}는 없는 선택지")
        self.families = list(dict.fromkeys(self.families))
        if len(CANVAS_FAMILIES.intersection(self.families)) > 1:
            raise ValueError("현재 단일 캔버스 조립 어댑터에서는 그리기·유체·표면 변형 중 하나만 지원")
        self.inputs = list(dict.fromkeys(self.inputs))
        if self.reveal and self.families and not REVEAL_FAMILIES[self.reveal].intersection(self.families):
            allowed = sorted(f.value for f in REVEAL_FAMILIES[self.reveal])
            raise ValueError(f"{self.reveal.value}에는 표현 방식 {allowed} 중 하나가 필요")
        return self


class Intent(BaseModel):
    """작품 의도. direction 수준에 따라 채워지는 칸이 다르다(none이면 title·theme만)."""
    title: str
    theme: str
    mood: list[str] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    concept: str | None = None
    stages: list[str] = Field(default_factory=list)   # timeline=stages일 때만
    subject: str | None = None      # 보이지 않는 주인공
    reveal: Reveal = Reveal.none


class Phase(BaseModel):
    """안무 한 단계. target은 주인공 형상으로 끌리는 힘(0~1), 단계 동안 from→to로 바뀐다."""
    name: str
    seconds: float = Field(gt=0)
    target_from: float = Field(0, ge=0, le=1)
    target_to: float = Field(0, ge=0, le=1)
    burst: bool = False             # 단계 시작에 바깥으로 흩어지는 순간 힘


class Element(BaseModel):
    family: Family
    module: str | None
    position: str = "center"


class Mapping(BaseModel):
    """입력 → 반응. 어떤 입력이 어떤 표현을 어떻게 움직이는지."""
    source: InputSource
    family: Family
    reaction: str
    strength: str = "normal"
    memory: str | None = None   # 비면 모듈 기본값(유체 short, 그리기 long, 나머지 medium)


class Needs(BaseModel):
    required: list[str]
    optional: list[str]


class Step(BaseModel):
    name: str
    detail: str | dict | list


class DraftEdit(BaseModel):
    """편집 모드 확정 요청. 바꾼 칸만 보낸다. 보내지 않은 칸은 AI 초안 값을 그대로 쓴다."""
    model_config = {"extra": "forbid"}
    title: str | None = Field(None, min_length=1, max_length=30)
    families: list[Family] | None = Field(None, min_length=1, max_length=MAX_ELEMENTS)
    subject: str | None = Field(None, max_length=40)
    reveal: Reveal | None = None
    mood: str | None = None
    palette: str | None = None
    intensity: str | None = None

    @model_validator(mode="after")
    def _check(self):
        for name in ("mood", "palette", "intensity"):
            value = getattr(self, name)
            if value is not None and value not in CHIPS[name]:
                raise ValueError(f"{name}={value}는 없는 선택지 (가능: {list(CHIPS[name])})")
        if self.title is not None:
            self.title = self.title.strip()
            if not self.title:
                raise ValueError("title은 비울 수 없음")
        for name in self.model_fields_set:   # 보낸 칸은 값이 있어야 한다(주인공을 없애려면 subject="")
            if getattr(self, name) is None:
                raise ValueError(f"{name}은 null로 보낼 수 없음")
        return self


class ReviewDecision(BaseModel):
    """검수 대기 작품의 승인·반려."""
    model_config = {"extra": "forbid"}
    approve: bool
    note: str = Field("", max_length=200)


class ArtworkOut(BaseModel):
    id: int
    status: str            # prepared(전체 후보 준비) / published(과거 기록) / held / failed / awaiting / running
                           # / asset_review(소재 검수 대기) / final_review(최종 검수 대기)
    title: str
    steps: list[Step]
    scene_path: str | None
    preparation: dict | None = None                    # 필수 출력별 파일 준비 결과. 런타임 증거가 아니다.
    runtime_phase: str = "Unknown"                     # 동기 제작 경로에는 실제 Show 보고가 연결되지 않음
    draft: dict | None = None                          # 편집 모드 초안의 칸 값 (awaiting일 때)
    review: list[str] = Field(default_factory=list)    # 사용자가 먼저 볼 칸: AI가 채웠거나 비어 있음
    gate: str | None = None                            # 검수 대기 단계: assets / final
    gate_files: list[str] = Field(default_factory=list)          # 소재 검수 때 볼 파일 이름(/review/files/{name})
    gate_targets: dict[str, str] = Field(default_factory=dict)   # 최종 검수 때 렌더러별 검수 폴더 버전
