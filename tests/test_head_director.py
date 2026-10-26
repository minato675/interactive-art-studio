"""연출 계획을 현재 조립 계약으로 제한하고 원본 파일을 재사용하는 검사."""
import hashlib

import pytest
from pydantic import ValidationError

from contracts.head import AssetsPlan, HeadRequest
from studio.head import director


def direction_plan():
    return {
        "reason": "배경의 열린 공간을 따라 동일한 잎 두 장을 배치한다.",
        "assets": [{"name": "잎", "render_mode": "2d", "prompt": "투명 배경의 청록 잎"}],
        "director": {
            "title": "빛의 숲", "intent": "관객의 움직임에 잎이 다가온다.",
            "chosen_template": "image_stir", "template_rationale": "입력 좌표를 따르는 스프라이트를 지원한다.",
            "background_observation": {
                "palette": ["어두운 청록", "따뜻한 금색"], "lighting": "왼쪽 위에서 들어오는 빛",
                "perspective": "중앙 소실점과 전경의 큰 줄기",
                "occupied_regions": ["좌측 (0,0)~(0.2,1)은 줄기가 차지한다."],
                "usable_regions": ["우측 (0.3,0.2)~(0.9,0.8)은 어두운 빈 공간이다."],
            },
            "instances": [
                {"id": "leaf_near", "source_asset_index": 1, "placement": {"u": 0.4, "v": 0.5, "size": 0.2},
                 "behaviors": ["follow"], "role": "interactor", "purpose": "가장 먼저 관객에게 반응한다."},
                {"id": "leaf_still", "source_asset_index": 1, "placement": {"u": 0.8, "v": 0.3, "size": 0.1},
                 "behaviors": [], "role": "noninteractive", "purpose": "정적인 기준점을 만든다."},
            ],
            "effects": [], "interactions": [{
                "id": "follow_input", "target_ids": ["leaf_near"], "description": "잎이 관객 좌표를 따른다.",
                "execution_status": "template_driven", "notes": "현재 행동 매핑이며 현장 센서는 별도 검증한다.",
                "input": "position", "reaction": "follow",
            }],
            "particles": [], "cues": [],
            "limitations": ["회전·색조 변형·정해진 시간의 큐는 현재 조립되지 않는다."],
            "acceptance_criteria": ["복제 두 장이 원본 한 장을 공유하고 빈 공간에 배치된다.",
                                    "실제 센서 입력에 잎이 반응하는지는 현장에서 검증한다."],
        },
    }


def selected(behaviors=None, template="image_stir"):
    return {"template": template, "behaviors": ["follow"] if behaviors is None else behaviors,
            "allowed_render_modes": ["2d"], "background_usage": "display"}


def record(**changes):
    return {"id": "extra", "target_ids": ["leaf_near"], "description": "추가 연출의 의도",
            "execution_status": "design_only", "notes": "현재 조립기에 적용하지 않는 설계 의도이다.", **changes}


def test_director_optional_for_legacy_but_required_for_new_flow():
    data = direction_plan()
    data.pop("director")
    plan = AssetsPlan.model_validate(data)
    director.validate_plan(plan, selected())
    with pytest.raises(ValueError, match="director"):
        director.validate_plan(plan, selected(), required=True)


def test_valid_director_allows_static_clone_and_required_interactor():
    director.validate_plan(AssetsPlan.model_validate(direction_plan()), selected(), required=True)


@pytest.mark.parametrize("field,value", [("u", -0.1), ("v", 1.1), ("size", 0), ("size", 1.1),
                                         ("u", float("nan")), ("size", float("inf"))])
def test_invalid_normalized_placements_rejected(field, value):
    data = direction_plan()
    data["director"]["instances"][0]["placement"][field] = value
    with pytest.raises(ValidationError):
        AssetsPlan.model_validate(data)


@pytest.mark.parametrize("field", ["instances", "assets"])
def test_source_and_instance_count_bounds(field):
    data = direction_plan()
    owner = data if field == "assets" else data["director"]
    owner[field] = [owner[field][0]] * 13
    with pytest.raises(ValidationError):
        AssetsPlan.model_validate(data)
    owner[field] = []
    with pytest.raises(ValueError, match="원본"):
        director.validate_plan(AssetsPlan.model_validate(data), selected())


@pytest.mark.parametrize("value", ["leaf-near", "LeafNear", "1leaf", "a" * 49])
def test_instance_id_matches_ue_sprite_contract(value):
    data = direction_plan()
    data["director"]["instances"][0]["id"] = value
    with pytest.raises(ValidationError):
        AssetsPlan.model_validate(data)


