"""UE 자동 조립: 작품 명세(Spec) + 소재 목록(assets.json) → 공용 전시 맵용 scene.json과 함께 보낼 파일.

검증된 템플릿(ue_templates/<id>/scene.json, 라넬 공용 맵 계열 장면)을 대응표(mapping-v1.json)로 고르고,
템플릿의 slots.json이 허용한 칸만 채운다. 품질은 템플릿 수준으로 두고, 입력 형식이 바뀌면 대응표와 이 어댑터만 고친다.
  - 템플릿 선택(select_template)은 생성 전에 한 번 부르고 결과(TemplateChoice)를 기록해 조립에 넘긴다. 전용 템플릿이 있는
    반응만 바로 고르고, 객체 행동으로만 되는 반응(follow · glow)은 매질로 고르며 대체를 기록한다. 그 밖은 보류한다.
    원본 명세는 바꾸지 않고, 다르게 실행하는 부분(반응 방식 · 첫 규칙만 · 제스처 → 좌표)은 substitutions에 남긴다.
  - 층: 층마다 화면을 채우는 평면(LayerFar · LayerMid · LayerNear, 카탈로그 Layer_* 객체)을 둔다. 고정 배경 배치
    (Background)는 설치 레벨이 정한 회전 · 크기라 이미지를 얹지 않고 그대로 둔다. 그림 휘젓기는 뒤 층 이미지가 휘저어지는
    캔버스(SourceImage)로 들어간다. 반응 효과가 투명한 템플릿은 층들을 배경과 효과 사이에, 불투명 캔버스가 화면을 덮는
    템플릿은 중간 · 앞 층을 캔버스 앞에 알파로 겹친다(앞 층이 반응을 가리는지는 PC1 화면 변화량으로 판정).
  - 명세 값: 반응 → 같은 템플릿 안의 반응별 값(slots.json reaction_values: 표면 변형 ripple은 물결 · press는 누름(Elastic),
    GPU 입자 push · swirl · scatter는 밀기 · 소용돌이 · 손 속도 비율), influence_range → 반응 반경, density → 입자 수, interactions[0].return → 복귀 시간.
  - 재질 값: Look 기본 프리셋의 Materials로 넣는다(Look은 부분 패치가 안 되어 전체 프로필). 층 재질은 Tint · EmissiveStrength만 받는다.
  - 인터랙션: 좌표 반응만 연결한다. 손 흔들기 · 점프는 TD 제스처가 생기기 전까지 좌표 반응으로 대신하고 경고를 남긴다.
  - 객체: 오려 낸 알파 PNG마다 범용 Sprite 배치(Obj_<id>)를 층들 앞에 둔다(불투명 캔버스 템플릿은 캔버스 앞, 투명 효과
    템플릿은 입력 평면이라 GPU 입자(X≈290~410)와 겹쳐 입자가 객체 위로도 지나간다). 화면 좌표(u, v)는 그 깊이 평면의 위치로,
    행동은 Float(떠다님) · AttractRepel(따라감 · 피함) · MaterialScalar(빛남) 바인딩으로 바꾼다. 따라감 · 피함은 객체가
    입력 평면에 놓이는 투명 효과 템플릿에서만 받는다(runtime이 손 좌표를 객체 깊이로 투영하지 않음).
    층 · 객체 반투명 평면의 앞뒤는 runtime의 r.TranslucentSortPolicy=1(화면 깊이 정렬)에 기댄다.
  - 형상(formation) · 흔적(trace)은 검증된 UE 템플릿이 없어 생성 전에 보류한다(TD는 작품을 그리지 않는 입력 센서).
"""
import colorsys
import copy
import hashlib
import json
import math
import struct
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from contracts.assets import ArtworkAssets, Behavior, LayerDepth
from contracts.spec import NONE, Spec, SpecReveal
from studio.verify.checks import inspect_assets, png_has_alpha
from studio.verify.png import decode_png
from studio.verify.ue_contract import (
    AFTERIMAGE_HALF_LIFE,
    STAY_HALF_LIFE,
    check_scene,
    persistence,
    shared_catalog,
    shared_stage,
)

TEMPLATES = Path(__file__).parent / "ue_templates"
LAYER_OBJECTS = {LayerDepth.far: "Layer_Far", LayerDepth.mid: "Layer_Mid", LayerDepth.near: "Layer_Near"}
LAYER_PLACEMENTS = {LayerDepth.far: "LayerFar", LayerDepth.mid: "LayerMid", LayerDepth.near: "LayerNear"}
LAYER_MATERIAL_VALUES = {"Tint", "EmissiveStrength"}   # M_LayerPlane · M_LayerBackdrop 파라미터(Build-RuntimeIntegrationProof.py)
LAYER_ROTATION = "(Pitch=0,Yaw=-90,Roll=90)"   # 카메라를 보는 세로 평면. PC1 실측: Yaw=90이면 이미지 좌우가 뒤집힘(FlowStir 캔버스와 같은 -90)
PLANE_CM = 100                                 # /Engine/BasicShapes/Plane 한 변
FAR_X = 2                                      # 뒤 층: 고정 배경(X=0) 바로 앞
FRONT_SHARE = {LayerDepth.mid: 0.15, LayerDepth.near: 0.3}    # 효과 → 카메라 거리 중 앞 층 위치
FRONT_OPAQUE_LIMIT = 0.3   # 반응 앞에 놓이는 층의 불투명 픽셀 비율 경고 기준(설계 결정 2)
BEHIND_SHARE = {LayerDepth.mid: 1 / 3, LayerDepth.near: 2 / 3}  # 뒤 층 → 효과 거리 중 위치
SPRITE_SHARE = 0.2   # 불투명 캔버스 템플릿: 효과 → 카메라 거리 중 객체 위치(앞 층 mid 0.15와 near 0.3 사이)
# runtime AttractRepel은 손 위치를 입력 평면(SurfaceOrigin.X) 좌표 그대로 쓰고 객체 깊이로 투영하지 않는다.
# 객체가 입력 평면에 있어야 손 쪽으로 정확히 움직이므로, 불투명 캔버스(입력 평면 앞)를 쓰는 템플릿에서는 받지 않는다.
SCREEN_MOVES = {Behavior.follow, Behavior.flee}
MOVE_CM = 400          # AttractRepel MaxOffsetCm
SPRITE_PATTERNS = {Behavior.float: "Float", Behavior.follow: "AttractRepel", Behavior.flee: "AttractRepel",
                   Behavior.glow: "MaterialScalar", Behavior.wander: "Locomotion"}
