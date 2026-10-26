"""확정한 모드·미지원 오류·전체 후보 준비 정책의 관찰 가능한 결과를 검증한다."""
import json
from datetime import UTC, datetime

import pytest
from tests.test_flow import FakeLLM, create, make_client, step
from tests.test_infra import LiveProcess
from tests.test_spec_matching import code, text

from contracts.schemas import CreateRequest
from show_agent.runtime import observe
from studio.head.llm import LLMError
from studio.head.planning import LLMPlanner, RulePlanner
from studio.head.spec_matching import LLMSpecMatcher


def active_pointers(tmp_path):
    pointers = []
    for folder in ("delivery", "delivery_td"):
        path = tmp_path / folder / "release.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"SchemaVersion":1,"Version":"current-A"}', encoding="utf-8")
        pointers.append((path, path.read_bytes()))
    return pointers


def test_manual_theme_and_exhibition_theme_have_different_subject_policy():
    planner = RulePlanner()
    manual = planner.intent(CreateRequest(theme="바다"), "ocean")
    automatic = planner.intent(CreateRequest(mode="exhibition", theme="바다"), "ocean")
    assert manual.subject is None and manual.reveal.value == "none"
    assert automatic.subject == "고래" and automatic.reveal.value == "formation"


def test_exhibition_selects_within_both_capabilities_without_overriding_explicit_demands(tmp_path):
    with make_client(tmp_path, "both") as client:
        auto = create(client, mode="exhibition", theme="바다")
        assert auto["status"] == "prepared" and "자동 기획 범위" in [s["name"] for s in auto["steps"]]
        explicit = create(client, mode="exhibition", theme="바닷속 고래")
        assert explicit["status"] == "failed" and "UE 조립 어댑터" in step(explicit, "상영")


@pytest.mark.parametrize("prompt", ["바닷속 고래가 지나간다", "고래의 형상", "a whale swims"])
def test_prompt_subject_is_kept_without_subject_field(prompt):
    intent = RulePlanner().intent(CreateRequest(theme=prompt), "custom")
    assert intent.subject in ("고래", "whale") and intent.reveal.value == "formation"


@pytest.mark.parametrize("prompt", ["바다", "새로운 바다", "고래 없이 파도만", "바다에서 고래를 제외해", "ocean without whale"])
def test_theme_or_excluded_subject_does_not_create_a_protagonist(prompt):
    assert RulePlanner().intent(CreateRequest(theme=prompt), "ocean").subject is None


def test_manual_llm_cannot_invent_a_subject_and_exhibition_can():
    answer = {"title": "바다", "mood": ["calm"], "palette": ["teal"], "subject": "고래", "reveal": "formation"}
    manual = LLMPlanner(FakeLLM(answer, answer))
    assert manual.intent(CreateRequest(theme="바다"), "ocean").subject is None
    assert manual.sources["의도"] == "rule"
    exhibition = LLMPlanner(FakeLLM(answer))
    assert exhibition.intent(CreateRequest(mode="exhibition", theme="바다"), "ocean").subject == "고래"


def test_llm_can_extract_a_new_name_only_with_prompt_evidence():
    answer = {"title": "장면", "mood": ["calm"], "palette": ["teal"], "subject": "북극여우", "reveal": "trace"}
    intent = LLMPlanner(FakeLLM(answer)).intent(CreateRequest(theme="북극여우가 지나가는 장면"), "custom")
    assert intent.subject == "북극여우"


