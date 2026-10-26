"""작품 명세: 자연어 입력을 정규화한 결과. 설계는 docs/architecture/distributed-system-design.md 3.3·6절.

칸 값은 세 가지다. 실제 값, NONE("none": 편집 제작에서 운영자가 일부러 비운 선택 칸),
None(막힌 칸이거나 아직 정하지 못한 칸). 아직 계획 단계(schemas.py)와는 연결하지 않았다.
"""
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import Family, Timeline

NONE = "none"
Omitted = Literal["none"]


class TemplateFamily(StrEnum):
    scene_family = "scene_family"   # 장면: 배경·캐릭터 이미지를 만들어 조립
    flow_family = "flow_family"     # 흐름: 입자·궤적·장의 움직임 규칙만 (이미지 생성 없음)


class ProductionMode(StrEnum):
    auto = "auto"   # AI가 채운 명세를 그대로 넘김
    edit = "edit"   # AI가 채운 명세를 운영자가 편집한 뒤 넘김


class Input(StrEnum):
    appear = "appear"
    wave = "wave"
    jump = "jump"
    move = "move"


class Reaction(StrEnum):
    """반응. 평상시 움직임(idle.motion)도 이 목록을 쓴다."""
    follow = "follow"
    push = "push"
    scatter = "scatter"
    swirl = "swirl"
    stir = "stir"
    ignore = "ignore"
    bloom = "bloom"
    reveal = "reveal"
    emerge = "emerge"
    gather = "gather"
    glow = "glow"
    pulse = "pulse"
    ripple = "ripple"
    press = "press"
    draw = "draw"


class ReactionMode(StrEnum):
    once = "once"
    while_ = "while"
    accumulate = "accumulate"


class Return(StrEnum):
    instant = "instant"
    fade = "fade"
    stay = "stay"


class SpecReveal(StrEnum):
    """드러내는 방식. field는 계획 단계 schemas.Reveal.none에 해당한다("none"과 겹치지 않게 이름을 바꿈)."""
    direct = "direct"
    formation = "formation"
    trace = "trace"
    field = "field"


class Dimension(StrEnum):
    flat = "2d"
    layered = "2.5d"


class Medium(StrEnum):
    particle = "particle"
    swarm = "swarm"
    fluid = "fluid"
    surface = "surface"
    stroke = "stroke"
    light = "light"


class Style(StrEnum):
    realistic = "realistic"
    illustration = "illustration"
    watercolor = "watercolor"
    ink = "ink"
    pixel = "pixel"
    three_d = "3d"
    minimal = "minimal"


class InfluenceRange(StrEnum):
    local = "local"
    wide = "wide"


class Origin(StrEnum):
    user_explicit = "user_explicit"
    extracted = "extracted"
    head_selected = "head_selected"
    template_default = "template_default"
    unextracted = "unextracted"


class Confidence(StrEnum):
    high = "high"
    low = "low"


@dataclass(frozen=True)
class CatalogEntry:
    label: str
    phrases: tuple[str, ...]            # 매칭에 쓰는 자연어 표현
    families: tuple[Family, ...] = ()   # 지금 되는 표현 방식(비면 아직 없음)


def _e(label, *phrases, families=()):
    return CatalogEntry(label, phrases, tuple(families))


