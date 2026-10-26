import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from central.config import Settings
from central.main import create_app
from studio.head.planning import RulePlanner


def make_client(tmp_path, renderer="td", **kwargs):
    app = create_app(Settings(data_dir=tmp_path, operator_token="secret", renderer=renderer), seed=1, **kwargs)
    return TestClient(app, headers={"X-Operator-Token": "secret"})


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as test_client:
        yield test_client


@pytest.fixture
def ue_client(tmp_path):
    with make_client(tmp_path, "ue") as test_client:
        yield test_client


def create(client, **body):
    response = client.post("/api/artworks", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def step(artwork, name):
    return next(s["detail"] for s in artwork["steps"] if s["name"] == name)


def spec_of(artwork):
    path = Path(artwork["scene_path"])
    return json.loads(path.read_text("utf-8")), path.parent


# --- 간접 표현: 보이지 않는 주인공 -------------------------------------------------------------

def test_ocean_default_is_invisible_whale_formed_by_flock(client):
    artwork = create(client, mode="exhibition", theme="바다")
    assert artwork["status"] == "prepared", artwork["steps"]
    intent = step(artwork, "의도")
    assert intent["subject"] == "고래" and intent["reveal"] == "formation"
    spec, folder = spec_of(artwork)
    assert spec["layers"] == [{"family": "flock", "position": "center"}]
    assert [p["name"] for p in spec["phases"]] == ["흩어져 유영", "모여듦", "형상 유영", "흩어짐"]
    silhouette = json.loads((folder / spec["subject"]["file"]).read_text("utf-8"))
    assert silhouette["source"] == "local-shapes" and len(silhouette["points"]) == 600
    assert (folder / "background.png").exists()


def test_forest_default_is_deer_footsteps(client):
    artwork = create(client, mode="exhibition", theme="숲")
    spec, folder = spec_of(artwork)
    assert spec["subject"]["reveal"] == "trace" and spec["layers"][0]["family"] == "paint"
    path = json.loads((folder / "path.json").read_text("utf-8"))
    assert len(path["steps"]) >= 6 and {s["side"] for s in path["steps"]} == {"L", "R"}


def test_silhouette_is_reused_across_themes(client):
    create(client, theme="바다", subject="고래")
    second = create(client, theme="우주", subject="고래", reveal="formation", families=["particles"])
    assert any("실루엣: 풀에서 사용" in line for line in step(second, "소재"))
    silhouettes = [a for a in client.get("/api/pool").json() if a["kind"] == "silhouette"]
    assert len(silhouettes) == 1 and silhouettes[0]["uses"] == 2


def test_exhibition_always_generates_silhouette(client):
    first = create(client, mode="exhibition", theme="바다")
    second = create(client, mode="exhibition", theme="바다")
    for artwork in (first, second):
        assert artwork["status"] == "prepared", artwork["steps"]
        assert any("silhouette.json: 새로 생성" in line for line in step(artwork, "소재"))
    silhouettes = [a for a in client.get("/api/pool").json() if a["kind"] == "silhouette"]
    assert len(silhouettes) == 2   # 새 실루엣도 풀에는 쌓인다


def test_unknown_subject_is_an_error(client):
    artwork = create(client, theme="우주", subject="용", families=["particles"])
    assert artwork["status"] == "failed" and "지원하지 않는 주인공" in step(artwork, "상영")


def test_reveal_none_has_no_subject_file(client):
    artwork = create(client, theme="고요한 빛", reveal="none", families=["particles"])
    spec, _ = spec_of(artwork)
    assert spec["subject"] is None and [p["name"] for p in spec["phases"]] == ["장"]


def test_user_family_wins_over_theme_default(client):
    intent = step(create(client, mode="exhibition", theme="바다", families=["fluid"]), "의도")
    assert intent["reveal"] == "trace"   # 고래 형상 대신 유체로 남는 흔적


def test_intensity_changes_tempo(client):
    slow = step(create(client, mode="exhibition", theme="바다", chips={"intensity": "gentle"}), "안무")
    fast = step(create(client, mode="exhibition", theme="바다", chips={"intensity": "strong"}), "안무")
    assert slow[0] != fast[0]


def test_incompatible_reveal_and_family_is_422(client):
    assert client.post("/api/artworks", json={"reveal": "formation", "families": ["paint"]}).status_code == 422


# --- TD에서 읽는 도구 ----------------------------------------------------------------------

def test_artwork_reader_follows_release_and_phases(client, tmp_path):
    from td import artwork_reader as reader

    artwork = create(client, mode="exhibition", theme="바다")
    # reader는 명시적 게시를 읽는 구형 어댑터다. 제작 API는 활성 포인터를 쓰지 않는다.
    folder = Path(artwork["scene_path"]).parent
    (tmp_path / "delivery_td" / "release.json").write_text(json.dumps({"SchemaVersion": 1, "Version": folder.name}))
    current = reader.load_current(tmp_path / "delivery_td")
    assert len(current.points()) == 600
    at = {t: reader.phase_at(current.spec, t) for t in (0, 24, 30, 44.5, 46)}
    assert at[0].name == "흩어져 유영" and at[0].target == 0
    assert at[24].name == "모여듦" and abs(at[24].target - 0.5) < 1e-9
    assert at[30].target == 1 and at[44.5].burst
    assert at[46].name == "흩어져 유영"   # 반복


# --- UE 렌더러 ---------------------------------------------------------------------------

def test_ue_receives_real_scene_for_field_artwork(ue_client):
    artwork = create(ue_client, theme="바다", reveal="none", families=["fluid"])
    assert artwork["status"] == "prepared", artwork["steps"]
    scene_path = Path(artwork["scene_path"])
    scene = json.loads(scene_path.read_text("utf-8"))
    assert scene["SchemaVersion"] == 1 and "FluidField" in scene["Profiles"]
    assert scene["ExternalAssets"][0]["ImageFile"] == "background.png"
    assert (scene_path.parent / "background.png").exists()


@pytest.mark.parametrize(("theme", "family"), [("꽃밭", "object"), ("우주", "paint"), ("우주", "particles"),
                                                ("바다", "deformation"), ("바다", "fluid"), ("바다", "flock"),
                                                ("숲", "flock")])
def test_every_ready_family_passes_ue_checks(ue_client, theme, family):
    artwork = create(ue_client, theme=theme, families=[family], reveal="none", chips={"intensity": "strong"})
    assert artwork["status"] == "prepared", step(artwork, "검사")


def test_ue_only_holds_invisible_subject(ue_client):
    artwork = create(ue_client, theme="바다", subject="고래")
    assert artwork["status"] == "failed" and "현재 UE 조립 어댑터" in step(artwork, "상영")
    assert ue_client.get("/api/pool").json() == []


def test_both_rejects_ue_unsupported_subject(tmp_path):
    with make_client(tmp_path, "both") as test_client:
        artwork = create(test_client, theme="바다", subject="고래")
        assert artwork["status"] == "failed" and "UE 조립 어댑터" in step(artwork, "상영")
        both = create(test_client, theme="바다", reveal="none", families=["fluid"])
        assert "TD · UE 후보 파일 준비 완료" in step(both, "상영")
        assert "Ready·Applied 미확인" in step(both, "상영")
    assert not (tmp_path / "delivery" / "release.json").exists() and not (tmp_path / "delivery_td" / "release.json").exists()


# --- 입력 · 풀 · 실패 처리 -----------------------------------------------------------------

def test_story_is_optional(client):
    intent = step(create(client, theme="숲", direction="story"), "의도")
    assert intent["concept"]
    assert "concept" not in step(create(client, theme="숲"), "의도")


def test_particles_need_no_background(client):
    assert step(create(client, theme="우주", families=["particles"]), "필요한 소재")["required"] == []


def test_exhibition_always_generates_background(client):
    first = create(client, mode="exhibition", theme="바다", reveal="none", families=["fluid"])
    second = create(client, mode="exhibition", theme="바다", reveal="none", families=["fluid"], assets="pool")
    assert "새로 생성" in step(first, "소재")[0]
    assert "새로 생성" in step(second, "소재")[0]
    assert len(client.get("/api/pool").json()) == 2   # 새 소재도 풀에는 쌓인다


def test_pool_choice_reuses_background(client):
    create(client, theme="바다", reveal="none", families=["fluid"])
    second = create(client, theme="바다", reveal="none", families=["fluid"], assets="pool")
    assert "풀에서 사용" in step(second, "소재")[0]


def test_new_always_generates(client):
    create(client, theme="바다", families=["fluid"], assets="new")
    assert "새로 생성" in step(create(client, theme="바다", families=["fluid"], assets="new"), "소재")[0]


def test_unconnected_family_holds_before_assets(client):
    artwork = create(client, theme="바다", families=["media"], reveal="none")
    assert artwork["status"] == "failed"
    assert client.get("/api/pool").json() == []


def test_stages_timeline_is_held_before_assets(client):
    artwork = create(client, theme="바다", timeline="stages", stages=4)
    assert artwork["status"] == "failed" and "stages" in step(artwork, "상영")
    assert client.get("/api/pool").json() == []


def test_exhibition_with_family_still_varies_theme(client):
    first = create(client, mode="exhibition", families=["particles"])
    second = create(client, mode="exhibition", families=["particles"])
    assert step(first, "자동 요청 생성")["preset"] != step(second, "자동 요청 생성")["preset"]


def test_theme_presets_are_not_selectable(client):
    from contracts.schemas import CreateRequest

    assert "preset" not in CreateRequest.model_fields   # 내부 기본 테마는 테마 문장이 빌 때 규칙만 고른다
    artwork = create(client, mode="exhibition", preset="ocean")   # 예전 요청의 preset은 무시하고 자동 기획
    assert step(artwork, "자동 요청 생성")["preset"]


def test_free_text_theme_shares_pool(client):
    create(client, theme="Calm ocean at night", reveal="none", families=["fluid"])
    second = create(client, theme="Stormy sea", reveal="none", families=["fluid"], assets="pool")
    assert "풀에서 사용" in step(second, "소재")[0] and second["title"] == "Stormy sea"


def test_planner_error_is_recorded_as_failed(tmp_path):
    class Broken(RulePlanner):
        def intent(self, request, theme, character=None):
            raise ValueError("LLM 응답 형식 오류")

    with make_client(tmp_path, planner=Broken()) as test_client:
        assert create(test_client, theme="바다")["status"] == "failed"
        assert test_client.get("/api/artworks").json()[0]["status"] == "failed"


def test_failure_after_generation_removes_new_files(tmp_path, monkeypatch):
    import central.pipeline

    def broken_publish(*args):
        raise OSError("디스크 오류")

    monkeypatch.setattr(central.pipeline, "prepare_version", broken_publish)
    with make_client(tmp_path) as test_client:
        assert create(test_client, theme="바다")["status"] == "failed"
        assert test_client.get("/api/pool").json() == []
    assert not list((tmp_path / "generated").glob("*.png")) and not list((tmp_path / "subjects").glob("*.json"))


def test_unconnected_sound_input_is_an_error(client):
    result = create(client, theme="우주", families=["particles"], inputs=["hand", "sound"])
    assert result["status"] == "failed" and "sound" in step(result, "상영")


def test_invalid_input_is_422(client):
    assert client.post("/api/artworks", json={"families": ["paint", "fluid"]}).status_code == 422
    assert client.post("/api/artworks", json={"chips": {"mood": "angry"}}).status_code == 422


def test_theme_accepts_up_to_2000_chars(client):
    from contracts.schemas import CreateRequest

    assert 'name="theme" maxlength="2000"' in client.get("/").text
    assert len(CreateRequest(theme="가" * 2000).theme) == 2000
    with pytest.raises(ValueError):
        CreateRequest(theme="가" * 2001)


def test_operator_token(client):
    assert client.post("/api/artworks", json={}, headers={"X-Operator-Token": "wrong"}).status_code == 401
    assert client.post("/api/artworks", json={}, headers={"X-Operator-Token": "토큰".encode()}).status_code == 401


def test_web_form_and_families_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "주인공 · 드러내기 요구" in page.text
    assert {f["key"] for f in client.get("/api/families").json()} >= {"fluid", "flock", "media"}


# --- 계획기 · 소재 검사 단위 ---------------------------------------------------------------

class FakeLLM:
    name = "fake"

    def __init__(self, *answers):
        self.answers = list(answers)

    def complete_json(self, system, user, schema):
        answer = self.answers.pop(0) if self.answers else {}
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_llm_planner_uses_llm_then_repairs_then_falls_back():
    from contracts.schemas import CreateRequest
    from studio.head.llm import LLMError
    from studio.head.planning import LLMPlanner

    request = CreateRequest(theme="바다", subject="고래")
    good = {"title": "푸른 숨", "mood": ["dreamy"], "palette": ["teal"]}
    planner = LLMPlanner(FakeLLM(good))
    assert planner.intent(request, "ocean").title == "푸른 숨" and planner.sources["의도"] == "llm"
    planner = LLMPlanner(FakeLLM({"title": "x", "mood": ["angry"], "palette": ["teal"]}, good))
    assert planner.intent(request, "ocean").mood == ["dreamy"] and planner.sources["의도"] == "llm-repaired"
    planner = LLMPlanner(FakeLLM(LLMError("연결 실패"), LLMError("연결 실패")))
    assert planner.intent(request, "ocean").title == "바다" and planner.sources["의도"] == "rule"


def test_llm_picks_invisible_subject_when_empty():
    from contracts.schemas import CreateRequest
    from studio.head.planning import LLMPlanner

    answer = {"title": "흩어지는 노래", "mood": ["dreamy"], "palette": ["teal"], "subject": "해파리",
              "reveal": "formation"}
    intent = LLMPlanner(FakeLLM(answer)).intent(CreateRequest(mode="exhibition", theme="바다"), "ocean")
    assert intent.subject == "해파리" and intent.reveal.value == "formation"


def test_malformed_llm_answer_falls_back_to_rules():
    from contracts.schemas import CreateRequest, Element, Family
    from studio.head.planning import LLMPlanner

    planner = LLMPlanner(FakeLLM({"reactions": ["x"]}, {"reactions": "x"}))
    mappings = planner.mappings([Element(family=Family.fluid, module="Fluid")], CreateRequest(theme="바다"))
    assert mappings[0].reaction == "stir" and planner.sources["반응"] == "rule"


def test_rollback_removes_every_new_file_for_two_kinds(tmp_path, client):
    from sqlmodel import Session

    from central.pipeline import _rollback
    from contracts.schemas import AssetChoice, Mode
    from studio.generation.assets import gather
    from studio.generation.images import LocalImage

    with Session(client.app.state.engine) as session:
        assets, _ = gather(session, ["background", "panel"], AssetChoice.new, Mode.manual, "ocean", ["teal"],
                           LocalImage(), tmp_path, set_id="t", seed=7)
        assert len({p.read_bytes() for p in assets.values()}) == 2
        _rollback(session)
    assert not any(p.exists() for p in assets.values())


def test_inspect_rejects_blank_and_bad_subject_data(tmp_path, client):
    import random

    from interactive_controller.providers.images import ProceduralImage
    from sqlmodel import Session

    from studio.generation import subjects
    from studio.verify.checks import inspect_image as inspect
    from studio.verify.checks import inspect_subject

    blank = tmp_path / "blank.png"
    blank.write_bytes(ProceduralImage().generate("x", size=(1280, 720)).data)
    with Session(client.app.state.engine) as session:
        assert any("빈 화면" in p for p in inspect(session, blank)[0])
    good = subjects.silhouette("고래", subjects.LocalShapes(), random.Random(1))
    assert inspect_subject(good) == []
    tiny = {**good, "area": 0.001}
    assert inspect_subject(tiny) and inspect_subject({**good, "points": good["points"][:10]})


# --- 평가 지적 회귀 테스트 -----------------------------------------------------------------

def test_trace_path_follows_tempo(client):
    spec, folder = spec_of(create(client, mode="exhibition", theme="숲", chips={"intensity": "strong"}))
    passing = next(p["seconds"] for p in spec["phases"] if p["name"] == "지나감")
    path = json.loads((folder / "path.json").read_text("utf-8"))
    assert path["seconds"] == passing and path["steps"][-1]["t"] <= passing and path["gait"] == "steps"


def test_comet_leaves_trail_not_footsteps(client):
    _, folder = spec_of(create(client, mode="exhibition", theme="우주"))
    path = json.loads((folder / "path.json").read_text("utf-8"))
    assert path["gait"] == "trail" and {s["side"] for s in path["steps"]} == {"C"}


def test_placeholder_outline_is_not_pooled(client):
    create(client, theme="우주", subject="용", families=["particles"])
    assert [a for a in client.get("/api/pool").json() if a["kind"] == "silhouette"] == []


def test_dropped_subject_is_announced(client):
    artwork = create(client, theme="바다", subject="사슴", families=["object"])
    assert artwork["status"] == "failed" and "드러낼 수 없음" in step(artwork, "상영")


def test_flat_outline_fails_fast():
    import random

    from studio.generation import subjects

    class Flat:
        name = "flat"

        def outline(self, subject):
            return [(0, 0.5), (0.5, 0.5), (1, 0.5)], "flat"

    with pytest.raises(RuntimeError):
        subjects.silhouette("선", Flat(), random.Random(1))


def test_llm_reveal_must_fit_user_families():
    from contracts.schemas import CreateRequest
    from studio.head.planning import LLMPlanner

    bad = {"title": "t", "mood": ["calm"], "palette": ["teal"], "subject": "고래", "reveal": "formation"}
    good = {**bad, "reveal": "trace"}
    planner = LLMPlanner(FakeLLM(bad, good))
    intent = planner.intent(CreateRequest(mode="exhibition", theme="숲", families=["paint"]), "forest")
    assert intent.reveal.value == "trace" and planner.sources["의도"] == "llm-repaired"


def test_llm_subject_needs_reveal():
    from contracts.schemas import CreateRequest
    from studio.head.planning import LLMPlanner

    request = CreateRequest(theme="수달이 떠다니는 밤")   # 수달은 규칙 단어 목록에 없어 LLM이 뽑는다
    bad = {"title": "t", "mood": ["calm"], "palette": ["teal"], "subject": "수달", "reveal": "none"}
    planner = LLMPlanner(FakeLLM(bad, {**bad, "reveal": "formation"}))
    intent = planner.intent(request, "custom")
    assert (intent.subject, intent.reveal.value) == ("수달", "formation")
    assert planner.sources["의도"] == "llm-repaired"
    planner = LLMPlanner(FakeLLM(bad, bad))   # 두 번 다 틀리면 규칙: 주인공 없이 장
    intent = planner.intent(request, "custom")
    assert (intent.subject, intent.reveal.value) == (None, "none") and planner.sources["의도"] == "rule"


def test_llm_composition_fits_reveal():
    from contracts.schemas import Intent, Reveal
    from studio.head.planning import LLMPlanner

    intent = Intent(title="고래", theme="ocean", mood=["calm"], subject="고래", reveal=Reveal.formation)
    llm = FakeLLM({"families": ["fluid"]}, {"families": ["flock"]})
    schemas = []
    complete = llm.complete_json
    llm.complete_json = lambda system, user, schema: schemas.append(schema) or complete(system, user, schema)
    planner = LLMPlanner(llm)
    assert [e.family.value for e in planner.composition(intent, [])] == ["flock"]
    assert planner.sources["구성"] == "llm-repaired"
    assert set(schemas[0]["properties"]["families"]["items"]["enum"]) == {"flock", "particles"}
    planner = LLMPlanner(FakeLLM({"families": ["fluid"]}, {"families": ["paint", "flock"]}))
    assert [e.family.value for e in planner.composition(intent, [])] == ["flock"]   # 규칙: 바다 형상은 군집
    assert planner.sources["구성"] == "rule"


def test_both_fails_when_ue_preparation_fails(tmp_path, monkeypatch):
    import central.pipeline

    real = central.pipeline.prepare_version

    def flaky(delivery, *args):
        if delivery.name == "delivery":
            raise OSError("UE 폴더 잠김")
        return real(delivery, *args)

    monkeypatch.setattr(central.pipeline, "prepare_version", flaky)
    with make_client(tmp_path, "both") as test_client:
        artwork = create(test_client, theme="바다", reveal="none", families=["fluid"])
        assert artwork["status"] == "failed" and "UE 준비 실패" in step(artwork, "상영")
