"""UE 자동 조립(studio.assembly.ue_assemble): 작품 명세 + assets.json → 공용 전시 맵 scene.json."""
import hashlib
import json
import math
import struct
import zlib

import pytest
from tests.test_assets import FLUID_LAYERS, PAINT, PARTICLES, chunk, png, silhouette

from contracts.assets import ArtworkAssets
from contracts.spec import Reaction, Spec
from studio.assembly import ue_assemble
from studio.assembly.ue_assemble import assemble, choose_template, template_scene, template_slots
from studio.verify.ue_contract import AFTERIMAGE_HALF_LIFE, check_scene, persistence, shared_catalog, shared_stage

MAPPED = ["fluid", "paint", "deformation", "gpu_particles", "image_stir"]
# 라넬 10/21~22 템플릿(공용 맵 회귀 통과). 대응표에는 아직 연결하지 않아 TemplateChoice로 직접 지정해서만 쓴다.
UNMAPPED = ["ripple_lake", "ripple_star", "pigment_flow"]
TEMPLATES = MAPPED + UNMAPPED
THREE = {"format": "artwork-assets", "version": 1, "layers": [
    {"id": "far", "image": "far.png", "depth": "far"},
    {"id": "mid", "image": "mid.png", "depth": "mid"},
    {"id": "near", "image": "near.png", "depth": "near"}]}
ONE = {"format": "artwork-assets", "version": 1, "layers": [{"id": "far", "image": "far.png", "depth": "far"}]}
HD = (64, 36)   # 16:9


def spec(reaction="stir", medium="fluid", **content):
    rule = {"input": content.pop("input", "move"), "reaction": reaction, "reaction_mode": "while",
            "return": content.pop("return_", "fade")}
    return Spec.model_validate({
        "template_family": "flow_family", "raw_prompt": "시험",
        "content": {"theme": "바다", "mood": "고요", "medium": medium, **content},
        "idle": {"motion": "ignore"}, "interactions": [rule]})


def files(folder, data=THREE, opaque=("far.png", "paper.png", "sky.png"), size=HD):
    assets = ArtworkAssets.model_validate(data)
    for name in assets.files():
        if name.endswith(".json"):
            silhouette(folder / name)
        else:
            png(folder / name, alpha=name not in opaque, size=size)
    return assets


@pytest.mark.parametrize("reaction, medium, template", [
    ("stir", "fluid", "fluid"), ("stir", "particle", "image_stir"), ("draw", "surface", "paint"),
    ("ripple", "fluid", "deformation"), ("press", "particle", "deformation"), ("push", "fluid", "gpu_particles"),
    ("swirl", "light", "gpu_particles"), ("scatter", "surface", "gpu_particles"),
    ("glow", "stroke", "paint"), ("glow", "particle", "gpu_particles"), ("glow", "fluid", "fluid"),
    ("follow", "particle", "gpu_particles"), ("follow", "swarm", None),
    ("bloom", "surface", None), ("reveal", "stroke", None), ("ignore", "fluid", None), ("emerge", "particle", None),
    ("gather", "particle", None), ("pulse", "light", None),
])
def test_mapping_chooses_or_holds(reaction, medium, template):
    assert choose_template(spec(reaction, medium)) == template


@pytest.mark.parametrize("template", TEMPLATES)
def test_templates_are_valid_on_shared_map(template):
    """템플릿 자체가 공용 카탈로그 · 고정 무대 정적 검사를 통과하고, 필요한 패턴 · 객체가 카탈로그에 있다."""
    assert check_scene(template_scene(template), catalog=shared_catalog(), stage=shared_stage()).errors == []
    assert ue_assemble.compatibility(template) == []
    assert template_slots(template)["template_version"] == 1


def test_image_stir_reads_still_image():
    """ExternalAssets.ImageFile은 정지 이미지 경로로만 들어간다(Video 모드면 runtime이 MediaSource를 요구해 실패)."""
    profile = template_scene("image_stir")["Profiles"]["ImageFlow"][0]
    assert profile["SourceType"] == "StillImage"


@pytest.mark.parametrize("data, reaction, medium, template", [
    (FLUID_LAYERS, "stir", "fluid", "fluid"), (PAINT, "draw", "stroke", "paint"),
    (PARTICLES, "push", "particle", "gpu_particles")])
def test_design_samples_assemble_and_pass(tmp_path, data, reaction, medium, template):
    """설계 샘플 3개(유체 + 층 · 그리기 · 입자)가 조립되고 정적 검사를 통과한다."""
    result = assemble(spec(reaction, medium), files(tmp_path, data), tmp_path)
    assert result.errors == [] and result.template == template
    assert check_scene(result.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
    layers = [layer.image for layer in ArtworkAssets.model_validate(data).layers]
    assert result.files == {name: tmp_path / name for name in layers}   # 깊이 지도 · 실루엣은 보내지 않는다


def test_sample_material_object_is_reported(tmp_path):
    result = assemble(spec(), files(tmp_path, FLUID_LAYERS), tmp_path)
    assert any("재질 프리셋 객체 layer_glow" in warning for warning in result.warnings)
    assert result.scene["Look"]["Materials"] == [
        {"Target": "LayerMid", "Visible": True, "Scalars": {"EmissiveStrength": 1.2}, "Colors": {"Tint": [0.6, 0.8, 1.0]}}]


@pytest.mark.parametrize("reaction, medium, data", [
    ("ripple", "surface", THREE), ("stir", "particle", ONE), ("swirl", "light", THREE)])
def test_other_templates_pass_static_check(tmp_path, reaction, medium, data):
    result = assemble(spec(reaction, medium), files(tmp_path, data), tmp_path)
    assert result.errors == [] and result.scene is not None
    assert all(row["TextureParameter"] == "SourceImage" for row in result.scene["ExternalAssets"])
    assert {row["ImageFile"] for row in result.scene["ExternalAssets"]} == set(result.files)


def test_same_input_same_output(tmp_path):
    assets = files(tmp_path)
    first, second = assemble(spec(), assets, tmp_path), assemble(spec(), assets, tmp_path)
    assert json.dumps(first.scene, sort_keys=True) == json.dumps(second.scene, sort_keys=True)
    name = first.scene["Scene"][0]["Name"]
    assert name.startswith("Auto-fluid-")
    assert assemble(spec(mood="불안"), assets, tmp_path).scene["Scene"][0]["Name"] != name
    renamed = ArtworkAssets.model_validate({**THREE, "spec_id": "다른 명세"})
    assert assemble(spec(), renamed, tmp_path).scene["Scene"][0]["Name"] != name   # 소재 목록도 이름에 들어감
    png(tmp_path / "mid.png", size=HD, color=4)   # 같은 이름, 다른 픽셀
    assert assemble(spec(), assets, tmp_path).scene["Scene"][0]["Name"] != name


def placements(result):
    return {row["Name"]: row for row in result.scene["Placements"]}


def vector(text, key):
    return float(text.split(f"{key}=")[1].split(",")[0].rstrip(")"))


@pytest.mark.parametrize("template, reaction, medium, expected", [
    ("gpu_particles", "push", "particle", {"LayerFar": 2, "LayerMid": 101.3, "LayerNear": 200.7}),
    ("fluid", "stir", "fluid", {"LayerFar": 2, "LayerMid": 712.5, "LayerNear": 975}),
    ("paint", "draw", "stroke", {"LayerFar": 2, "LayerMid": 712.5, "LayerNear": 975}),
    ("deformation", "ripple", "surface", {"LayerFar": 2, "LayerMid": 712.5, "LayerNear": 975}),
])
def test_layer_depths(tmp_path, template, reaction, medium, expected):
    """투명 효과(GPU 입자)는 뒤 층 → 효과 사이, 불투명 캔버스 템플릿은 중간 · 앞 층을 캔버스 앞에."""
    result = assemble(spec(reaction, medium), files(tmp_path), tmp_path)
    rows = placements(result)
    assert {name: vector(rows[name]["Location"], "X") for name in expected} == expected
    assert rows["Background"] == next(r for r in template_scene(template)["Placements"] if r["Name"] == "Background")
    # 불투명 캔버스도 이제 재질 그림 입력(SourceImage)으로 뒤 층 그림을 바탕에 보여 가려진다는 경고가 없다
    assert not any("뒤 층이 거의 보이지 않음" in warning for warning in result.warnings)


def test_layer_planes_fill_screen_and_face_camera(tmp_path):
    result = assemble(spec("push", "particle"), files(tmp_path), tmp_path)
    stage = shared_stage()
    for name in ("LayerFar", "LayerMid", "LayerNear"):
        row = placements(result)[name]
        distance = stage["CameraLocation"][0] - vector(row["Location"], "X")
        width, height = vector(row["Scale"], "X") * 100, vector(row["Scale"], "Y") * 100
        assert width == pytest.approx(2 * distance * 0.70021, rel=1e-3)   # tan(35°), 수평 FOV 70도
        assert width / height == pytest.approx(stage["AspectRatio"], rel=1e-3)
        assert vector(row["Location"], "Z") == stage["SurfaceOrigin"][2]
        assert row["Rotation"] == "(Pitch=0,Yaw=-90,Roll=90)"   # PC1 실측: 이미지 좌우가 그대로


def test_image_stir_uses_far_image_on_canvas_and_rejects_more_layers(tmp_path):
    result = assemble(spec("stir", "particle"), files(tmp_path, ONE), tmp_path)
    assert result.scene["ExternalAssets"] == [{"Name": "Canvas", "ImageFile": "far.png", "TextureParameter": "SourceImage"}]
    assert "LayerFar" not in placements(result)
    result = assemble(spec("stir", "particle"), files(tmp_path), tmp_path)
    assert result.scene is None and any("mid 층을 받지 않음" in error for error in result.errors)


def test_spec_values_fill_slots(tmp_path):
    assets = files(tmp_path)
    wide = assemble(spec("push", "particle", influence_range="wide", density=1.0, return_="stay"), assets, tmp_path)
    local = assemble(spec("push", "particle", influence_range="local", density=0.0, return_="instant"), assets, tmp_path)
    for result, radius, count, release in ((wide, 900, 65536, 4.0), (local, 300, 4096, 0.3)):
        profile = result.scene["Profiles"]["GpuFlow"][0]
        assert (profile["ReactionRadiusCm"], profile["Count"]) == (radius, count)
        assert result.scene["Bindings"][0]["ReleaseSeconds"] == release
    fade = assemble(spec("stir", "fluid", influence_range="local"), assets, tmp_path).scene
    assert fade["Profiles"]["FluidField"][0]["Field"]["InfluenceRadiusCm"] == 120
    assert fade["Bindings"][0]["ReleaseSeconds"] == 1.5
    surface = assemble(spec("ripple", "surface", influence_range="wide"), assets, tmp_path).scene
    assert surface["Profiles"]["SurfaceDeformation"][0]["Surface"]["InfluenceRadiusCm"] == 260


def test_image_stir_and_fluid_slot_values(tmp_path):
    stir = assemble(spec("stir", "particle", influence_range="local", density=1.0), files(tmp_path, ONE), tmp_path).scene
    assert stir["Profiles"]["ImageFlow"][0]["Field"]["InfluenceRadiusCm"] == 200
    assert "GpuFlow" not in stir["Profiles"]
    wide = assemble(spec("stir", "fluid", influence_range="wide"), files(tmp_path), tmp_path).scene
    assert wide["Profiles"]["FluidField"][0]["Field"]["InfluenceRadiusCm"] == 320


def test_front_layer_without_alpha_is_rejected(tmp_path):
    """불투명 캔버스 앞에 겹치는 층은 맨 뒤 층이 아니어도 알파가 있어야 한다(뒤 층이 없으면 mid가 맨 뒤가 된다)."""
    only_mid = {"format": "artwork-assets", "version": 1, "layers": [{"id": "mid", "image": "mid.png", "depth": "mid"}]}
    result = assemble(spec("stir", "fluid"), files(tmp_path, only_mid, opaque=("mid.png",)), tmp_path)
    assert result.scene is None and result.errors == ["층 mid는 반응 캔버스 앞에 놓여 알파가 있어야 함"]
    behind = assemble(spec("push", "particle"), files(tmp_path, only_mid, opaque=("mid.png",)), tmp_path)
    assert behind.errors == []   # 입자 장은 층이 효과 뒤라 가리지 않는다


def test_front_layer_coverage_warning(tmp_path):
    assets = files(tmp_path)
    png(tmp_path / "near.png", size=HD, opaque_alpha=True)   # RGBA인데 모두 불투명
    result = assemble(spec("stir", "fluid"), assets, tmp_path)
    assert result.errors == [] and any("층 near가 반응 앞을 100% 덮음" in warning for warning in result.warnings)
    quiet = assemble(spec("stir", "fluid"), files(tmp_path), tmp_path)
    assert not any("덮음" in warning for warning in quiet.warnings)


def rgba_rows(path, opaque_share):
    """위쪽 opaque_share 비율의 줄만 불투명(α=255), 나머지는 투명인 RGBA PNG."""
    width, height = HD
    cut = round(height * opaque_share)
    rows = b"".join(b"\x00" + bytes([200, 50, 50, 255 if y < cut else 0]) * width for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows))
                     + chunk(b"IEND", b""))


