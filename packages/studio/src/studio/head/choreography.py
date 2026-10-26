"""AI — 안무: 드러내는 방식마다 단계(이름·시간·형상으로 끌리는 힘)를 정한다. 숫자는 코드가 정한다.

형상(formation): 흩어져 유영 → 모여듦 → 형상 유영 → 흩어짐 → (반복)
흔적(trace):     고요 → 지나감(발자국이 찍힘) → 사라짐 → (반복)
없음(none):      장(field) 하나가 계속 반복
반응 세기 칩(gentle/normal/strong)은 템포를 바꾼다: 살며시는 느리게, 힘차게는 빠르게.
"""
from contracts.schemas import Phase, Reveal

TEMPO = {"gentle": 1.3, "normal": 1.0, "strong": 0.75}

BASE = {
    Reveal.formation: [("흩어져 유영", 20, 0, 0, False), ("모여듦", 8, 0, 1, False),
                       ("형상 유영", 15, 1, 1, False), ("흩어짐", 3, 0, 0, True)],
    Reveal.trace: [("고요", 5, 0, 0, False), ("지나감", 20, 0, 0, False), ("사라짐", 8, 0, 0, False)],
    Reveal.none: [("장", 40, 0, 0, False)],
}


def phases(reveal: Reveal, intensity: str = "normal") -> list[Phase]:
    scale = TEMPO.get(intensity, 1.0)
    return [Phase(name=name, seconds=round(seconds * scale, 2), target_from=a, target_to=b, burst=burst)
            for name, seconds, a, b, burst in BASE[reveal]]


def total_seconds(steps: list[Phase]) -> float:
    return sum(p.seconds for p in steps)
