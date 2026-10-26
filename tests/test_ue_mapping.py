"""기획 선택이 UE 파일 계약까지 유지되는지 검사한다. 실제 화면·성능 검증은 포함하지 않는다."""
import json
import re

import pytest
from tests.test_flow import FakeLLM, create, make_client, step

from contracts.schemas import CreateRequest, Element, Family, InputSource, Intent, Mapping, Reveal
from studio.assembly import ue_scene
from studio.head.planning import LLMPlanner, RulePlanner


def channels(text):
    return {key: float(value) for key, value in re.findall(r"([RGBA])=([-\d.]+)", text)}


def test_explicit_warm_palette_reaches_ue_fluid_colors_and_preserves_mood():
    request = CreateRequest(theme="바다", families=[Family.fluid],
                            chips={"palette": "warm", "mood": "joyful"})
    planner = LLMPlanner(FakeLLM({"title": "따뜻한 바다", "mood": ["mysterious"], "palette": ["navy"]}))
    intent = planner.intent(request, "ocean")
    elements = planner.composition(intent, request.families)
    mappings = RulePlanner().mappings(elements, request)
    brief = ue_scene.to_brief(intent, elements, mappings, "ocean", "")
    scene, _ = ue_scene.compile_scene(brief, {})

    assert intent.palette == ["amber", "coral"] and intent.mood == ["joyful"]
    assert brief["palette"] == ["#c98a2b", "#d9705a"]
    assert brief["palette_names_en"] == ["amber", "coral"] and brief["mood"] == "joyful"
    assert ue_scene.check(brief, scene) == []
    colors = scene["Profiles"]["FluidField"][0]["ContactColors"]
    assert len(colors) == 2
    for color in colors:
        rgb = channels(color)
        assert rgb["R"] > rgb["G"] > rgb["B"]


def test_single_llm_color_does_not_add_an_unrequested_theme_color():
    intent = Intent(title="단색 입자", theme="ocean", mood=["calm"], palette=["coral"])
    element = Element(family=Family.particles, module="Particles")
    mapping = Mapping(source=InputSource.hand, family=Family.particles, reaction="swirl")
    brief = ue_scene.to_brief(intent, [element], [mapping], "ocean", "")
    scene, _ = ue_scene.compile_scene(brief, {})

    assert brief["palette"] == ["#d9705a", "#d9705a"]
    assert brief["palette_names_en"] == ["coral", "coral"]
    rgb = channels(scene["Profiles"]["ParticleField"][0]["Tint"])
    assert rgb["R"] > rgb["G"] > rgb["B"]
    assert ue_scene.check(brief, scene) == []


def test_element_positions_survive_brief_and_compiled_placements():
    intent = Intent(title="좌우 배치", theme="ocean")
    elements = [Element(family=Family.object, module="Object", position="left"),
                Element(family=Family.flock, module="Flock", position="right")]
    mappings = [Mapping(source=InputSource.hand, family=Family.object, reaction="bloom"),
                Mapping(source=InputSource.hand, family=Family.flock, reaction="follow")]
    brief = ue_scene.to_brief(intent, elements, mappings, "ocean", "")
    scene, _ = ue_scene.compile_scene(brief, {})

    assert [element["position"] for element in brief["elements"]] == ["left", "right"]
    locations = {row["Name"]: dict(re.findall(r"([XYZ])=([-\d.]+)", row["Location"]))
                 for row in scene["Placements"]}
    assert float(locations["Flower"]["Y"]) > 0  # 기준 카메라에서 +Y는 화면 왼쪽
    fish_y = [float(location["Y"]) for name, location in locations.items() if name.startswith("Fish")]
    assert fish_y and sum(fish_y) / len(fish_y) < 0
    assert ue_scene.check(brief, scene) == []


@pytest.mark.parametrize(("family", "module", "position"),
                         [(Family.fluid, "Fluid", "left"), (Family.object, "Object", "diagonal")])
def test_adapter_rejects_positions_it_cannot_represent(family, module, position):
    intent = Intent(title="미지원 배치", theme="ocean")
    with pytest.raises(ValueError, match="위치"):
        ue_scene.to_brief(intent, [Element(family=family, module=module, position=position)], [], "ocean", "")


def test_adapter_rejects_unknown_palette_instead_of_restoring_theme_default():
    intent = Intent(title="미지원 색", theme="ocean", palette=["unknown-red"])
    with pytest.raises(ValueError, match="팔레트"):
        ue_scene.to_brief(intent, [Element(family=Family.fluid, module="Fluid")], [], "ocean", "")


def test_llm_keeps_explicit_palette_and_mood_with_existing_subject_selection():
    answer = {"title": "숲", "mood": ["calm"], "palette": ["teal"],
              "subject": "고래", "reveal": "formation"}
    intent = LLMPlanner(FakeLLM(answer)).intent(
        CreateRequest(mode="exhibition", theme="숲", chips={"palette": "warm", "mood": "joyful"}), "forest")
    assert intent.subject == "고래" and intent.reveal is Reveal.formation
    assert intent.palette == ["amber", "coral"] and intent.mood == ["joyful"]


def test_bad_planner_position_fails_without_replacing_the_previous_candidate(tmp_path):
    class UnrepresentablePosition(RulePlanner):
        def composition(self, intent, families):
            result = super().composition(intent, families)
            return [element.model_copy(update={"position": "left"}) for element in result]

    with make_client(tmp_path, "ue") as client:
        first = create(client, theme="바다", reveal="none", families=["fluid"])
        assert first["status"] == "prepared", first["steps"]
    pointer = tmp_path / "delivery" / "release.json"
    pointer.write_text('{"SchemaVersion":1,"Version":"old-active"}')
    previous = pointer.read_bytes()
    with make_client(tmp_path, "ue", planner=UnrepresentablePosition()) as client:
        failed = create(client, theme="바다", reveal="none", families=["fluid"], assets="new")
        assert failed["status"] == "failed" and "위치" in step(failed, "상영")
        assert len(client.get("/api/pool").json()) == 1
    assert pointer.read_bytes() == previous
    assert len(list((tmp_path / "generated").glob("*.png"))) == 1


@pytest.mark.parametrize("gate", ["assets", "final"])
def test_ue_only_review_context_saved_by_mr23_can_resume(tmp_path, gate):
    from sqlmodel import Session

    from db.models import Artwork

    with make_client(tmp_path, "ue") as client:
        pending = create(client, theme="바다", reveal="none", families=["fluid"], **{f"review_{gate}": True})
        assert pending["status"] == {"assets": "asset_review", "final": "final_review"}[gate]
        with Session(client.app.state.engine) as session:
            stored = session.get(Artwork, pending["id"])
            steps = json.loads(json.dumps(stored.steps))
            context = next(s["detail"]["context"] for s in reversed(steps)
                           if s["name"] == "검수" and isinstance(s["detail"], dict) and "context" in s["detail"])
            context.pop("phases", None)
            context.pop("subject_files", None)
            stored.steps = steps
            session.add(stored)
            session.commit()
        response = client.post(f"/api/artworks/{pending['id']}/review", json={"approve": True})
        assert response.status_code == 200 and response.json()["status"] == "prepared", response.text
        assert not (tmp_path / "delivery" / "release.json").exists()
        assert not (tmp_path / "delivery_td").exists()