# 돌아다님(runtime Locomotion): 위치를 입력 평면(X=300)에 고정하므로 객체를 입력 평면에 두는 템플릿에서만 받는다.
# Locomotion은 로컬 +X를 진행 방향으로 돌리고 roll을 0으로 둬서, 엔진 평면(로컬 XY)은 좌우로 날 때 수평으로 누워 안 보인다.
# 로컬 XZ 평면 사각형(그림 위쪽이 +X)은 좌우로 날 때도 카메라를 본다(10/25 PC1 시연 확인). 크기는 배치 Scale로 준다.
WANDER_MESH = "wander_quad.obj"
WANDER_MESH_CM = 100
WANDER_ROTATION = "(Pitch=0,Yaw=90,Roll=0)"   # 첫 프레임에 그림이 카메라를 보게. Locomotion이 곧 진행 방향으로 덮어쓴다
WANDER_BOUNDS = 0.05      # 화면 가장자리 여백(UV). 이동 영역은 여기서 외형 반경 + 패딩만큼 더 안쪽
WANDER_PADDING_CM = 20    # 프로필 BoundaryPaddingCm(runtime 기본 30)과 같은 값이어야 시작 위치 계산이 맞는다
# 공용 카탈로그에 라넬 청람의 날갯길 나비(스켈레탈 + 날갯짓 BlendSpace)가 있으면 사각형 대신 쓴다(10/25 라넬 작업 폴더에서 등록).
# 크기 · 외형 반경은 배치 Scale 1 기준 값이다. 머리 방향은 메시 기준 −90° 보정(AzureWingGarden 프로필과 같음).
# 실측(10/25 에디터): SK_AzureButterfly 경계 반폭 5.2 · 3.12 · 0.57cm, 구 반경 6.09cm, 중심 Z 0.54cm, 카탈로그 VisualScale 28.
BUTTERFLY_OBJECT = "Butterfly_AzureButterfly"
BUTTERFLY_WING_CM = 2 * 5.2 * 28           # Scale 1일 때 날개폭(cm) ≈ 291
BUTTERFLY_RADIUS_CM = (6.09 + 0.54) * 28   # Scale 1일 때 외형 구 반경 + 중심 오프셋(cm) ≈ 186. runtime 이동 영역 계산용
BUTTERFLY_FLAP = {   # AzureWingGarden Butterfly0Pose: 평상 날갯짓 BlendSpace(이동 속도로 빠르기), 손이 오면 반응 큐
    "CueTable": "/Game/ContentRuntime/AzureWingGarden/DT_ContactGlow.DT_ContactGlow",
    "AmbientAnimation": "/Game/ContentRuntime/ImportedRigProof/BS_CC0ButterflyV1.BS_CC0ButterflyV1",
    "AmbientBlendAtRest": "(X=100,Y=0,Z=0)", "AmbientBlendWhenActive": "(X=100,Y=0,Z=0)", "AmbientPlayRate": 0.9,
    "bUseMotionForAmbient": True, "MotionSpeedAtFullBlend": 250, "MotionBlendResponseSpeed": 3,
    "MotionPlayRateRange": "(X=0.8,Y=1.75)", "DurationSeconds": 2.4, "CooldownSeconds": 1.0, "BlendInSeconds": 0.2,
    "BlendOutSeconds": 0.6, "Phases": {"bEnabled": True, "OpeningEndSeconds": 0.35, "ClosingStartSeconds": 1.55},
    "Execution": {"InputExitPolicy": "CompleteSequence", "ReentryPolicy": "Ignore"}}
# 회전식에서 화면 평면 안 방향 전환은 기울기(bank)가 생기지 않아 0. 좌우가 바뀌는 순간 사각형이 얇아지므로 선회를 빠르게.
WANDER_PROFILE = {
    "BoundsMinUV": f"(X={WANDER_BOUNDS},Y={WANDER_BOUNDS})", "BoundsMaxUV": f"(X={1 - WANDER_BOUNDS},Y={1 - WANDER_BOUNDS})",
    "ScreenOverscanUV": "(X=0,Y=0)", "BoundaryPaddingCm": WANDER_PADDING_CM, "CruiseSpeedCmPerSecond": 140,
    "AccelerationCmPerSecondSquared": 140, "DecelerationCmPerSecondSquared": 180, "TurnSpeedDegreesPerSecond": 300,
    "HeadingResponseSeconds": 0.4, "TurnAccelerationDegreesPerSecondSquared": 2000, "MaxPitchAngleDegrees": 60,
    "MaxBankAngleDegrees": 0, "BoundaryLookAheadSeconds": 2.0, "BoundaryRecoveryMarginCm": 80, "ArrivalRadiusCm": 70,
    "GoalIntervalMinSeconds": 3, "GoalIntervalMaxSeconds": 6, "TouchReaction": "Evade", "EvadeSpeedMultiplier": 2.0,
    "bUseEvasionLifecycle": True, "Evasion": {"MinimumSeconds": 0.8, "MaximumSeconds": 2.5, "SafeDistanceCm": 450,
                                              "RecoverySeconds": 1.3, "RearmDelaySeconds": 0.7,
                                              "bRequireExitToRearm": True},
    "VisualForwardYawCorrectionDegrees": 0, "bPauseDuringAction": False}
# 라넬 공용 맵 회귀(Test-RuntimeIntegrationShipping.py inspect_sprites)에서 화면 반응을 확인한 값
SPRITE_PROFILES = {
    "Float": [{"Name": "SpriteFloat", "AmplitudeCm": "(X=0,Y=0,Z=40)", "PeriodSeconds": 3, "ResponseSpeed": 5}],
    "AttractRepel": [{"Name": "SpriteFollow", "bRepel": False, "MaxOffsetCm": MOVE_CM},
                     {"Name": "SpriteFlee", "bRepel": True, "MaxOffsetCm": MOVE_CM}],
    "MaterialScalar": [{"Name": "SpriteGlow", "ParameterName": "EmissiveStrength", "RestValue": 1, "ActiveValue": 3,
                        "ResponseSpeed": 6}]}
SPRITE_PROFILE_NAMES = {Behavior.float: "SpriteFloat", Behavior.follow: "SpriteFollow", Behavior.flee: "SpriteFlee",
                        Behavior.glow: "SpriteGlow"}