@pytest.mark.parametrize("share, warned", [(0.25, False), (0.4, True)])
def test_front_layer_coverage_threshold(tmp_path, share, warned):
    assets = files(tmp_path)
    rgba_rows(tmp_path / "mid.png", share)
    result = assemble(spec("stir", "fluid"), assets, tmp_path)
    assert any("층 mid가 반응 앞을" in warning for warning in result.warnings) is warned


def test_front_layer_unreadable_format_is_warned(tmp_path):
    assets = files(tmp_path)
    png(tmp_path / "near.png", size=HD, color=3, trns=True)   # 팔레트 + 투명색: 알파는 있지만 비율은 못 읽음
    result = assemble(spec("stir", "fluid"), assets, tmp_path)
    assert result.errors == [] and any("층 near의 불투명 비율을 읽지 못함" in warning for warning in result.warnings)


def test_template_defaults_kept_when_spec_leaves_them_empty(tmp_path):
    base = spec("push", "particle")
    base.interactions[0].return_ = None
    result = assemble(base, files(tmp_path), tmp_path)
    template = template_scene("gpu_particles")
    forces = {"RepulsionSpeed", "SwirlSpeed", "HandVelocityGain"}   # 반응(push)이 정하는 값
    profile = {k: v for k, v in result.scene["Profiles"]["GpuFlow"][0].items() if k not in forces}
    assert profile == template["Profiles"]["GpuFlow"][0]
    assert result.scene["Bindings"][0]["ReleaseSeconds"] == template["Bindings"][0]["ReleaseSeconds"]


def test_gesture_inputs_fall_back_to_coordinates(tmp_path):
    result = assemble(spec(input="wave"), files(tmp_path), tmp_path)
    assert result.errors == [] and any("입력 wave" in item for item in result.substitutions)
    assert all(row["SourceIds"] == ["td_main/forest_hand", "td_main/hand_tracking_2"]
               for row in result.scene["Conditions"])
    empty = spec()
    empty.interactions[0].input = None
    assert not any("입력" in item for item in assemble(empty, files(tmp_path), tmp_path).substitutions)


def test_materials_become_full_look(tmp_path):
    data = {**THREE, "materials": [{"target": "far", "values": {"EmissiveStrength": 0.8}},
                                   {"target": "near", "values": {"Tint": [0.6, 0.8, 1.0]}}]}
    result = assemble(spec("push", "particle"), files(tmp_path, data), tmp_path)
    assert result.errors == []
    look = result.scene["Look"]
    assert [m["Target"] for m in look["Materials"]] == ["LayerFar", "LayerNear"]
    assert look["Lights"] and look["PostProcess"] and "Environment" not in look   # 전체 프로필, 하늘 · 안개 없음
    assert any(warning.startswith("PC1 검증 필요: Look") for warning in result.warnings)
    assert "Look" not in assemble(spec("push", "particle"), files(tmp_path), tmp_path).scene


def test_layer_material_rejects_values_it_cannot_take(tmp_path):
    data = {**THREE, "materials": [{"target": "mid", "values": {"Roughness": 0.5}}]}
    result = assemble(spec("push", "particle"), files(tmp_path, data), tmp_path)
    assert result.scene is None and "Roughness" in result.errors[0]
    stir = {**ONE, "materials": [{"target": "far", "values": {"Tint": [1, 1, 1]}}]}
    result = assemble(spec("stir", "particle"), files(tmp_path, stir), tmp_path)
    assert result.scene is None and result.errors == ["층 far은 Canvas(템플릿 캔버스)에 들어가 재질 값을 받지 않음"]


def test_rejections(tmp_path):
    assets = files(tmp_path)
    for reveal in ("formation", "trace"):
        hidden = Spec.model_validate({**spec().model_dump(by_alias=True), "template_family": "scene_family",
                                      "content": {**spec().content.model_dump(), "reveal": reveal,
                                                  "main_character": "고래", "background": "바다", "dimension": "2.5d"}})
        assert assemble(hidden, assets, tmp_path).errors == [f"{reveal}(보이지 않는 주인공) 작품은 아직 UE 템플릿이 없어 보류"]
    assert assemble(spec("follow", "swarm"), assets, tmp_path).errors == [
        "반응 follow은 객체 행동으로만 되고, 매질 swarm로 고를 템플릿이 없어 보류"]
    (tmp_path / "near.png").unlink()
    result = assemble(spec(), assets, tmp_path)
    assert result.scene is None and result.files == {} and "파일 없음: near.png" in result.errors


