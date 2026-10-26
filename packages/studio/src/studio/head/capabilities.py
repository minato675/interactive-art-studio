"""렌더러별 지원 범위. 전시 디렉터가 고를 수 있는 선택지를 코드가 계산해 준다."""
from dataclasses import dataclass
from types import MappingProxyType

from contracts.families import CANVAS_FAMILIES, FAMILIES, REVEAL_FAMILIES
from contracts.schemas import CHIPS, MAX_ELEMENTS, Family, Reveal
from studio.assembly import ue_scene
from studio.generation.subjects import ALIASES


@dataclass(frozen=True)
class Capabilities:
    families: tuple[Family, ...]
    reveals: tuple[Reveal, ...]
    shape_subjects: tuple[str, ...]
    moods: tuple[str, ...]
    palettes: tuple[str, ...]
    max_elements: int
    canvas_families: tuple[Family, ...]   # 이 중 하나까지만 고를 수 있다
    reveal_families: MappingProxyType   # Reveal → 그 방식에 쓸 수 있는 표현 방식(Family 순서)

    def as_dict(self) -> dict:
        """JSON으로 기록하거나 지시문에 넣을 값."""
        return {
            "families": [f.value for f in self.families],
            "reveals": [r.value for r in self.reveals],
            "shape_subjects": list(self.shape_subjects),
            "moods": list(self.moods),
            "palettes": list(self.palettes),
            "max_elements": self.max_elements,
            "canvas_families": [f.value for f in self.canvas_families],
            "reveal_families": {r.value: [f.value for f in fams] for r, fams in self.reveal_families.items()},
        }


def capabilities(renderers: list[str]) -> Capabilities:
    """선택한 렌더러가 모두 처리할 수 있는 범위. 렌더러가 여럿이면 교집합이다."""
    ue = "ue" in renderers
    families = tuple(
        family for family, spec in FAMILIES.items()
        if spec.status != "todo" and (not ue or spec.module is not None)
    )
    allowed = set(Reveal)
    if ue:
        allowed &= ue_scene.SUPPORTED_REVEALS   # monkeypatch가 먹도록 호출 때마다 읽는다
    reveals = tuple(r for r in Reveal if r in allowed)
    return Capabilities(
        families=families,
        reveals=reveals,
        shape_subjects=tuple(ALIASES),
        moods=CHIPS["mood"],
        palettes=CHIPS["palette"],
        max_elements=MAX_ELEMENTS,
        canvas_families=tuple(f for f in Family if f in CANVAS_FAMILIES and f in families),
        reveal_families=MappingProxyType({
            r: tuple(f for f in Family if f in REVEAL_FAMILIES[r] and f in families) for r in reveals}),
    )