LAKE = "잔잔한 호수에서 사람이 등장하면 파동이 일렁이는 미디어아트"
# Input Agent 답: 관람객('사람이 등장하면')은 입력 칸으로 가고 메인 캐릭터는 비어 있다.
LAKE_SPEC = {"fields": {
    "content.theme": text("노을 호수의 파동", "잔잔한 호수"), "content.mood": text("고요함"),
    "content.background": text("노을 지는 호수", "잔잔한 호수에서"), "content.dimension": code("2.5d", None, None),
}, "interactions": [{
    "input": code("appear", "사람이 등장하면"), "reaction": code("ripple", "파동이 일렁이는"),
    "reaction_mode": code("while", None, None), "return": code("fade", None, None),
}], "holds": []}
# 기획(planning)의 의도 답: 관람객을 주인공으로 고른다(2026-10-22 실제 GPT 측정에서 반복된 실패).
LAKE_INTENT = {"title": "노을 호수", "mood": ["calm"], "palette": ["amber"], "subject": "사람", "reveal": "trace"}
PENGUIN = "물방울로 만들어진 펭귄이 사람을 향해 걸어와"
PENGUIN_SPEC = {"fields": {
    "content.theme": text("물방울 펭귄"), "content.mood": text("호기심"), "content.background": text("얼음 바다"),
    "content.dimension": code("2.5d", None, None),
    "content.main_character": text("물방울로 만들어진 펭귄", "물방울로 만들어진 펭귄"),
    "content.reveal": code("formation", "물방울로 만들어진 펭귄"),
}, "interactions": [{
    "input": code("appear", "사람을 향해"), "reaction": code("follow", "사람을 향해 걸어와"),
    "reaction_mode": code("while", None, None), "return": code("fade", None, None),
}], "holds": []}
PENGUIN_INTENT = {"title": "물방울 펭귄", "mood": ["calm"], "palette": ["teal"], "subject": "펭귄", "reveal": "formation"}


def test_input_agent_keeps_audience_out_of_the_subject(tmp_path):
    """Input Agent가 메인 캐릭터 없음으로 판단하면 기획에 '드러내기 없음'을 넘겨, 관람객을 주인공으로 뽑아 실패하지 않는다."""
    body = {"theme": LAKE, "families": ["deformation"]}
    with make_client(tmp_path / "before", "both", planner=LLMPlanner(FakeLLM(LAKE_INTENT))) as client:
        before = create(client, **body)
    assert before["status"] == "failed" and "trace" in step(before, "상영")
    agent = LLMSpecMatcher(FakeLLM(LAKE_SPEC))
    with make_client(tmp_path / "after", "both", planner=LLMPlanner(FakeLLM(LAKE_INTENT)), input_agent=agent) as client:
        after = create(client, **body)
    assert after["status"] == "prepared"
    read = step(after, "작품 명세")
    assert read["decision"].startswith("메인 캐릭터 없음") and read["sources"] == {"명세": "llm"}
    assert "main_character" not in read["spec"]["content"]
    assert read["spec"]["interactions"][0]["input"] == "appear"
    assert step(after, "의도")["reveal"] == "none" and "subject" not in step(after, "의도")


def test_input_agent_leaves_a_real_character_to_the_planner(tmp_path):
    agent = LLMSpecMatcher(FakeLLM(PENGUIN_SPEC))
    with make_client(tmp_path, planner=LLMPlanner(FakeLLM(PENGUIN_INTENT)), input_agent=agent) as client:
        draft = client.post("/api/drafts", json={"theme": PENGUIN, "families": ["particles"]}).json()
    assert draft["status"] == "awaiting"
    assert step(draft, "작품 명세")["decision"].startswith("메인 캐릭터 있음(물방울로 만들어진 펭귄)")
    assert draft["draft"]["subject"] == "펭귄" and draft["draft"]["reveal"] == "formation"


@pytest.mark.parametrize("body,decision", [
    ({"theme": LAKE}, "LLM 답이 규칙으로"),                       # Input Agent가 두 번 다 실패 → 판단에 쓰지 않음
    ({"theme": LAKE, "subject": "백조"}, "사용자 · 규칙이 정한"),   # 사용자가 주인공을 직접 정함
])
def test_input_agent_does_not_override_without_a_reliable_answer(tmp_path, body, decision):
    agent = LLMSpecMatcher(FakeLLM(LLMError("연결 실패"), LLMError("연결 실패")) if "subject" not in body
                           else FakeLLM(LAKE_SPEC))
    with make_client(tmp_path, planner=LLMPlanner(FakeLLM(LAKE_INTENT)), input_agent=agent) as client:
        draft = client.post("/api/drafts", json={**body, "families": ["deformation"]}).json()
    assert step(draft, "작품 명세")["decision"].startswith(decision)
    assert draft["draft"]["reveal"] == "trace"   # 기존 기획 판단 그대로