# 목록 칸의 값 표(6.1). LLM에 이 표를 넘겨 원문을 코드로 바꾼다.
CATALOG: dict[type[StrEnum], dict[StrEnum, CatalogEntry]] = {
    Input: {
        Input.appear: _e("등장", "사람이 오면", "나타나면", "앞에 서면", "있는 동안"),
        Input.wave: _e("손 흔들기", "손을 흔들면", "손짓하면", "인사하면"),
        Input.jump: _e("점프", "뛰면", "점프하면", "껑충"),
        Input.move: _e("움직이기", "걸으면", "지나가면", "움직이면", "따라 걸으면"),
    },
    Reaction: {
        Reaction.follow: _e("따라옴", "다가옴", "쫓아옴", "향해 옴", families=[Family.flock]),
        Reaction.push: _e("밀어냄", "피함", "비켜감", "밀려남", families=[Family.particles]),
        Reaction.scatter: _e("흩어짐", "도망침", "퍼짐", "놀라서 흩어짐", families=[Family.flock]),
        Reaction.swirl: _e("소용돌이", "감김", "휘감김", "회오리", families=[Family.particles]),
        Reaction.stir: _e("휘저음", "섞임", "일렁임", families=[Family.fluid]),
        Reaction.ignore: _e("무시", "반응 없음", "제 갈 길 감", families=[Family.flock]),
        Reaction.bloom: _e("피어남", "꽃이 핌", "자라남", "돋아남", families=[Family.object]),
        Reaction.reveal: _e("드러남", "가려진 게 보임", "벗겨짐", families=[Family.media]),
        Reaction.emerge: _e("튀어나옴", "뛰쳐나옴", "솟아오름", "나타남"),
        Reaction.gather: _e("모임", "모여서 형체가 됨", "뭉침", families=[Family.flock, Family.particles]),
        Reaction.glow: _e("빛남", "반짝임", "밝아짐", families=[Family.object]),
        Reaction.pulse: _e("맥동", "두근거림", "박자에 맞춰 울림", families=[Family.audio]),
        Reaction.ripple: _e("물결", "파문", "출렁임", "땅이 울림", families=[Family.deformation]),
        Reaction.press: _e("눌림", "움푹 들어감", "자국 남음", families=[Family.deformation]),
        Reaction.draw: _e("그리기", "지나간 자리에 선", "붓질", families=[Family.paint]),
    },
    ReactionMode: {
        ReactionMode.once: _e("한 번 일어남", "입력이 들어온 순간 한 번 반응한다"),
        ReactionMode.while_: _e("입력하는 동안 계속", "입력이 이어지는 동안 반응도 이어진다"),
        ReactionMode.accumulate: _e("쌓임", "입력이 반복될수록 반응이 커지거나 쌓인다"),
    },
    Return: {
        Return.instant: _e("바로 돌아감", "곧바로", "다시 들어감", "사라짐"),
        Return.fade: _e("서서히 돌아감", "잔상", "천천히 사라짐", "여운"),
        Return.stay: _e("남아 있음", "흔적이 남음", "쌓임"),
    },
    SpecReveal: {
        SpecReveal.direct: _e("직접", "뛰쳐나오는 호랑이", "서 있는 사슴"),
        SpecReveal.formation: _e("형상", "~로 만들어진", "~가 모여서 ~이 됨"),
        SpecReveal.trace: _e("흔적", "발자취", "지나간 자리", "흔적만"),
        SpecReveal.field: _e("없음", "메인 캐릭터가 없을 때"),
    },
    Dimension: {
        Dimension.flat: _e("2D", "평면", "납작한", "그림책처럼", "2D"),
        Dimension.layered: _e("2.5D", "입체감", "깊이감", "앞뒤 층", "원근", "2.5D"),
    },
    Medium: {
        Medium.particle: _e("입자", "물방울", "빗방울", "모래", "반딧불", "먼지", families=[Family.particles]),
        Medium.swarm: _e("무리", "물고기 떼", "새 떼", "나비 떼", families=[Family.flock]),
        Medium.fluid: _e("유체", "연기", "잉크", "물감 번짐", "안개", families=[Family.fluid]),
        Medium.surface: _e("표면", "수면", "천", "모래밭", "막", families=[Family.deformation]),
        Medium.stroke: _e("선", "붓선", "빛줄기", "실", "궤적", families=[Family.paint]),
        Medium.light: _e("빛의 장", "오로라", "빛 번짐", "광채"),
    },
    Style: {
        Style.realistic: _e("실사", "사실적인", "사진 같은"),
        Style.illustration: _e("일러스트", "동화풍", "그림책"),
        Style.watercolor: _e("수채화", "번지는", "맑은 물감"),
        Style.ink: _e("수묵화", "동양화", "먹"),
        Style.pixel: _e("픽셀", "도트", "레트로 게임"),
        Style.three_d: _e("3D 렌더", "입체적인", "게임 그래픽"),
        Style.minimal: _e("미니멀", "단순한 도형", "선만"),
    },
    InfluenceRange: {
        InfluenceRange.local: _e("국소", "국소"),
        InfluenceRange.wide: _e("광역", "광역"),
    },
}


class FieldState(StrEnum):
    required = "required"
    optional = "optional"
    blocked = "blocked"


_R, _O, _B = FieldState.required, FieldState.optional, FieldState.blocked


def _states(scene: FieldState, flow: FieldState) -> dict[TemplateFamily, FieldState]:
    return {TemplateFamily.scene_family: scene, TemplateFamily.flow_family: flow}


# 계열별 칸 상태(3.3). interactions[]는 규칙마다 같다.
FIELD_STATES: dict[str, dict[TemplateFamily, FieldState]] = {
    "content.theme": _states(_R, _R),
    "content.mood": _states(_R, _R),
    "content.palette": _states(_O, _O),
    "content.composition": _states(_O, _O),
    "content.main_character": _states(_O, _B),   # 장면 계열은 템플릿이 요구할 때 필수
    "content.reveal": _states(_O, _B),           # 메인 캐릭터가 있으면 필수(Spec 검사)
    "content.background": _states(_R, _O),       # 흐름 계열은 원문이 배경 이미지를 요구할 때만

    "content.dimension": _states(_R, _B),
    "content.style": _states(_O, _B),
    "content.material": _states(_O, _B),
    "content.lighting": _states(_O, _B),
    "content.medium": _states(_O, _R),
    "content.density": _states(_O, _O),
    "content.influence_range": _states(_O, _O),
    "idle.motion": _states(_O, _R),
    "idle.description": _states(_O, _O),
    "idle.timeline": _states(_O, _O),
    "interactions[].input": _states(_R, _R),
    "interactions[].reaction": _states(_R, _R),
    "interactions[].reaction_mode": _states(_R, _R),
    "interactions[].return": _states(_R, _R),
    "interactions[].duration_sec": _states(_O, _B),
}


