"""Head 전송 전용 축소 사본. 원본 PNG와 원본 픽셀 기반 계측은 보존한다."""
import base64
import hashlib
import math
import struct
import zlib

from studio.verify.png import decode_png


def preview(url, maximum=768):
    if not url.startswith("data:image/png;base64,"):
        return url, {"kind": "original", "reason": "not_png"}
    raw = base64.b64decode(url.split(",", 1)[1], validate=True)
    width, height, rgb = decode_png(raw)
    scale = max(1, math.ceil(max(width, height) / maximum))
    if scale == 1:
        return url, {"kind": "original", "width": width, "height": height}
    target_width, target_height = math.ceil(width / scale), math.ceil(height / scale)
    scanlines = bytearray()
    for y in range(0, height, scale):
        scanlines.append(0)
        for x in range(0, width, scale):
            points = [3 * (sy * width + sx) for sy in range(y, min(y + scale, height))
                      for sx in range(x, min(x + scale, width))]
            scanlines.extend(sum(rgb[i + c] for i in points) // len(points) for c in range(3))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", target_width, target_height, 8, 2, 0, 0, 0)
    result = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
              + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(result).decode(), {
        "kind": "box_average_preview", "source_width": width, "source_height": height,
        "width": target_width, "height": target_height, "sha256": hashlib.sha256(result).hexdigest(),
        "bytes": len(result), "original_preserved": True, "fine_detail_verifiable": False}