_HANDS = {"SourceIds": ["td_main/forest_hand", "td_main/hand_tracking_2"], "SampleId": "position_uv",
          "CoordinateSpace": "mediapipe_image_uv", "MaxContacts": 8}
# 따라감: 화면 어디든 손이 있으면. 빛남 · 피함: 그 객체 가까이(입력 평면 cm, 객체 현재 위치 기준) 손이 오면.
# 피함은 밀려난 뒤에도 손이 따라오면 계속 피하도록 나가는 반경을 이동 한도(400)보다 넉넉히 둔다.
SPRITE_CONDITIONS = {
    Behavior.follow: {"Name": "SpriteHands", **_HANDS, "ContactSelection": "All", "Region": "InputSurface",
                      "Type": "Presence"},
    Behavior.glow: {"Name": "SpriteTouch", **_HANDS, "ContactSelection": "Closest", "Type": "Proximity",
                    "Anchor": "CurrentObject", "RadiusCm": 250, "ExitRadiusCm": 300},
    Behavior.flee: {"Name": "SpriteNear", **_HANDS, "ContactSelection": "Closest", "Type": "Proximity",
                    "Anchor": "CurrentObject", "RadiusCm": 300, "ExitRadiusCm": 800},
    Behavior.wander: {"Name": "SpriteWanderNear", **_HANDS, "ContactSelection": "Closest", "Type": "Proximity",
                      "Anchor": "CurrentObject", "RadiusCm": 300, "ExitRadiusCm": 450}}
CLEAR_ALPHA = 0.01     # 객체 PNG에서 투명(알파 < 250) 픽셀이 이보다 적으면 사각형으로 보인다고 경고
# runtime은 외부 그림을 원본 크기 밉 1장으로 올린다(ArtworkExternalAssetLoader). 화면에 작게 보이는 객체에 큰 PNG를 쓰면
# 축소 때 거칠게 보인다. 송출 해상도는 미정이라 가로 1920px 기준으로 잡는다.
DISPLAY_WIDTH_PX = 1920
OVERSIZE = 2           # PNG 가로가 화면 표시 가로의 이 배수를 넘으면 경고
# 객체 행동 ↔ 명세 반응. flee는 명세 값 목록에 없어 밀어내는 반응과 짝지어 둔다(생성 쪽 대응표 확정 전 가정).
BEHAVIOR_REACTIONS = {Behavior.follow: {"follow"}, Behavior.glow: {"glow"}, Behavior.flee: {"push", "scatter"}}

# 불투명 캔버스 반응 색(slots.json palette_targets): 반응(염료 · 붓 · 평소 물감) 색을 뒤 층 대표색으로 맞춰 주제 색을 띠게 한다.
PALETTE_SAMPLES = 4096                 # 대표색을 셀 표본 화소 수(전체를 고르게 건너뜀)
PALETTE_MIN_VALUE = 0.2               # 이보다 어두운 화소는 빼고 센다
PALETTE_MIN_SATURATION = (0.25, 0.08)  # 무채색 화소를 빼는 기준. 못 뽑으면 한 번 낮춰 본다(한지 · 모래처럼 옅은 색 원화)
PALETTE_BINS, PALETTE_MAX = 12, 4      # 색상환 30° 구간, 최대 4색. 고른 색끼리는 2구간(60°) 이상 떨어지게
PALETTE_BRIGHT = 0.75                  # 어두운 원화 위 반응 색: sRGB 최대 채널을 이 값 이상으로(어두운 바탕에서 보이게)
PALETTE_DARK, PALETTE_LIGHT_PICTURE = 0.25, 0.6   # 밝은 원화(평균 밝기 이상) 위 반응 색: 최대 채널을 이 값으로 낮춤
# 캔버스 바탕에 원화가 깔리므로 반응 색이 원화와 같은 밝기면 안 보인다(10/25 PC1 paint: 한지 위 미색 붓 → 손 반응 0).


@dataclass
class Assembled:
    scene: dict | None = None
    files: dict[str, Path] = field(default_factory=dict)   # 버전 폴더 파일 이름 → 원본
    template: str | None = None
    substitutions: list[str] = field(default_factory=list)   # 템플릿 선택에서 기록한 대체(TemplateChoice)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@cache
def mapping() -> dict:
    return json.loads((TEMPLATES / "mapping-v1.json").read_text(encoding="utf-8"))


@cache
def _template(name: str) -> tuple[dict, dict]:
    folder = TEMPLATES / name
    return (json.loads((folder / "scene.json").read_text(encoding="utf-8")),
            json.loads((folder / "slots.json").read_text(encoding="utf-8")))


def template_scene(name: str) -> dict:
    return copy.deepcopy(_template(name)[0])


def template_slots(name: str) -> dict:
    return copy.deepcopy(_template(name)[1])


@dataclass
class TemplateChoice:
    """생성 전에 한 번 고르고 기록해 두었다가 조립에서 그대로 쓰는 템플릿 선택(다시 고르면 그사이 규칙이 바뀔 수 있다)."""
    template: str | None = None
    substitutions: list[str] = field(default_factory=list)   # 원본 명세와 다르게 실행하는 부분. 원본 값은 명세에 그대로 남긴다
    holds: list[str] = field(default_factory=list)            # 비어 있지 않으면 생성 전에 보류
    required_behaviors: list[str] = field(default_factory=list)   # 생성 쪽이 이 행동을 가진 객체를 만들어야 함(follow · glow 대체)
    # 고를 때 본 명세 값. 조립 때 명세와 다르면 다른 명세의 선택으로 보고 거부한다. None(직접 만든 선택)은 비교하지 않고,
    # 그런 선택은 required_behaviors도 비어 있어 객체 없음 보류가 걸리지 않는다 — select_template 결과를 그대로 넘길 것.
    basis: dict | None = None


def _basis(spec: Spec) -> dict:
    rule = spec.interactions[0]
    return {"reaction": _text(rule.reaction), "medium": _text(spec.content.medium), "reveal": _text(spec.content.reveal),
            "reaction_mode": _text(rule.reaction_mode), "input": _text(rule.input), "rules": len(spec.interactions),
            "later_reactions": [_text(other.reaction) for other in spec.interactions[1:]]}


def _text(value):
    return None if value is None else str(value)


