"""Worker — 보이지 않는 주인공 데이터.

주인공(고래·사슴…)은 화면에 그리지 않는다. 렌더러가 쓸 데이터만 만든다.
  형상(formation): 실루엣 윤곽 → 점 목록. 작은 것들이 이 점으로 모였다 흩어진다.  → silhouette.json
  흔적(trace):     걸음 경로 + 박자 → 발자국 목록. 보이지 않는 주인공이 지나간 자리에 자국이 남는다. → path.json

실루엣 공급자:
  LocalShapes: 코드로 그린 기본 윤곽(고래·물고기·새·나비). 목록에 없는 주인공은 오류 처리한다.
  실제 운영: AI 이미지("단색 실루엣, 흰 배경") → 마스크 → 윤곽. 같은 outline 인터페이스로 붙인다.
좌표는 화면 기준 0~1 (u: 왼→오, v: 위→아래).
"""
import json
import math
import random
import uuid
from pathlib import Path
from typing import Protocol

from sqlmodel import Session, select

from contracts.schemas import Intent, Reveal
from db.models import Asset
from studio.verify.checks import MIN_AREA, inspect_subject

POINTS = 600              # 형상 점 개수(작은 물고기·입자 수의 기준)
EDGE_SHARE = 0.4          # 점 중 윤곽선 위에 두는 비율(나머지는 내부)

# 옆모습 기본 윤곽(가로·세로 0~1 상자 안). 시계 방향 다각형.
SHAPES = {
    "whale": [(0.0, 0.52), (0.08, 0.38), (0.25, 0.28), (0.45, 0.25), (0.62, 0.3), (0.75, 0.4), (0.84, 0.45),
              (0.92, 0.3), (1.0, 0.18), (0.97, 0.45), (1.0, 0.75), (0.9, 0.6), (0.82, 0.58), (0.7, 0.66),
              (0.5, 0.75), (0.3, 0.78), (0.18, 0.72), (0.06, 0.64)],
    "fish": [(0.0, 0.5), (0.2, 0.25), (0.5, 0.2), (0.72, 0.35), (0.85, 0.2), (1.0, 0.1), (0.9, 0.5), (1.0, 0.9),
             (0.85, 0.8), (0.72, 0.65), (0.5, 0.8), (0.2, 0.75)],
    "bird": [(0.0, 0.55), (0.25, 0.45), (0.4, 0.05), (0.55, 0.0), (0.6, 0.4), (0.8, 0.42), (1.0, 0.5),
             (0.8, 0.6), (0.6, 0.6), (0.45, 0.95), (0.35, 0.9), (0.3, 0.62)],
    "butterfly": [(0.5, 0.35), (0.3, 0.0), (0.05, 0.05), (0.0, 0.35), (0.2, 0.5), (0.05, 0.75), (0.2, 1.0),
                  (0.45, 0.7), (0.5, 0.6), (0.55, 0.7), (0.8, 1.0), (0.95, 0.75), (0.8, 0.5), (1.0, 0.35),
                  (0.95, 0.05), (0.7, 0.0)],
}
ALIASES = {"고래": "whale", "물고기": "fish", "새": "bird", "나비": "butterfly"}
# 발로 걷는 주인공만 좌우 교대 발자국을 남긴다. 나머지(혜성·고래·바람…)는 이어진 자취를 남긴다.
LEGGED = {"사슴", "말", "곰", "늑대", "여우", "고양이", "개", "사람", "코끼리", "deer", "horse", "bear", "wolf", "fox",
          "cat", "dog", "human", "elephant"}


class OutlineProvider(Protocol):
    name: str

    def outline(self, subject: str) -> tuple[list[tuple[float, float]], str]: ...


class LocalShapes:
    name = "local-shapes"

    def outline(self, subject):
        key = ALIASES.get(subject, subject.lower())
        if key in SHAPES:
            return SHAPES[key], self.name
        raise ValueError(f"현재 실루엣 공급자가 지원하지 않는 주인공: {subject}")


def _inside(polygon, u, v):
    hit = False
    for (u1, v1), (u2, v2) in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if (v1 > v) != (v2 > v) and u < (u2 - u1) * (v - v1) / (v2 - v1) + u1:
            hit = not hit
    return hit


def _area(polygon):
    return abs(sum(u1 * v2 - u2 * v1 for (u1, v1), (u2, v2) in zip(polygon, polygon[1:] + polygon[:1],
                                                                       strict=True))) / 2


def place(shape, width=0.6, center=(0.5, 0.5)):
    """0~1 상자 윤곽을 화면에 놓는다(가로 폭 width, 화면 비 16:9를 고려해 세로를 맞춘다)."""
    height = width * 16 / 9 * 0.5   # 옆모습은 대략 가로 2 : 세로 1
    return [(center[0] + (u - 0.5) * width, center[1] + (v - 0.5) * height) for u, v in shape]


