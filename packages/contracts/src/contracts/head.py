"""분산 Head 경계. AI는 계획만 제안하고 실행 가능한 작업은 서버가 만든다."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .infra import Identifier
from .schemas import CreateRequest
from .spec import Spec


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperatorHeadIntake(StrictModel):
    request_id: Identifier
    request: CreateRequest

    @model_validator(mode="after")
    def supported(self):
        if not self.request.theme.strip():
            raise ValueError("분산 제작 요청 원문을 입력하세요")
        if self.request.review_assets:
            raise ValueError("분산 제작의 소재 중간 검수는 아직 지원하지 않습니다")
        if len(self.request.families) > 1:
            raise ValueError("현재 UE 분산 제작은 표현 방식 한 개만 선택할 수 있습니다")
        return self


class ExhibitionFeedToggle(StrictModel):
    """전시 자동 순환 켜기·끄기. 끄면 새 요청만 멈추고 접수된 작품은 끝까지 진행한다."""
    enabled: bool


class HeadRequest(StrictModel):
    request_id: Identifier
    artwork_id: Identifier
    version: Identifier
    catalog_version: Identifier = "v1"
    prompt: str = Field(min_length=1, max_length=8000)
    asset_count: int | None = Field(default=None, ge=0, le=12)
    max_asset_count: int = Field(default=12, ge=1, le=12)
    automation: bool = True
    auto_publish: bool = True
    show_node: Identifier = "ue-show-1"
    max_reworks: int = Field(default=2, ge=0, le=3)
    spec: Spec
    profile: Literal["production", "test"] = "production"


    @model_validator(mode="after")
    def within_budget(self):
        if self.asset_count is not None and self.asset_count > self.max_asset_count:
            raise ValueError("asset_count는 max_asset_count 이하여야 합니다")
        return self


class BackgroundPlan(StrictModel):
    reason: str = Field(min_length=1, max_length=2000)
    prompt: str = Field(min_length=1, max_length=8000)


class AssetPlacement(StrictModel):
    u: float = Field(ge=0, le=1)
    v: float = Field(ge=0, le=1)
    size: float = Field(gt=0, le=1)


class AssetPlan(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    render_mode: Literal["2d", "2.5d"]
    prompt: str = Field(min_length=1, max_length=8000)
    placement: AssetPlacement | None = None


DirectionText = Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]
ObjectBehavior = Literal["float", "follow", "glow"]


class BackgroundObservation(StrictModel):
    """실제 배경 이미지에서 관찰한 색·빛·원근과 화면 영역. 좌표는 좌상단 (0,0), 우하단 (1,1)."""

    palette: list[Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]] = Field(
        min_length=1, max_length=8)
    lighting: DirectionText
    perspective: DirectionText
    occupied_regions: list[DirectionText] = Field(max_length=12)
    usable_regions: list[DirectionText] = Field(min_length=1, max_length=12)


class ScenarioInstance(StrictModel):
    id: str = Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_]*$")
    source_asset_index: int = Field(ge=1, le=12)
    placement: AssetPlacement
    behaviors: list[ObjectBehavior] = Field(max_length=3)
    role: Literal["noninteractive", "interactor"]
    purpose: DirectionText


class DirectionRecord(StrictModel):
    """계획의 실행 범위. template_driven도 런타임 적용·현장 입력 검증을 뜻하지 않는다."""

    id: Identifier
    target_ids: list[Identifier] = Field(max_length=12)
    description: DirectionText
    execution_status: Literal["template_driven", "design_only"]
    notes: DirectionText


class ScenarioEffect(DirectionRecord):
    kind: Literal["glow", "trail", "ripple", "lighting", "color_variation", "rotation_variation", "depth_variation", "other"]


class ScenarioInteraction(DirectionRecord):
    input: Literal["position", "appear", "move", "gesture", "sound"]
    reaction: Literal["float", "follow", "glow", "other"]


class ScenarioParticles(DirectionRecord):
    count: int | None = Field(default=None, ge=1, le=1_000_000)
    lifetime_seconds: float | None = Field(default=None, gt=0, le=3600)
    speed: float | None = Field(default=None, ge=0, le=100_000)


class ScenarioCue(DirectionRecord):
    start_seconds: float = Field(ge=0, le=7200)
    duration_seconds: float = Field(gt=0, le=7200)


class ScenarioDirection(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=160, pattern=r"\S")]
    intent: DirectionText
    chosen_template: Identifier
    template_rationale: DirectionText
    background_observation: BackgroundObservation
    instances: list[ScenarioInstance] = Field(min_length=0, max_length=12)
    effects: list[ScenarioEffect] = Field(max_length=12)
    interactions: list[ScenarioInteraction] = Field(max_length=12)
    particles: list[ScenarioParticles] = Field(max_length=12)
    cues: list[ScenarioCue] = Field(max_length=24)
    limitations: list[DirectionText] = Field(min_length=1, max_length=16)
    acceptance_criteria: list[DirectionText] = Field(min_length=1, max_length=16)


class AssetsPlan(StrictModel):
    reason: str = Field(min_length=1, max_length=2000)
    assets: list[AssetPlan] = Field(min_length=0, max_length=12)
    director: ScenarioDirection | None = None


class BackgroundInput(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    data_url: str = Field(max_length=7_000_000)


class ReviewPlan(StrictModel):
    decision: Literal["approve", "rework_assets", "rework_background", "hold"]
    reason: str = Field(min_length=1, max_length=2000)
    instructions: str = Field(max_length=4000)

    @model_validator(mode="after")
    def rework_has_instructions(self):
        if self.decision.startswith("rework_") and not self.instructions.strip():
            raise ValueError("재작업 판단에는 제작 지시가 필요합니다")
        return self