def select_template(spec: Spec) -> TemplateChoice:
    """명세 → 템플릿. 전용 템플릿이 있는 반응만 바로 고른다. 객체 행동으로만 되는 반응(follow · glow)은 매질로 템플릿을 고르고
    대체를 기록하며, 그 밖의 반응과 형상 · 흔적은 보류한다. 매질만 보고 조용히 고르지 않는다."""
    table, rule = mapping(), spec.interactions[0]
    reaction, medium = rule.reaction, spec.content.medium
    choice = TemplateChoice(basis=_basis(spec))
    if spec.content.reveal in (SpecReveal.formation, SpecReveal.trace):
        choice.holds.append(f"{spec.content.reveal}(보이지 않는 주인공) 작품은 아직 UE 템플릿이 없어 보류")
        return choice
    row = next((row for row in table["dedicated"]
                if reaction in row["reaction"] and ("medium" not in row or medium in row["medium"])), None)
    if row is not None:
        choice.template = row["template"]
    elif reaction in table["sprite_only"]:
        choice.template = table["by_medium"].get(medium)
        if choice.template is None:
            choice.holds.append(f"반응 {reaction}은 객체 행동으로만 되고, 매질 {medium}로 고를 템플릿이 없어 보류")
            return choice
        if reaction in SCREEN_MOVES and template_slots(choice.template)["layer_side"] == "front":
            choice.holds.append(f"반응 {reaction}은 객체 행동으로만 되는데, 매질 {medium}의 {choice.template} 템플릿은 "
                                f"불투명 캔버스 앞에 객체를 두어 따라감 · 피함을 받지 않아 보류")
            choice.template = None
            return choice
        choice.substitutions.append(f"반응 {reaction}은 전용 템플릿이 없어 매질 {medium}의 {choice.template} 템플릿을 쓰고, "
                                    f"반응은 객체 행동으로 표현")
        choice.required_behaviors.append(str(reaction))
    else:
        choice.holds.append(f"반응 {reaction}에 맞는 검증된 템플릿이 없어 보류(medium={medium})")
        return choice
    if rule.reaction_mode is None:   # 필수 칸이 아직 안 채워짐
        choice.template, choice.required_behaviors = None, []
        choice.holds.append("반응 방식(reaction_mode)이 비어 있어 보류")
        return choice
    modes = table["reaction_modes"].get(choice.template, table["reaction_modes"]["default"])
    if rule.reaction_mode not in modes:
        choice.substitutions.append(f"반응 방식 {rule.reaction_mode}은 {choice.template} 템플릿에서 {modes[0]}로 실행")
    if len(spec.interactions) > 1:
        dropped = [str(other.reaction) for other in spec.interactions[1:]]
        choice.substitutions.append(f"규칙 {len(spec.interactions)}개 중 첫 규칙만 템플릿에 연결(연결 안 된 반응: {dropped})")
    if rule.input is not None and rule.input not in table["coordinate_inputs"]:
        choice.substitutions.append(f"입력 {rule.input}은 TD 제스처 연결 전이라 좌표 반응으로 대신함")
    return choice


def choose_template(spec: Spec) -> str | None:
    return select_template(spec).template


def compatibility(name: str, assets: ArtworkAssets | None = None) -> list[str]:
    """템플릿(과 소재 객체)이 요구하는 패턴 · 객체가 공용 카탈로그에 있는지(실행본과 호환되는지)."""
    requires, catalog = template_slots(name)["requires"], shared_catalog()
    patterns, objects = list(requires["patterns"]), list(requires["objects"])
    if assets is not None and assets.objects:
        objects.append("Sprite")
        patterns += sorted({SPRITE_PATTERNS[b] for item in assets.objects for b in item.behaviors} - set(patterns))
    problems = [f"카탈로그에 패턴 {p}가 없음" for p in patterns if not catalog.has_pattern(p)]
    problems += [f"카탈로그에 객체 {o}가 없음" for o in objects if catalog.object(o) is None]
    return problems