def sample_points(polygon, count, rng):
    """윤곽선 위(EDGE_SHARE) + 내부 점. 윤곽이 있어야 형상이 또렷하게 읽힌다.
    면적이 0에 가까운 윤곽은 내부 점을 못 뽑으므로 시도 횟수에 상한을 둔다."""
    edges = list(zip(polygon, polygon[1:] + polygon[:1], strict=True))
    lengths = [math.dist(a, b) for a, b in edges]
    total = sum(lengths)
    points = []
    for _ in range(int(count * EDGE_SHARE)):
        distance = rng.random() * total
        for (a, b), length in zip(edges, lengths, strict=True):
            if distance <= length:
                k = distance / length if length else 0
                points.append((a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k))
                break
            distance -= length
    us, vs = [u for u, _ in polygon], [v for _, v in polygon]
    attempts = count * 200
    while len(points) < count:
        attempts -= 1
        if attempts < 0:
            raise RuntimeError("실루엣 안쪽 점을 뽑지 못함 (윤곽이 너무 가늘거나 비어 있음)")
        u, v = rng.uniform(min(us), max(us)), rng.uniform(min(vs), max(vs))
        if _inside(polygon, u, v):
            points.append((u, v))
    return [[round(u, 4), round(v, 4)] for u, v in points]


def silhouette(subject, provider: OutlineProvider, rng) -> dict:
    shape, source = provider.outline(subject)
    if source == "placeholder":
        raise ValueError(f"주인공 {subject}을 대체 형상으로 바꿀 수 없음")
    polygon = place(shape)
    area = _area(polygon)
    if area < MIN_AREA:   # 점을 뽑기 전에 거른다(면적 0이면 표본 추출이 끝나지 않음)
        raise RuntimeError(f"실루엣 면적 {area:.3f}가 기준 {MIN_AREA}보다 작음")
    return {"kind": "silhouette", "subject": subject, "source": source, "outline": [list(p) for p in polygon],
            "area": round(area, 4), "points": sample_points(polygon, POINTS, rng)}


def footsteps(subject, rng, seconds=20.0) -> dict:
    """화면을 천천히 가로지르는 자취. t는 안무의 '지나감' 단계 시작부터 센 초다.
    발로 걷는 주인공은 0.6초마다 좌우 교대 발자국(L/R), 나머지는 0.2초마다 이어진 자취(C)."""
    legged = subject in LEGGED
    stride = 0.6 if legged else 0.2
    start_v, bend, phase = rng.uniform(0.4, 0.65), rng.uniform(0.06, 0.15), rng.uniform(0, math.pi)
    steps, t, side = [], 0.0, 1
    while t <= seconds:
        k = t / seconds
        u = 0.08 + 0.84 * k
        v = start_v + bend * math.sin(phase + k * math.pi * 1.5)
        offset = side * 0.015 if legged else 0.0
        steps.append({"t": round(t, 3), "u": round(u, 4), "v": round(v + offset, 4),
                      "side": ("L" if side > 0 else "R") if legged else "C"})
        t, side = t + stride, -side
    return {"kind": "path", "subject": subject, "source": "local-path", "gait": "steps" if legged else "trail",
            "seconds": seconds, "steps": steps}


def prepare(session: Session, intent: Intent, provider: OutlineProvider, data_dir: Path,
            rng: random.Random, trace_seconds: float = 20.0,
            status: str = "approved", reuse: bool = True) -> tuple[dict[str, Path], list[str]]:
    """주인공 데이터 파일을 준비한다. 실루엣은 풀에 넣어 테마와 상관없이 다시 쓴다(같은 고래를 다른 색으로).
    status="pending"이면 새 실루엣은 사람이 승인할 때까지 다시 쓰지 않는다. reuse=False(전시 모드)면 항상 새로 만든다."""
    if intent.reveal is Reveal.none or not intent.subject:
        return {}, ["주인공 없음 — 장(field)만"]
    created = session.info.setdefault("new_files", [])
    folder = data_dir / "subjects"
    folder.mkdir(parents=True, exist_ok=True)
    if intent.reveal is Reveal.formation:
        query = select(Asset).where(Asset.kind == "silhouette", Asset.theme == intent.subject,
                                    Asset.status == "approved").order_by(Asset.uses, Asset.id)
        asset = session.exec(query).first() if reuse else None
        if asset:
            asset.uses += 1
            session.add(asset)
            return {"silhouette.json": Path(asset.path)}, [f"실루엣: 풀에서 사용 ({intent.subject})"]
        data, name = silhouette(intent.subject, provider, rng), "silhouette.json"
    else:
        data, name = footsteps(intent.subject, rng, trace_seconds), "path.json"
    problems = inspect_subject(data)
    if problems:
        raise RuntimeError(f"주인공 데이터 검사 탈락: {', '.join(problems)}")
    path = folder / f"{data['kind']}_{uuid.uuid4().hex[:10]}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    created.append(path)
    log = f"{name}: 새로 생성({data['source']}) → 검사 통과"
    if data["kind"] == "silhouette":
        session.add(Asset(kind="silhouette", theme=intent.subject, path=path.as_posix(), uses=1, status=status))
        log += " → 풀 저장"
    return {name: path}, [log]
