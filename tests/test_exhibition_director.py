import json
import re

import pytest
from sqlmodel import Session
from test_flow import create, make_client, step

from contracts.families import Family
from contracts.schemas import CreateRequest, Element, Intent, Mode, Reveal
from db.models import Artwork
from studio.assembly import ue_scene
from studio.head.capabilities import capabilities
from studio.head.exhibition_director import (
    MAX_ATTEMPTS,
    DirectorChoice,
    ExhibitionDirector,
    problems,
    schema,
    to_request,
)
from studio.head.llm import FallbackLLM, LLMError, LLMUnavailable
from studio.head.planning import SUBJECT_WORDS


def test_ue_capabilities_follow_supported_reveals():
    caps = capabilities(["ue"])
    assert caps.reveals == (Reveal.none,)
    assert Family.media not in caps.families and Family.audio not in caps.families
    assert Family.fluid in caps.families and Family.particles in caps.families
    assert caps.moods == ("calm", "dreamy", "mysterious", "joyful")
    assert caps.palettes == ("cool", "warm", "night", "pastel")
    assert caps.max_elements == 3


def test_td_allows_every_reveal_and_both_is_intersection():
    assert set(capabilities(["td"]).reveals) == set(Reveal)
    assert capabilities(["td", "ue"]).reveals == (Reveal.none,)
    assert "고래" in capabilities(["td"]).shape_subjects


def test_adding_formation_to_ue_widens_check_and_capabilities(monkeypatch):
    monkeypatch.setattr(ue_scene, "SUPPORTED_REVEALS", frozenset({Reveal.none, Reveal.formation}))
    assert Reveal.formation in capabilities(["ue"]).reveals
    intent = Intent(title="t", theme="ocean", subject="고래", reveal=Reveal.formation)
    element = Element(family=Family.flock, module="Flock", position="center")
    assert ue_scene.unsupported(intent, [element]) == []


def test_capabilities_carry_combination_rules():
    ue = capabilities(["ue"])
    assert ue.canvas_families == (Family.paint, Family.deformation, Family.fluid)
    assert tuple(ue.reveal_families) == (Reveal.none,)
    td = capabilities(["td"])
    assert Family.paint in td.reveal_families[Reveal.trace]
    assert Family.object not in td.reveal_families[Reveal.trace] and Family.flock not in td.reveal_families[Reveal.trace]
    dumped = json.loads(json.dumps(td.as_dict()))
    assert dumped["canvas_families"] == ["paint", "deformation", "fluid"]
    assert set(dumped["reveal_families"]) == {"none", "formation", "trace"}


GOOD = {"prompt": "비 내리는 밤의 네온 골목, 손끝을 따라 빛 입자가 흩어진다", "families": ["particles"],
        "mood": "mysterious", "palette": "night", "subject": "", "reveal": "none"}


class FakeLLM:
    name, model = "fake", "luna"

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def complete_json(self, system, user, schema):
        self.prompts.append(user)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_schema_enums_come_from_capabilities():
    s = schema(capabilities(["ue"]))["properties"]
    assert s["reveal"]["enum"] == ["none"]
    assert "media" not in s["families"]["items"]["enum"] and s["families"]["maxItems"] == 3
    assert s["mood"]["enum"] == ["calm", "dreamy", "mysterious", "joyful"]


def test_problems_reports_each_violation():
    caps = capabilities(["ue"])
    assert problems(DirectorChoice.model_validate(GOOD), caps) == []
    bad = DirectorChoice.model_validate({**GOOD, "families": ["paint", "fluid"], "mood": "angry",
                                         "reveal": "formation", "subject": "고래"})
    found = " ".join(problems(bad, caps))
    assert "캔버스" in found and "angry" in found and "formation" in found


def test_formation_subject_must_be_a_shape():
    caps = capabilities(["td"])
    choice = DirectorChoice.model_validate({**GOOD, "families": ["flock"], "reveal": "formation", "subject": "용"})
    assert any("용" in p for p in problems(choice, caps))
    assert problems(choice.model_copy(update={"subject": "고래"}), caps) == []


def test_problems_rejects_prompt_that_reads_as_subject():
    choice = DirectorChoice.model_validate({**GOOD, "prompt": "물고기 떼가 어둠 속을 흐른다"})
    found = problems(choice, capabilities(["ue"]))
    assert any("물고기" in p for p in found)
    assert problems(DirectorChoice.model_validate(GOOD), capabilities(["ue"])) == []


