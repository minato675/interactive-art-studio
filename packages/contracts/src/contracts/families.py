"""8가지 표현 방식: UE 모듈, 필요한 소재, 반응 메뉴.

needs는 소재 종류(풀의 kind)다. 고른 방식이 쓰는 소재만 만든다(유체를 고르면 객체 소재는 없음).
status: done=UE·controller에 있음, part=일부, todo=UE 모듈 연결 예정.
"""
from dataclasses import dataclass

from .schemas import Family, Reveal


@dataclass(frozen=True)
class FamilySpec:
    label: str
    module: str | None          # UE 모듈(None = 아직 없음)
    needs: tuple[str, ...]
    optional: tuple[str, ...]
    reactions: tuple[str, ...]  # 첫 번째가 기본값
    status: str


FAMILIES: dict[Family, FamilySpec] = {
    Family.object: FamilySpec("객체 반응", "Object", ("background",), (), ("bloom", "glow"), "done"),
    Family.paint: FamilySpec("그리기·혼합", "Paint", ("background",), (), ("draw",), "done"),
    Family.particles: FamilySpec("입자·흐름", "Particles", (), (), ("swirl", "push"), "done"),
    Family.media: FamilySpec("영역·미디어", None, ("background", "panel"), ("loop",), ("reveal",), "todo"),
    Family.deformation: FamilySpec("표면 변형", "Deformation", ("background",), (), ("ripple", "press"), "done"),
    Family.fluid: FamilySpec("유체·장", "Fluid", ("background",), ("fluid_image",), ("stir",), "part"),
    Family.flock: FamilySpec("군집", "Flock", ("background",), ("skin",), ("follow", "scatter", "ignore"), "done"),
    Family.audio: FamilySpec("시청각", None, ("background", "sound"), (), ("pulse",), "todo"),
}

# 화면 가운데 캔버스를 같이 쓰는 방식은 한 작품에 하나만.
CANVAS_FAMILIES = frozenset({Family.paint, Family.fluid, Family.deformation})

# 드러내는 방식마다 쓸 수 있는 표현 방식.
# 형상: 작은 것들이 모여야 하므로 군집·입자. 흔적: 자국이 남는 그리기·유체·표면 변형·입자.
REVEAL_FAMILIES: dict[Reveal, frozenset[Family]] = {
    Reveal.formation: frozenset({Family.flock, Family.particles}),
    Reveal.trace: frozenset({Family.paint, Family.fluid, Family.deformation, Family.particles}),
    Reveal.none: frozenset(Family),
}
