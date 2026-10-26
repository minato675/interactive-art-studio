"""UE scene.json 정적 검사(studio.verify.ue_contract) — 지금 runtime 계약(Look · GpuFlow · 고정 무대) 기준.

fixtures/ue/의 장면은 라넬 interactive-runtime cb7f699의 Content/ArtworkTemplates 사본이다
(SceneLookProof.json · GpuLuminousField.json · FlowStir/scene.json → FlowStir.json). runtime이 실제로 받는 장면이므로,
옮긴 규칙이 runtime과 같다면 통과해야 한다.
"""
import copy
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from interactive_controller.validation.scene import validate_scene

from studio.verify import ue_contract
from studio.verify.ue_contract import check_scene, look_errors, stage_from_defaults

FIXTURES = Path(__file__).parent / "fixtures" / "ue"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


LOOK = load("SceneLookProof.json")   # 조명: Lights 1 + 접촉 2 + Spot 1 + Directional 1 + Sky 1 = 6
GPU = load("GpuLuminousField.json")
SPOT = LOOK["Look"]["Environment"]["SpotLights"][0]


@pytest.mark.parametrize("name", ["SceneLookProof.json", "GpuLuminousField.json", "FlowStir.json"])
def test_verified_runtime_templates_pass(name):
    result = check_scene(load(name))
    assert result.errors == []
    assert result.unverified   # 오프라인으로 못 보는 항목은 숨기지 않는다


@pytest.mark.parametrize("family", ["ImageFlow", "GpuFlow", "FlowStir", "Paint"])
def test_shared_map_families_pass_with_catalog_and_stage(family):
    """공용 전시 맵에 새로 합친 계열(ImageFlow · GpuFlow · FlowStir)과 기존 계열이 카탈로그 · 고정 무대까지 통과한다."""
    scene = load(f"RuntimeIntegration-{family}.json")
    result = check_scene(scene, catalog=ue_contract.shared_catalog(), stage=ue_contract.shared_stage())
    assert result.errors == []
    assert any(row["Name"] == "Background" for row in scene["Placements"])   # 설치 레벨 고정 배경 배치


def test_shared_catalog_has_layers_and_new_patterns():
    catalog = ue_contract.shared_catalog()
    for pattern in ("GpuFlow", "ImageFlow", "FluidField", "SurfacePaint"):
        assert catalog.has_pattern(pattern)
    exported = json.loads((ue_contract.DATA / "runtime-integration.json").read_text(encoding="utf-8"))
    assert exported["runtimeCommit"] == ue_contract.runtime_pin()["runtimeCommit"]
    for layer in ("Layer_Far", "Layer_Mid", "Layer_Near"):
        assert exported["objects"][layer]["textureParameters"] == ["SourceImage"]
    assert ue_contract.shared_stage()["bMirrorHorizontal"] is True


def test_unknown_object_fails_with_shared_catalog():
    scene = load("RuntimeIntegration-GpuFlow.json")
    scene["Placements"][0]["ObjectId"] = "NoSuchObject"
    assert check_scene(scene, catalog=ue_contract.shared_catalog()).errors


def test_vendored_controller_contract_is_behind():
    """우리 계약 사본이 필요한 이유: 예전 controller 계약은 Look · GpuFlow를 거부한다."""
    assert validate_scene(GPU).errors and validate_scene(LOOK).errors


def look_check(change):
    scene = copy.deepcopy(LOOK)
    change(scene["Look"])
    return check_scene(scene).errors


def light(look):
    return look["Lights"][0]


def material(look):
    return look["Materials"][0]


def env(look):
    return look["Environment"]