def _set(scene, path, value):
    target = scene
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def _linear(channel: float) -> float:
    """sRGB 0~1 → 선형(runtime 색 값)."""
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def palette(path: Path, count: int) -> list[str]:
    """그림의 대표색 count개(UE 선형 색 문자열). 어둡거나 무채색인 화소를 빼고 색상 구간별로 채도 × 밝기 가중치를 모아 큰 구간부터
    서로 60° 이상 떨어진 것을 고른다. 그림 위에 그려지므로 밝은 그림이면 같은 색상의 어두운 색, 어두운 그림이면 밝은 색으로. 고른 색이 count보다 적으면 돌려 쓰고, 고를 색이 없으면(무채색 그림) 빈 목록."""
    width, height, rgb = decode_png(path.read_bytes())
    step = max(1, width * height // PALETTE_SAMPLES)
    samples = [colorsys.rgb_to_hsv(*(value / 255 for value in rgb[index * 3:index * 3 + 3]))
               for index in range(0, width * height, step)]
    light = sum(v for _, _, v in samples) / len(samples) >= PALETTE_LIGHT_PICTURE
    for floor in PALETTE_MIN_SATURATION:
        bins = [[0.0, 0.0, 0.0, 0.0] for _ in range(PALETTE_BINS)]   # 가중치, 가중 r · g · b 합
        for h, s, v in samples:
            if v < PALETTE_MIN_VALUE or s < floor:
                continue
            r, g, b = colorsys.hsv_to_rgb(h, s, v)
            row = bins[int(h * PALETTE_BINS) % PALETTE_BINS]
            weight = s * v
            row[0] += weight
            row[1], row[2], row[3] = row[1] + r * weight, row[2] + g * weight, row[3] + b * weight
        if any(row[0] > 0 for row in bins):
            break
    chosen = []
    for k in sorted(range(PALETTE_BINS), key=lambda k: (-bins[k][0], k)):
        near = any(min(abs(k - c), PALETTE_BINS - abs(k - c)) < 2 for c in chosen)
        if bins[k][0] > 0 and not near and len(chosen) < min(count, PALETTE_MAX):
            chosen.append(k)
    colors = []
    for k in chosen:
        weight, *sums = bins[k]
        color = [part / weight for part in sums]
        peak = max(max(color), 1e-6)
        lift = PALETTE_DARK / peak if light else max(1, PALETTE_BRIGHT / peak)
        r, g, b = (_linear(min(1, part * lift)) for part in color)
        colors.append(f"(R={r:.3f},G={g:.3f},B={b:.3f},A=1)")
    return [colors[i % len(colors)] for i in range(count)] if colors else []


def _vector(text: str) -> dict[str, float]:
    return {key: float(value) for key, value in (part.split("=") for part in text.strip("()").split(","))}


def _canvas_source(scene, slots, assets, result) -> None:
    """slots.json canvas_source: 불투명 캔버스 바탕에 뒤 층 그림(재질 SourceImage)을 보낸다. 캔버스는 화면 일부만 덮으므로
    캔버스가 덮는 화면 구간만 읽게 UV 범위를 준다(뒤 층과 같은 자리 그림). 캔버스 회전(Yaw 90)은 층(Yaw −90)과 좌우가 반대라
    가로 크기를 음수로 넣는다. 값은 MaterialScalar(Rest = Active, 조건 없음)로 첫 프레임부터 고정."""
    if not slots.get("canvas_source"):
        return
    stage = shared_stage()
    canvas = next(row for row in scene["Placements"] if row["Name"] == "Canvas")
    location, scale = _vector(canvas["Location"]), _vector(canvas["Scale"])
    view_width = _view_width_cm(location["X"])
    view_height = view_width / stage["AspectRatio"]
    half_w, half_h = scale["X"] * PLANE_CM / 2, scale["Y"] * PLANE_CM / 2
    left, right = 0.5 - (location["Y"] + half_w) / view_width, 0.5 - (location["Y"] - half_w) / view_width
    top = 0.5 - (location["Z"] - stage["CameraLocation"][2] + half_h) / view_height
    bottom = 0.5 - (location["Z"] - stage["CameraLocation"][2] - half_h) / view_height
    mirrored = _vector(canvas["Rotation"])["Yaw"] != _vector(LAYER_ROTATION)["Yaw"]
    values = {"SourceBlend": 1, "SourceUvMinU": right if mirrored else left, "SourceUvMinV": top,
              "SourceUvSizeU": (left - right) if mirrored else (right - left), "SourceUvSizeV": bottom - top}
    scene.setdefault("ExternalAssets", []).append(
        {"Name": "Canvas", "ImageFile": assets.back_layer.image, "TextureParameter": "SourceImage"})
    for name, value in values.items():
        scene["Bindings"].append({"Name": f"Canvas{name}", "PlacementId": "Canvas", "PatternId": "MaterialScalar",
                                  "ProfileId": f"Canvas{name}"})
        scene["Profiles"].setdefault("MaterialScalar", []).append(
            {"Name": f"Canvas{name}", "ParameterName": name, "RestValue": round(value, 6), "ActiveValue": round(value, 6),
             "ResponseSpeed": 1})


def _palette_targets(scene, slots, assets, folder, result) -> None:
    """slots.json palette_targets: 뒤 층 대표색을 반응 색(염료 · 붓 · 평소 물감)으로."""
    targets = slots.get("palette_targets", [])
    if not targets:
        return
    colors = palette(folder / assets.back_layer.image, max(target["count"] for target in targets))
    if not colors:
        result.warnings.append(f"뒤 층 {assets.back_layer.image}에서 대표색을 못 뽑아(무채색) 템플릿 반응 색을 그대로 씀")
        return
    for target in targets:
        _set(scene, target["path"], colors[:target["count"]])


def _view_width_cm(x) -> float:
    """깊이 x 평면에서 화면 가로가 덮는 길이."""
    stage = shared_stage()
    return 2 * (stage["CameraLocation"][0] - x) * math.tan(math.radians(stage["FieldOfView"] / 2))


def _plane(name, object_id, x):
    """카메라(고정 무대 CameraLocation.X)에서 FOV로 화면을 꽉 채우는 크기."""
    stage = shared_stage()
    width = _view_width_cm(x) / PLANE_CM
    height = width / stage["AspectRatio"]
    return {"Name": name, "ObjectId": object_id, "Location": f"(X={x:g},Y=0,Z={stage['SurfaceOrigin'][2]:g})",
            "Rotation": LAYER_ROTATION, "Scale": f"(X={width:.4f},Y={height:.4f},Z=1)"}


def _layer_x(slots, depth) -> float:
    effect = slots["effect_x"]
    if depth is LayerDepth.far:
        return FAR_X
    if slots["layer_side"] == "behind":
        return round(FAR_X + (effect - FAR_X) * BEHIND_SHARE[depth], 1)
    return round(effect + (shared_stage()["CameraLocation"][0] - effect) * FRONT_SHARE[depth], 1)


def _png_size(path: Path) -> tuple[int, int]:
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return width, height


def _front_layer_problems(layer, path: Path, result) -> None:
    """불투명 캔버스 앞에 겹치는 층: 알파가 없으면 반응을 통째로 덮으므로 거부, 불투명 비율이 크면 경고."""
    data = path.read_bytes()
    if not png_has_alpha(data):
        result.errors.append(f"층 {layer.id}는 반응 캔버스 앞에 놓여 알파가 있어야 함")
        return
    try:
        _, _, alpha = decode_png(data, alpha=True)
    except ValueError:
        result.warnings.append(f"층 {layer.id}의 불투명 비율을 읽지 못함(8비트 RGB/RGBA PNG만) — PC1 화면 변화량으로 확인")
        return
    ratio = sum(1 for a in alpha if a >= 250) / max(len(alpha), 1)
    if ratio > FRONT_OPAQUE_LIMIT:
        result.warnings.append(f"층 {layer.id}가 반응 앞을 {ratio:.0%} 덮음(기준 {FRONT_OPAQUE_LIMIT:.0%})")


def _layers(scene, slots, assets, folder, result) -> dict[str, str]:
    """층 id → 배치 이름(재질 값 대상). 받지 않는 층은 오류로 남긴다."""
    external, targets = [], {}
    aspect = shared_stage()["AspectRatio"]
    for layer in assets.layers:
        if layer.depth.value not in slots["layers"]:
            result.errors.append(f"템플릿 {result.template}은 {layer.depth.value} 층을 받지 않음(받는 층: {slots['layers']})")
            continue
        width, height = _png_size(folder / layer.image)
        if abs(width / height - aspect) / aspect > 0.02:
            result.warnings.append(f"층 {layer.id}({width}×{height})는 화면 비율과 달라 늘어나 보임")
        if layer.depth is LayerDepth.far and slots["far_target"] != "LayerFar":
            target = slots["far_target"]   # 그림 휘젓기 · 그림 위 물결: 원화가 곧 반응 캔버스
            if slots.get("water_from_alpha") and not _has_clear_pixels(folder / layer.image):
                result.warnings.append(f"층 {layer.id}에 알파가 없어 그림 전체가 물로 출렁임(물 영역은 알파로 표시). "
                                       "수평선 · 물 깊이 값은 라넬 예제 원화 기준")
        else:
            target = LAYER_PLACEMENTS[layer.depth]
            scene["Placements"].append(_plane(target, LAYER_OBJECTS[layer.depth], _layer_x(slots, layer.depth)))
            if slots["layer_side"] == "front" and layer.depth is not LayerDepth.far:
                _front_layer_problems(layer, folder / layer.image, result)
        if layer.depth is LayerDepth.far and slots["far_hidden"] and not slots.get("canvas_source"):
            result.warnings.append(f"템플릿 {result.template}은 불투명 반응 캔버스가 화면 대부분을 덮어 뒤 층이 거의 보이지 않음"
                                   "(캔버스 재질에 그림 입력이 없음)")
        external.append({"Name": target, "ImageFile": layer.image, "TextureParameter": "SourceImage"})
        targets[layer.id] = target
    if external:
        scene["ExternalAssets"] = external
    return targets


def _sprite_x(slots) -> float:
    """투명 효과 템플릿은 입력 평면(손 좌표가 맞는 깊이, 뒤 층들 앞), 불투명 캔버스 템플릿은 캔버스와 앞 층 사이."""
    stage = shared_stage()
    if slots["layer_side"] == "behind":
        return stage["SurfaceOrigin"][0]
    effect = slots["effect_x"]
    return round(effect + (stage["CameraLocation"][0] - effect) * SPRITE_SHARE, 1)


def _sprite_warnings(item, path: Path, aspect: float, view_width: float, result) -> None:
    width, height = _png_size(path)
    u, v = item.position
    half_u = item.size / 2 + (MOVE_CM / view_width if SCREEN_MOVES.intersection(item.behaviors) else 0)
    half_v = item.size * aspect * height / width / 2
    if Behavior.wander not in item.behaviors and (u - half_u < 0 or u + half_u > 1 or v - half_v < 0 or v + half_v > 1):
        result.warnings.append(f"객체 {item.id}가 (움직임 포함) 화면 밖으로 일부 나감")
    shown = item.size * DISPLAY_WIDTH_PX
    if width > shown * OVERSIZE:
        result.warnings.append(f"객체 {item.id} PNG 가로 {width}px가 화면 표시(약 {shown:.0f}px)보다 훨씬 커서 거칠게 보일 수 있음"
                               f"(외부 그림은 밉맵 없이 올라감) — 표시 크기 근처로 줄여 주세요")
    if not _has_clear_pixels(path):
        result.warnings.append(f"객체 {item.id} PNG에 투명 픽셀이 거의 없어 사각형으로 보임")


def _has_clear_pixels(path: Path) -> bool:
    """투명(알파 < 250) 픽셀이 CLEAR_ALPHA 이상인지. 알파 채널이 있어도 전부 불투명하면 False.
    8비트가 아닌 PNG는 비율을 못 읽어 알파 채널 유무로만 본다."""
    data = path.read_bytes()
    try:
        _, _, alpha = decode_png(data, alpha=True)
    except ValueError:
        return png_has_alpha(data)
    return sum(1 for a in alpha if a < 250) / max(len(alpha), 1) >= CLEAR_ALPHA


def _behavior_agreement(spec: Spec, assets: ArtworkAssets, choice: TemplateChoice, result) -> None:
    """명세 반응과 객체 행동이 서로 다른 반응을 말하지 않는지. 템플릿 선택이 객체 행동으로 표현하기로 한 반응(required_behaviors)을
    가진 객체가 없으면 반응이 화면에 나오지 않으므로 보류한다."""
    reactions = {str(rule.reaction) for rule in spec.interactions}
    for item in assets.objects:
        for behavior in item.behaviors:
            wanted = BEHAVIOR_REACTIONS.get(behavior)
            if wanted and not wanted & reactions:
                result.warnings.append(f"객체 {item.id}의 행동 {behavior}에 맞는 반응이 명세에 없음(명세 반응: {sorted(reactions)})")
    for needed in choice.required_behaviors:
        if not any(needed in item.behaviors for item in assets.objects):
            result.errors.append(f"반응 {needed}을 객체 행동으로 표현하기로 했는데 {needed} 행동을 가진 객체가 없음")


def _objects(scene, slots, assets, folder, result) -> None:
    """객체마다 Sprite 배치 · 외부 이미지 · 행동 바인딩. 카메라가 -X를 보므로(Yaw 180) 화면 왼쪽이 +Y다."""
    if not assets.objects:
        return
    if slots["layer_side"] == "front":
        blocked = [item.id for item in assets.objects
                   if SCREEN_MOVES.intersection(item.behaviors) or Behavior.wander in item.behaviors]
        if blocked:
            result.errors.append(f"템플릿 {result.template}은 불투명 캔버스 앞에 객체를 두어 따라감 · 피함 · 돌아다님을 받지 않음"
                                 f"(손 좌표와 객체 깊이가 달라 엉뚱한 쪽으로 움직임): {blocked}")
            return
    stage = shared_stage()
    x = _sprite_x(slots)
    view_width = _view_width_cm(x)
    view_height = view_width / stage["AspectRatio"]
    used, wanders = set(), []
    for item in assets.objects:
        name = f"Obj_{item.id}"
        width, height = _png_size(folder / item.image)
        _sprite_warnings(item, folder / item.image, stage["AspectRatio"], view_width, result)
        u, v = item.position
        y, z = (0.5 - u) * view_width, (0.5 - v) * view_height
        asset = {"Name": name, "ImageFile": item.image, "TextureParameter": "SourceImage"}
        object_id = "Sprite"
        if Behavior.wander in item.behaviors:
            butterfly = shared_catalog().object(BUTTERFLY_OBJECT) is not None
            if butterfly and Behavior.glow in item.behaviors:   # 나비 재질에 EmissiveStrength가 있는지 오프라인으로 알 수 없다
                butterfly = False
                result.warnings.append(f"객체 {item.id}: 빛남(glow)과 함께라 날갯짓 나비 대신 그림 평면으로 돌아다님")
            if butterfly:
                size = item.size * view_width / BUTTERFLY_WING_CM
                radius = size * BUTTERFLY_RADIUS_CM + WANDER_PADDING_CM + 1
                # 스켈레탈 컴포넌트 경계는 메시 에셋 실측보다 클 수 있어(물리 에셋 · 경계 확장) 시작 위치를 당길 때는 더 안쪽으로
                safe = size * BUTTERFLY_RADIUS_CM * 1.2 + WANDER_PADDING_CM + 30
                object_id, asset = BUTTERFLY_OBJECT, None
                scale = f"(X={size:.4f},Y={size:.4f},Z={size:.4f})"
                result.warnings.append(f"객체 {item.id}는 돌아다님: 라넬 3D 나비로 날갯짓하며 남. PNG 무늬는 쓰지 않음(나비 재질의 "
                                       "SourceImage는 날개 텍스처 UV라 오려 낸 PNG가 맞지 않음 — 나비 원래 색)")
            else:
                # 고정 사각형 메시는 로컬 X(머리) · Z(날개폭) 평면이라 배치 Scale도 그 축으로 준다(Locomotion이 돌린 로컬 축에 걸림)
                wing = item.size * view_width
                head = wing * height / width
                radius = safe = math.hypot(wing / 2, head / 2) + WANDER_PADDING_CM + 1
                scale = f"(X={head / WANDER_MESH_CM:.4f},Y=1,Z={wing / WANDER_MESH_CM:.4f})"
                asset["MeshFile"] = WANDER_MESH
                result.warnings.append(f"객체 {item.id}는 돌아다님: 위에서 본 좌우 대칭 그림(나비 등)이어야 자연스러움 — 그림 위쪽이 "
                                       "머리로 진행 방향을 봄. 옆모습(물고기 · 새)은 배나 등을 앞으로 하고 남")
            # runtime 이동 영역 = 화면 경계에서 외형 반경 + 패딩만큼 안쪽. 밖에서 시작하면 Prepare가 실패해 장면 전체가
            # 적용되지 않는다. 반올림 대비 1cm 더 안쪽으로(radius에 포함).
            surface_w, surface_h = stage["SurfaceSize"]   # runtime 이동 영역은 입력 평면 크기(SurfaceSize) 기준
            room_y, room_z = (0.5 - WANDER_BOUNDS) * surface_w - radius, (0.5 - WANDER_BOUNDS) * surface_h - radius
            if room_y <= 0 or room_z <= 0:
                result.errors.append(f"객체 {item.id}가 화면에 비해 너무 커서 돌아다닐 영역이 없음(size를 줄여 주세요)")
                continue
            room_y = max(min(room_y, (0.5 - WANDER_BOUNDS) * surface_w - safe), 0)
            room_z = max(min(room_z, (0.5 - WANDER_BOUNDS) * surface_h - safe), 0)
            inside = max(-room_y, min(room_y, y)), max(-room_z, min(room_z, z))
            if inside != (y, z):
                result.warnings.append(f"객체 {item.id}의 시작 위치가 돌아다닐 수 있는 영역 밖이라 안으로 옮김")
            (y, z), rotation = inside, WANDER_ROTATION
            wanders.append((item.id, butterfly))
        else:
            size = item.size * view_width / PLANE_CM
            rotation, scale = LAYER_ROTATION, f"(X={size:.4f},Y={size * height / width:.4f},Z=1)"
        scene["Placements"].append({
            "Name": name, "ObjectId": object_id, "Rotation": rotation,
            "Location": f"(X={x:g},Y={y:.1f},Z={stage['SurfaceOrigin'][2] + z:.1f})", "Scale": scale})
        if asset is not None:
            scene.setdefault("ExternalAssets", []).append(asset)
        for behavior in item.behaviors:
            pattern = SPRITE_PATTERNS[behavior]
            profile = f"SpriteWander_{item.id}" if behavior is Behavior.wander else SPRITE_PROFILE_NAMES[behavior]
            binding = {"Name": f"{name}_{behavior.value}", "PlacementId": name, "PatternId": pattern,
                       "ProfileId": profile}
            if behavior in SPRITE_CONDITIONS:
                binding["ConditionId"] = SPRITE_CONDITIONS[behavior]["Name"]
                used.add(behavior)
            scene["Bindings"].append(binding)
            used.add(pattern)
            if behavior is Behavior.wander and object_id == BUTTERFLY_OBJECT:
                scene["Bindings"].append({"Name": f"{name}_flap", "PlacementId": name, "PatternId": "ActionSequence",
                                          "ProfileId": f"SpriteFlap_{item.id}", "ConditionId": binding["ConditionId"]})
    profiles = scene.setdefault("Profiles", {})
    for pattern, rows in SPRITE_PROFILES.items():
        if pattern in used:
            profiles.setdefault(pattern, []).extend(copy.deepcopy(rows))
    for object_id, butterfly in wanders:   # 객체마다 경로 난수만 다르게(같은 입력 → 같은 결과)
        seed = int(hashlib.sha256(object_id.encode()).hexdigest()[:8], 16) % 2**31
        row = {"Name": f"SpriteWander_{object_id}", **copy.deepcopy(WANDER_PROFILE), "RandomSeed": seed}
        if butterfly:
            row["VisualForwardYawCorrectionDegrees"] = -90
            profiles.setdefault("ActionSequence", []).append({"Name": f"SpriteFlap_{object_id}", **copy.deepcopy(BUTTERFLY_FLAP)})
        profiles.setdefault("Locomotion", []).append(row)
    scene.setdefault("Conditions", []).extend(
        copy.deepcopy(condition) for behavior, condition in SPRITE_CONDITIONS.items() if behavior in used)


def _look(assets, layer_targets, result):
    if not assets.materials:
        return None
    look = json.loads((TEMPLATES / "looks" / f"{mapping()['look']}.json").read_text(encoding="utf-8"))
    for material in assets.materials:
        target = layer_targets.get(material.target)
        if target is None:
            continue   # 받지 않은 층의 재질 값(그 층은 이미 오류로 남음)
        if material.object is not None:
            result.warnings.append(f"재질 프리셋 객체 {material.object}는 아직 지원하지 않아 무시함(층 재질에 값만 적용)")
        if not target.startswith("Layer"):
            result.errors.append(f"층 {material.target}은 {target}(템플릿 캔버스)에 들어가 재질 값을 받지 않음")
            continue
        unsupported = sorted(set(material.values) - LAYER_MATERIAL_VALUES)
        if unsupported:
            result.errors.append(f"층 {material.target}의 재질은 {sorted(LAYER_MATERIAL_VALUES)}만 받음: {unsupported}")
            continue
        scalars = {k: v for k, v in material.values.items() if not isinstance(v, list)}
        colors = {k: v for k, v in material.values.items() if isinstance(v, list)}
        look["Materials"].append({"Target": target, "Visible": True, "Scalars": scalars, "Colors": colors})
    return look


def _scene_name(spec: Spec, assets: ArtworkAssets, template: str, files: dict[str, Path]) -> str:
    """입력(명세 · 소재 목록 · 파일 내용 · 템플릿)이 같으면 같은 이름."""
    contents = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(files.items())}
    listed = assets.model_dump(mode="json", exclude=None if assets.objects else {"objects"})   # 객체 없으면 이전 이름 유지
    digest = hashlib.sha256(json.dumps([spec.model_dump(mode="json", by_alias=True), listed,
                                        template, contents], sort_keys=True).encode()).hexdigest()
    return f"Auto-{template}-{digest[:12]}"


