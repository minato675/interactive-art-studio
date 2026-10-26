"""TD 연결: 의미 → 작품 데이터(artwork.json) → 검사. 전달은 exhibition/delivery.publish_version.

artwork.json은 렌더러와 무관한 작품 설명이다. TD는 이것을 읽어 장면 파라미터를 바꾼다(td/artwork_reader.py).
숫자(시간·힘·색)는 여기서 정하고, TD는 그대로 쓴다.
"""
from pathlib import Path

from contracts.schemas import Element, Intent, Mapping, Phase, Reveal
from studio.generation.images import DEFAULT, PALETTE_HEX

SCHEMA_VERSION = 1
SUBJECT_FILES = {Reveal.formation: "silhouette.json", Reveal.trace: "path.json"}


def build_spec(intent: Intent, elements: list[Element], mappings: list[Mapping], phases: list[Phase],
               files: dict[str, Path]) -> dict:
    palette = [PALETTE_HEX[name] for name in intent.palette if name in PALETTE_HEX] or list(DEFAULT)
    subject = None
    if intent.reveal is not Reveal.none and intent.subject:
        subject = {"name": intent.subject, "reveal": intent.reveal.value, "file": SUBJECT_FILES[intent.reveal]}
    return {
        "SchemaVersion": SCHEMA_VERSION,
        "title": intent.title,
        "mood": intent.mood,
        "palette": palette,
        "background": "background.png" if "background.png" in files else None,
        "subject": subject,                     # 보이지 않는 주인공 (그리지 않음)
        "phases": [p.model_dump() for p in phases],   # 반복되는 안무
        "layers": [{"family": e.family.value, "position": e.position} for e in elements],
        "reactions": [m.model_dump(mode="json", exclude_none=True) for m in mappings],
    }


def check(spec: dict, files: dict[str, Path]) -> list[str]:
    problems = []
    if not spec["layers"]:
        problems.append("표현 레이어가 없음")
    if not spec["phases"] or any(p["seconds"] <= 0 for p in spec["phases"]):
        problems.append("안무 단계 시간이 잘못됨")
    if spec["subject"] and spec["subject"]["file"] not in files:
        problems.append(f"주인공 데이터 {spec['subject']['file']}가 없음")
    if spec["background"] and spec["background"] not in files:
        problems.append("배경 파일이 없음")
    return problems