@pytest.mark.parametrize("change, reason", [
    (lambda k: k.update(Extra=1), "profile.Extra: unknown field"),
    (lambda k: k["PostProcess"].update(Exposure=5), "PostProcess.Exposure: expected finite number in [-4, 4]"),
    (lambda k: k["PostProcess"].update(Exposure=10**400), "PostProcess.Exposure: expected finite"),   # 크래시 아님
    (lambda k: k["PostProcess"].update(Lumen=1), "PostProcess.Lumen: expected boolean"),
    (lambda k: k["PostProcess"].update(Bloom=True), "PostProcess.Bloom: expected finite"),            # bool은 숫자 아님
    (lambda k: k.pop("Materials"), "profile.Materials: expected array"),
    (lambda k: k["Interaction"].update(PlaneNormal=[0, 0, 0]), "Interaction.PlaneNormal: must be nonzero"),
    (lambda k: k["Interaction"].update(PlaneNormal=[1e-5, 0, 0]), "Interaction.PlaneNormal: must be nonzero"),
    (lambda k: k["Lights"].append({**light(k), "Id": light(k)["Id"].upper()}), "duplicate Id"),   # 대소문자 무시
    (lambda k: light(k).update(Aim=light(k)["Position"]), "Aim must differ from Position"),
    (lambda k: light(k).update(Aim=[p + 5e-5 for p in light(k)["Position"]]), "Aim must differ from Position"),
    (lambda k: light(k)["Response"].update(Extra=1), "Response.Extra: unknown field"),
    (lambda k: light(k)["Response"].update(RiseRate=0), "Response.RiseRate"),
    (lambda k: k["Lights"].extend([{**light(k), "Id": f"x{i}"} for i in range(8)]), "at most 8 entries"),
    (lambda k: k["Lights"].extend([{**light(k), "Id": f"x{i}"} for i in range(7)])
     or k["ContactLights"]["Colors"].extend([[1, 1, 1]] * 3), "Lights: total light budget is 12"),
    (lambda k: material(k)["Scalars"].update(Wobble=1), "unsupported scalar parameter"),
    (lambda k: material(k)["Scalars"].update(Roughness=1.5), "Materials.Petal.Roughness: expected finite number in [0, 1]"),
    (lambda k: material(k)["Scalars"].update(DyeColorStrength=4.1), "DyeColorStrength: expected finite number in [0, 4]"),
    (lambda k: material(k)["Colors"].update(Glow=[1, 1, 1]), "Materials.Petal.Glow: unknown field"),
    (lambda k: material(k)["Colors"].update(Tint=[1, 2, 0]), "expected finite number in [0, 1]"),
    (lambda k: k["Materials"].append({**material(k), "Target": "PETAL"}), "duplicate target"),   # 대소문자 무시
    (lambda k: material(k).pop("Visible"), "Visible: expected boolean"),
    (lambda k: material(k).update(ResponseGain=8), "base + gain within its range"),        # 0.2 + 8 > 8
    (lambda k: material(k).update(ResponseParameter="Roughness", ResponseGain=0.8), "base + gain"),   # 0.25+0.8>1
    (lambda k: material(k).update(ResponseParameter="Specular"), "explicit scalar base"),   # Scalars에 Specular 없음
    (lambda k: material(k).pop("Response"), "Response is required with response parameter/gain"),
    (lambda k: env(k)["Fog"].update(Density=0.055), "Fog.Density"),
    (lambda k: env(k)["Fog"].update(HeightFalloff=0.001), "Fog.HeightFalloff"),   # float32(0.001f) > 0.001
    (lambda k: env(k)["SpotLights"].append({**SPOT, "Id": SPOT["Id"].upper()}), "duplicate Id"),
    (lambda k: env(k)["SpotLights"][0].update(Aim=SPOT["Position"]), "Aim must differ from Position"),
    (lambda k: env(k)["SpotLights"][0].update(InnerConeDegrees=50), "inner cone must not exceed outer cone"),
    (lambda k: env(k)["SpotLights"][0]["LightFunction"].update(Asset="None"), "registered light function"),
    (lambda k: env(k)["SpotLights"][0]["LightFunction"].update(Strength=2), "LightFunction.Strength"),
    (lambda k: env(k)["Directional"].update(Direction=[0, 0, 0]), "Direction must be nonzero"),
    (lambda k: env(k)["Sky"].update(Extra=1), "Environment.Sky.Extra: unknown field"),
    (lambda k: material(k).update(Target="Nope"), "Placement가 없습니다: Nope"),
])
def test_look_rejects(change, reason):
    errors = look_check(change)
    assert any(reason in error for error in errors), errors