METEOR = "별이 빛나는 밤하늘에서 손이 지나간 자리에 유성우가 떨어지는 미디어아트"
METEOR_SPEC = {"fields": {
    "content.theme": text("손길을 따르는 유성우"), "content.mood": text("신비로움"),
    "content.background": text("별이 빛나는 밤하늘", "별이 빛나는 밤하늘"), "content.dimension": code("2.5d", None, None),
    "content.main_character": text("떨어지는 유성우", "유성우가 떨어지는"),
    "content.reveal": code("direct", "유성우가 떨어지는"),
}, "interactions": [{
    "input": code("move", "손이 지나간"), "reaction": code("follow", "손이 지나간 자리에"),
    "reaction_mode": code("while", None, None), "return": code("fade", None, None),
}], "holds": []}


def test_planner_picks_the_subject_inside_the_input_agent_character(tmp_path):
    """기획이 관람객의 손을 주인공으로 고르면 Input Agent의 메인 캐릭터 밖이라 틀린 답으로 보고 다시 묻는다."""
    hand = {"title": "별의 궤적", "mood": ["dreamy"], "palette": ["deep blue"], "subject": "손", "reveal": "trace"}
    agent = LLMSpecMatcher(FakeLLM(METEOR_SPEC))
    planner = LLMPlanner(FakeLLM(hand, {**hand, "subject": "유성우"}))
    with make_client(tmp_path, planner=planner, input_agent=agent) as client:
        draft = client.post("/api/drafts", json={"theme": METEOR, "families": ["paint"]}).json()
    assert step(draft, "작품 명세")["decision"] == "메인 캐릭터 있음(떨어지는 유성우) → 기획이 그 안에서 주인공을 고름"
    assert draft["draft"]["subject"] == "유성우" and step(draft, "초안")["sources"]["의도"] == "llm-repaired"


def test_input_agent_error_does_not_fail_the_artwork(tmp_path):
    class Broken(LLMSpecMatcher):
        def match(self, raw_prompt, family, mode="auto"):
            raise RuntimeError("예상 못 한 오류")

    agent = Broken(FakeLLM())
    with make_client(tmp_path, planner=LLMPlanner(FakeLLM(LAKE_INTENT)), input_agent=agent) as client:
        draft = client.post("/api/drafts", json={"theme": LAKE, "families": ["deformation"]}).json()
    assert draft["status"] == "awaiting"
    assert step(draft, "작품 명세") == {"decision": "Input Agent 오류(RuntimeError) → 기획만으로 진행"}
    assert draft["draft"]["reveal"] == "trace"   # 기획만으로 진행한 판단 그대로


@pytest.mark.parametrize("body,reason", [
    ({"inputs": ["body"]}, "body"), ({"inputs": ["hand", "sound"]}, "sound"),
    ({"reference": {"path": "reference.png"}}, "reference"),
    ({"chips": {"composition": "diagonal"}}, "composition"),
    ({"chips": {"focus": "far"}}, "focus"), ({"chips": {"space": "full"}}, "space"),
    ({"families": ["media"]}, "media"), ({"families": ["audio"]}, "audio"),
    ({"timeline": "stages"}, "stages"), ({"timeline": "accumulate"}, "accumulate"),
    ({"direction": "none", "chips": {"palette": "warm"}}, "palette"),
    ({"subject": "고래"}, "UE 조립 어댑터"),
])
def test_unsupported_requests_fail_before_assets_and_keep_both_active_versions(tmp_path, body, reason):
    pointers = active_pointers(tmp_path)
    with make_client(tmp_path, "both") as client:
        result = create(client, theme="바다", **body)
        assert result["status"] == "failed" and reason in step(result, "상영")
        assert client.get("/api/pool").json() == []
        assert not any(s["name"] == "소재" for s in result["steps"])
        assert result["runtime_phase"] == "Unknown"
    assert all(path.read_bytes() == previous for path, previous in pointers)


