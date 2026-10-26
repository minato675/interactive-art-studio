"""PNG 읽기(8비트 RGB/RGBA, 비인터레이스) — 새 의존성 없이 표준 라이브러리로. UE 화면 캡처와 층 알파 비율에 쓴다."""
import struct
import zlib


def decode_png(data: bytes, alpha: bool = False) -> tuple[int, int, bytes]:
    """(너비, 높이, RGB 바이트). alpha=True면 픽셀마다 알파 1바이트만(RGB PNG는 모두 255)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG가 아님")
    position, header, idat = 8, None, []
    while position + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        body = data[position + 8:position + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        position += 12 + length
    if header is None:
        raise ValueError("IHDR 없음")
    width, height, depth, color, _, _, interlace = header
    if depth != 8 or color not in (2, 6) or interlace:
        raise ValueError(f"지원하지 않는 PNG 형식(depth={depth}, color={color}, interlace={interlace})")
    channels = 3 if color == 2 else 4
    raw, stride = zlib.decompress(b"".join(idat)), width * channels
    if len(raw) != height * (stride + 1):
        raise ValueError("PNG 이미지 데이터 길이가 크기와 맞지 않음")
    out, previous = bytearray(), bytearray(stride)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if kind > 4:
            raise ValueError(f"모르는 PNG 필터 {kind}")
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up, corner = previous[i], previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 255
            elif kind == 2:
                line[i] = (line[i] + up) & 255
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        previous = line
        if alpha:
            out += bytes([255]) * width if channels == 3 else line[3::4]
        else:
            out += line if channels == 3 else bytes(b for j, b in enumerate(line) if j % 4 != 3)
    return width, height, bytes(out)