@pytest.mark.parametrize("spots, sky, directional, ok", [
    (7, True, True, True),     # 1 + 2 + 7 + 1 + 1 = 12
    (8, True, True, False),    # 13
    (8, False, True, True),    # Sky를 빼면 12 → Sky도 센다
    (8, True, False, True),    # Directional을 빼면 12 → Directional도 센다
])
def test_light_budget_counts_environment(spots, sky, directional, ok):
    look = copy.deepcopy(LOOK["Look"])
    env(look)["SpotLights"] = [{**SPOT, "Id": f"spot{i}"} for i in range(spots)]
    if not sky:
        env(look).pop("Sky")
    if not directional:
        env(look).pop("Directional")
    errors = look_errors(look)
    assert (errors == []) is ok, errors
    if not ok:
        assert errors == ["Look: Environment: total light budget including Rect/Point is 12"]


def test_look_size_limit_counts_characters():
    look = copy.deepcopy(LOOK["Look"])
    look["Id"] = "가"   # 한글 한 글자 = 1자 (\uXXXX 6자가 아님)
    look["Environment"]["Sky"]["Pad"] = None
    assert len(json.dumps(look, ensure_ascii=False)) < 131072 and look_errors(look)[0].endswith("unknown field")
    big = {**LOOK["Look"], "Id": "x" * 131072}
    assert look_errors(big) == ["Look: profile: invalid JSON or exceeds 128K characters"]
    assert look_errors([]) == ["Look은 공통 조명 프로필 객체여야 합니다."]


def test_look_condition_rules():
    scene = copy.deepcopy(LOOK)
    scene.pop("Look")
    assert check_scene(scene).errors == ["LookConditionId에는 Look 설정이 필요합니다."]
    scene = copy.deepcopy(LOOK)
    scene["LookConditionId"] = "Missing"
    assert "Placement 기준 조건" in check_scene(scene).errors[0]
    scene["LookConditionId"] = "none"
    assert check_scene(scene).errors == ["LookConditionId는 None 대신 생략하세요."]
    scene = copy.deepcopy(LOOK)
    scene["Conditions"][0]["Anchor"] = "EArtworkConditionAnchor::Placement"   # enum 접두어도 같은 값
    assert check_scene(scene).errors == []
    scene["Conditions"][0]["Anchor"] = "CurrentObject"
    assert "Placement 기준 조건" in check_scene(scene).errors[0]


NO_POSITION = {"Name": "Present", "Type": "Boolean", "Region": "InputSurface", "SampleId": "present"}


def test_look_condition_must_provide_position():
    scene = copy.deepcopy(LOOK)
    scene["Conditions"].append(NO_POSITION)
    scene["LookConditionId"] = "Present"
    errors = check_scene(scene).errors
    assert any("위치를 제공하는 Placement 기준 조건" in error for error in errors), errors


def gpu_check(change):
    scene = copy.deepcopy(GPU)
    change(scene)
    return check_scene(scene).errors


def profile(scene):
    return scene["Profiles"]["GpuFlow"][0]


@pytest.mark.parametrize("change, reason", [
    (lambda s: profile(s).update(Count=63), "Count=63"),
    (lambda s: profile(s).update(Count=131073), "Count=131073"),
    (lambda s: profile(s).update(Seed=-1), "Seed=-1"),
    (lambda s: profile(s).pop("System"), "System · Material이 필요"),
    (lambda s: profile(s).update(AreaMin="(X=0.5,Y=0.1)", AreaMax="(X=0.4,Y=0.9)"), "정규화 영역"),
    (lambda s: profile(s).update(AreaMax="(X=1.1,Y=0.9)"), "정규화 영역"),
    (lambda s: profile(s).update(SizeRangeCm="(X=5,Y=2)"), "SizeRangeCm"),
    (lambda s: profile(s).update(AmbientVelocity="(X=900,Y=900)"), "AmbientVelocity"),
    (lambda s: profile(s).update(RestColor="(R=1,G=1,B=1,A=2)"), "RestColor"),
    (lambda s: profile(s).update(ActiveColor="(R=17,G=1,B=1,A=1)"), "ActiveColor"),
    (lambda s: profile(s).update(FlowFieldGain=1), "FlowFieldPlacementId가 필요"),
    (lambda s: profile(s).update(FlowFieldGain=1, FlowFieldPlacementId="none"), "FlowFieldPlacementId가 필요"),
    (lambda s: profile(s).update(CurlSpeed=1600), "CurlSpeed=1600"),
    (lambda s: profile(s).update(TrailWidthCm=21), "TrailWidthCm=21"),
    (lambda s: profile(s).update(TrailSeconds=0.04), "TrailSeconds="),        # float32(0.04) < 0.04
    (lambda s: profile(s).update(ResponseSeconds=0.02), "ResponseSeconds="),
    (lambda s: s["Bindings"][0].pop("ConditionId"), "입력 조건이 필요"),
])
def test_gpu_flow_rejects(change, reason):
    errors = gpu_check(change)
    assert any(reason in error for error in errors), errors