def _afterimage_warnings(scene, rule, result) -> None:
    """반응 값까지 적용한 최종 장면의 손 뗀 뒤 화면 반감기로 잔상 기준을 본다."""
    found = persistence(scene)
    if found is None or rule.return_ == "instant":
        return
    half_life, medium = found
    if rule.return_ == "stay" and half_life < STAY_HALF_LIFE:
        result.warnings.append(f"stay: {medium}의 런타임 한도 · 반응 방식 때문에 잔상 반감기 {half_life:.1f}초까지만")
    elif rule.return_ != "stay" and half_life < AFTERIMAGE_HALF_LIFE:
        result.warnings.append(f"잔상 5초 기준 미달({medium} 반감기 {half_life:.1f}초)")


def _apply_spec(scene, slots, spec, result):
    rule = spec.interactions[0]
    returns = str(rule.return_ or "fade")   # 비어 있으면 템플릿 기본(잔상이 남는 fade)
    for row in slots.get("return_values", {}).get(returns, []):   # 복귀 방식별 매질 감쇠. 반응 값이 뒤에서 덮는다
        _set(scene, row["path"], row["value"])
    for row in slots.get("reaction_values", {}).get(str(rule.reaction), []):   # 같은 템플릿 안의 반응별 모드
        _set(scene, row["path"], row["value"])
        if "unverified" in row:
            result.warnings.append(f"PC1 검증 필요: {row['unverified']}")
    # 반응 값이 감쇠까지 정하는 반응(누름)은 그 반응 안에서 복귀 방식별 값을 마지막에 다시 준다
    for row in slots.get("reaction_return_values", {}).get(str(rule.reaction), {}).get(returns, []):
        _set(scene, row["path"], row["value"])
        if "unverified" in row:
            result.warnings.append(f"PC1 검증 필요: {row['unverified']}")
    if "influence_radius" in slots and spec.content.influence_range not in (None, NONE):
        radius = slots["influence_radius"]
        _set(scene, radius["path"], radius[spec.content.influence_range])
    if "density" in slots and isinstance(spec.content.density, float):
        density = slots["density"]
        _set(scene, density["path"], round(density["low"] + (density["high"] - density["low"]) * spec.content.density))
    if rule.return_ is not None:   # 비어 있으면 템플릿 기본 복귀 시간
        binding = next(row for row in scene["Bindings"] if row["Name"] == slots["release_binding"])
        binding["ReleaseSeconds"] = mapping()["release_seconds"][rule.return_]
    _afterimage_warnings(scene, rule, result)


