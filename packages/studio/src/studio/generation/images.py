"""이미지 생성. 기본은 로컬 생성기(외부 호출 없음)이며, 같은 generate 인터페이스로 실제 이미지 API를 붙인다.

LocalImage는 색 그라데이션에 고운 결을 더한 1280×720 PNG를 만든다(표준 라이브러리만).
단순 그라데이션은 풀 입구 검사의 '빈 화면'에 걸리므로 결을 넣어 통과시킨다. 작품 품질 소재가 아니라 흐름 시험용이다.
"""
import random
import struct
import zlib
from typing import Protocol

SIZE = (1280, 720)
PALETTE_HEX = {"deep blue": "#123a6b", "teal": "#1f8a8a", "amber": "#c98a2b", "coral": "#d9705a",
               "navy": "#141c3a", "silver": "#9aa4b8", "lavender": "#9b87c9", "mint": "#8fd1b5"}
DEFAULT = ("#101828", "#3a5a80", "#c9e4f0")
GRAIN = bytes(i * 48 // 256 for i in range(256))   # 결의 세기 0~47 (밝기 상한 200과 더해도 255를 넘지 않음)


class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str, palette: list[str], seed: int) -> bytes: ...


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


class LocalImage:
    name = "local"

    def generate(self, prompt, palette, seed):
        colors = [PALETTE_HEX[name] for name in palette if name in PALETTE_HEX] or list(DEFAULT)
        if len(colors) == 1:
            colors.append("#0b0f1a")
        stops = [tuple(min(200, int(c[i:i + 2], 16)) for i in (1, 3, 5)) for c in colors]
        width, height = SIZE
        rng = random.Random(seed)
        rows = []
        for y in range(height):
            position = y / (height - 1) * (len(stops) - 1)
            low = min(int(position), len(stops) - 2)
            mix = position - low
            base = bytes(round(stops[low][c] * (1 - mix) + stops[low + 1][c] * mix) for c in range(3)) * width
            grain = rng.randbytes(width * 3).translate(GRAIN)
            # 바이트마다 더해도 올림이 생기지 않으므로(최대 247) 큰 정수 덧셈 한 번으로 줄 전체를 더한다.
            row = (int.from_bytes(base) + int.from_bytes(grain)).to_bytes(width * 3)
            rows.append(b"\x00" + row)
        header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
                + _chunk(b"IEND", b""))