def test_problems_rejects_families_outside_reveal_and_blank_prompt():
    caps = capabilities(["td"])
    base = {**GOOD, "reveal": "trace", "subject": "사슴"}
    mixed = DirectorChoice.model_validate({**base, "families": ["paint", "object"]})
    assert any("object" in p for p in problems(mixed, caps))
    assert problems(DirectorChoice.model_validate({**base, "families": ["paint"]}), caps) == []
    blank = DirectorChoice.model_construct(**{**GOOD, "prompt": "   ", "families": [Family.particles],
                                              "reveal": Reveal.none})
    assert any("prompt" in p for p in problems(blank, capabilities(["ue"])))


def test_to_request_keeps_explicit_request_values():
    base = CreateRequest(mode=Mode.exhibition, families=["object"], chips={"palette": "warm"})
    request = to_request(DirectorChoice.model_validate(GOOD), base)
    assert request.theme == GOOD["prompt"] and request.mode is Mode.exhibition
    assert [f.value for f in request.families] == ["object"]
    assert request.chips == {"palette": "warm", "mood": "mysterious"}
    assert request.reveal.value == "none" and request.subject == ""


def test_to_request_rejects_long_prompt_as_problem():
    with pytest.raises(ValueError):
        to_request(DirectorChoice.model_construct(**{**GOOD, "prompt": "가" * 2001}),
                   CreateRequest(mode=Mode.exhibition))


def test_propose_sends_capabilities_and_rejections():
    llm = FakeLLM(GOOD)
    choice = ExhibitionDirector(llm).propose(capabilities(["ue"]), ["캔버스 방식은 하나만"])
    assert choice.prompt == GOOD["prompt"]
    assert "캔버스 방식은 하나만" in llm.prompts[0] and "particles" in llm.prompts[0]
    assert "canvas_families" in llm.prompts[0] and "reveal_families" in llm.prompts[0]
    assert all(word in llm.prompts[0] for word in SUBJECT_WORDS) and "물고기" in llm.prompts[0]
    assert not re.search(r"\b(trace|formation)\b", llm.prompts[0])   # deformation(표현 방식)과 구분
    assert ExhibitionDirector(llm).model_ref == "fake:luna" and MAX_ATTEMPTS == 3


def test_propose_raises_value_error_on_malformed_answer():
    with pytest.raises(ValueError):
        ExhibitionDirector(FakeLLM({"prompt": ""})).propose(capabilities(["ue"]), [])


OBJECT = {**GOOD, "families": ["object"], "palette": "warm"}


def director_client(tmp_path, *answers, renderer="ue"):
    return make_client(tmp_path, renderer, director=ExhibitionDirector(FakeLLM(*answers)))


def test_director_prompt_becomes_the_artwork(tmp_path):
    with director_client(tmp_path, OBJECT) as client:
        artwork = create(client, mode="exhibition")
        assert artwork["status"] == "prepared", artwork["steps"]
        detail = step(artwork, "디렉터")
        assert detail["source"] == "llm" and detail["fallback"] is None and detail["model"] == "fake:luna"
        with Session(client.app.state.engine) as session:   # 응답 모델에는 요청이 없어 기록(DB)을 직접 본다
            assert session.get(Artwork, artwork["id"]).request["theme"] == OBJECT["prompt"]


def test_director_retries_after_invalid_answers(tmp_path):
    too_long = {**OBJECT, "prompt": "가" * 201}
    with director_client(tmp_path, {**OBJECT, "reveal": "formation", "subject": "고래"}, too_long, OBJECT) as client:
        artwork = create(client, mode="exhibition")
        detail = step(artwork, "디렉터")
        assert artwork["status"] == "prepared" and detail["source"] == "llm-repaired"
        assert len(detail["attempts"]) == 3 and all(a["problems"] for a in detail["attempts"][:2])
        assert len(client.get("/api/pool").json()) == 1   # 소재는 통과한 회차에서 한 번만


def test_director_errors_fall_back_to_preset(tmp_path):
    with director_client(tmp_path, LLMError("both down"), {"prompt": ""}, LLMError("both down")) as client:
        artwork = create(client, mode="exhibition")
        assert artwork["status"] == "prepared"
        assert step(artwork, "디렉터")["fallback"] == "다시 쓰기 소진"
        assert step(artwork, "자동 요청 생성")["preset"]


def test_unreachable_llm_falls_back_immediately(tmp_path):
    llm = FakeLLM(LLMUnavailable("GMS down"), OBJECT)
    with make_client(tmp_path, "ue", director=ExhibitionDirector(llm)) as client:
        artwork = create(client, mode="exhibition")
        assert step(artwork, "디렉터")["fallback"] == "연결 실패" and llm.answers == [OBJECT]


