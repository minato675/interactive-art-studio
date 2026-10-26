"""UE scene.json 정적 검사 — 지금 runtime 계약(ue_contract/runtime-pin.json의 커밋) 기준.

vendored controller 검사기는 예전 runtime 계약이라 Look · GpuFlow를 모른다. controller/는 라넬 사본이라 고치지 않고,
  - 최신 runtime 헤더에서 뽑은 ue_contract/scene-structs.json을 controller validate_scene에 넘기고
  - validate_scene이 모르는 Look · LookConditionId는 떼어 내 여기서 runtime 파서(LookProfile · LookEnvironment)대로 검사하고
  - GpuFlow 프로필 범위 · 조건 · 출력 중복 · 장면 GPU 입자 예산, 고정 무대(stage.json) 일치를 더 본다.
표 값은 controller의 행 읽기(_Reader · _read_table)로 정규화한다(UE 문자열 · enum 접두어 · float32 · 헤더 기본값 ·
대소문자 무시). vendored 사본의 내부 함수지만 같은 저장소에 고정된 사본이라 그대로 쓴다.
UE JSON 읽기는 숫자 · 문자열을 서로 바꿔 받기도 하고 Look 재질 값 이름(FName)의 대소문자도 무시하지만,
여기서는 받지 않는다(생성기 출력을 더 엄격하게 막는 쪽).
오프라인으로 알 수 없는 것(자산 실제 등록, Niagara 계약, 다른 행동과 Look의 재질 출력 중복)은 unverified로 남기고
PC1 실행 검증이 맡는다.

  check_scene       계약 · Look · GpuFlow · 무대 검사 → SceneCheck(errors, unverified)
  catalog_version   카탈로그 JSON의 sha256 (템플릿 호환 확인용)
  runtime_drift     핀 이후 바뀐 runtime 계약 파일 (runtime clone 필요)
  persistence       손 뗀 뒤 화면 반감기(초)와 그 매질 — 조립 경고와 실행 검증이 같이 쓴다
  refresh           runtime clone에서 scene-structs.json · runtime-pin.json을 다시 뜬다
                    uv run python -m studio.verify.ue_contract <runtime clone>  (검사 규칙도 함께 다시 맞출 것)
"""
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from interactive_controller.validation.catalog import load_catalog
from interactive_controller.validation.scene import (
    _float32,
    _is_number,
    _key,
    _none,
    _read_table,
    _Reader,
    validate_scene,
)

DATA = Path(__file__).parent / "ue_contract"
CONTRACT_FILES = [   # 이 모듈의 규칙이 옮겨 온 runtime 파일 (헤더 추출 대상 전체는 scene-structs.json이 맡음)
    "Source/Art/ContentRuntime/Data/ArtworkSceneRows.h",
    "Source/Art/ContentRuntime/Data/ArtworkSceneData.h",
    "Source/Art/ContentRuntime/Data/ArtworkSceneData.cpp",
    "Source/Art/ContentRuntime/Data/ArtworkSceneLookData.cpp",
    "Source/Art/ContentRuntime/Look/LookProfile.cpp",
    "Source/Art/ContentRuntime/Look/LookProfileReader.h",
    "Source/Art/ContentRuntime/Look/LookEnvironment.h",
    "Source/Art/ContentRuntime/Look/LookEnvironment.cpp",
    "Source/Art/ContentRuntime/Conditions/ArtworkSignalCondition.cpp",
    "Source/Art/ContentRuntime/Behaviors/ArtworkGpuFlowBehavior.h",
    "Source/Art/ContentRuntime/Behaviors/ArtworkGpuFlowBehavior.cpp",
]
LOOK_FIELDS = ("Look", "LookConditionId")
GPU_PARTICLE_BUDGET = 262144   # ArtworkSceneData.h MaximumGpuParticleBudget
LIGHT_BUDGET = 12
SMALL = 1e-8      # FVector::Normalize: 제곱합이 이 이하면 0벡터
TOLERANCE = 1e-4  # FVector::Equals 기본 오차


@dataclass
class SceneCheck:
    errors: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)


@cache
def scene_contract() -> dict:
    return json.loads((DATA / "scene-structs.json").read_text(encoding="utf-8"))