def test_static_check_errors_return_no_scene(tmp_path, monkeypatch):
    class Failed:
        errors, unverified = ["일부러 실패"], []

    monkeypatch.setattr(ue_assemble, "check_scene", lambda *args, **kwargs: Failed())
    result = assemble(spec(), files(tmp_path), tmp_path)
    assert result.errors == ["일부러 실패"] and result.scene is None and result.files == {}


def test_warnings_for_unknown_fields_extra_rules_and_aspect(tmp_path):
    data = {**THREE, "camera": "wide"}
    two = spec()
    two.interactions.append(two.interactions[0].model_copy())
    result = assemble(two, files(tmp_path, data, size=(64, 64)), tmp_path)
    assert result.errors == []
    assert "모르는 칸: camera" in result.warnings
    assert any("첫 규칙만" in item for item in result.substitutions)
    assert any("화면 비율과 달라" in warning for warning in result.warnings)


def test_incompatible_catalog_is_reported(tmp_path, monkeypatch):
    class Empty:
        def has_pattern(self, pattern):
            return False

        def object(self, object_id):
            return None

    monkeypatch.setattr(ue_assemble, "shared_catalog", lambda: Empty())
    result = assemble(spec("push", "particle"), files(tmp_path), tmp_path)
    assert "카탈로그에 패턴 GpuFlow가 없음" in result.errors and result.scene is None


FISH = {**THREE, "objects": [
    {"id": "fish", "image": "fish.png", "position": [0.25, 0.75], "size": 0.1, "behaviors": ["float", "glow"]},
    {"id": "bird", "image": "bird.png", "position": [0.8, 0.2], "behaviors": ["follow"]},
    {"id": "moth", "image": "moth.png", "position": [0.5, 0.5], "behaviors": ["flee"]},
    {"id": "stone", "image": "stone.png", "position": [0.5, 0.85]}]}
STILL = {**THREE, "objects": [o for o in FISH["objects"] if o["id"] in ("fish", "stone")]}
ONE_FISH = {**ONE, "objects": FISH["objects"]}
BEHIND = [("gpu_particles", "push", "particle", THREE), ("image_stir", "stir", "particle", ONE)]
FRONT = [("fluid", "stir", "fluid"), ("paint", "draw", "stroke"), ("deformation", "ripple", "surface")]


