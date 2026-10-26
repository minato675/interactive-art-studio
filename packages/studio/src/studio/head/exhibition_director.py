"""전시 모드 AI 미디어아트 디렉터. 렌더러가 처리할 수 있는 범위 안에서 작품 한 건의 방향을 LLM이 고른다.

LLM은 선택지(Capabilities) 안에서만 고르고, 코드(problems)가 범위를 다시 검사한다. 틀리면 이유를 붙여 다시 묻는 일은 부르는 쪽이 한다.
"""
import json

from pydantic import BaseModel, Field

from contracts.families import CANVAS_FAMILIES, REVEAL_FAMILIES
from contracts.schemas import CreateRequest, Family, Reveal
from studio.head.capabilities import Capabilities
from studio.head.planning import SUBJECT_WORDS, prompt_subject

PROMPT_VERSION = "exhibition-director-1"
MAX_ATTEMPTS = 3

SYSTEM = ("너는 AI 미디어아트 디렉터다. 전시장에 올릴 인터랙티브 작품 한 건의 방향을 정한다. 대상을 직접 보여 주지 않고 "
          "흔적이나 순간의 형상으로 간접적으로 드러낸다(예: 사슴 대신 사슴의 발자취, 고래 대신 물고기 떼가 잠깐 이루는 "
          "고래 모양). 주어진 선택지 밖의 값은 쓰지 않고, 주어진 JSON 형식으로만 답한다.")


class DirectorChoice(BaseModel):
    prompt: str = Field(min_length=1, max_length=200)
    families: list[Family]
    mood: str
    palette: str
    subject: str = Field("", max_length=20)
    reveal: Reveal


def schema(caps: Capabilities) -> dict:
    """LLM에 주는 JSON 형식. 고를 수 있는 값은 caps의 값으로 제한한다."""
    properties = {
        "prompt": {"type": "string", "minLength": 1, "maxLength": 200},
        "families": {"type": "array", "items": {"type": "string", "enum": [f.value for f in caps.families]},
                     "minItems": 1, "maxItems": caps.max_elements},
        "mood": {"type": "string", "enum": list(caps.moods)},
        "palette": {"type": "string", "enum": list(caps.palettes)},
        "subject": {"type": "string", "maxLength": 20},
        "reveal": {"type": "string", "enum": [r.value for r in caps.reveals]},
    }
    return {"type": "object", "properties": properties, "required": list(properties)}


def problems(choice: DirectorChoice, caps: Capabilities) -> list[str]:
    """디렉터의 선택이 거부되는 이유. 빈 목록이면 통과한다. 이유에는 틀린 값을 넣어 고칠 수 있게 한다."""
    found = []
    if not choice.prompt.strip():
        found.append("prompt가 비어 있음")
    allowed = {f.value for f in caps.families}
    wrong = [f.value for f in choice.families if f.value not in allowed]
    if wrong:
        found.append(f"표현 방식 {wrong}은 지원하지 않음. 가능: {sorted(allowed)}")
    if len(set(choice.families)) != len(choice.families):
        found.append("표현 방식이 중복됨")
    if len(choice.families) > caps.max_elements:
        found.append(f"표현 방식은 최대 {caps.max_elements}개")
    if len(CANVAS_FAMILIES.intersection(choice.families)) > 1:
        names = sorted(f.value for f in CANVAS_FAMILIES)
        found.append(f"캔버스 방식 {names}은 하나만 고를 수 있음")
    if choice.mood not in caps.moods:
        found.append(f"분위기 {choice.mood}는 목록 밖. 가능: {list(caps.moods)}")
    if choice.palette not in caps.palettes:
        found.append(f"색 {choice.palette}는 목록 밖. 가능: {list(caps.palettes)}")
    if choice.reveal not in caps.reveals:
        found.append(f"드러내는 방식 {choice.reveal.value}는 지원하지 않음. 가능: {[r.value for r in caps.reveals]}")
    subject = choice.subject.strip()
    if choice.reveal is Reveal.none and subject:
        found.append("reveal이 none이면 subject는 비워야 함")
    if choice.reveal is not Reveal.none and not subject:
        found.append(f"reveal {choice.reveal.value}에는 subject(주인공)가 필요")
    extra = set(choice.families) - REVEAL_FAMILIES[choice.reveal]
    if extra:
        usable = sorted(f.value for f in REVEAL_FAMILIES[choice.reveal])
        names = sorted(f.value for f in extra)
        found.append(f"reveal {choice.reveal.value}에는 표현 방식 {names}을 쓸 수 없음. 가능: {usable}")
    if choice.reveal is Reveal.formation and subject and subject not in caps.shape_subjects:
        found.append(f"formation의 주인공 {subject}는 형상이 없음. 가능: {list(caps.shape_subjects)}")
    if not subject:
        try:
            named = prompt_subject(CreateRequest(theme=choice.prompt))
        except ValueError:
            named = None   # 길이 문제는 다른 곳에서 알린다
        if named:
            found.append(f"subject가 비었는데 prompt에 '{named}'가 나옴. 생물·캐릭터 이름을 빼고 장면·분위기로 다시 쓸 것 "
                         "(다른 낱말의 일부로 들어간 경우도 걸리니 그 글자 자체를 피할 것)")
    return found