def test_gpu_flow_lowercase_pattern_key_is_still_checked():
    scene = copy.deepcopy(GPU)
    scene["Profiles"] = {"gpuflow": scene["Profiles"]["GpuFlow"]}
    scene["Bindings"][0]["PatternId"] = "gpuflow"
    profile_row = scene["Profiles"]["gpuflow"][0]
    profile_row["Count"] = 10
    assert any("Count=10" in error for error in check_scene(scene).errors)


def test_gpu_flow_needs_position_condition():
    scene = copy.deepcopy(GPU)
    scene["Conditions"].append(NO_POSITION)
    scene["Bindings"][0]["ConditionId"] = "Present"
    assert any("ScalarRange/Boolean과 연결할 수 없습니다" in error for error in check_scene(scene).errors)


def add_stage(scene, name, **extra):
    scene["Placements"].append({**scene["Placements"][0], "Name": name, **extra})


def bind(scene, name, **target):
    scene["Bindings"].append({**scene["Bindings"][0], "Name": name, **target})


def test_gpu_particle_budget_multiplies_targets():
    scene = copy.deepcopy(GPU)
    profile(scene)["Count"] = 131072
    add_stage(scene, "Stage2")
    bind(scene, "Flow2", PlacementId="Stage2")
    assert check_scene(scene).errors == []   # 131072 × 2 = 262144 = 예산 그대로
    add_stage(scene, "Stage3")
    bind(scene, "Flow3", PlacementId="Stage3")
    assert any("GPU 입자 예산" in error for error in check_scene(scene).errors)


def test_gpu_particle_budget_scene_scope():
    scene = copy.deepcopy(GPU)
    profile(scene)["Count"] = 131072
    scene["Placements"][0]["bAllowSceneReaction"] = True
    add_stage(scene, "Stage2", bAllowSceneReaction=True)
    add_stage(scene, "Stage3", bAllowSceneReaction=True)
    scene["Bindings"][0].pop("PlacementId")
    scene["Bindings"][0]["TargetScope"] = "EArtworkTargetScope::Scene"   # enum 접두어 · 대상 3개
    assert any("GPU 입자 예산" in error for error in check_scene(scene).errors)


def test_gpu_flow_same_target_twice():
    scene = copy.deepcopy(GPU)
    bind(scene, "Flow2")
    assert any("여러 행동이 연결됐습니다" in error for error in check_scene(scene).errors)


def test_fixed_stage():
    stage = stage_from_defaults()
    assert stage["FieldOfView"] == 70 and stage["CameraLocation"] == [2200, 0, 100]
    assert check_scene(GPU, stage=stage).errors == []   # 생략한 칸은 헤더 기본값
    scene = copy.deepcopy(GPU)
    scene["Scene"][0]["CameraLocation"] = "(X=2200,Y=0,Z=100)"   # 같은 값을 UE 문자열로
    scene["Scene"][0]["AspectRatio"] = 16 / 9
    assert check_scene(scene, stage=stage).errors == []
    scene["Scene"][0]["FieldOfView"] = 60
    scene["Scene"][0]["bMirrorHorizontal"] = True
    assert check_scene(scene, stage=stage).errors == [
        "Scene.FieldOfView=60.0가 고정 무대 값 70.0과 다릅니다.",
        "Scene.bMirrorHorizontal=True가 고정 무대 값 False과 다릅니다."]
    assert check_scene(GPU, stage={"Bogus": 1, "FieldOfView": [70], "CameraLocation": 5}).errors == [
        "stage.json: Scene 행에 없는 칸 Bogus",
        "stage.json: FieldOfView 값의 모양이 Scene 행과 다릅니다([70])",
        "stage.json: CameraLocation 값의 모양이 Scene 행과 다릅니다(5)"]
    scene["Scene"].append({"Name": "Second"})
    assert check_scene(scene, stage=stage).errors == ["Scene은 정확히 한 행이어야 합니다."]   # controller가 먼저 거름