@cache
def runtime_pin() -> dict:
    return json.loads((DATA / "runtime-pin.json").read_text(encoding="utf-8"))


def catalog_version(path: Path = DATA / "runtime-integration.json") -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@cache
def shared_catalog():
    """공용 전시 맵(RuntimeIntegrationProof) 카탈로그. ue/tools/export_catalog.py로 UE에서 내보낸 것."""
    return load_catalog(DATA / "runtime-integration.json")


@cache
def shared_stage() -> dict:
    """공용 전시 맵 고정 무대(Scene 행 값). 헤더 기본값 + 좌우 반전(Build-RuntimeIntegrationProof.py)."""
    return json.loads((DATA / "stage.json").read_text(encoding="utf-8"))


def runtime_drift(runtime_root: Path, commit: str = "HEAD") -> list[str]:
    """핀을 뜬 뒤 바뀐 계약 파일. 비어 있으면 검사 규칙이 그 runtime과 맞는다."""
    def blob(path):
        result = subprocess.run(["git", "rev-parse", f"{commit}:{path}"], cwd=runtime_root,
                                capture_output=True, text=True, check=False)   # 없는 파일도 '바뀜'으로 본다
        return result.stdout.strip() if result.returncode == 0 else None
    return [path for path, pinned in runtime_pin()["contractFiles"].items() if blob(path) != pinned]


