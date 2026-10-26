import pytest
from pydantic import ValidationError

from contracts.families import FAMILIES
from contracts.spec import (
    CATALOG,
    FIELD_STATES,
    NONE,
    FieldState,
    Reaction,
    Spec,
    TemplateFamily,
    fields_for,
    unfilled_required,
)

# 분산형 설계 6.4의 예시. 문서의 꺾쇠 자리는 Head가 채운 값으로 바꿨다.
WHALE = {
    "template_family": "scene_family",
    "raw_prompt": "수많은 물방울로 만들어진 고래가 헤엄치는 장면. 고래는 사람을 향해 헤엄쳐 와",
    "content": {
        "theme": "다가오는 바다", "mood": "고요하고 신비로움",
        "main_character": "물방울로 이뤄진 고래 한 마리", "reveal": "formation",
        "background": "깊고 푸른 바닷속", "dimension": "2.5d", "medium": "particle",
    },
    "idle": {"motion": None, "description": "고래가 천천히 헤엄침", "timeline": "loop"},
    "interactions": [{"input": "appear", "reaction": "follow", "reaction_mode": "while", "return": "fade"}],
    "unmatched_phrases": [{"field_path": "idle.motion", "text": "고래가 헤엄치는"}],
}
TIGER = {
    "template_family": "scene_family",
    "raw_prompt": "사람이 등장하면 풀숲에서 호랑이가 뛰쳐나오는 장면형 미디어아트",
    "content": {
        "theme": "숨어 있던 야생", "mood": "긴장감", "main_character": "호랑이 한 마리",
        "reveal": "direct", "background": "풀숲", "dimension": "2.5d",
    },
    "idle": {"description": "풀숲이 바람에 흔들림", "timeline": "loop"},
    "interactions": [{"input": "appear", "reaction": "emerge", "reaction_mode": "once", "return": "instant"}],
}
RAIN = {
    "template_family": "flow_family",
    "raw_prompt": "검은 화면에 비가 쏟아져 내리는데 사람이 위치한 부분만 피해서 비가 내리는 미디어아트",
    "content": {
        "theme": "비가 그리는 사람", "mood": "차분함",
        "palette": [{"role": "background", "hex": "#000000"}], "medium": "particle",
    },
    "idle": {"motion": "ignore", "description": "비가 위에서 쏟아짐", "timeline": "loop"},
    "interactions": [{"input": "appear", "reaction": "push", "reaction_mode": "while", "return": "instant"}],
}


def with_changes(base, **parts):
    data = {**base, **{k: v for k, v in parts.items() if k not in ("content", "idle", "rule")}}
    data["content"] = {**base["content"], **parts.get("content", {})}
    data["idle"] = {**base.get("idle", {}), **parts.get("idle", {})}
    data["interactions"] = [{**base["interactions"][0], **parts.get("rule", {})}]
    return data


@pytest.mark.parametrize("example", [WHALE, TIGER, RAIN])
def test_design_examples_pass(example):
    spec = Spec.model_validate(example)
    assert unfilled_required(spec) == []


def test_blocked_field_must_be_null():
    with pytest.raises(ValidationError, match="content.main_character는 이 계열에서 막힌 칸"):
        Spec.model_validate(with_changes(RAIN, content={"main_character": "호랑이"}))
    with pytest.raises(ValidationError, match="content.dimension는 이 계열에서 막힌 칸"):
        Spec.model_validate(with_changes(RAIN, content={"dimension": "2d"}))


def test_none_is_only_for_optional_fields():
    with pytest.raises(ValidationError, match="content.theme는 필수 칸"):
        Spec.model_validate(with_changes(TIGER, content={"theme": NONE}))
    with pytest.raises(ValidationError, match="content.dimension는 필수 칸"):
        Spec.model_validate(with_changes(TIGER, content={"dimension": NONE}))
    with pytest.raises(ValidationError):   # 입력·반응은 늘 필수라 타입에서 막힌다
        Spec.model_validate(with_changes(TIGER, rule={"reaction": NONE}))
    spec = Spec.model_validate(with_changes(TIGER, content={"palette": NONE, "style": NONE}))
    assert spec.content.palette == NONE and spec.content.style == NONE


def test_character_needs_a_visible_reveal():
    with pytest.raises(ValidationError, match="content.reveal은 direct·formation·trace"):
        Spec.model_validate(with_changes(TIGER, content={"reveal": "field"}))
    no_reveal = Spec.model_validate(with_changes(TIGER, content={"reveal": None}))
    assert "content.reveal" in unfilled_required(no_reveal)
    field_only = Spec.model_validate(with_changes(TIGER, content={"main_character": None, "reveal": "field"}))
    assert unfilled_required(field_only) == []


def test_unfilled_required_lists_empty_required_fields():
    spec = Spec.model_validate(with_changes(RAIN, content={"theme": None, "mood": None}, idle={"motion": None}))
    assert unfilled_required(spec) == ["content.theme", "content.mood", "idle.motion"]


def test_json_keeps_none_text_and_null():
    data = Spec.model_validate(with_changes(TIGER, content={"style": NONE})).model_dump(mode="json")
    assert data["content"]["style"] == "none"
    assert data["content"]["medium"] is None
    assert data["interactions"][0]["return"] == "instant"


def test_field_states_cover_every_field():
    spec = Spec.model_validate(TIGER)
    paths = {path.replace("[0]", "[]") for path, _ in spec.field_values()}
    assert paths == set(FIELD_STATES)
    assert "content.dimension" in fields_for(TemplateFamily.scene_family)
    assert "content.dimension" not in fields_for(TemplateFamily.flow_family)
    assert FIELD_STATES["content.theme"][TemplateFamily.flow_family] is FieldState.required


def test_flow_background_is_optional():
    """흐름 계열도 원문이 요구하면 배경 이미지를 둘 수 있다. 비워 두면 배경 없이 팔레트 배경색만 쓴다."""
    assert FIELD_STATES["content.background"][TemplateFamily.flow_family] is FieldState.optional
    city = Spec.model_validate(with_changes(RAIN, content={"background": "비 내리는 도시 야경"}))
    assert city.content.background == "비 내리는 도시 야경" and unfilled_required(city) == []
    assert Spec.model_validate(with_changes(RAIN, content={"background": NONE})).content.background == NONE
    assert unfilled_required(Spec.model_validate(RAIN)) == []   # 비어 있어도 필수가 아님


def test_catalog_covers_every_value():
    for enum_type, entries in CATALOG.items():
        assert set(entries) == set(enum_type), enum_type.__name__
        assert all(entry.label and entry.phrases for entry in entries.values())
    current = {r for family in FAMILIES.values() for r in family.reactions}
    assert current <= {r.value for r in Reaction}   # 지금 되는 반응은 모두 명세 목록에 있음