def test_unexpected_director_error_falls_back_and_keeps_the_record(tmp_path):
    llm = FakeLLM(RuntimeError("boom"), OBJECT)
    with make_client(tmp_path, "ue", director=ExhibitionDirector(llm)) as client:
        artwork = create(client, mode="exhibition")   # 500이면 create가 실패한다
        detail = step(artwork, "디렉터")
        assert artwork["status"] == "prepared" and detail["fallback"] == "디렉터 오류" and detail["source"] == "preset"
        assert detail["attempts"] == [{"answer": None, "problems": ["RuntimeError: boom"], "model": "fake:luna"}]
        assert step(artwork, "자동 요청 생성")["preset"] and llm.answers == [OBJECT]   # 다시 묻지 않는다
        assert len(client.get("/api/artworks").json()) == 1


def test_consecutive_director_works_make_new_assets(tmp_path):
    dawn = {**OBJECT, "prompt": "새벽 호숫가에 안개가 번진다"}
    desert = {**OBJECT, "prompt": "사막 모래 위로 바람 자국이 흐른다", "palette": "cool"}
    with director_client(tmp_path, dawn, desert) as client:
        logs = [step(create(client, mode="exhibition"), "소재") for _ in range(2)]
        backgrounds = [next(line for line in log if line.startswith("background:")) for log in logs]
        assert all("새로 생성" in line for line in backgrounds) and backgrounds[0] != backgrounds[1]
        assert [asset["uses"] for asset in client.get("/api/pool").json()] == [1, 1]


def test_plan_failure_before_assets_asks_director_again(tmp_path):
    from studio.head.planning import RulePlanner

    class FlakyPlanner(RulePlanner):
        calls = 0

        def intent(self, request, theme, character=None):
            FlakyPlanner.calls += 1
            if FlakyPlanner.calls == 1:
                raise ValueError("기획 실패")
            return super().intent(request, theme, character)

    with make_client(tmp_path, "ue", planner=FlakyPlanner(),
                     director=ExhibitionDirector(FakeLLM(OBJECT, OBJECT))) as client:
        artwork = create(client, mode="exhibition")
        assert artwork["status"] == "prepared" and "기획 실패" in step(artwork, "디렉터")["attempts"][0]["problems"][0]


def test_director_subject_survives_when_ue_supports_formation(tmp_path, monkeypatch):
    monkeypatch.setattr(ue_scene, "SUPPORTED_REVEALS", frozenset({Reveal.none, Reveal.formation}))
    whale = {**GOOD, "families": ["flock"], "reveal": "formation", "subject": "고래"}
    with director_client(tmp_path, whale) as client:
        artwork = create(client, mode="exhibition")
        assert step(artwork, "의도")["subject"] == "고래" and step(artwork, "디렉터")["source"] == "llm"


def test_director_is_not_used_for_manual_or_themed_requests(tmp_path):
    with director_client(tmp_path) as client:   # 답이 없으므로 부르면 IndexError
        assert create(client, theme="바다", reveal="none")["status"] == "prepared"
        assert create(client, mode="exhibition", theme="숲")["status"] == "prepared"


def test_no_llm_keeps_preset_flow(tmp_path):
    with make_client(tmp_path, "ue") as client:
        artwork = create(client, mode="exhibition")
        assert artwork["status"] == "prepared"
        assert not any(s["name"] == "디렉터" for s in artwork["steps"])


def test_model_is_recorded_per_attempt(tmp_path):
    with director_client(tmp_path, {"prompt": ""}, OBJECT) as client:
        detail = step(create(client, mode="exhibition"), "디렉터")
        assert [a["model"] for a in detail["attempts"]] == ["fake:luna", "fake:luna"] and detail["model"] == "fake:luna"


def test_both_llms_unreachable_falls_back_after_one_call(tmp_path):
    primary, backup = FakeLLM(LLMUnavailable("GMS down"), OBJECT), FakeLLM(LLMUnavailable("Ollama down"), OBJECT)
    with make_client(tmp_path, "ue", director=ExhibitionDirector(FallbackLLM(primary, backup))) as client:
        artwork = create(client, mode="exhibition")
        assert artwork["status"] == "prepared" and step(artwork, "디렉터")["fallback"] == "연결 실패"
        assert primary.answers == [OBJECT] and backup.answers == [OBJECT]   # 각각 한 번만 불림