def fields_for(family: TemplateFamily) -> list[str]:
    """그 계열에서 막히지 않은 칸. Head에는 이 칸만 넘긴다."""
    return [path for path, states in FIELD_STATES.items() if states[family] is not FieldState.blocked]


class PaletteColor(BaseModel):
    role: str
    hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class Content(BaseModel):
    """무엇이 보이나."""
    theme: str | None = None
    mood: str | None = None
    palette: list[PaletteColor] | Omitted | None = None
    composition: str | None = None
    main_character: str | None = None
    reveal: SpecReveal | Omitted | None = None
    background: str | None = None
    dimension: Dimension | Omitted | None = None
    style: Style | Omitted | None = None
    material: str | None = None
    lighting: str | None = None
    medium: Medium | Omitted | None = None
    density: float | Omitted | None = None
    influence_range: InfluenceRange | Omitted | None = None

    @field_validator("density")
    @classmethod
    def _density_range(cls, value):
        if isinstance(value, float) and not 0 <= value <= 1:
            raise ValueError("density는 0~1")
        return value


class Idle(BaseModel):
    """아무도 없을 때."""
    motion: Reaction | Omitted | None = None
    description: str | None = None
    timeline: Timeline | Omitted | None = None


class Interaction(BaseModel):
    """사람이 오면. 입력·반응·반응 방식·복귀는 늘 필수라 "none"을 받지 않는다."""
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    input: Input | None = None
    reaction: Reaction | None = None
    reaction_mode: ReactionMode | None = None
    return_: Return | None = Field(None, alias="return")
    duration_sec: float | Omitted | None = None

    @field_validator("duration_sec")
    @classmethod
    def _positive(cls, value):
        if isinstance(value, float) and value <= 0:
            raise ValueError("duration_sec는 0보다 커야 함")
        return value


class UnmatchedPhrase(BaseModel):
    field_path: str
    text: str


class Provenance(BaseModel):
    """칸 하나의 값이 어디서 왔는지(6.3)."""
    field_path: str
    value: Any = None
    origin: Origin
    source_text: str | None = None
    confidence: Confidence | None = None
    candidates: list[str] = Field(default_factory=list)
    was_mapped: bool = False
    mapping_reason: str | None = None
    user_edited: bool = False
    model_ref: str | None = None
    prompt_version: str | None = None
    validation_result: str | None = None


class Spec(BaseModel):
    spec_schema_version: str = "proposal-2"
    mode: ProductionMode = ProductionMode.auto
    template_family: TemplateFamily
    raw_prompt: str
    catalog_snapshot_id: str | None = None
    template_ref: str | None = None
    content: Content = Field(default_factory=Content)
    idle: Idle = Field(default_factory=Idle)
    interactions: list[Interaction] = Field(min_length=1)
    display_policy: Literal["until_next_applied"] = "until_next_applied"
    unmatched_phrases: list[UnmatchedPhrase] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)

    def field_values(self) -> list[tuple[str, Any]]:
        """칸 경로와 값. 규칙은 interactions[0].input처럼 번호를 붙인다."""
        out = [(f"content.{name}", value) for name, value in self.content]
        out += [(f"idle.{name}", value) for name, value in self.idle]
        for i, rule in enumerate(self.interactions):
            out += [(f"interactions[{i}].{name}", value) for name, value in rule.model_dump(by_alias=True).items()]
        return out

    def state_of(self, path: str) -> FieldState:
        return FIELD_STATES[re.sub(r"\[\d+\]", "[]", path)][self.template_family]

    @model_validator(mode="after")
    def _check(self):
        problems = []
        for path, value in self.field_values():
            state = self.state_of(path)
            if state is FieldState.blocked and value is not None:
                problems.append(f"{path}는 이 계열에서 막힌 칸이라 null이어야 함")
            elif state is FieldState.required and value == NONE:
                problems.append(f"{path}는 필수 칸이라 비울 수 없음")
        if self.content.main_character not in (None, NONE) and self.content.reveal in (NONE, SpecReveal.field):
            problems.append("메인 캐릭터가 있으면 content.reveal은 direct·formation·trace 중 하나")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def unfilled_required(spec: Spec) -> list[str]:
    """생성 단계로 넘기기 전(5.1 ④)에 아직 비어 있는 필수 칸. 처리 중에는 비어 있을 수 있어 모델 검사와 따로 둔다."""
    missing = [path for path, value in spec.field_values() if value is None and spec.state_of(path) is FieldState.required]
    if spec.content.main_character not in (None, NONE) and spec.content.reveal is None:
        missing.append("content.reveal")
    return missing