@pytest.mark.parametrize("mutation,match", [
    (lambda d: d["director"].update(chosen_template="gpu_particles"), "템플릿"),
    (lambda d: d["director"]["instances"][1].update(id="leaf_near"), "ID"),
    (lambda d: d["director"]["instances"][1].update(id="background"), "ID"),
    (lambda d: d["director"]["instances"][1].update(source_asset_index=2), "원본"),
    (lambda d: d["assets"].append(dict(d["assets"][0])), "원본"),
    (lambda d: d["director"]["instances"][1].update(behaviors=["float"]), "behaviors"),
    (lambda d: d["director"]["instances"][0].update(behaviors=["follow", "follow"]), "behaviors"),
    (lambda d: d["director"]["instances"][0].update(role="noninteractive"), "역할"),
    (lambda d: d["director"]["interactions"][0].update(target_ids=["missing"]), "target_ids"),
    (lambda d: d["director"]["interactions"][0].update(target_ids=["leaf_near", "leaf_near"]), "target_ids"),
    (lambda d: d["director"]["interactions"][0].update(target_ids=["leaf_still"]), "design_only"),
    (lambda d: d["director"]["interactions"][0].update(input="gesture"), "design_only"),
])
def test_invalid_director_links_and_support_rejected(mutation, match):
    data = direction_plan()
    mutation(data)
    with pytest.raises(ValueError, match=match):
        director.validate_plan(AssetsPlan.model_validate(data), selected())


@pytest.mark.parametrize("behavior", ["follow", "glow"])
def test_required_selected_behavior_cannot_disappear(behavior):
    data = direction_plan()
    data["director"]["instances"][0].update(behaviors=[], role="noninteractive")
    data["director"]["interactions"] = []
    with pytest.raises(ValueError, match="최소 한 인스턴스"):
        director.validate_plan(AssetsPlan.model_validate(data), selected([behavior]))


def test_every_selected_interactive_behavior_must_be_retained():
    with pytest.raises(ValueError, match="최소 한 인스턴스"):
        director.validate_plan(AssetsPlan.model_validate(direction_plan()), selected(["follow", "glow"]))


def test_float_is_noninteractive_and_can_have_static_instances():
    data = direction_plan()
    data["director"]["instances"][0].update(behaviors=["float"], role="noninteractive")
    data["director"]["interactions"] = []
    director.validate_plan(AssetsPlan.model_validate(data), selected(["float"]))


@pytest.mark.parametrize("kind", ["rotation_variation", "color_variation", "depth_variation", "trail", "ripple"])
def test_richer_effects_remain_design_only(kind):
    data = direction_plan()
    data["director"]["effects"] = [record(kind=kind)]
    director.validate_plan(AssetsPlan.model_validate(data), selected())
    data["director"]["effects"][0]["execution_status"] = "template_driven"
    with pytest.raises(ValueError, match="design_only"):
        director.validate_plan(AssetsPlan.model_validate(data), selected())


def test_glow_effect_requires_glow_target():
    data = direction_plan()
    data["director"]["instances"][0]["behaviors"] = ["glow"]
    data["director"]["interactions"][0]["reaction"] = "glow"
    data["director"]["effects"] = [record(kind="glow", execution_status="template_driven")]
    director.validate_plan(AssetsPlan.model_validate(data), selected(["glow"]))
    data["director"]["effects"][0]["target_ids"] = ["leaf_still"]
    with pytest.raises(ValueError, match="design_only"):
        director.validate_plan(AssetsPlan.model_validate(data), selected(["glow"]))


@pytest.mark.parametrize("numbers", [{"count": 300}, {"lifetime_seconds": 6}, {"speed": 10}])
def test_exact_particle_values_are_design_intent(numbers):
    data = direction_plan()
    data["director"]["chosen_template"] = "gpu_particles"
    data["director"]["particles"] = [record(target_ids=[], **numbers)]
    director.validate_plan(AssetsPlan.model_validate(data), selected(template="gpu_particles"))
    data["director"]["particles"][0]["execution_status"] = "template_driven"
    with pytest.raises(ValueError, match="design_only"):
        director.validate_plan(AssetsPlan.model_validate(data), selected(template="gpu_particles"))


def test_only_particle_template_defaults_are_template_driven():
    data = direction_plan()
    data["director"]["particles"] = [record(target_ids=[], execution_status="template_driven")]
    with pytest.raises(ValueError, match="design_only"):
        director.validate_plan(AssetsPlan.model_validate(data), selected())
    data["director"]["chosen_template"] = "gpu_particles"
    director.validate_plan(AssetsPlan.model_validate(data), selected(template="gpu_particles"))


def test_template_particle_field_cannot_claim_instance_attachment():
    data = direction_plan()
    data["director"]["chosen_template"] = "gpu_particles"
    data["director"]["particles"] = [record(execution_status="template_driven")]
    with pytest.raises(ValueError, match="대상 연결"):
        director.validate_plan(AssetsPlan.model_validate(data), selected(template="gpu_particles"))
    data["director"]["particles"][0]["execution_status"] = "design_only"
    director.validate_plan(AssetsPlan.model_validate(data), selected(template="gpu_particles"))


