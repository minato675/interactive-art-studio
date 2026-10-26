"""검은 배경에 생성한 빛나는 요소를 알파 PNG로 바꾼다: 밝기 → 투명도, 색은 밝기로 나눠 원래 빛 색을 되살린다.

실행: uv run --no-project --with pillow python infra/generation/luma_alpha.py 원본.png 결과.png [--size 256] [--floor 8]
- 빛나는 요소(반딧불 · 빛나는 잎)용이다. 어두운 부분도 투명해지므로 불투명한 물체에는 쓰지 않는다.
- floor 이하 밝기는 완전 투명(검은 배경의 잡티 제거). size는 긴 변 픽셀(밉맵이 없어 화면 표시 크기에 맞춘다).
"""
import argparse


def to_rgba(r: int, g: int, b: int, floor: int) -> tuple[int, int, int, int]:
    """픽셀 하나: 가장 밝은 채널이 투명도, 색은 그 밝기로 나눠 되살린다."""
    peak = max(r, g, b)
    if peak <= floor:
        return 0, 0, 0, 0
    alpha = round((peak - floor) * 255 / (255 - floor))
    return r * 255 // peak, g * 255 // peak, b * 255 // peak, alpha


def main():
    from PIL import Image  # Pillow는 uv run --with로 임시로만 쓴다(계산 함수는 Pillow 없이 테스트)

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--floor", type=int, default=8)
    args = parser.parse_args()
    image = Image.open(args.source).convert("RGB")
    data = image.tobytes()
    pixels = bytearray()
    for i in range(0, len(data), 3):
        pixels.extend(to_rgba(data[i], data[i + 1], data[i + 2], args.floor))
    result = Image.frombytes("RGBA", image.size, bytes(pixels))
    box = result.getbbox()   # 투명 아닌 영역만 남기고 여백 약간
    if box:
        pad = max(4, (box[2] - box[0]) // 20)
        result = result.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                              min(result.width, box[2] + pad), min(result.height, box[3] + pad)))
    result.thumbnail((args.size, args.size), Image.LANCZOS)
    result.save(args.target, "PNG")
    print(f"{args.source} -> {args.target} {result.width}x{result.height} RGBA")


if __name__ == "__main__":
    main()
