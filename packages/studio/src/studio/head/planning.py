"""AI — 의미 정하기: 자동 요청 → 의도 → 구성 → 입력·반응 매핑.

RulePlanner: LLM 없이 규칙으로 정한다(기본).
LLMPlanner: LLM(로컬 Ollama 등)에 먼저 묻고, 틀리면 이유를 붙여 한 번 더 묻고, 그래도 틀리면 규칙으로 대체한다.
LLM은 의미(분위기·색·방식·반응)만 고르고, 숫자는 assembly/ue_scene이 UE 허용 범위 안에서 정한다.
"""
import random
import re
from typing import Protocol

from pydantic import BaseModel, Field

from contracts.families import CANVAS_FAMILIES, FAMILIES, REVEAL_FAMILIES
from contracts.schemas import (
    CHIPS,
    MAX_ELEMENTS,
    PRESETS,
    CreateRequest,
    Direction,
    Element,
    Family,
    InputSource,
    Intent,
    Mapping,
    Mode,
    Reveal,
    Timeline,
)

from .llm import LLMError

LABELS = {"ocean": "바다", "forest": "숲", "space": "우주", "flowers": "꽃밭"}
DEFAULT_FAMILY = {"ocean": Family.fluid, "forest": Family.flock, "space": Family.particles, "flowers": Family.object}
PALETTES = {"cool": ["deep blue", "teal"], "warm": ["amber", "coral"], "night": ["navy", "silver"],
            "pastel": ["lavender", "mint"]}
DEFAULT_PALETTE = {"ocean": "cool", "forest": "warm", "space": "night", "flowers": "pastel"}
PALETTE_NAMES = sorted({name for names in PALETTES.values() for name in names})
STAGE_NAMES = ("고요", "깨어남", "절정", "잦아듦", "어둠")
# 테마별 기본 '보이지 않는 주인공'과 드러내는 방식. 주인공은 그리지 않고 흔적·형상으로만 드러낸다.
SUBJECTS = {"ocean": ("고래", Reveal.formation), "forest": ("사슴", Reveal.trace),
            "space": ("혜성", Reveal.trace), "flowers": ("나비", Reveal.formation)}
# 규칙 계획기의 제한된 원문 추출. 자유로운 명칭은 명시 subject 또는 LLM의 원문 근거로 받는다.
SUBJECT_WORDS = ("물고기", "코끼리", "고양이", "호랑이", "해파리", "고래", "사슴", "혜성", "나비", "늑대", "여우",
                 "whale", "deer", "comet", "butterfly", "fish", "bird", "dragon", "용", "새")


def subject_in_prompt(subject: str, prompt: str) -> bool:
    """원문에 직접 나온 이름만 허용하고 명시적인 제외 표현은 주인공으로 뽑지 않는다."""
    for match in re.finditer(r"(?<![\w])" + re.escape(subject), prompt, re.IGNORECASE):
        tail = prompt[match.end():]
        if tail and (tail[0].isascii() and tail[0].isalpha()):
            continue
        if subject == "새" and tail.startswith(("로운", "롭게", "벽")):
            continue
        if re.match(r"\s*(?:는|은|을|를|이|가)?\s*(?:없이|없는|없고|빼고|제외|말고)", tail):
            continue
        if re.search(r"(?:without|no|exclude)\s*$", prompt[:match.start()], re.IGNORECASE):
            continue
        return True
    return False


def prompt_subject(request: CreateRequest) -> str | None:
    if request.subject.strip():
        return request.subject.strip()
    explicit = re.search(r"주인공\s*[:=]\s*([^,.;\n]+)", request.theme)
    if explicit:
        return explicit.group(1).strip()[:40] or None
    return next((word for word in SUBJECT_WORDS if subject_in_prompt(word, request.theme)), None)
# 드러내는 방식별 기본 표현 방식(테마별, "_"는 그 밖).
REVEAL_DEFAULT = {
    Reveal.formation: {"ocean": Family.flock, "_": Family.particles},
    Reveal.trace: {"ocean": Family.deformation, "forest": Family.paint, "space": Family.particles, "_": Family.fluid},
}
SYSTEM = ("너는 인터랙티브 미디어아트 연출가다. 대상을 직접 보여 주지 않고 흔적이나 순간의 형상으로 간접적으로 드러낸다 "
          "(예: 사슴 대신 사슴의 발자취, 고래 대신 물고기 떼가 잠깐 이루는 고래 모양). 주어진 JSON 형식으로만 답한다.")