def test_timed_cue_is_design_only_and_unknown_instance_settings_are_rejected():
    data = direction_plan()
    data["director"]["cues"] = [record(start_seconds=0, duration_seconds=20)]
    director.validate_plan(AssetsPlan.model_validate(data), selected())
    data["director"]["cues"][0]["execution_status"] = "template_driven"
    with pytest.raises(ValueError, match="시간 큐"):
        director.validate_plan(AssetsPlan.model_validate(data), selected())
    data["director"]["instances"][0]["rotation"] = 45
    with pytest.raises(ValidationError, match="rotation"):
        AssetsPlan.model_validate(data)


def test_observation_notes_and_acceptance_criteria_cannot_be_empty():
    data = direction_plan()
    data["director"]["background_observation"]["lighting"] = "   "
    data["director"]["acceptance_criteria"] = []
    data["director"]["interactions"][0]["notes"] = ""
    with pytest.raises(ValidationError) as error:
        AssetsPlan.model_validate(data)
    assert {"lighting", "acceptance_criteria", "notes"} <= {item["loc"][-1] for item in error.value.errors()}


def test_assembly_clones_reuse_original_file_and_only_compile_supported_fields():
    data = direction_plan()
    data["director"]["effects"] = [record(kind="rotation_variation")]
    plan = AssetsPlan.model_validate(data)
    jobs = [{"id": "source-job"}]
    files = [{"source_job_id": "bg-job", "name": "background.png"},
             {"source_job_id": "source-job", "name": "asset-001.png"}]
    result = director.assembly_objects(plan, jobs, files)
    assert len(result) == 2
    assert {item["image"] for item in result} == {"asset-001.png"}
    assert result[0] == {"id": "leaf_near", "image": "asset-001.png", "position": [0.4, 0.5],
                         "size": 0.2, "behaviors": ["follow"]}
    assert result[1] == {"id": "leaf_still", "image": "asset-001.png", "position": [0.8, 0.3],
                         "size": 0.1, "behaviors": []}
    assert len(files) == 2 and len(jobs) == 1
    with pytest.raises(ValueError, match="파일 수"):
        director.assembly_objects(plan, jobs, files[:1])
    with pytest.raises(ValueError, match="파일 순서"):
        director.assembly_objects(plan, [{"id": "other-job"}], files)


def test_legacy_assembly_preserves_grid_fallback_and_explicit_selected_behaviors():
    data = direction_plan()
    data.pop("director")
    files = [{"source_job_id": "bg", "name": "background.png"},
             {"source_job_id": "source", "name": "asset-001.png"}]
    result = director.assembly_objects(AssetsPlan.model_validate(data), [{"id": "source", "behaviors": ["follow"]}],
                                      files)
    assert result == [{"id": "asset_001", "image": "asset-001.png", "position": [0.5, 0.5],
                       "size": 0.15, "behaviors": ["follow"]}]


def test_capabilities_distinguish_sources_instances_and_execution_evidence():
    request = HeadRequest.model_validate({
        "request_id": "director", "artwork_id": "forest", "version": "v1", "prompt": "잎이 다가오는 숲",
        "asset_count": 2, "max_asset_count": 4,
        "spec": {"template_family": "scene_family", "raw_prompt": "잎이 다가오는 숲",
                 "interactions": [{"input": "move", "reaction": "follow", "reaction_mode": "while", "return": "fade"}]},
    })
    result = director.capabilities(selected(), request)
    assert (result["requested_source_assets"], result["max_source_assets"], result["max_instances"]) == (2, 4, 12)
    assert result["required_behaviors"] == ["follow"]
    assert "rotation" in result["design_only"]
    assert "rotation" not in result["compiled_instance_fields"]
    assert any("sensor" in value for value in result["evidence_limits"])
    assert "global" in result["template_driven"]["particles"]
    assert "target_ids=[]" in result["template_driven"]["particles"]


def test_bundled_skill_metadata_matches_loaded_prompt_and_every_phase():
    raw = director.PROMPT_PATH.read_bytes()
    assert director.skill_metadata() == {"version": "ranel-director-v2", "sha256": hashlib.sha256(raw).hexdigest()}
    for phase in ("background", "assets", "review"):
        assert director.instructions(phase).startswith(raw.decode("utf-8"))
    with pytest.raises(ValueError, match="판단 단계"):
        director.instructions("../../unexpected")


def test_missing_or_empty_bundled_skill_fails_clearly(tmp_path, monkeypatch):
    missing = tmp_path / "missing.md"
    monkeypatch.setattr(director, "PROMPT_PATH", missing)
    with pytest.raises(director.SkillUnavailable, match="서버 패키지") as error:
        director.skill_metadata()
    assert str(missing) not in str(error.value)
    missing.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="서버 패키지"):
        director.instructions("assets")