@pytest.mark.parametrize("failed_folder", ["delivery", "delivery_td"])
@pytest.mark.parametrize("gate", ["none", "final", "approve"])
def test_either_output_preparation_failure_keeps_all_active_versions(tmp_path, monkeypatch, failed_folder, gate):
    from central import pipeline

    pointers = active_pointers(tmp_path)
    real = pipeline.prepare_version
    with make_client(tmp_path, "both") as client:
        if gate == "approve":
            pending = create(client, theme="바다", review_final=True)
            assert pending["status"] == "final_review"

        def fail_one(folder, *args):
            if folder.name == failed_folder:
                raise OSError("시험 디스크 장애")
            return real(folder, *args)

        monkeypatch.setattr(pipeline, "prepare_version", fail_one)
        result = (client.post(f"/api/artworks/{pending['id']}/review", json={"approve": True}).json()
                  if gate == "approve" else create(client, theme="바다", review_final=(gate == "final")))
        assert result["status"] == "failed" and "시험 디스크 장애" in step(result, "상영")
        assert result["preparation"]["complete"] is False and result["scene_path"] is None
        assert sorted(t["status"] for t in result["preparation"]["targets"].values()) == ["failed", "prepared"]
    assert all(path.read_bytes() == previous for path, previous in pointers)


def test_both_success_is_only_prepared_and_never_invents_runtime_ready(tmp_path):
    pointers = active_pointers(tmp_path)
    with make_client(tmp_path, "both") as client:
        result = create(client, theme="바다")
        assert result["status"] == "prepared" and result["preparation"]["complete"]
        assert set(result["preparation"]["targets"]) == {"td", "ue"}
        assert result["runtime_phase"] == "Unknown"
        assert client.get(f"/api/artworks/{result['id']}").json()["runtime_phase"] == "Unknown"
    assert all(path.read_bytes() == previous for path, previous in pointers)


def test_old_partial_review_cannot_be_approved_as_both(tmp_path):
    from sqlmodel import Session

    from db.models import Artwork

    pointers = active_pointers(tmp_path)
    with make_client(tmp_path, "both") as client:
        pending = create(client, theme="바다", review_final=True)
        with Session(client.app.state.engine) as session:
            row = session.get(Artwork, pending["id"])
            steps = json.loads(json.dumps(row.steps))
            context = next(s["detail"]["context"] for s in steps if s["name"] == "검수")
            context["reviewed"].pop("ue")
            row.steps = steps
            session.add(row)
            session.commit()
        result = client.post(f"/api/artworks/{pending['id']}/review", json={"approve": True}).json()
        assert result["status"] == "failed" and "누락" in step(result, "상영")
    assert all(path.read_bytes() == previous for path, previous in pointers)


def test_runtime_ready_is_not_applied_and_conflicting_reports_are_unknown(tmp_path):
    path = tmp_path / "runtime.json"
    data = {"ProcessId": 123, "RuntimeSessionId": "session", "UpdatedAtUtc": datetime.now(UTC).isoformat(),
            "Phase": "Ready", "Applied": False, "RequestedVersion": "B", "ActiveReleaseVersion": "A"}
    path.write_text(json.dumps(data))
    report = observe(path, 123, "B", LiveProcess)
    assert report.phase == "Ready" and report.active_version == "A"
    data["Applied"] = True
    path.write_text(json.dumps(data))
    assert observe(path, 123, "B", LiveProcess).phase == "Unknown"
    data.update(Phase="Applied", ActiveReleaseVersion="B")
    path.write_text(json.dumps(data))
    assert observe(path, 123, "B", LiveProcess).phase == "Applied"
    assert observe(path, 123, "C", LiveProcess).phase == "Unknown"