def to_request(choice: DirectorChoice, base: CreateRequest) -> CreateRequest:
    """base에 명시된 값을 우선하고 나머지는 디렉터 값으로 채운다. 다시 검증하며 ValueError가 날 수 있다."""
    update = {
        "theme": choice.prompt,
        "families": base.families or list(choice.families),
        "chips": {"mood": choice.mood, "palette": choice.palette, **base.chips},
        "subject": base.subject.strip() or choice.subject.strip(),
        "reveal": base.reveal if base.reveal is not None else choice.reveal,
    }
    return CreateRequest.model_validate({**base.model_dump(), **update})


class ExhibitionDirector:
    def __init__(self, llm):
        self.llm = llm

    @property
    def model_ref(self) -> str:
        model = getattr(self.llm, "model", None)
        return f"{self.llm.name}:{model}" if model else self.llm.name

    def propose(self, caps: Capabilities, rejected: list[str]) -> DirectorChoice:
        """작품 방향을 하나 제안한다. LLMError는 그대로 올리고 형식이 틀린 답은 ValueError."""
        user = ("전시장에 올릴 새 작품 하나의 방향을 정하라.\n"
                f"선택지(이 밖의 값은 쓰지 말 것): {json.dumps(caps.as_dict(), ensure_ascii=False)}\n"
                "prompt는 장면·분위기·움직임을 한국어 한두 문장으로 쓴다. 관람객의 손끝에 장면이 반응한다.\n"
                f"드러내는 방식(reveal)이 '{Reveal.none.value}'이면 subject를 비우고 prompt에 생물·캐릭터 이름을 쓰지 말라. "
                f"'{Reveal.none.value}'이 아닌 값이면 subject(주인공)가 필요하다. reveal은 선택지에 있는 값만 쓴다.\n"
                "고른 표현 방식(families)은 모두 reveal_families[reveal] 안에 있어야 하고, "
                "canvas_families에서는 하나까지만 고른다.\n"
                "subject가 없으면 prompt에 다음 낱말이 글자로 들어가지 않게 하라(다른 낱말의 일부여도 걸린다): "
                f"{', '.join(SUBJECT_WORDS)}")
        if rejected:
            user += "\n이전 제안이 거부된 이유:\n" + "\n".join(f"- {reason}" for reason in rejected) + "\n고쳐서 다시 답하라."
        data = self.llm.complete_json(SYSTEM, user, schema(caps))
        return DirectorChoice.model_validate(data)   # ValidationError는 ValueError