def assemble(spec: Spec, assets: ArtworkAssets, folder: Path, choice: TemplateChoice | None = None) -> Assembled:
    """같은 (명세, 소재 목록, 파일, 템플릿 선택)이면 같은 결과. errors가 있으면 scene은 None이다.
    choice는 생성 전에 select_template으로 기록해 둔 선택이다. 없으면 여기서 고른다."""
    folder = Path(folder)
    result = Assembled(warnings=[f"모르는 칸: {name}" for name in assets.unknown_fields()])
    choice = choice or select_template(spec)
    if choice.basis is not None and choice.basis != _basis(spec):
        result.errors.append(f"기록된 템플릿 선택이 이 명세에서 나온 것이 아님(선택 근거 {choice.basis} ≠ 명세 {_basis(spec)})")
        return result
    result.template, result.substitutions = choice.template, list(choice.substitutions)
    if choice.holds or choice.template is None:
        result.errors += choice.holds or ["템플릿 선택이 비어 있음"]
        return result
    if not (TEMPLATES / choice.template / "scene.json").is_file():
        result.errors.append(f"기록된 템플릿 {choice.template}이 없음(템플릿 목록이 바뀜)")
        return result
    result.errors += compatibility(result.template, assets) + inspect_assets(folder, assets)
    _behavior_agreement(spec, assets, choice, result)
    if result.errors:
        return result
    scene, slots = template_scene(result.template), template_slots(result.template)
    layer_targets = _layers(scene, slots, assets, folder, result)
    _objects(scene, slots, assets, folder, result)
    _apply_spec(scene, slots, spec, result)
    _canvas_source(scene, slots, assets, result)
    _palette_targets(scene, slots, assets, folder, result)
    look = _look(assets, layer_targets, result)
    if look is not None:
        scene["Look"] = look
    if result.errors:
        return result
    files = {row["ImageFile"]: folder / row["ImageFile"] for row in scene.get("ExternalAssets", [])}
    # 소재 파일은 PNG만 받으므로(contracts.assets) 고정 메시 이름과 겹치지 않는다
    files.update({row["MeshFile"]: TEMPLATES / "meshes" / row["MeshFile"]
                  for row in scene.get("ExternalAssets", []) if "MeshFile" in row})
    scene["Scene"][0]["Name"] = _scene_name(spec, assets, result.template, files)
    check = check_scene(scene, catalog=shared_catalog(), stage=shared_stage())
    if check.errors:
        result.errors += check.errors
        return result
    if "Look" in scene:
        result.warnings.append("PC1 검증 필요: Look 재질 값이 층에 반영되는지")
    result.warnings += [f"PC1 검증 필요: {item}" for item in check.unverified if item.startswith("GpuFlow")]
    result.scene, result.files = scene, files
    return result
