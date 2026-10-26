"""UE 작품 소재 목록(assets.json v1): 생성 쪽이 보내는 파일마다 역할을 적는다. UE 자동 조립의 입력이다.

무엇을 만들지 · 드러내는 방식 · 인터랙션은 작품 명세(spec.Spec)가 맡고, 이 파일에는 파일 역할만 둔다.
- 층: 2.5D 배경을 깊이 순서로 나눈 PNG. 공용 카탈로그에 깊이마다 층 객체가 하나라 깊이당 한 장이다.
  맨 뒤 층만 불투명을 허용하고 앞 층은 알파가 있어야 한다(파일 검사는 studio.verify.checks.inspect_assets).
- 재질 값: 층(또는 층 위 객체)의 재질 값. UE Look이 받는 이름과 범위만 허용한다. 재질 텍스처는 그 층 이미지다.
- 깊이 지도: 층을 나눈 근거로 보관만 한다(조립에 쓰지 않음).
- 실루엣: 형상(formation) 작품의 점 목록 JSON. 형상은 표현 방식 중 하나일 뿐이라 없어도 된다.
- 객체: 배경에서 오려 낸 알파 PNG(물고기 · 나비 등). 공용 카탈로그의 범용 Sprite 객체에 얹혀 화면 좌표에 놓이고,
  행동(떠다님 · 손 따라감 · 손 피함 · 손 닿으면 빛남 · 돌아다님)을 고른다. 떠다님 · 따라감 · 피함 · 돌아다님은 모두 위치를
  움직여 하나만 쓴다. 따라감 · 피함 · 돌아다님은 반응 효과가 투명한 템플릿(GPU 입자 장 · 그림 휘젓기 · 그림 위 물결)에서만
  받는다(조립 단계에서 거부).
형식이 바뀔 수 있어 모르는 칸은 거부하지 않고 unknown_fields()로 경고만 돌려준다.
"""
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]*"   # 버전 폴더 안 파일 이름만(경로 · 상위 폴더 불가)
FILE_NAME = _NAME + "$"


class LayerDepth(StrEnum):
    far = "far"
    mid = "mid"
    near = "near"


DEPTH_ORDER = [LayerDepth.far, LayerDepth.mid, LayerDepth.near]   # 뒤 → 앞

# UE Look 재질 값의 허용 이름과 범위(runtime LookProfile).
SCALAR_RANGES = {
    "Roughness": (0.0, 1.0), "Metallic": (0.0, 1.0), "Specular": (0.0, 1.0),
    "EmissiveStrength": (0.0, 8.0), "NormalStrength": (0.0, 8.0), "DyeColorStrength": (0.0, 4.0),
}
COLOR_NAMES = {"Tint", "BaseColor"}   # RGB 0~1


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Layer(_Open):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    image: str = Field(pattern=_NAME + r"\.png$")
    depth: LayerDepth


class Behavior(StrEnum):
    float = "float"     # 제자리에서 위아래로 떠다님
    follow = "follow"   # 화면에 손이 있으면 그쪽으로 다가감(최대 4m)
    flee = "flee"       # 손이 가까이(3m) 오면 피함
    glow = "glow"       # 손이 그 객체 가까이(2.5m) 오면 밝아짐
    wander = "wander"   # 화면을 날아다니고 손이 가까이(3m) 오면 피함. 위에서 본 좌우 대칭 그림만(그림 위쪽이 머리)


MOVING = {Behavior.float, Behavior.follow, Behavior.flee, Behavior.wander}   # 위치를 쓰는 행동: 하나만


class SpriteObject(_Open):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=48)   # 바인딩 이름 Obj_<id>_<행동>이 64자 안
    image: str = Field(pattern=_NAME + r"\.png$")
    position: tuple[float, float] = Field(description="화면 좌표 (u, v), 0~1, 왼쪽 위가 (0, 0)")
    size: float = Field(0.15, gt=0, le=1, description="화면 너비 대비 가로 크기")
    behaviors: list[Behavior] = Field(default_factory=list)

    @field_validator("position")
    @classmethod
    def _on_screen(cls, position):
        if not all(0 <= value <= 1 for value in position):
            raise ValueError("position은 0~1")
        return position

    @field_validator("behaviors")
    @classmethod
    def _one_motion(cls, behaviors):
        if len(set(behaviors)) != len(behaviors):
            raise ValueError("행동이 겹침")
        if len(MOVING.intersection(behaviors)) > 1:
            raise ValueError(f"{sorted(MOVING.intersection(behaviors))}는 모두 위치를 움직여 하나만 고름")
        return behaviors


class MaterialValues(_Open):
    target: str                       # 층 id
    object: str | None = None         # 카탈로그 재질 객체 id. 없으면 그 층의 기본 객체
    values: dict[str, float | list[float]]

    @field_validator("values")
    @classmethod
    def _allowed(cls, values):
        problems = []
        for name, value in values.items():
            if name in SCALAR_RANGES:
                low, high = SCALAR_RANGES[name]
                if not isinstance(value, float) or not low <= value <= high:
                    problems.append(f"{name}는 {low}~{high} 숫자")
            elif name in COLOR_NAMES:
                if not isinstance(value, list) or len(value) != 3 or not all(0 <= c <= 1 for c in value):
                    problems.append(f"{name}는 0~1 RGB 3개")
            else:
                problems.append(f"{name}는 허용하지 않는 재질 값")
        if problems:
            raise ValueError("; ".join(problems))
        return values


class ArtworkAssets(_Open):
    format: Literal["artwork-assets"]
    version: Literal[1]
    spec_id: str | None = None
    layers: list[Layer] = Field(min_length=1)
    materials: list[MaterialValues] = Field(default_factory=list)
    objects: list[SpriteObject] = Field(default_factory=list, max_length=12)   # 한 장면 객체 수 상한(GPU 공유 PC)
    depth_map: str | None = Field(None, pattern=FILE_NAME)
    silhouette: str | None = Field(None, pattern=_NAME + r"\.json$")

    @model_validator(mode="after")
    def _check(self):
        problems = []
        ids = [layer.id for layer in self.layers]
        if len(set(ids)) != len(ids):
            problems.append("층 id가 겹침")
        depths = [layer.depth for layer in self.layers]
        if len(set(depths)) != len(depths):
            problems.append("깊이마다 층은 하나")
        problems += [f"재질 값 대상 {m.target}이 층에 없음" for m in self.materials if m.target not in ids]
        object_ids = [item.id for item in self.objects]
        if len(set(object_ids)) != len(object_ids):
            problems.append("객체 id가 겹침")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def back_layer(self) -> Layer:
        return min(self.layers, key=lambda layer: DEPTH_ORDER.index(layer.depth))

    def files(self) -> list[str]:
        """이 목록이 가리키는 파일 이름 전부."""
        return [layer.image for layer in self.layers] + [item.image for item in self.objects] + [name for name in (self.depth_map, self.silhouette) if name]

    def unknown_fields(self) -> list[str]:
        """모르는 칸(경고용). 예: layers[0].opacity"""
        found = [name for name in self.model_extra or {}]
        for kind, items in (("layers", self.layers), ("materials", self.materials), ("objects", self.objects)):
            found += [f"{kind}[{i}].{name}" for i, item in enumerate(items) for name in item.model_extra or {}]
        return found
