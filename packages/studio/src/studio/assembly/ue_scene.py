"""UE 연결: 의미 → UE scene.json 변환 → 검사. 전달은 exhibition/delivery.publish_version.

숫자 변환과 검사는 GitLab controller 확장 구현을 재사용한다. 회사 원본으로 확인된 관측 코드와 구분한다.
  to_brief      우리 의도·구성·반응 → controller 브리프 형식
  compile_scene controller compile_brief: 기준 맵 프리셋 + 반응을 UE 허용 범위 숫자로
  check         validate_scene(계약·카탈로그) + ue_limit_errors(C++ 범위·교차 조건)

현재 UE 조립 어댑터의 형상·흔적 미지원 여부는 unsupported()로 확인한다.
개발 설정 both에서는 별도 TD 작품 파일을 준비할 수 있다. 실제 전시 TD 역할은 입력 센서다.
이 어댑터의 지원 범위를 회사의 최신 UE 전체 기능 제한으로 일반화하지 않는다.

UE와는 네트워크가 아니라 파일(JSON)로 주고받는다.
  delivery/versions/<V>/scene.json (+ 배경 그림)   공개 후 수정하지 않음
  delivery/release.json                             {"SchemaVersion":1,"Version":"<V>"} — 마지막에 원자 교체
  delivery/release.json.status.json                 UE가 적용 결과를 기록
"""
from functools import cache
from pathlib import Path

from interactive_controller.orchestration import brief as cbrief
from interactive_controller.orchestration.compiler import accepts_background_image, compile_brief, load_presets
from interactive_controller.orchestration.reactions import default_reaction, ue_limit_errors
from interactive_controller.validation.catalog import load_catalog
from interactive_controller.validation.scene import validate_scene

from contracts.schemas import Element, Family, InputSource, Intent, Mapping, Reveal
from studio.generation.images import PALETTE_HEX

THEMES = {"ocean": "ocean", "forest": "forest", "space": "space", "flowers": "flowerfield"}
BACKGROUND_FILE = "background.png"
SUPPORTED_REVEALS = frozenset({Reveal.none})   # 현재 UE 조립 어댑터가 처리하는 드러내는 방식


@cache
def _presets():
    return load_presets()


@cache
def _catalog():
    return load_catalog()


def unsupported(intent: Intent, elements: list[Element]) -> list[str]:
    """UE 런타임이 아직 못 하는 것. 비어 있으면 UE로 보낼 수 있다."""
    reasons = [f"UE 모듈이 없는 방식 {e.family.value}" for e in elements if e.module is None]
    if intent.reveal not in SUPPORTED_REVEALS:
        reasons.append(f"드러내는 방식 {intent.reveal.value}은 현재 UE 조립 어댑터에 연결되지 않음")
    return reasons


def _modules(element: Element, theme: str, reaction: str) -> str:
    """표현 방식 → controller 모듈. 객체는 반응(bloom/glow)으로, 군집은 테마로 고른다."""
    if element.family is Family.object:
        return "orb" if reaction == "glow" else "flower"
    if element.family is Family.flock:
        return "fish" if theme == "ocean" else "birds"
    return element.family.value   # paint / fluid / deformation / particles 는 이름이 같다


def to_brief(intent: Intent, elements: list[Element], mappings: list[Mapping], theme_key: str, text: str) -> dict:
    theme = THEMES.get(theme_key) or cbrief.rule_theme(text)
    direction = cbrief.rule_direction(theme)
    for field, value in (("title", intent.title), ("concept", intent.concept), ("mood", ", ".join(intent.mood))):
        if value:
            direction[field] = value
    if intent.palette:
        unknown = [name for name in intent.palette if name not in PALETTE_HEX]
        if unknown:
            raise ValueError(f"UE 색상 변환표에 없는 팔레트: {unknown}")
        direction["palette_names_en"] = list(intent.palette) if len(intent.palette) > 1 else intent.palette * 2
        colors = [PALETTE_HEX[name] for name in intent.palette]
        # 기존 브리프는 최소 두 색을 요구한다. 단색은 같은 색을 반복하며 다른 색을 추가하지 않는다.
        direction["palette"] = colors if len(colors) > 1 else colors * 2
    problems = cbrief.validate_direction(direction)
    if problems:
        raise ValueError("UE 연출서 변환 불가: " + "; ".join(problems))
    # 기존 프로토타입은 미연결 입력을 pipeline 알림에 남기고 연결된 손·터치만 UE에 전달한다.
    hand = {m.family: m for m in mappings if m.source in (InputSource.hand, InputSource.touch)}
    composition = {"elements": [], "background_reveal": False, "camera_loop": False, "reactions": []}
    for element in elements:
        mapping = hand.get(element.family)
        module = _modules(element, theme, mapping.reaction if mapping else "")
        if element.position not in cbrief.POSITIONS:
            raise ValueError(f"UE에 연결되지 않은 위치: {element.position}")
        if module not in cbrief.POSITIONED and element.position != "center":
            raise ValueError(f"현재 {module} 템플릿은 center 위치만 지원: {element.position}")
        composition["elements"].append({"module": module, "height": "middle", "amount": "some",
                                        "position": element.position})
        if mapping:
            memory = mapping.memory or default_reaction(module)["memory"]
            composition["reactions"].append({"module": module, "reaction": mapping.reaction,
                                             "strength": mapping.strength, "memory": memory})
    return cbrief.merge(direction, composition)


def compile_scene(brief: dict, assets: dict[str, Path]) -> tuple[dict, dict[str, Path]]:
    """(scene, 함께 보낼 파일). 배경 그림은 버전 폴더에 background.png로 복사한다."""
    files = {}
    if "background" in assets and accepts_background_image(brief):
        files[BACKGROUND_FILE] = assets["background"]
    scene = compile_brief(brief, _presets(), background_image=BACKGROUND_FILE if files else None)
    return scene, files


def check(brief: dict, scene: dict) -> list[str]:
    return cbrief.validate_brief(brief) + validate_scene(scene, catalog=_catalog()).errors + ue_limit_errors(scene)