def refresh(runtime_root: Path) -> str:
    """runtime clone의 HEAD로 계약 사본과 핀을 다시 쓴다. 헤더 추출은 controller 스크립트를 그대로 쓴다."""
    script = Path(__file__).resolve().parents[5] / "controller/scripts/extract_scene_structs.py"
    spec = importlib.util.spec_from_file_location("extract_scene_structs", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    contract = module.extract(Path(runtime_root))

    def git(*args):
        return subprocess.run(["git", *args], cwd=runtime_root, capture_output=True, text=True, check=True).stdout.strip()

    commit = git("rev-parse", "HEAD")
    pin = {"runtimeRepository": "https://github.com/lanael-studio/interactive-runtime", "runtimeCommit": commit,
           "contractFiles": {path: git("rev-parse", f"{commit}:{path}") for path in CONTRACT_FILES},
           "notes": "studio.verify.ue_contract가 따라가는 runtime 계약 파일. 바뀌면 runtime_drift()가 알리고, "
                    "검사 규칙과 scene-structs.json을 다시 맞춘다."}
    for name, data in (("scene-structs.json", contract), ("runtime-pin.json", pin)):
        (DATA / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    scene_contract.cache_clear()
    runtime_pin.cache_clear()
    return commit


# ---- Look: runtime LookProfile::Parse · LookEnvironment::Parse · ArtworkLook::FProfileReader ----

class _LookReader:
    """FProfileReader와 같은 규칙. 첫 오류 하나만 남기고, 경계는 C++처럼 float32로 비교한다."""

    def __init__(self):
        self.error = None

    def fail(self, path, reason):
        self.error = self.error or f"{path}: {reason}"

    def object(self, parent, key, path):
        if isinstance(parent.get(key), dict):
            return parent[key]
        self.fail(f"{path}.{key}", "expected object")
        return {}

    def keys(self, obj, path, allowed):
        for key in obj:
            if key not in allowed:
                self.fail(f"{path}.{key}", "unknown field")

    def string(self, obj, key, path):
        value = obj.get(key)
        if not isinstance(value, str) or not 1 <= len(value) <= 64:
            self.fail(f"{path}.{key}", "expected nonempty string, at most 64 characters")
            return ""
        return value

    def boolean(self, obj, key, path):
        if type(obj.get(key)) is not bool:
            self.fail(f"{path}.{key}", "expected boolean")
            return False
        return obj[key]

    def number(self, value, low, high, path):
        """C++ Number(float Minimum, float Maximum): double 값을 float32 경계와 비교하고 float32로 돌려준다."""
        low, high = _float32(low), _float32(high)
        if not _is_number(value) or not low <= value <= high:
            self.fail(path, f"expected finite number in [{low:g}, {high:g}]")
            return low
        return _float32(value)

    def field(self, obj, key, low, high, path):
        return self.number(obj.get(key), low, high, f"{path}.{key}")

    def array(self, obj, key, maximum, path):
        value = obj.get(key)
        if not isinstance(value, list) or len(value) > maximum:
            self.fail(f"{path}.{key}", f"expected array, at most {maximum} entries")
            return []
        return value

    def vector(self, obj, key, low, high, path):
        values = self.array(obj, key, 3, path)
        if len(values) != 3:
            self.fail(f"{path}.{key}", "expected three numbers")
            return [0.0, 0.0, 0.0]
        return [self.number(v, low, high, f"{path}.{key}") for v in values]

    def color(self, obj, key, path):
        return self.vector(obj, key, 0, 1, path)

    def entry(self, value, path):
        if isinstance(value, dict):
            return value
        self.fail(path, "expected object")
        return {}

    def response(self, parent, path):
        where = f"{path}.Response"
        obj = self.object(parent, "Response", path)
        self.keys(obj, where, {"Center", "RadiusCm", "RiseRate", "FallRate"})
        self.vector(obj, "Center", -100000, 100000, where)
        self.field(obj, "RadiusCm", 1, 10000, where)
        self.field(obj, "RiseRate", 0.01, 30, where)
        self.field(obj, "FallRate", 0.01, 30, where)

    def unique(self, seen, name, path):
        """TSet<FString> · TSet<FName>은 대소문자를 무시한다."""
        if name.lower() in seen:
            self.fail(path, "duplicate Id" if not path.startswith("Materials") else "duplicate target")
        seen.add(name.lower())


SCALAR_RANGES = {"Roughness": (0, 1), "Metallic": (0, 1), "Specular": (0, 1),
                 "EmissiveStrength": (0, 8), "NormalStrength": (0, 8), "DyeColorStrength": (0, 4)}


def _zero(vector):
    return sum(v * v for v in vector) <= SMALL


def _equal(a, b):
    return all(abs(x - y) <= TOLERANCE for x, y in zip(a, b, strict=True))


def _light_function(reader, parent, path):
    if "LightFunction" not in parent:
        return
    where = f"{path}.LightFunction"
    obj = reader.object(parent, "LightFunction", path)
    reader.keys(obj, where, {"Asset", "ScaleCm", "Strength", "Speed"})
    if _none(reader.string(obj, "Asset", where)):
        reader.fail(where, "Asset must name a registered light function")
    reader.vector(obj, "ScaleCm", 1, 10000, where)
    reader.field(obj, "Strength", 0, 1, where)
    reader.field(obj, "Speed", 0, 5, where)


def _environment(reader, root):
    """LookEnvironment::Parse. 반환: LightCount() = Spot 수 + Directional + Sky."""
    if "Environment" not in root:
        return 0
    env = reader.object(root, "Environment", "profile")
    reader.keys(env, "Environment", {"SpotLights", "Directional", "Sky", "Fog"})
    lights, ids = 0, set()
    if "SpotLights" in env:
        for value in reader.array(env, "SpotLights", 8, "Environment"):
            obj = reader.entry(value, "Environment.SpotLights")
            reader.keys(obj, "SpotLights", {"Id", "Position", "Aim", "Color", "Lumens", "AttenuationRadiusCm",
                                            "InnerConeDegrees", "OuterConeDegrees", "VolumetricScattering",
                                            "LightFunction"})
            spot_id = reader.string(obj, "Id", "SpotLights")
            path = f"Environment.SpotLights.{spot_id}"
            reader.unique(ids, spot_id, path)
            position = reader.vector(obj, "Position", -100000, 100000, path)
            if _equal(position, reader.vector(obj, "Aim", -100000, 100000, path)):
                reader.fail(path, "Aim must differ from Position")
            reader.color(obj, "Color", path)
            reader.field(obj, "Lumens", 0, 20000, path)
            reader.field(obj, "AttenuationRadiusCm", 1, 10000, path)
            inner = reader.field(obj, "InnerConeDegrees", 0, 80, path)
            outer = reader.field(obj, "OuterConeDegrees", 1, 85, path)
            if inner > outer:
                reader.fail(path, "inner cone must not exceed outer cone")
            reader.field(obj, "VolumetricScattering", 0, 4, path)
            _light_function(reader, obj, path)
            lights += 1
    if "Directional" in env:
        path = "Environment.Directional"
        obj = reader.object(env, "Directional", "Environment")
        reader.keys(obj, path, {"Direction", "Color", "Lux", "VolumetricScattering", "LightFunction"})
        if _zero(reader.vector(obj, "Direction", -1, 1, path)):
            reader.fail(path, "Direction must be nonzero")
        reader.color(obj, "Color", path)
        reader.field(obj, "Lux", 0, 100000, path)
        reader.field(obj, "VolumetricScattering", 0, 4, path)
        _light_function(reader, obj, path)
        lights += 1
    if "Sky" in env:
        path = "Environment.Sky"
        obj = reader.object(env, "Sky", "Environment")
        reader.keys(obj, path, {"IntensityScale", "Color"})
        reader.field(obj, "IntensityScale", 0, 10, path)
        reader.color(obj, "Color", path)
        lights += 1
    if "Fog" in env:
        path = "Environment.Fog"
        obj = reader.object(env, "Fog", "Environment")
        reader.keys(obj, path, {"HeightCm", "Density", "HeightFalloff", "Color", "Volumetric",
                                "ScatteringDistribution", "ViewDistanceCm"})
        reader.field(obj, "HeightCm", -10000, 10000, path)
        reader.field(obj, "Density", 0, 0.05, path)
        reader.field(obj, "HeightFalloff", 0.001, 2, path)
        reader.color(obj, "Color", path)
        reader.boolean(obj, "Volumetric", path)
        reader.field(obj, "ScatteringDistribution", -0.9, 0.9, path)
        reader.field(obj, "ViewDistanceCm", 100, 20000, path)
    return lights


def _materials(reader, look):
    targets = set()
    for value in reader.array(look, "Materials", 32, "profile"):
        obj = reader.entry(value, "Materials")
        reader.keys(obj, "Materials", {"Target", "Visible", "Scalars", "Colors", "Response", "ResponseParameter",
                                       "ResponseGain"})
        target = reader.string(obj, "Target", "Materials")
        path = f"Materials.{target}"
        reader.unique(targets, target, path)
        reader.boolean(obj, "Visible", path)
        scalars, parsed = reader.object(obj, "Scalars", path), {}
        for name, number in scalars.items():
            if name not in SCALAR_RANGES:
                reader.fail(f"{path}.{name}", "unsupported scalar parameter")
                continue
            parsed[name] = reader.number(number, *SCALAR_RANGES[name], f"{path}.{name}")
        colors = reader.object(obj, "Colors", path)
        reader.keys(colors, path, {"Tint", "BaseColor"})
        for name in colors:
            reader.color(colors, name, path)
        if "Response" in obj:
            reader.response(obj, path)
            parameter = reader.string(obj, "ResponseParameter", path)
            gain = reader.field(obj, "ResponseGain", -8, 8, path)
            base = parsed.get(parameter)
            low, high = SCALAR_RANGES.get(parameter, (0, -1))
            if base is None or not _float32(low) <= _float32(base + gain) <= _float32(high):
                reader.fail(path, "response parameter needs an explicit scalar base and base + gain within its range")
        elif "ResponseParameter" in obj or "ResponseGain" in obj:
            reader.fail(path, "Response is required with response parameter/gain")


def look_errors(look) -> list[str]:
    """FLookProfile::Parse와 같은 규칙. 부분 설정은 runtime이 받지 않으므로 전체 프로필이어야 한다."""
    if not isinstance(look, dict):
        return ["Look은 공통 조명 프로필 객체여야 합니다."]
    if len(json.dumps(look, ensure_ascii=False, separators=(",", ":"))) > 131072:
        return ["Look: profile: invalid JSON or exceeds 128K characters"]
    reader = _LookReader()
    reader.keys(look, "profile", {"SchemaVersion", "Id", "Interaction", "PostProcess", "Lights", "ContactLights",
                                  "Materials", "Environment"})
    reader.field(look, "SchemaVersion", 1, 1, "profile")
    reader.string(look, "Id", "profile")
    interaction = reader.object(look, "Interaction", "profile")
    reader.keys(interaction, "Interaction", {"IgnoreDepth", "PlaneNormal"})
    reader.boolean(interaction, "IgnoreDepth", "Interaction")
    if _zero(reader.vector(interaction, "PlaneNormal", -1, 1, "Interaction")):
        reader.fail("Interaction.PlaneNormal", "must be nonzero")
    post = reader.object(look, "PostProcess", "profile")
    reader.keys(post, "PostProcess", {"Lumen", "Exposure", "Bloom"})
    reader.boolean(post, "Lumen", "PostProcess")
    reader.field(post, "Exposure", -4, 4, "PostProcess")
    reader.field(post, "Bloom", 0, 2, "PostProcess")
    light_ids = set()
    lights = reader.array(look, "Lights", 8, "profile")
    for value in lights:
        obj = reader.entry(value, "Lights")
        reader.keys(obj, "Lights", {"Id", "Position", "Aim", "Color", "Lumens", "Ambient", "AttenuationRadiusCm",
                                    "WidthCm", "HeightCm", "Response"})
        light_id = reader.string(obj, "Id", "Lights")
        path = f"Lights.{light_id}"
        reader.unique(light_ids, light_id, path)
        position = reader.vector(obj, "Position", -100000, 100000, path)
        if _equal(position, reader.vector(obj, "Aim", -100000, 100000, path)):
            reader.fail(path, "Aim must differ from Position")
        reader.color(obj, "Color", path)
        reader.field(obj, "Lumens", 0, 10000, path)
        reader.field(obj, "Ambient", 0, 1, path)
        reader.field(obj, "AttenuationRadiusCm", 1, 10000, path)
        reader.field(obj, "WidthCm", 1, 2000, path)
        reader.field(obj, "HeightCm", 1, 2000, path)
        reader.response(obj, path)
    contacts = reader.object(look, "ContactLights", "profile")
    reader.keys(contacts, "ContactLights", {"Colors", "Offset", "Lumens", "AttenuationRadiusCm", "SourceRadiusCm"})
    contact_colors = reader.array(contacts, "Colors", 8, "ContactLights")
    for value in contact_colors:
        reader.color({"Color": value}, "Color", "ContactLights")
    reader.vector(contacts, "Offset", -10000, 10000, "ContactLights")
    reader.field(contacts, "Lumens", 0, 5000, "ContactLights")
    reader.field(contacts, "AttenuationRadiusCm", 1, 10000, "ContactLights")
    reader.field(contacts, "SourceRadiusCm", 0, 1000, "ContactLights")
    if len(lights) + len(contact_colors) > LIGHT_BUDGET:
        reader.fail("Lights", f"total light budget is {LIGHT_BUDGET}")
    _materials(reader, look)
    if reader.error:
        return [f"Look: {reader.error}"]
    environment_lights = _environment(reader, look)
    if reader.error:
        return [f"Look: {reader.error}"]
    if len(lights) + len(contact_colors) + environment_lights > LIGHT_BUDGET:
        return [f"Look: Environment: total light budget including Rect/Point is {LIGHT_BUDGET}"]
    return []


# ---- 장면 표 위의 규칙 (UArtworkSceneData) ----

def _tables(scene) -> tuple[dict, dict]:
    """validate_scene을 통과한 장면을 controller 행 읽기로 정규화한다. ({표: {키: 행}}, {패턴 키: {키: 행}})."""
    contract, errors = scene_contract(), []
    reader = _Reader(contract, errors)
    tables = {table: _read_table(reader, scene.get(table, []), struct, table, errors)
              for table, struct in contract["tables"].items()}
    patterns = {_key(pattern): struct for pattern, struct in contract["patterns"].items()}
    profiles = {_key(pattern): _read_table(reader, rows, patterns[_key(pattern)], pattern, errors)
                for pattern, rows in scene.get("Profiles", {}).items()}
    return tables, profiles


def _provides_position(condition) -> bool:
    """FArtworkSignalCondition::ProvidesPosition."""
    return condition["Type"] not in ("ScalarRange", "Boolean")


def _look_scene_errors(scene, tables) -> list[str]:
    """UArtworkSceneData::ReadLook · ValidateLook 중 장면 표로 확인할 수 있는 것."""
    if "LookConditionId" in scene:
        condition = scene["LookConditionId"]
        if not isinstance(condition, str) or not 1 <= len(condition) <= 64:
            return ["LookConditionId는 1~64자 조건 ID여야 합니다."]
        if _none(condition):
            return ["LookConditionId는 None 대신 생략하세요."]
        if "Look" not in scene:
            return ["LookConditionId에는 Look 설정이 필요합니다."]
    if "Look" not in scene:
        return []
    errors = look_errors(scene["Look"])
    if errors:
        return errors
    if "LookConditionId" in scene:
        row = tables["Conditions"].get(_key(scene["LookConditionId"]))
        if row is None or not _provides_position(row) or row["Anchor"] != "Placement":
            return ["LookConditionId에는 위치를 제공하는 Placement 기준 조건이 필요합니다. 중심은 장면 SurfaceOrigin입니다."]
    return [f"Look.Materials.Target에 해당하는 Placement가 없습니다: {m['Target']}"
            for m in scene["Look"]["Materials"] if _key(m["Target"]) not in tables["Placements"]]


GPU_FLOW_RANGES = {
    "Count": (64, 131072), "Seed": (0, 2**31 - 1), "ForwardOffsetCm": (0, 2000), "DepthCm": (0, 500),
    "CurlSpeed": (0, 1500), "CurlScaleCm": (20, 5000), "CurlPeriodSeconds": (1, 120), "ReactionRadiusCm": (10, 5000),
    "RepulsionSpeed": (-2000, 2000), "SwirlSpeed": (-2000, 2000), "HandVelocityGain": (0, 4),
    "GlobalMotionGain": (0, 2), "ResponseSeconds": (0.02, 5), "RecoverySeconds": (0.05, 20),
    "MaximumSpeed": (1, 4000), "LifetimeSeconds": (1, 60), "TrailSeconds": (0.04, 2), "TrailWidthCm": (0, 20),
    "SceneEnergyGain": (0, 2), "FlowFieldGain": (0, 4),
}
GPU_FLOW_CHANNEL = "ParticleField.State"   # GetWriteChannels


def _in_range(value, low, high):
    """InRange(double Value, double Minimum, double Maximum). 값은 이미 float32로 저장된 것."""
    return math.isfinite(value) and low <= value <= high


def _gpu_flow_profile_errors(row) -> list[str]:
    """FArtworkGpuFlowProfileRow::Validate."""
    name = f"GpuFlow/{row['Name']}"
    errors = [f"{name}: System · Material이 필요합니다."] if not row["System"] or not row["Material"] else []
    errors += [f"{name}.{key}={row[key]}가 UE 범위 {low}~{high} 밖입니다."
               for key, (low, high) in GPU_FLOW_RANGES.items() if not _in_range(row[key], low, high)]
    low, high = row["AreaMin"], row["AreaMax"]
    if not (min(low.values()) >= 0 and max(high.values()) <= 1 and high["X"] > low["X"] and high["Y"] > low["Y"]):
        errors.append(f"{name}: 정규화 영역은 0≤AreaMin<AreaMax≤1입니다.")
    size = row["SizeRangeCm"]
    if not (_in_range(size["X"], 0.1, 30) and _in_range(size["Y"], size["X"], 50)):
        errors.append(f"{name}: SizeRangeCm은 X 0.1~30, Y X~50입니다.")
    if math.hypot(row["AmbientVelocity"]["X"], row["AmbientVelocity"]["Y"]) > 1000:
        errors.append(f"{name}: AmbientVelocity 크기는 1000 이하입니다.")
    for color in ("RestColor", "ActiveColor"):
        rgba = row[color]
        if not all(_in_range(rgba[c], 0, 16) for c in "RGB") or not _in_range(rgba["A"], 0, 1):
            errors.append(f"{name}.{color}: HDR RGB 0~16 · Alpha 0~1입니다.")
    if row["FlowFieldGain"] > 0 and _none(row["FlowFieldPlacementId"]):
        errors.append(f"{name}: FlowFieldGain이 0보다 크면 FlowFieldPlacementId가 필요합니다.")
    return errors


def _targets(binding, placements) -> list[str]:
    """GetTargets: Scene은 bAllowSceneReaction 배치 전부, 아니면 배치 · 그룹 ID로 고른다."""
    if binding["TargetScope"] == "Scene":
        return [key for key, row in placements.items() if row["bAllowSceneReaction"]]
    return [key for key, row in placements.items()
            if (not _none(binding["PlacementId"]) and _key(binding["PlacementId"]) == key)
            or (not _none(binding["GroupId"]) and _key(binding["GroupId"]) == _key(row["GroupId"]))]


def _gpu_flow_errors(tables, profiles) -> list[str]:
    rows = profiles.get(_key("GpuFlow"), {})
    errors = [error for row in rows.values() for error in _gpu_flow_profile_errors(row)]
    total, writes = 0, set()
    for binding in tables["Bindings"].values():
        if _key(binding["PatternId"]) != _key("GpuFlow"):
            continue
        name = f"연결 '{binding['Name']}'"
        condition = tables["Conditions"].get(_key(binding["ConditionId"])) if not _none(binding["ConditionId"]) else None
        if _none(binding["ConditionId"]):
            errors.append(f"{name}: 패턴 'GpuFlow'에는 입력 조건이 필요합니다.")
        elif condition is not None and not _provides_position(condition):
            errors.append(f"{name}: 패턴 'GpuFlow'는 실제 위치 입력이 필요하므로 ScalarRange/Boolean과 연결할 수 없습니다.")
        targets = _targets(binding, tables["Placements"])
        profile = rows.get(_key(binding["ProfileId"]))   # 없는 프로필은 validate_scene이 거름
        total += (profile["Count"] if profile else 0) * len(targets)
        for target in targets:
            if target in writes:
                name = tables["Placements"][target]["Name"]
                errors.append(f"배치 '{name}'의 출력 '{GPU_FLOW_CHANNEL}'에 여러 행동이 연결됐습니다.")
            writes.add(target)
    if total > GPU_PARTICLE_BUDGET:
        errors.append(f"장면 GPU 입자 예산 {GPU_PARTICLE_BUDGET}개를 초과합니다({total}).")
    return errors


def _stage_errors(tables, stage) -> list[str]:
    """고정 무대: Scene 행(생략한 칸은 헤더 기본값)이 stage.json 값과 같아야 한다. 벡터는 성분 순서 리스트로 적는다."""
    (row,) = tables["Scene"].values()
    errors = []
    for key, expected in stage.items():
        if key not in row:
            errors.append(f"stage.json: Scene 행에 없는 칸 {key}")
            continue
        value = row[key]
        actual = list(value.values()) if isinstance(value, dict) else value
        if isinstance(expected, list) != isinstance(actual, list) or (
                not isinstance(expected, list) and isinstance(expected, bool) != isinstance(actual, bool)):
            errors.append(f"stage.json: {key} 값의 모양이 Scene 행과 다릅니다({expected!r})")
            continue
        if isinstance(expected, list):
            same = len(actual) == len(expected) and all(math.isclose(a, e, abs_tol=TOLERANCE)
                                                        for a, e in zip(actual, expected, strict=True))
        elif isinstance(expected, bool) or not isinstance(expected, (int, float)):
            same = actual == expected
        else:
            same = math.isclose(actual, _float32(expected), abs_tol=TOLERANCE)
        if not same:
            errors.append(f"Scene.{key}={actual!r}가 고정 무대 값 {expected!r}과 다릅니다.")
    return errors


# 손을 떼면 행동에 힘이 더 들어가지 않는다(ArtworkBehavior.cpp GetActiveContacts). 그 뒤 화면에 남는 시간은 매질 감쇠값이 정한다.
# 값이 없으면 헤더 기본값. 0이 '끔'인 값(그림 복원 · 그리기 자국)은 영원히 남는다.
SCREEN_MEDIA = ("FluidField", "ImageFlow", "SurfacePaint")   # 손 주변 화면 변화로 잔상을 판정할 수 있는 매질
AFTERIMAGE_HALF_LIFE = 2.5   # 이 이상이면 손 뗀 뒤 5초에 25% 넘게 남는다(전시기획서: 잔상 5초 이상)
STAY_HALF_LIFE = 12          # 이 이상이면 '남아 있음'으로 본다


def _half_life(kind: str, row: dict) -> float | None:
    if kind == "FluidField":   # 색 exp(-ln2·t/h)
        return row.get("Field", {}).get("DyeHalfLifeSeconds", 3.0)
    if kind == "ImageFlow":
        return row.get("RestoreHalfLifeSeconds", 20.0) or math.inf
    if kind == "SurfacePaint":
        return row.get("FadeHalfLifeSeconds", 0.0) or math.inf
    if kind == "SurfaceDeformation":
        # 가속도 = 전파 항 − ω²·높이 − d·속도 (ArtworkDeformationSurface.cpp:130-131, ω = 2π·복귀 주파수).
        # 진동하면(d² < 4ω²) 진폭 exp(-d·t/2), 진동 없이 돌아오면 느린 쪽 exp(-(d − √(d² − 4ω²))/2·t).
        # 전파 항은 빠진 근사라 실제 화면은 이보다 빨리 준다. 누름처럼 전파 속도가 크고 반경이 작으면 진동 없는 경우
        # 퍼짐(확산 c²/d)으로 크게 빨라질 수 있다(PC1 확인).
        surface = row.get("Surface", {})
        damping = max(surface.get("DampingPerSecond", 3.0), 0.5)   # runtime 하한 0.5
        spring = (2 * math.pi * surface.get("ReturnFrequencyHz", 1.0)) ** 2
        rate = (damping - math.sqrt(damping**2 - 4 * spring)) / 2 if damping**2 > 4 * spring else damping / 2
        return math.log(2) / rate
    # 활동량 · 입자 속도 exp(-3t/R): ArtworkGpuFlowBehavior.cpp:150-151, Shaders/ArtworkGpuFlow/Update.hlsl:61-62 (runtime bb36122)
    if kind == "GpuFlow":
        return math.log(2) / 3 * row.get("RecoverySeconds", 2.2)
    return None


def persistence(scene: dict) -> tuple[float, str] | None:
    """손 뗀 뒤 화면 반감기(초, 영원히 남으면 inf)와 그 매질. 매질이 여럿이면 가장 오래 남는 것. 아는 매질이 없으면 None."""
    found = [(h, kind) for kind, rows in scene.get("Profiles", {}).items() for row in rows
             if (h := _half_life(kind, row)) is not None]
    return max(found) if found else None


def check_scene(scene: dict, catalog=None, stage: dict | None = None) -> SceneCheck:
    """정적 검사. stage(고정 무대 = Scene 행 값)를 주면 Scene 행이 같아야 한다."""
    if not isinstance(scene, dict):
        return SceneCheck(["장면 파일을 JSON 객체로 읽을 수 없습니다."])
    report = validate_scene({k: v for k, v in scene.items() if k not in LOOK_FIELDS},
                            contract=scene_contract(), catalog=catalog)
    result = SceneCheck(list(report.errors), list(report.unverified))
    if result.errors:
        return result
    tables, profiles = _tables(scene)
    result.errors += _look_scene_errors(scene, tables) + _gpu_flow_errors(tables, profiles)
    if stage is not None:
        result.errors += _stage_errors(tables, stage)
    if "Look" in scene:
        result.unverified.append("Look: 다른 행동과 재질 출력 채널 중복, LightFunction 자산 등록")
    if _key("GpuFlow") in profiles:
        result.unverified.append("GpuFlow: System · Material의 카탈로그 등록과 Niagara 계약(User Parameter · GPU 이미터), "
                                 "GpuFlow 외 행동과의 출력 중복")
    return result


def stage_from_defaults() -> dict:
    """runtime 헤더 기본 무대(Scene 행을 비웠을 때). stage.json의 출발점."""
    reader = _Reader(scene_contract(), [])
    row = reader.default("FArtworkSceneRow", None)
    return {key: list(value.values()) if isinstance(value, dict) else value for key, value in row.items()}


if __name__ == "__main__":
    print(refresh(Path(sys.argv[1])))