class Planner(Protocol):
    def auto_request(self, recent: list[tuple[str, tuple[str, ...]]], rng: random.Random,
                     families: list[Family]) -> dict: ...
    def intent(self, request: CreateRequest, theme: str, character: str | None = None) -> Intent: ...
    def composition(self, intent: Intent, families: list[Family]) -> list[Element]: ...
    def mappings(self, elements: list[Element], request: CreateRequest) -> list[Mapping]: ...


class RulePlanner:
    def auto_request(self, recent, rng, families):
        """전시 모드: 최근 작품과 테마+방식이 겹치지 않게 고른다. 방식을 받았으면 테마만 고른다."""
        ready = [f for f, spec in FAMILIES.items() if spec.status == "done"]

        def draw():
            return rng.choice(PRESETS), list(families) or [rng.choice(ready)]

        preset, chosen = draw()
        for _ in range(50):   # 50번 모두 겹치면 마지막 후보를 쓴다
            if (preset, tuple(f.value for f in chosen)) not in recent:
                break
            preset, chosen = draw()
        return {"preset": preset, "families": chosen}

    def intent(self, request, theme, character=None):
        if theme == "custom":
            theme = next((key for key, label in LABELS.items()
                          if label in request.theme or key in request.theme.lower()), theme)
        title = LABELS.get(theme) or request.theme.strip()[:30] or "무제"
        intent = Intent(title=title, theme=theme)
        if request.direction in (Direction.mood, Direction.story):
            intent.mood = [request.chips.get("mood", "calm")]
            intent.palette = PALETTES[request.chips.get("palette", DEFAULT_PALETTE.get(theme, "cool"))]
        if request.direction is Direction.story:
            intent.title = f"{title}의 기억"
            intent.concept = request.theme or f"{title}에서 관람객의 움직임이 장면을 깨운다"
        if request.timeline is Timeline.stages:
            n = request.stages
            intent.stages = list(STAGE_NAMES[:n - 1]) + [STAGE_NAMES[-1]]
        subject, reveal = SUBJECTS.get(theme, ("", Reveal.none)) if request.mode is Mode.exhibition else ("", Reveal.none)
        requested_subject = prompt_subject(request)
        intent.subject = requested_subject or subject or None
        if request.reveal is Reveal.none and not requested_subject:
            intent.subject = None
        if request.reveal is not None:
            intent.reveal = request.reveal
        elif requested_subject:
            intent.reveal = Reveal.formation   # 주인공만 받았으면 형상으로 드러낸다
        else:
            intent.reveal = reveal if intent.subject else Reveal.none
        # 사용자가 고른 표현 방식이 우선: 맞지 않으면 그 방식으로 할 수 있는 드러내는 방식으로 바꾼다.
        if request.reveal is None and request.families and \
                not REVEAL_FAMILIES[intent.reveal].intersection(request.families):
            usable = [r for r in (Reveal.trace, Reveal.formation) if REVEAL_FAMILIES[r].intersection(request.families)]
            intent.reveal = usable[0] if usable and intent.subject else Reveal.none
        return intent

    def composition(self, intent, families):
        allowed = REVEAL_FAMILIES[intent.reveal]
        incompatible = [f.value for f in families if f not in allowed]
        if incompatible:
            raise ValueError(f"요청한 표현 방식 {incompatible}은 {intent.reveal.value}와 함께 지원하지 않음")
        families = list(families)
        if not families:
            defaults = REVEAL_DEFAULT.get(intent.reveal)
            fallback = defaults.get(intent.theme, defaults["_"]) if defaults else DEFAULT_FAMILY.get(
                intent.theme, Family.object)
            families = [fallback]
        kept, canvas = [], False
        for family in families:   # 순서 유지, 캔버스 방식은 먼저 나온 하나만
            if family in CANVAS_FAMILIES:
                if canvas:
                    continue
                canvas = True
            kept.append(family)
        positions = iter(("center", "left", "right"))
        return [Element(family=f, module=FAMILIES[f].module,
                        position=next(positions) if f in (Family.object, Family.flock) else "center")
                for f in kept[:MAX_ELEMENTS]]

    def mappings(self, elements, request):
        strength = request.chips.get("intensity", "normal")
        result = []
        for element in elements:
            for source in request.inputs:
                reaction = "pulse" if source is InputSource.sound else FAMILIES[element.family].reactions[0]
                result.append(Mapping(source=source, family=element.family, reaction=reaction, strength=strength))
        return result