@pytest.mark.parametrize("template, reaction, medium, base", BEHIND)
def test_objects_become_sprites_with_behaviors(tmp_path, template, reaction, medium, base):
    """오려 낸 객체마다 Sprite 배치 · 외부 이미지 · 행동 바인딩이 붙고 정적 검사를 통과한다."""
    result = assemble(spec(reaction, medium), files(tmp_path, {**base, "objects": FISH["objects"]}), tmp_path)
    assert result.errors == [] and result.template == template
    assert check_scene(result.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
    rows = placements(result)
    assert {rows[f"Obj_{name}"]["ObjectId"] for name in ("fish", "bird", "moth", "stone")} == {"Sprite"}
    images = {row["Name"]: row["ImageFile"] for row in result.scene["ExternalAssets"]}
    assert images["Obj_fish"] == "fish.png" and set(result.files) >= {"fish.png", "bird.png", "moth.png", "stone.png"}
    bindings = {row["Name"]: row for row in result.scene["Bindings"]}
    assert bindings["Obj_fish_float"]["PatternId"] == "Float" and "ConditionId" not in bindings["Obj_fish_float"]
    assert bindings["Obj_fish_glow"]["PatternId"] == "MaterialScalar"
    assert bindings["Obj_bird_follow"]["ProfileId"] == "SpriteFollow"
    assert bindings["Obj_moth_flee"]["ProfileId"] == "SpriteFlee"
    assert not any(name.startswith("Obj_stone") for name in bindings)
    flee = next(row for row in result.scene["Profiles"]["AttractRepel"] if row["Name"] == "SpriteFlee")
    assert flee["bRepel"] is True


def test_sprite_conditions_match_behavior_meaning(tmp_path):
    """따라감은 화면 어디든 손이 있으면, 빛남 · 피함은 그 객체 가까이 손이 올 때만(객체 현재 위치 기준 근접)."""
    result = assemble(spec("push", "particle"), files(tmp_path, FISH), tmp_path)
    bindings = {row["Name"]: row for row in result.scene["Bindings"]}
    conditions = {row["Name"]: row for row in result.scene["Conditions"]}
    follow = conditions[bindings["Obj_bird_follow"]["ConditionId"]]
    assert follow["Type"] == "Presence" and follow["Region"] == "InputSurface"
    for name in ("Obj_fish_glow", "Obj_moth_flee"):
        near = conditions[bindings[name]["ConditionId"]]
        assert near["Type"] == "Proximity" and near["Anchor"] == "CurrentObject"
        assert near["ExitRadiusCm"] > near["RadiusCm"]
    assert conditions["SpriteNear"]["ExitRadiusCm"] > conditions["SpriteNear"]["RadiusCm"] + ue_assemble.MOVE_CM
    assert len(result.scene["Conditions"]) == len(template_scene("gpu_particles")["Conditions"]) + 3


@pytest.mark.parametrize("template, reaction, medium", FRONT)
def test_front_templates_take_still_objects_but_not_screen_moves(tmp_path, template, reaction, medium):
    """불투명 캔버스 템플릿은 객체를 입력 평면 앞에 둬서, 손 좌표를 그대로 쓰는 따라감 · 피함은 엉뚱한 쪽으로 움직인다."""
    still = assemble(spec(reaction, medium), files(tmp_path, STILL), tmp_path)
    assert still.errors == [] and still.template == template
    assert check_scene(still.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
    moving = assemble(spec(reaction, medium), files(tmp_path, FISH), tmp_path)
    assert moving.scene is None and len(moving.errors) == 1
    assert "따라감 · 피함" in moving.errors[0] and "['bird', 'moth']" in moving.errors[0]


def test_sprite_depths(tmp_path):
    """투명 효과 템플릿: 입력 평면(X=300, 손 좌표 깊이) · 뒤 층들 앞. 불투명 캔버스: 캔버스(450) → 카메라의 20%."""
    stage = shared_stage()
    behind = placements(assemble(spec("push", "particle"), files(tmp_path, FISH), tmp_path))
    assert vector(behind["Obj_fish"]["Location"], "X") == stage["SurfaceOrigin"][0] == 300
    assert vector(behind["Obj_fish"]["Location"], "X") > vector(behind["LayerNear"]["Location"], "X")
    stir = placements(assemble(spec("stir", "particle"), files(tmp_path, ONE_FISH), tmp_path))
    assert vector(stir["Obj_fish"]["Location"], "X") == 300 > vector(stir["Canvas"]["Location"], "X")
    front = placements(assemble(spec(), files(tmp_path, STILL), tmp_path))
    x = vector(front["Obj_fish"]["Location"], "X")
    assert x == 800 and vector(front["LayerMid"]["Location"], "X") < x < vector(front["LayerNear"]["Location"], "X")


def test_sprite_position_and_size_follow_screen_coordinates(tmp_path):
    """(u, v)는 객체 깊이 평면의 화면 위치: 카메라가 -X를 보므로 왼쪽(u<0.5)이 +Y, 위(v<0.5)가 +Z."""
    result = assemble(spec("push", "particle"), files(tmp_path, FISH), tmp_path)
    stage, rows = shared_stage(), placements(result)
    fish, bird = rows["Obj_fish"], rows["Obj_bird"]
    view = 2 * (stage["CameraLocation"][0] - vector(fish["Location"], "X")) * 0.70021
    assert vector(fish["Location"], "Y") == pytest.approx(0.25 * view, rel=1e-3)
    assert vector(fish["Location"], "Z") == pytest.approx(100 - 0.25 * view / stage["AspectRatio"], rel=1e-3)
    assert vector(bird["Location"], "Y") < 0 and vector(bird["Location"], "Z") > 100
    assert vector(fish["Scale"], "X") * 100 == pytest.approx(0.1 * view, rel=1e-3)
    assert vector(fish["Scale"], "Y") == pytest.approx(vector(fish["Scale"], "X") * 36 / 64, rel=1e-3)
    assert fish["Rotation"] == "(Pitch=0,Yaw=-90,Roll=90)"


BUTTERFLY = {"id": "butterfly", "image": "butterfly.png", "position": [0.7, 0.3], "size": 0.12, "behaviors": ["wander"]}


def no_butterfly(monkeypatch):
    """공용 카탈로그에 나비가 없을 때(10/25 이전 실행본)의 대체 경로: 사각형 평면."""
    catalog = shared_catalog()
    real = type(catalog).object
    monkeypatch.setattr(type(catalog), "object",
                        lambda self, object_id: None if object_id == ue_assemble.BUTTERFLY_OBJECT else real(self, object_id))


def wander_room(item):
    """런타임 Locomotion 이동 영역의 반폭(cm): 화면 0.05~0.95에서 외형 반경(사각형 반대각선) + 경계 패딩만큼 안쪽."""
    stage = shared_stage()
    view = 2 * (stage["CameraLocation"][0] - 300) * math.tan(math.radians(stage["FieldOfView"] / 2))
    wing = item["size"] * view
    head = wing * HD[1] / HD[0]
    radius = math.hypot(wing / 2, head / 2) + ue_assemble.WANDER_PADDING_CM
    surface_w, surface_h = stage["SurfaceSize"]   # runtime 이동 영역은 입력 평면 크기 기준(ArtworkLocomotionBehavior.cpp:149)
    return view, wing, head, 0.45 * surface_w - radius, 0.45 * surface_h - radius


WANDER_BEHIND = BEHIND + [("ripple_lake", "ripple", "surface", ONE), ("ripple_star", "ripple", "surface", ONE)]


@pytest.mark.parametrize("template, reaction, medium, base", WANDER_BEHIND)
def test_wander_object_flies_with_quad_mesh_and_locomotion(tmp_path, monkeypatch, template, reaction, medium, base):
    """카탈로그에 나비가 없으면: 고정 사각형 메시(엔진 평면은 좌우로 날 때 누워 안 보임) + 런타임 Locomotion. 크기는 배치 Scale."""
    no_butterfly(monkeypatch)
    choice = ue_assemble.TemplateChoice(template=template) if template.startswith("ripple") else None
    result = assemble(spec(reaction, medium), files(tmp_path, {**base, "objects": [BUTTERFLY]}), tmp_path, choice)
    assert result.errors == [] and result.template == template
    assert check_scene(result.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
    _, wing, head, _, _ = wander_room(BUTTERFLY)
    row = placements(result)["Obj_butterfly"]
    assert row["ObjectId"] == "Sprite" and row["Rotation"] == "(Pitch=0,Yaw=90,Roll=0)"
    assert vector(row["Scale"], "X") * 100 == pytest.approx(head, rel=1e-3) and vector(row["Scale"], "Y") == 1
    assert vector(row["Scale"], "Z") * 100 == pytest.approx(wing, rel=1e-3)
    asset = next(a for a in result.scene["ExternalAssets"] if a["Name"] == "Obj_butterfly")
    assert asset["MeshFile"] == "wander_quad.obj" and asset["ImageFile"] == "butterfly.png"
    assert result.files["wander_quad.obj"] == ue_assemble.TEMPLATES / "meshes" / "wander_quad.obj"
    binding = next(b for b in result.scene["Bindings"] if b["Name"] == "Obj_butterfly_wander")
    assert binding["PatternId"] == "Locomotion"
    condition = next(c for c in result.scene["Conditions"] if c["Name"] == binding["ConditionId"])
    assert condition["Type"] == "Proximity" and condition["Anchor"] == "CurrentObject"
    profile = next(p for p in result.scene["Profiles"]["Locomotion"] if p["Name"] == binding["ProfileId"])
    assert profile["BoundaryPaddingCm"] == ue_assemble.WANDER_PADDING_CM   # 런타임 기본 30과 다르므로 반드시 명시
    assert profile["TouchReaction"] == "Evade" and profile["MaxBankAngleDegrees"] == 0


def test_wander_quad_mesh_lies_on_local_xz_plane():
    """그림 위쪽(머리)이 +X, 날개폭이 Z, 법선 ±Y. 원점 중심이라 런타임 외형 반경이 반대각선과 같다."""
    lines = (ue_assemble.TEMPLATES / "meshes" / "wander_quad.obj").read_text("ascii").splitlines()
    vertices = [tuple(map(float, line.split()[1:])) for line in lines if line.startswith("v ")]
    assert sorted(vertices) == sorted([(50, 0, 50), (50, 0, -50), (-50, 0, -50), (-50, 0, 50)])


def test_wander_start_is_pulled_inside_runtime_area(tmp_path, monkeypatch):
    """시작 위치가 이동 영역 밖이면 런타임 Prepare가 실패해 장면 전체가 안 뜬다 → 안으로 당기고 경고."""
    no_butterfly(monkeypatch)
    corner = {**BUTTERFLY, "position": [0.0, 1.0]}
    result = assemble(spec("push", "particle"), files(tmp_path, {**THREE, "objects": [corner]}), tmp_path)
    assert result.errors == [] and any("butterfly" in w and "안으로" in w for w in result.warnings)
    _, _, _, room_y, room_z = wander_room(corner)
    row = placements(result)["Obj_butterfly"]
    assert abs(vector(row["Location"], "Y")) <= room_y and abs(vector(row["Location"], "Z") - 100) <= room_z


def test_wander_too_large_for_screen_is_rejected(tmp_path, monkeypatch):
    no_butterfly(monkeypatch)
    huge = {**BUTTERFLY, "size": 0.9}
    result = assemble(spec("push", "particle"), files(tmp_path, {**THREE, "objects": [huge]}), tmp_path)
    assert result.scene is None and any("butterfly" in e and "돌아다닐" in e for e in result.errors)


@pytest.mark.parametrize("template, reaction, medium", FRONT + [("pigment_flow", "stir", "fluid")])
def test_wander_is_rejected_on_front_templates(tmp_path, template, reaction, medium):
    choice = ue_assemble.TemplateChoice(template=template) if template == "pigment_flow" else None
    result = assemble(spec(reaction, medium), files(tmp_path, {**THREE, "objects": [BUTTERFLY]}), tmp_path, choice)
    assert result.scene is None and "['butterfly']" in result.errors[0]


def test_wander_mixes_with_other_objects(tmp_path, monkeypatch):
    """돌아다니는 객체 둘(하나는 빛남 겸용)과 기존 객체가 한 장면에: 조건 · 프로필 이름이 겹치지 않고 메시는 한 번만 보낸다."""
    no_butterfly(monkeypatch)
    moth = {**BUTTERFLY, "id": "moth2", "image": "moth2.png", "position": [0.3, 0.6], "behaviors": ["wander", "glow"]}
    data = {**THREE, "objects": FISH["objects"] + [BUTTERFLY, moth]}
    result = assemble(spec("push", "particle"), files(tmp_path, data), tmp_path)
    assert result.errors == [] and check_scene(result.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
    names = [row["Name"] for row in result.scene["Conditions"]]
    assert len(names) == len(set(names)) and "SpriteWanderNear" in names and "SpriteNear" in names
    walkers = [row["Name"] for row in result.scene["Profiles"]["Locomotion"]]
    assert walkers == ["SpriteWander_butterfly", "SpriteWander_moth2"]
    seeds = {row["RandomSeed"] for row in result.scene["Profiles"]["Locomotion"]}
    assert len(seeds) == 2 and [n for n in result.files if n.endswith(".obj")] == ["wander_quad.obj"]
    assert sum("MeshFile" in row for row in result.scene["ExternalAssets"]) == 2
    assert any(b["Name"] == "Obj_moth2_glow" for b in result.scene["Bindings"])


@pytest.mark.parametrize("template, reaction, medium, base", WANDER_BEHIND)
def test_wander_uses_flapping_butterfly_from_catalog(tmp_path, template, reaction, medium, base):
    """공용 카탈로그에 라넬 3D 나비가 있으면 그 나비 + Locomotion + ActionSequence(평상 날갯짓)로 난다. 외부 그림은 붙이지 않는다."""
    choice = ue_assemble.TemplateChoice(template=template) if template.startswith("ripple") else None
    result = assemble(spec(reaction, medium), files(tmp_path, {**base, "objects": [BUTTERFLY]}), tmp_path, choice)
    assert result.errors == [] and check_scene(result.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
    row = placements(result)["Obj_butterfly"]
    assert row["ObjectId"] == ue_assemble.BUTTERFLY_OBJECT
    assert vector(row["Scale"], "X") == vector(row["Scale"], "Y") == vector(row["Scale"], "Z")
    assert not any(a["Name"] == "Obj_butterfly" for a in result.scene.get("ExternalAssets", []))
    bindings = {b["Name"]: b for b in result.scene["Bindings"]}
    flap, walk = bindings["Obj_butterfly_flap"], bindings["Obj_butterfly_wander"]
    assert flap["PatternId"] == "ActionSequence" and flap["ConditionId"] == walk["ConditionId"]   # 반응이 켜져 있어 조건 필수
    pose = next(p for p in result.scene["Profiles"]["ActionSequence"] if p["Name"] == flap["ProfileId"])
    assert pose["AmbientAnimation"].endswith("BS_CC0ButterflyV1") and pose["bUseMotionForAmbient"] is True
    motion = next(p for p in result.scene["Profiles"]["Locomotion"] if p["Name"] == walk["ProfileId"])
    assert motion["VisualForwardYawCorrectionDegrees"] == -90


def test_glowing_wanderer_falls_back_to_picture_plane(tmp_path):
    """나비 재질에 밝기 값이 있는지 오프라인으로 알 수 없어, 빛남까지 하는 객체는 그 객체만 그림 평면으로 돌아다닌다."""
    item = {**BUTTERFLY, "behaviors": ["wander", "glow"]}
    result = assemble(spec("push", "particle"), files(tmp_path, {**THREE, "objects": [item]}), tmp_path)
    assert result.errors == [] and placements(result)["Obj_butterfly"]["ObjectId"] == "Sprite"
    assert any("빛남" in w and "그림 평면" in w for w in result.warnings)


def test_flapping_butterfly_start_is_pulled_inside(tmp_path):
    corner = {**BUTTERFLY, "position": [1.0, 0.0]}
    result = assemble(spec("push", "particle"), files(tmp_path, {**THREE, "objects": [corner]}), tmp_path)
    stage, row = shared_stage(), placements(result)["Obj_butterfly"]
    # 스켈레탈 경계가 메시 실측보다 클 수 있어 당길 때는 반경 × 1.2 + 30cm만큼 더 안쪽(런타임 이동 영역 식보다 보수적)
    radius = vector(row["Scale"], "X") * ue_assemble.BUTTERFLY_RADIUS_CM * 1.2 + ue_assemble.WANDER_PADDING_CM + 30
    surface_w, surface_h = stage["SurfaceSize"]
    assert abs(vector(row["Location"], "Y")) <= 0.45 * surface_w - radius
    assert abs(vector(row["Location"], "Z") - 100) <= 0.45 * surface_h - radius


@pytest.mark.parametrize("reaction, medium, template", [("stir", "fluid", "fluid"), ("draw", "stroke", "paint"),
                                                       ("ripple", "surface", "deformation"), ("stir", "fluid", "pigment_flow")])
def test_front_canvas_shows_back_layer_image(tmp_path, reaction, medium, template):
    """불투명 캔버스 바탕에 뒤 층 그림(재질 SourceImage). 캔버스가 덮는 화면 구간만 읽고, 캔버스 회전(Yaw 90)이 층(Yaw −90)과
    좌우가 반대라 가로 크기가 음수다."""
    choice = ue_assemble.TemplateChoice(template=template) if template == "pigment_flow" else None
    result = assemble(spec(reaction, medium), files(tmp_path), tmp_path, choice)
    assert result.errors == [] and check_scene(result.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
    canvas = next(a for a in result.scene["ExternalAssets"] if a["Name"] == "Canvas")
    assert canvas == {"Name": "Canvas", "ImageFile": "far.png", "TextureParameter": "SourceImage"}
    values = {p["ParameterName"]: p["RestValue"] for p in result.scene["Profiles"]["MaterialScalar"]
              if p["Name"].startswith("CanvasSource")}
    assert values["SourceBlend"] == 1 and values["SourceUvSizeU"] < 0 < values["SourceUvSizeV"]
    assert values["SourceUvMinU"] + values["SourceUvSizeU"] / 2 == pytest.approx(0.5, abs=1e-3)   # 가운데 놓인 캔버스
    assert values["SourceUvMinV"] + values["SourceUvSizeV"] / 2 == pytest.approx(0.5, abs=1e-3)
    if template == "pigment_flow":   # 화면을 꽉 채우는 캔버스는 원화 전체(좌우만 뒤집힘)
        assert [round(values[k], 2) for k in ("SourceUvMinU", "SourceUvMinV", "SourceUvSizeU", "SourceUvSizeV")] == [1, 0, -1, 1]
    else:   # X=450의 24 × 13.5 캔버스는 화면 가운데 약 98%
        assert -values["SourceUvSizeU"] == pytest.approx(2400 / ue_assemble._view_width_cm(450), rel=1e-3)
    bindings = [b for b in result.scene["Bindings"] if b["Name"].startswith("CanvasSource")]
    assert len(bindings) == 5 and all(b["PlacementId"] == "Canvas" and "ConditionId" not in b for b in bindings)


def test_wander_seed_is_stable_per_object(tmp_path):
    first = assemble(spec("push", "particle"), files(tmp_path, {**THREE, "objects": [BUTTERFLY]}), tmp_path)
    again = assemble(spec("push", "particle"), files(tmp_path, {**THREE, "objects": [BUTTERFLY]}), tmp_path)
    assert first.scene == again.scene


def test_wander_cannot_combine_with_other_motion():
    with pytest.raises(ValueError):
        ArtworkAssets.model_validate({**THREE, "objects": [{**BUTTERFLY, "behaviors": ["wander", "float"]}]})


def halves(path, left, right, size=HD):
    """왼쪽 절반 left, 오른쪽 절반 right 색인 RGB PNG(대표색 시험용)."""
    width, height = size
    row = b"\x00" + bytes(left) * (width // 2) + bytes(right) * (width - width // 2)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * height))
                     + chunk(b"IEND", b""))


def hue(color):
    r, g, b = (float(part.split("=")[1]) for part in color.strip("()").split(",")[:3])
    return "red" if r > max(g, b) else "blue" if b > max(r, g) else "other"


def test_palette_picks_distinct_hues_in_linear_rgb(tmp_path):
    halves(tmp_path / "far.png", (200, 40, 30), (30, 60, 200))
    colors = ue_assemble.palette(tmp_path / "far.png", 2)
    assert sorted(hue(c) for c in colors) == ["blue", "red"]
    assert all(c.endswith(",A=1)") for c in colors)


def test_palette_of_pale_image_uses_its_faint_hue(tmp_path):
    """한지처럼 옅은 미색 원화(채도 약 0.1)도 무채색 기준을 한 번 낮춰 그 색을 쓴다(10/25 시연 2번)."""
    halves(tmp_path / "far.png", (240, 228, 200), (232, 222, 196))
    colors = ue_assemble.palette(tmp_path / "far.png", 2)
    assert len(colors) == 2 and hue(colors[0]) == "red"   # 미색은 빨강 > 초록 > 파랑


def test_palette_on_light_picture_is_dark_enough_to_show(tmp_path):
    """캔버스 바탕에 원화가 깔리므로, 밝은 원화(한지)의 대표색을 그대로 쓰면 붓 자국이 안 보인다(10/25 PC1 paint 손 반응 0).
    밝은 원화면 같은 색상의 어두운 색, 어두운 원화면 밝은 색."""
    halves(tmp_path / "light.png", (240, 228, 200), (232, 222, 196))
    halves(tmp_path / "dark.png", (40, 20, 90), (20, 60, 70))
    light, dark = ue_assemble.palette(tmp_path / "light.png", 2), ue_assemble.palette(tmp_path / "dark.png", 2)

    def channels(color):
        return [float(part.split("=")[1]) for part in color.strip("()").split(",")[:3]]
    assert max(max(channels(c)) for c in light) <= 0.06 and hue(light[0]) == "red"   # 선형 0.051 = sRGB 0.25
    assert min(max(channels(c)) for c in dark) >= 0.5


def test_palette_of_gray_image_is_empty(tmp_path):
    halves(tmp_path / "far.png", (90, 90, 90), (160, 160, 160))
    assert ue_assemble.palette(tmp_path / "far.png", 2) == []


@pytest.mark.parametrize("reaction, medium, template, paths", [
    ("stir", "fluid", "fluid", [("FluidField", "ContactColors", 2)]),
    ("draw", "stroke", "paint", [("SurfacePaint", "StrokeColors", 2)]),
    ("stir", "fluid", "pigment_flow", [("FluidField", "ContactColors", 2), ("FluidField", "IdleInkColors", 3)]),
])
def test_front_canvas_reaction_colors_come_from_back_layer(tmp_path, reaction, medium, template, paths):
    """불투명 캔버스가 뒤 층을 가려도 반응 색(염료 · 붓 · 평소 물감)은 생성 배경의 대표색을 띤다."""
    assets = files(tmp_path)
    halves(tmp_path / "far.png", (200, 40, 30), (30, 60, 200))
    choice = ue_assemble.TemplateChoice(template=template) if template == "pigment_flow" else None
    result = assemble(spec(reaction, medium), assets, tmp_path, choice)
    assert result.errors == [] and result.template == template
    assert check_scene(result.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
    for profile, field, count in paths:
        colors = result.scene["Profiles"][profile][0][field]
        assert len(colors) == count and {hue(c) for c in colors} == {"red", "blue"}


def test_gray_back_layer_keeps_template_colors(tmp_path):
    assets = files(tmp_path)
    halves(tmp_path / "far.png", (90, 90, 90), (160, 160, 160))
    result = assemble(spec("draw", "stroke"), assets, tmp_path)
    assert result.scene["Profiles"]["SurfacePaint"][0]["StrokeColors"] == \
        template_scene("paint")["Profiles"]["SurfacePaint"][0]["StrokeColors"]
    assert any("대표색" in w for w in result.warnings)


def test_image_stir_idle_flow_keeps_the_painting():
    """손이 없을 때 흐름 속도 ≈ 대기 가속 ÷ 손 뗀 뒤 감쇠. 48 ÷ 0.8 ≈ 60cm/s는 손 대기 전부터 원화를 뭉갰다(10/25 시연)."""
    row = template_scene("image_stir")["Profiles"]["ImageFlow"][0]
    # 붓질 질감 원화는 몇 픽셀만 밀려도 화면 대부분이 바뀐다(PC1: 대기 16 → 평소 변화 0.63~0.83, 0 → 0.01). 손이 없으면 멈춘다
    assert row["Field"]["AmbientAccelerationCmPerSecondSquared"] == 0
    assert row["RestoreHalfLifeSeconds"] >= AFTERIMAGE_HALF_LIFE   # 잔상 5초 기준은 지킨다


@pytest.mark.parametrize("template", ["ripple_lake", "ripple_star"])
def test_ripple_hand_rings_do_not_depend_on_hand_speed(template):
    """물결 고리 세기가 장면 움직임(손 속도 ÷ 900cm/s)을 따르면 천천히 움직이는 손에서 거의 안 보였다(10/25 시연 0.04)."""
    scene = template_scene(template)
    rows = {row["Name"]: row for row in scene["Profiles"]["MaterialScalar"]}
    assert not rows["RingStrength"].get("bUseSceneMotionEnergy") and rows["RingStrength"]["RestValue"] > 0
    surface = scene["Profiles"]["SurfaceDeformation"][0]["Surface"]
    assert surface["RippleImpulseCmPerSecond"] <= surface["MaximumVelocityCmPerSecond"]   # 상한(기본 200)에 잘리지 않게


def test_offscreen_and_square_objects_are_warned(tmp_path):
    """움직임(따라감 4m)까지 더해 화면 밖으로 나가는 객체, 알파는 있지만 투명 픽셀이 없는 객체는 경고."""
    assets = files(tmp_path, FISH)
    png(tmp_path / "stone.png", size=HD, opaque_alpha=True)
    result = assemble(spec("push", "particle"), assets, tmp_path)
    assert result.errors == []
    sprite = [w for w in result.warnings if "화면 밖" in w or "사각형" in w]
    assert sprite == ["객체 bird가 (움직임 포함) 화면 밖으로 일부 나감", "객체 stone PNG에 투명 픽셀이 거의 없어 사각형으로 보임"]


def test_objects_without_alpha_are_rejected(tmp_path):
    result = assemble(spec(), files(tmp_path, STILL, opaque=("far.png", "stone.png")), tmp_path)
    assert result.scene is None and result.errors == ["객체 stone: 오려 낸 객체 PNG는 알파가 있어야 함"]


def test_no_objects_adds_nothing_and_keeps_scene_name(tmp_path):
    assets = files(tmp_path)
    result = assemble(spec(), assets, tmp_path)
    assert {row["Name"] for row in result.scene["Conditions"]} == {row["Name"] for row in template_scene("fluid")["Conditions"]}
    assert not any(row["ObjectId"] == "Sprite" for row in result.scene["Placements"])
    contents = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(result.files.items())}
    before = assets.model_dump(mode="json", exclude={"objects"})   # objects 칸이 생기기 전과 같은 입력
    digest = hashlib.sha256(json.dumps([spec().model_dump(mode="json", by_alias=True), before, "fluid", contents],
                                       sort_keys=True).encode()).hexdigest()
    assert result.scene["Scene"][0]["Name"] == f"Auto-fluid-{digest[:12]}"


def test_sprite_needs_catalog_support(monkeypatch):
    catalog = shared_catalog()
    monkeypatch.setattr(type(catalog), "has_pattern", lambda self, name: name != "Float")
    assets = ArtworkAssets.model_validate(FISH)
    assert ue_assemble.compatibility("fluid", assets) == ["카탈로그에 패턴 Float가 없음"]
    assert ue_assemble.compatibility("fluid") == []


def test_select_template_records_substitutions_and_holds():
    """매질만 보고 조용히 고르지 않는다: 전용 템플릿 반응은 대체 없이, follow · glow는 대체 기록, 그 밖은 보류."""
    direct = ue_assemble.select_template(spec("push", "particle"))
    assert (direct.template, direct.substitutions, direct.holds) == ("gpu_particles", [], [])
    sprite = ue_assemble.select_template(spec("glow", "particle"))
    assert sprite.template == "gpu_particles" and sprite.holds == []
    assert sprite.substitutions == ["반응 glow은 전용 템플릿이 없어 매질 particle의 gpu_particles 템플릿을 쓰고, 반응은 객체 행동으로 표현"]
    assert sprite.required_behaviors == ["glow"] and direct.required_behaviors == []
    held = ue_assemble.select_template(spec("bloom", "surface"))
    assert held.template is None and held.holds == ["반응 bloom에 맞는 검증된 템플릿이 없어 보류(medium=surface)"]


@pytest.mark.parametrize("mode, substituted", [("while", False), ("once", True), ("accumulate", True)])
def test_reaction_mode_other_than_while_is_recorded(mode, substituted):
    """지금 템플릿 5개는 손이 있는 동안 반응(while)만 검증됨. once · accumulate는 while로 실행하고 기록을 남긴다."""
    rule = spec("push", "particle")
    rule.interactions[0].reaction_mode = mode
    choice = ue_assemble.select_template(rule)
    assert choice.template == "gpu_particles"
    assert any(f"반응 방식 {mode}" in item for item in choice.substitutions) is substituted


def test_recorded_choice_is_reused_not_reselected(tmp_path, monkeypatch):
    """생성 전에 기록한 선택을 조립이 그대로 쓴다. 그사이 대응표가 바뀌어도 다시 고르지 않는다."""
    choice = ue_assemble.select_template(spec("push", "particle"))
    monkeypatch.setattr(ue_assemble, "select_template", lambda _spec: pytest.fail("다시 고르면 안 됨"))
    result = assemble(spec("push", "particle"), files(tmp_path), tmp_path, choice)
    assert result.errors == [] and result.template == "gpu_particles"
    held = assemble(spec(), files(tmp_path), tmp_path, ue_assemble.TemplateChoice(holds=["보류 사유"]))
    assert held.scene is None and held.errors == ["보류 사유"]
    gone = assemble(spec(), files(tmp_path), tmp_path, ue_assemble.TemplateChoice(template="nope"))
    assert gone.scene is None and gone.errors == ["기록된 템플릿 nope이 없음(템플릿 목록이 바뀜)"]


def test_substitutions_reach_assembled_result(tmp_path):
    result = assemble(spec("glow", "particle"), files(tmp_path, FISH), tmp_path)
    assert result.errors == [] and result.template == "gpu_particles"
    assert result.substitutions and "반응 glow" in result.substitutions[0]


def test_sprite_only_reaction_needs_an_object_with_that_behavior(tmp_path):
    """glow를 객체 행동으로 표현하기로 대체했는데 glow 객체가 없으면 반응이 화면에 안 나오므로 보류."""
    result = assemble(spec("glow", "particle"), files(tmp_path), tmp_path)
    assert result.scene is None
    assert result.errors == ["반응 glow을 객체 행동으로 표현하기로 했는데 glow 행동을 가진 객체가 없음"]


def test_object_behaviors_disagreeing_with_spec_are_warned(tmp_path):
    """명세는 push인데 객체가 glow · follow를 하면 경고. flee는 push · scatter와 짝(생성 쪽 대응표 확정 전 가정)."""
    result = assemble(spec("push", "particle"), files(tmp_path, FISH), tmp_path)
    assert result.errors == []
    disagree = sorted(w for w in result.warnings if "맞는 반응이 명세에 없음" in w)
    assert [w.split("의 행동")[0] for w in disagree] == ["객체 bird", "객체 fish"]   # moth(flee)는 push와 맞음


def test_oversized_object_png_is_warned(tmp_path):
    """외부 그림은 밉맵 없이 올라가므로 화면 표시보다 훨씬 큰 PNG는 거칠게 보일 수 있다고 경고."""
    data = {**THREE, "objects": [{"id": "fish", "image": "fish.png", "position": [0.5, 0.5], "size": 0.05}]}
    assets = files(tmp_path, data)
    png(tmp_path / "fish.png", size=(400, 400))   # 표시 가로 약 96px(1920 × 0.05)
    big = [w for w in assemble(spec(), assets, tmp_path).warnings if "거칠게" in w]
    assert len(big) == 1 and "fish" in big[0]
    png(tmp_path / "fish.png", size=(160, 160))
    assert not [w for w in assemble(spec(), assets, tmp_path).warnings if "거칠게" in w]


def scene_spec(reaction, medium):
    """장면 계열: 매질이 선택 칸이라 비우거나("none") 안 채울(None) 수 있다."""
    data = spec(reaction).model_dump(by_alias=True)
    data["template_family"] = "scene_family"
    data["content"] = {**data["content"], "medium": medium, "background": "바다", "dimension": "2.5d"}
    return Spec.model_validate(data)


@pytest.mark.parametrize("medium", [None, "none"])
def test_sprite_only_reaction_without_medium_is_held(medium):
    choice = ue_assemble.select_template(scene_spec("glow", medium))
    assert choice.template is None and choice.holds and "glow" in choice.holds[0]
    assert ue_assemble.select_template(scene_spec("stir", medium)).template == "image_stir"   # 전용 반응은 매질과 무관


def test_empty_reaction_mode_is_held_not_substituted():
    rule = spec("push", "particle")
    rule.interactions[0].reaction_mode = None
    choice = ue_assemble.select_template(rule)
    assert choice.template is None and choice.holds == ["반응 방식(reaction_mode)이 비어 있어 보류"]
    assert not any("반응 방식" in item for item in choice.substitutions)


def test_choice_from_another_spec_is_rejected(tmp_path):
    """기록된 선택이 다른 명세(반응 · 매질 등)에서 나왔으면 엉뚱한 장면을 만들지 않고 거부."""
    other = ue_assemble.select_template(spec("glow", "particle"))
    result = assemble(spec("stir", "fluid"), files(tmp_path, FISH), tmp_path, other)
    assert result.scene is None and result.errors[0].startswith("기록된 템플릿 선택이 이 명세에서 나온 것이 아님")


def test_follow_needs_a_follow_object(tmp_path):
    result = assemble(spec("follow", "particle"), files(tmp_path, STILL), tmp_path)   # STILL: fish(float · glow), stone
    assert result.errors == ["반응 follow을 객체 행동으로 표현하기로 했는데 follow 행동을 가진 객체가 없음"]
    assert assemble(spec("follow", "particle"), files(tmp_path, FISH), tmp_path).errors == []


def test_flee_pairs_with_scatter(tmp_path):
    result = assemble(spec("scatter", "particle"), files(tmp_path, FISH), tmp_path)
    disagree = [w for w in result.warnings if "맞는 반응이 명세에 없음" in w]
    assert not any("moth" in w for w in disagree) and any("bird" in w for w in disagree)


def test_later_rules_are_named_and_agreement_uses_all_rules(tmp_path):
    """연결 안 된 규칙의 반응을 대체 기록에 남긴다. 객체 행동 일치는 모든 규칙의 반응을 본다."""
    two = spec("push", "particle")
    two.interactions.append(two.interactions[0].model_copy(update={"reaction": Reaction.glow}))
    result = assemble(two, files(tmp_path, FISH), tmp_path)
    assert result.errors == []
    assert any("연결 안 된 반응: ['glow']" in item for item in result.substitutions)
    assert not any("fish" in w and "맞는 반응이 명세에 없음" in w for w in result.warnings)   # glow가 두 번째 규칙에 있음


def test_oversize_threshold_is_exactly_twice_display(tmp_path):
    data = {**THREE, "objects": [{"id": "fish", "image": "fish.png", "position": [0.5, 0.5], "size": 0.05}]}
    assets = files(tmp_path, data)
    png(tmp_path / "fish.png", size=(192, 192))   # 표시 96px의 정확히 2배 → 경고 없음
    assert not [w for w in assemble(spec(), assets, tmp_path).warnings if "거칠게" in w]
    png(tmp_path / "fish.png", size=(193, 193))
    assert [w for w in assemble(spec(), assets, tmp_path).warnings if "거칠게" in w]


def test_changed_later_rule_rejects_recorded_choice(tmp_path):
    """두 번째 규칙만 바뀌어도 선택 근거가 달라져 거부하고, 다른 명세의 기록은 결과에 싣지 않는다."""
    first = spec("push", "particle")
    first.interactions.append(first.interactions[0].model_copy(update={"reaction": Reaction.glow}))
    choice = ue_assemble.select_template(first)
    changed = spec("push", "particle")
    changed.interactions.append(changed.interactions[0].model_copy(update={"reaction": Reaction.bloom}))
    result = assemble(changed, files(tmp_path), tmp_path, choice)
    assert result.scene is None and result.errors[0].startswith("기록된 템플릿 선택이 이 명세에서 나온 것이 아님")
    assert result.template is None and result.substitutions == []


@pytest.mark.parametrize("medium", ["fluid", "stroke", "surface"])
def test_follow_on_front_template_is_held_before_generation(tmp_path, medium):
    """불투명 캔버스 템플릿은 follow 객체를 받지 않으므로, 생성 비용을 쓰기 전에 보류한다(조립에서야 실패하지 않게)."""
    choice = ue_assemble.select_template(spec("follow", medium))
    assert choice.template is None and choice.required_behaviors == []
    assert choice.holds and "따라감 · 피함을 받지 않아 보류" in choice.holds[0]
    assert ue_assemble.select_template(spec("glow", medium)).holds == []   # 빛남은 깊이와 무관


def surface_of(scene):
    return scene["Profiles"]["SurfaceDeformation"][0]["Surface"]


def test_ripple_and_press_set_different_surface_modes(tmp_path):
    """같은 표면 변형 템플릿이라도 ripple은 물결(Ripple), press는 누름(Elastic, 라넬 화면 검증 값) 모드로 실행한다."""
    ripple = assemble(spec("ripple", "surface"), files(tmp_path), tmp_path)
    press = assemble(spec("press", "surface"), files(tmp_path), tmp_path)
    for result in (ripple, press):
        assert result.errors == [] and result.substitutions == []
        assert check_scene(result.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
    assert surface_of(ripple.scene)["Mode"] == "Ripple"
    assert surface_of(ripple.scene)["ReturnFrequencyHz"] == surface_of(template_scene("deformation"))["ReturnFrequencyHz"]
    elastic = surface_of(press.scene)
    assert elastic["Mode"] == "Elastic" and elastic["ReturnFrequencyHz"] == 0.25   # 기본 fade: 출렁임 없이 천천히
    assert elastic["PressAccelerationCmPerSecondSquared"] == 2500
    assert any("Elastic" in w and w.startswith("PC1 검증 필요") for w in press.warnings)


def test_particle_reactions_set_their_own_forces(tmp_path):
    """GPU 입자 장은 밀기(RepulsionSpeed) · 소용돌이(SwirlSpeed) · 손 속도(HandVelocityGain)로 반응을 나눈다."""
    forces = {}
    for reaction in ("push", "swirl", "scatter"):
        result = assemble(spec(reaction, "particle"), files(tmp_path), tmp_path)
        assert result.errors == [] and result.substitutions == []
        assert check_scene(result.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
        profile = result.scene["Profiles"]["GpuFlow"][0]
        forces[reaction] = (profile["RepulsionSpeed"], profile["SwirlSpeed"], profile["HandVelocityGain"])
        assert any(f"GPU 입자 {reaction}" in w and "가정값" in w for w in result.warnings)
    assert forces["swirl"][1] > forces["swirl"][0] and forces["push"][0] > forces["push"][1]
    assert forces["scatter"][0] > forces["push"][0] and forces["scatter"][2] > forces["push"][2]
    assert len(set(forces.values())) == 3


def test_sprite_only_reaction_keeps_template_default_forces(tmp_path):
    """glow처럼 객체 행동으로 표현하는 반응은 입자 장 값을 바꾸지 않는다(템플릿 기본값)."""
    result = assemble(spec("glow", "particle"), files(tmp_path, FISH), tmp_path)
    assert result.scene["Profiles"]["GpuFlow"][0] == template_scene("gpu_particles")["Profiles"]["GpuFlow"][0]


def test_unmapped_templates_are_never_chosen_by_mapping():
    """새 템플릿은 생성 쪽 합의 전이라 대응표가 고르지 않는다."""
    table = json.dumps(ue_assemble.mapping())
    assert not [name for name in UNMAPPED if name in table]


@pytest.mark.parametrize("template, data", [("ripple_lake", ONE), ("ripple_star", ONE), ("pigment_flow", THREE)])
def test_unmapped_template_assembles_when_chosen_directly(tmp_path, template, data):
    """TemplateChoice로 직접 지정하면 조립되고 공용 카탈로그 · 고정 무대 검사를 통과한다."""
    result = assemble(spec("ripple", "surface"), files(tmp_path, data), tmp_path, ue_assemble.TemplateChoice(template=template))
    assert result.errors == [] and result.template == template
    assert check_scene(result.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
    targets = {row["Name"] for row in result.scene["ExternalAssets"]}
    if template.startswith("ripple_"):
        assert targets == {"Canvas"}   # 뒤 층 1장이 물결이 이는 원화 캔버스로 들어간다
    else:
        assert targets == {"LayerFar", "LayerMid", "LayerNear", "Canvas"}   # 물감 캔버스 바탕에도 원화


def test_ripple_canvas_warns_when_far_layer_has_no_water_mask(tmp_path):
    """그림 위 물결은 원화 알파를 물 영역으로 쓴다(MaskFromAlpha). 알파가 없으면 그림 전체가 출렁인다고 경고."""
    choice = ue_assemble.TemplateChoice(template="ripple_lake")
    opaque = assemble(spec("ripple", "surface"), files(tmp_path, ONE), tmp_path, choice)
    assert opaque.errors == [] and any("그림 전체가 물로 출렁임" in w for w in opaque.warnings)
    png(tmp_path / "far.png", size=HD, opaque_alpha=True)   # 알파 채널은 있지만 모두 불투명(생성 쪽에서 흔한 형식)
    full = assemble(spec("ripple", "surface"), ArtworkAssets.model_validate(ONE), tmp_path, choice)
    assert any("그림 전체가 물로 출렁임" in w for w in full.warnings)
    masked = assemble(spec("ripple", "surface"), files(tmp_path, ONE, opaque=()), tmp_path, choice)
    assert not any("그림 전체가 물로 출렁임" in w for w in masked.warnings)


@pytest.mark.parametrize("density", [0.0, 1.0])
def test_image_stir_keeps_surface_without_decorative_particles(tmp_path, density):
    result = assemble(spec("stir", "surface", density=density), files(tmp_path, ONE), tmp_path)
    assert not result.errors
    assert [(b["PatternId"], b["ConditionId"]) for b in result.scene["Bindings"]] == [("ImageFlow", "Hands")]
    assert "Current" not in placements(result)
    assert "GpuFlow" not in result.scene["Profiles"]
    assert result.scene["ExternalAssets"] == [{"Name": "Canvas", "ImageFile": "far.png", "TextureParameter": "SourceImage"}]
    assert result.scene["Profiles"]["ImageFlow"][0]["RestoreHalfLifeSeconds"] == 3   # 잔상 기본값(fade). 5면 평소 흐름이 원화를 뭉갬(10/25 시연)


# 손 뗀 뒤 잔상을 정하는 매질 감쇠값과 runtime C++ 검사 범위(라넬 runtime bb36122). (최소, 최대, 0 허용)
DECAY_RANGES = {
    ("FluidField", ("Field", "DyeHalfLifeSeconds")): (0.25, 60, False),   # ArtworkFluidSurface.cpp
    ("ImageFlow", ("RestoreHalfLifeSeconds",)): (0.25, 120, True),        # ArtworkImageFlowBehavior.cpp, 0 = 복원 안 함
    ("ImageFlow", ("Field", "ReleaseDampingPerSecond")): (0, 10, True),
    ("SurfacePaint", ("FadeHalfLifeSeconds",)): (0, 3600, True),          # ArtworkSurfacePaintBehavior.cpp, 0 = 사라지지 않음
    ("SurfaceDeformation", ("Surface", "DampingPerSecond")): (0.5, 30, False),   # ArtworkDeformationSurface.cpp
    ("SurfaceDeformation", ("Surface", "ReturnFrequencyHz")): (0.25, 4, False),
    ("GpuFlow", ("RecoverySeconds",)): (0.05, 20, False),                  # ArtworkGpuFlowBehavior.cpp
}
DATA_FOR = {"ripple_lake": ONE, "ripple_star": ONE, "image_stir": ONE}


def decay_values(scene):
    found = {}
    for (kind, path), limits in DECAY_RANGES.items():
        for row in scene["Profiles"].get(kind, []):
            value = row
            for key in path:
                value = value.get(key, {}) if isinstance(value, dict) else {}
            if not isinstance(value, dict):
                found[(kind, path[-1])] = (value, limits)
    return found


@pytest.mark.parametrize("returns", [None, "instant", "fade", "stay"])
@pytest.mark.parametrize("template", TEMPLATES)
def test_return_mode_decay_stays_in_runtime_range(tmp_path, template, returns):
    """템플릿 8개 × 복귀 방식: 조립 · 정적 검사를 통과하고 감쇠값이 runtime 검사 범위 안이다."""
    result = assemble(spec("ripple", "surface", return_=returns), files(tmp_path, DATA_FOR.get(template, THREE)),
                      tmp_path, ue_assemble.TemplateChoice(template=template))
    assert result.errors == []
    assert check_scene(result.scene, catalog=shared_catalog(), stage=shared_stage()).errors == []
    for (kind, name), (value, (low, high, zero)) in decay_values(result.scene).items():
        assert (zero and value == 0) or low <= value <= high, (kind, name, value)


@pytest.mark.parametrize("template", ["fluid", "pigment_flow", "image_stir", "paint", "deformation", "ripple_lake"])
def test_default_templates_leave_afterimage(tmp_path, template):
    """전시기획서: 사람이 떠나도 잔상 5초 이상. 기본(fade) 화면 반감기가 기준 이상이고 미달 경고가 없다."""
    result = assemble(spec("ripple", "surface"), files(tmp_path, DATA_FOR.get(template, THREE)), tmp_path,
                      ue_assemble.TemplateChoice(template=template))
    half_life, _ = persistence(result.scene)
    assert half_life >= AFTERIMAGE_HALF_LIFE and not any("잔상" in w for w in result.warnings)


@pytest.mark.parametrize("template, returns, path, value", [
    ("fluid", "instant", ("FluidField", "Field", "DyeHalfLifeSeconds"), 1.5),
    ("fluid", "stay", ("FluidField", "Field", "DyeHalfLifeSeconds"), 20),
    ("fluid", "fade", ("FluidField", "Field", "DyeHalfLifeSeconds"), 5),
    ("image_stir", "instant", ("ImageFlow", "RestoreHalfLifeSeconds"), 1.6),
    ("image_stir", "stay", ("ImageFlow", "RestoreHalfLifeSeconds"), 60),   # 0(영영 복원 안 함)이 아니라 천천히
    ("paint", "instant", ("SurfacePaint", "FadeHalfLifeSeconds"), 2),
    ("paint", "stay", ("SurfacePaint", "FadeHalfLifeSeconds"), 60),
    ("gpu_particles", "fade", ("GpuFlow", "RecoverySeconds"), 12),   # 템플릿 기본
    ("gpu_particles", "instant", ("GpuFlow", "RecoverySeconds"), 2.2),
    ("gpu_particles", "stay", ("GpuFlow", "RecoverySeconds"), 20),
    ("deformation", "instant", ("SurfaceDeformation", "Surface", "DampingPerSecond"), 3),
])
def test_return_mode_sets_medium_decay(tmp_path, template, returns, path, value):
    result = assemble(spec("ripple", "surface", return_=returns), files(tmp_path, DATA_FOR.get(template, THREE)),
                      tmp_path, ue_assemble.TemplateChoice(template=template))
    row = result.scene["Profiles"][path[0]][0]
    for key in path[1:]:
        row = row[key]
    assert row == value


@pytest.mark.parametrize("returns, damping, hz, warning", [
    ("instant", 10, 1, None), ("fade", 10, 0.25, None), (None, 10, 0.25, None),
    ("stay", 30, 0.25, "stay:"),   # 반감기 약 8초: 영원히 남지는 않는다
])
def test_press_return_mode_sets_elastic_decay(tmp_path, returns, damping, hz, warning):
    """누름(Elastic)은 instant면 반응 값(감쇠 10 · 복귀 1Hz)으로 튀어 오르고, fade · stay면 복귀 주파수를 낮춰
    출렁임 없이 천천히 가라앉는다(감쇠가 커서 진동하지 않음). 복귀 방식 값은 화면 확인 전이라 PC1 검증 경고."""
    result = assemble(spec("press", "surface", return_=returns), files(tmp_path), tmp_path)
    assert result.template == "deformation"
    surface = result.scene["Profiles"]["SurfaceDeformation"][0]["Surface"]
    assert (surface["Mode"], surface["DampingPerSecond"], surface["ReturnFrequencyHz"]) == ("Elastic", damping, hz)
    afterimage = [w for w in result.warnings if "잔상" in w]
    assert any(warning in w for w in afterimage) if warning else afterimage == []
    assert any("누름 복귀 방식 값" in w for w in result.warnings) == (returns != "instant")


@pytest.mark.parametrize("template", ["ripple_lake", "gpu_particles"])
def test_stay_beyond_runtime_limit_warns(tmp_path, template):
    """물결 · 입자는 runtime 한도로 영원히 남게 할 수 없어 stay면 경고한다."""
    result = assemble(spec("ripple", "surface", return_="stay"), files(tmp_path, DATA_FOR.get(template, THREE)),
                      tmp_path, ue_assemble.TemplateChoice(template=template))
    assert result.errors == [] and any(w.startswith("stay:") for w in result.warnings)


def test_particles_fade_leaves_afterimage(tmp_path):
    """gpu_particles fade: 복귀 12초(화면 반감기 약 2.8초)로 잔상 기준을 넘어 미달 경고가 없다."""
    result = assemble(spec("push", "particle"), files(tmp_path), tmp_path)
    assert result.template == "gpu_particles"
    assert persistence(result.scene)[0] >= AFTERIMAGE_HALF_LIFE
    assert not any("잔상" in w for w in result.warnings)