def test_contract_errors_stop_before_extra_checks():
    scene = copy.deepcopy(GPU)
    scene["Unknown"] = 1
    profile(scene)["Count"] = 1
    assert check_scene(scene).errors == ["알 수 없는 장면 필드: Unknown"]


def test_catalog_version_changes_with_content(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text('{"catalogId": "x"}', encoding="utf-8")
    first = ue_contract.catalog_version(path)
    path.write_text('{"catalogId": "y"}', encoding="utf-8")
    assert len(first) == 64 and ue_contract.catalog_version(path) != first


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git이 없는 환경(CI uv 이미지)에서는 임시 저장소를 만들 수 없음")
def test_runtime_drift(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "test")
    (tmp_path / "Rows.h").write_text("struct A {};", encoding="utf-8")
    (tmp_path / "Look.cpp").write_text("// look", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "pin")
    pin = {"contractFiles": {name: git(tmp_path, "rev-parse", f"HEAD:{name}") for name in ("Rows.h", "Look.cpp")}}
    monkeypatch.setattr(ue_contract, "runtime_pin", lambda: pin)
    assert ue_contract.runtime_drift(tmp_path) == []
    (tmp_path / "Look.cpp").write_text("// look v2", encoding="utf-8")
    git(tmp_path, "commit", "-q", "-am", "change")
    assert ue_contract.runtime_drift(tmp_path) == ["Look.cpp"]
    git(tmp_path, "rm", "-q", "Rows.h")
    git(tmp_path, "commit", "-q", "-m", "remove")
    assert ue_contract.runtime_drift(tmp_path) == ["Rows.h", "Look.cpp"]


def test_pinned_contract_matches_runtime_commit():
    pin = ue_contract.runtime_pin()
    assert ue_contract.scene_contract()["runtimeCommit"] == pin["runtimeCommit"]
    assert list(pin["contractFiles"]) == ue_contract.CONTRACT_FILES
    assert "GpuFlow" in ue_contract.scene_contract()["patterns"]


@pytest.mark.parametrize("profiles, expected", [
    ({"FluidField": [{"Field": {"DyeHalfLifeSeconds": 5}}]}, (5, "FluidField")),
    ({"FluidField": [{"Field": {}}]}, (3.0, "FluidField")),   # 헤더 기본값
    ({"ImageFlow": [{"RestoreHalfLifeSeconds": 0}]}, (math.inf, "ImageFlow")),   # 0이면 복원하지 않음
    ({"SurfacePaint": [{"FadeHalfLifeSeconds": 10}]}, (10, "SurfacePaint")),
    ({"SurfacePaint": [{}]}, (math.inf, "SurfacePaint")),   # 기본 0 = 사라지지 않음
    ({"SurfaceDeformation": [{"Surface": {"DampingPerSecond": 0.5}}]}, (2 * math.log(2) / 0.5, "SurfaceDeformation")),
    # 감쇠가 커서 진동 없이 돌아옴: 느린 쪽 (d − √(d² − 4ω²))/2
    ({"SurfaceDeformation": [{"Surface": {"DampingPerSecond": 10, "ReturnFrequencyHz": 0.25}}]},
     (math.log(2) / ((10 - math.sqrt(100 - 4 * (math.pi / 2) ** 2)) / 2), "SurfaceDeformation")),
    ({"GpuFlow": [{"RecoverySeconds": 15}]}, (math.log(2) / 3 * 15, "GpuFlow")),
    ({"GpuFlow": [{}], "FluidField": [{"Field": {"DyeHalfLifeSeconds": 1.5}}]}, (1.5, "FluidField")),   # 가장 오래 남는 것
])
def test_persistence_half_life_per_medium(profiles, expected):
    """손 뗀 뒤 화면 반감기: 색 · 그림 복원 · 그리기 자국은 값 그대로, 물결 1.386/감쇠, GPU 입자 0.231 × 복귀 시간."""
    half_life, medium = ue_contract.persistence({"Profiles": profiles})
    assert medium == expected[1] and half_life == pytest.approx(expected[0])


def test_persistence_unknown_without_decay_medium():
    assert ue_contract.persistence({"FakeMode": "react"}) is None
    assert ue_contract.persistence({"Profiles": {"MaterialScalar": [{"Name": "Glow"}]}}) is None