class IntentDraft(BaseModel):
    title: str = Field(min_length=1, max_length=30)
    mood: list[str] = Field(min_length=1, max_length=2)
    palette: list[str] = Field(min_length=1, max_length=3)
    concept: str | None = Field(None, max_length=200)
    subject: str | None = Field(None, max_length=20)
    reveal: str | None = None


class LLMPlanner:
    def __init__(self, llm, rule: RulePlanner | None = None):
        self.llm, self.rule = llm, rule or RulePlanner()
        self.sources: dict[str, str] = {}   # 단계별 출처: llm / llm-repaired / rule / input

    def _ask(self, stage, user, schema, validate):
        last_error = ""
        for attempt in range(2):
            prompt = user if not last_error else f"{user}\n\n이전 답의 문제: {last_error}\n문제를 고쳐 다시 답하라."
            try:
                result = validate(self.llm.complete_json(SYSTEM, prompt, schema))
            except (LLMError, ValueError, TypeError, AttributeError) as error:   # 모양이 틀린 답도 수리 대상
                last_error = str(error)
                continue
            self.sources[stage] = "llm" if attempt == 0 else "llm-repaired"
            return result
        self.sources[stage] = "rule"
        return None

    def auto_request(self, recent, rng, families):
        return self.rule.auto_request(recent, rng, families)

    def intent(self, request, theme, character=None):
        """character: Input Agent가 정한 메인 캐릭터 문구. 있으면 주인공은 그 안의 명칭만 고르게 한다(관람객 신체 · 장면 요소 제외)."""
        self.sources = {}
        base = self.rule.intent(request, theme)
        if request.direction is Direction.none:
            self.sources["의도"] = "rule"
            return base
        story = request.direction is Direction.story
        properties = {
            "title": {"type": "string", "maxLength": 30},
            "mood": {"type": "array", "items": {"type": "string", "enum": list(CHIPS["mood"])},
                     "minItems": 1, "maxItems": 2},
            "palette": {"type": "array", "items": {"type": "string", "enum": PALETTE_NAMES},
                        "minItems": 1, "maxItems": 3},
        }
        if story:
            properties["concept"] = {"type": "string", "maxLength": 200}
        pick_subject = not prompt_subject(request) and request.reveal is None
        if pick_subject:
            properties["subject"] = {"type": "string", "maxLength": 20}
            properties["reveal"] = {"type": "string", "enum": [r.value for r in Reveal]}
        schema = {"type": "object", "properties": properties, "required": list(properties)}

        def validate(data):
            draft = IntentDraft.model_validate(data)
            if any(m not in CHIPS["mood"] for m in draft.mood) or any(p not in PALETTE_NAMES for p in draft.palette):
                raise ValueError("mood·palette는 주어진 목록 안에서만 고를 것")
            if story and not draft.concept:
                raise ValueError("concept가 비어 있음")
            if pick_subject and draft.reveal not in {r.value for r in Reveal}:
                raise ValueError("reveal은 trace / formation / none 중 하나")
            if pick_subject and draft.subject and draft.reveal == Reveal.none.value:
                raise ValueError("subject가 있으면 reveal은 trace / formation 중 하나. 주인공 없이 하려면 subject를 비워라")
            if pick_subject and request.mode is Mode.manual and draft.subject and \
                    not subject_in_prompt(draft.subject, request.theme):
                raise ValueError("사용자 제작에서는 원문에 없는 주인공을 추가할 수 없음. subject를 비워라")
            if pick_subject and character and draft.subject and not subject_in_prompt(draft.subject, character):
                raise ValueError(f'subject는 메인 캐릭터 "{character}" 안의 명칭만 쓸 것')
            if pick_subject and request.families and draft.subject and \
                    not REVEAL_FAMILIES[Reveal(draft.reveal)].intersection(request.families):
                usable = [r.value for r in Reveal if REVEAL_FAMILIES[r].intersection(request.families)]
                raise ValueError(f"사용자가 고른 표현 방식 {[f.value for f in request.families]}에는 reveal {usable}만 가능")
            return draft

        user = (f"테마: {LABELS.get(theme, theme)} / 문장: {request.theme or '(없음)'} / 칩: {request.chips}\n"
                "간접적으로 표현하는 미디어아트의 제목·분위기·색을 고르라." + (" 짧은 컨셉도 쓰라." if story else "")
                + ((" 전시 모드: 주인공(subject)과 드러내는 방식(reveal: trace / formation / none)을 자율 선택하라."
                    if request.mode is Mode.exhibition else
                    " 사용자 제작: subject는 원문에 직접 등장한 주인공 명칭만 추출하라. 원문에 없다면 빈 문자열, "
                    "reveal은 none이다. 테마만 보고 주인공을 추가하지 말라.") if pick_subject else "")
                + (f' 메인 캐릭터: "{character}". subject는 이 메인 캐릭터를 가리키는 명칭으로 고르라. 관람객과 그 신체'
                   "(사람 · 손 · 몸)는 입력 수단이지 주인공이 아니다." if pick_subject and character else ""))
        draft = self._ask("의도", user, schema, validate)
        if draft is None:
            return base
        update = {"title": draft.title, "mood": base.mood if "mood" in request.chips else draft.mood,
                  "palette": base.palette if "palette" in request.chips else draft.palette,
                  "concept": draft.concept if story else None}
        if pick_subject:
            update["subject"] = draft.subject or None
            update["reveal"] = Reveal(draft.reveal) if draft.subject else Reveal.none
        return base.model_copy(update=update)

    def composition(self, intent, families):
        if families:
            self.sources["구성"] = "input"
            return self.rule.composition(intent, families)
        # 드러내는 방식(형상·흔적)에 쓸 수 없는 방식은 선택지에서 뺀다. 고르면 규칙 composition이 오류를 낸다.
        allowed = REVEAL_FAMILIES[intent.reveal]
        ready = [f.value for f, spec in FAMILIES.items() if spec.status != "todo" and f in allowed]
        schema = {"type": "object", "properties": {"families": {
            "type": "array", "items": {"type": "string", "enum": ready}, "minItems": 1, "maxItems": 2}},
            "required": ["families"]}

        def validate(data):
            values = data.get("families", [])
            wrong = [value for value in values if value not in ready]
            if wrong or not values:
                raise ValueError(f"families는 {ready} 중 1~2개 (드러내는 방식 {intent.reveal.value}), 틀린 값: {wrong}")
            return [Family(value) for value in values]

        guide = ", ".join(f"{f}({FAMILIES[Family(f)].label})" for f in ready)
        canvas = [FAMILIES[f].label for f in CANVAS_FAMILIES if f.value in ready]
        chosen = self._ask("구성", f"작품 '{intent.title}'(분위기 {intent.mood})에 어울리는 표현 방식을 1~2개 고르라: "
                           f"{guide}." + (f" {'·'.join(sorted(canvas))}은 함께 쓸 수 없다." if len(canvas) > 1 else ""),
                           schema, validate)
        return self.rule.composition(intent, chosen or [])

    def mappings(self, elements, request):
        base = self.rule.mappings(elements, request)
        menu = {e.family.value: list(FAMILIES[e.family].reactions) for e in elements}
        schema = {"type": "object", "properties": {"reactions": {"type": "array", "items": {
            "type": "object", "properties": {
                "family": {"type": "string", "enum": list(menu)},
                "reaction": {"type": "string", "enum": sorted({r for rs in menu.values() for r in rs})}},
            "required": ["family", "reaction"]}}}, "required": ["reactions"]}

        def validate(data):
            chosen = {}
            for item in data.get("reactions", []):
                family, reaction = item.get("family"), item.get("reaction")
                if reaction not in menu.get(family, []):
                    raise ValueError(f"{family}에는 {reaction} 반응이 없음 (가능: {menu.get(family)})")
                chosen[family] = reaction
            return chosen

        lines = "; ".join(f"{f}: {', '.join(rs)}" for f, rs in menu.items())
        chosen = self._ask("반응", f"관람객의 손에 각 표현이 어떻게 반응할지 메뉴에서 고르라. {lines}", schema, validate)
        if not chosen:
            return base
        return [m if m.source is InputSource.sound else m.model_copy(update={"reaction": chosen.get(m.family.value,
                                                                                                    m.reaction)})
                for m in base]
