"""검증 — 규칙 검사. 생성한 소재와 주인공 데이터가 풀·전달 전에 통과해야 하는 기준.

inspect_image: 배경 등 이미지 (UE 파일 규칙 · 16:9 · 최소 해상도 · 빈 화면 · 중복). controller pool.inspect_image와 같은 기준.
inspect_subject: 주인공 데이터 (실루엣 점 수 · 화면 안 · 차지 비율 / 흔적 개수 · 화면 안 · 시간 순서).
inspect_assets: UE 작품 소재 목록(assets.json)이 가리키는 파일 (있는지 · PNG 규칙 · 앞 층 알파 · 실루엣 기준).
UE scene 검사는 assembly/ue_scene.check (controller validate_scene + ue_limit_errors).
예정: 규칙 뒤의 독립 AI 판정(테마·스타일 일치, 마스크 품질)과 문제 유형별 수정 요청.
"""
import itertools
import json
import struct
import zlib
from pathlib import Path

from interactive_controller.validation.assets import MAX_BYTES, png_check, sha256_file
from sqlmodel import Session, select

from contracts.assets import ArtworkAssets
from db.models import Asset

ASPECT, ASPECT_TOLERANCE = 16 / 9, 0.02
MIN_SIZE = (1280, 720)
BLANK_RATIO = 0.02   # 다시 압축한(zlib 1) 크기 / 원본. 단색·단순 그라데이션은 약 0.01
MIN_AREA, MAX_AREA = 0.04, 0.6   # 실루엣이 화면에서 차지하는 비율(너무 작으면 안 보이고, 너무 크면 형상이 안 읽힘)


def _png_chunks(data: bytes):
    position = 8
    while position + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        yield kind, data[position + 8:position + 8 + length]
        if kind == b"IEND":
            break
        position += 12 + length


def _png_raw(data: bytes) -> bytes:
    return zlib.decompress(b"".join(body for kind, body in _png_chunks(data) if kind == b"IDAT"))


def png_has_alpha(data: bytes) -> bool:
    """알파 채널(회색+알파 · RGBA)이나 투명색(tRNS)이 있는지. 색 형식은 IHDR 10번째 바이트."""
    chunks = dict(_png_chunks(data))
    return chunks[b"IHDR"][9] in (4, 6) or b"tRNS" in chunks


def inspect_image(session: Session, path: Path) -> tuple[list[str], str | None]:
    """풀 입구 검사. (탈락 사유 목록, sha256). 사유가 없으면 통과."""
    try:
        width, height = png_check(path)
    except ValueError as error:
        return [f"UE 파일 규칙 위반: {error}"], None
    problems = []
    if abs(width / height - ASPECT) / ASPECT > ASPECT_TOLERANCE:
        problems.append(f"비율 {width}×{height}가 16:9가 아님")
    if width < MIN_SIZE[0] or height < MIN_SIZE[1]:
        problems.append(f"해상도 {width}×{height} < {MIN_SIZE[0]}×{MIN_SIZE[1]}")
    raw = _png_raw(path.read_bytes())
    ratio = len(zlib.compress(raw, 1)) / max(len(raw), 1)
    if ratio < BLANK_RATIO:
        problems.append(f"빈 화면에 가까움 (정보량 {ratio:.4f})")
    digest = sha256_file(path)
    if session.exec(select(Asset).where(Asset.sha256 == digest)).first():
        problems.append("같은 파일이 이미 풀에 있음")
    return problems, digest


def inspect_subject(data: dict) -> list[str]:
    """주인공 데이터 검사. 사유가 없으면 통과."""
    problems = []
    if data["kind"] == "silhouette":
        if len(data["points"]) < 200:
            problems.append("점이 200개 미만")
        if not all(0 <= u <= 1 and 0 <= v <= 1 for u, v in data["points"]):
            problems.append("화면 밖 점이 있음")
        if not MIN_AREA <= data["area"] <= MAX_AREA:
            problems.append(f"화면 차지 비율 {data['area']:.2f} (기준 {MIN_AREA}~{MAX_AREA})")
    else:
        steps = data["steps"]
        if len(steps) < 6:
            problems.append("발자국이 6개 미만")
        if not all(0.03 <= s["u"] <= 0.97 and 0.03 <= s["v"] <= 0.97 for s in steps):
            problems.append("화면 가장자리 밖 발자국")
        if any(b["t"] <= a["t"] for a, b in itertools.pairwise(steps)):
            problems.append("발자국 시간이 앞으로 가지 않음")
    return problems


def inspect_assets(folder: Path, assets: ArtworkAssets) -> list[str]:
    """소재 목록이 가리키는 파일 검사. 사유가 없으면 통과."""
    problems = [f"파일 없음: {name}" for name in assets.files() if not (folder / name).is_file()]
    images = [(f"층 {layer.id}", layer.image, layer is not assets.back_layer and "맨 뒤가 아닌 층은 알파가 있어야 함")
              for layer in assets.layers]
    images += [(f"객체 {item.id}", item.image, "오려 낸 객체 PNG는 알파가 있어야 함") for item in assets.objects]
    for label, image, alpha_reason in images:
        path = folder / image
        if not path.is_file():
            continue
        if path.stat().st_size > MAX_BYTES["png"]:
            problems.append(f"{label}: UE 파일 규칙 위반: {MAX_BYTES['png']}바이트 초과")
            continue
        try:
            png_check(path)
        except (ValueError, struct.error) as error:
            problems.append(f"{label}: UE 파일 규칙 위반: {error}")
            continue
        if alpha_reason and not png_has_alpha(path.read_bytes()):
            problems.append(f"{label}: {alpha_reason}")
    if assets.silhouette and (folder / assets.silhouette).is_file():
        try:
            data = json.loads((folder / assets.silhouette).read_text(encoding="utf-8"))
            if data["kind"] != "silhouette":
                raise ValueError(f"kind가 {data['kind']}")
            problems += [f"실루엣: {reason}" for reason in inspect_subject(data)]
        except (ValueError, KeyError, TypeError) as error:
            problems.append(f"실루엣 형식 오류: {error!r}")
    return problems
