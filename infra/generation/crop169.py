"""생성 이미지를 가운데 기준 16:9로 자른다(이미지 모델은 1536x1024 같은 3:2로 낸다).

실행: uv run --no-project --with pillow python infra/generation/crop169.py 원본.png 결과.png
알파가 있으면 유지하고, 없으면 RGB로 저장한다.
"""
import sys


def crop_box(width: int, height: int) -> tuple[int, int, int, int]:
    """가운데 기준 16:9 영역 (left, top, right, bottom)."""
    if width * 9 > height * 16:   # 너무 넓음: 좌우를 자른다
        new_w, new_h = height * 16 // 9, height
    else:                          # 너무 높음: 위아래를 자른다
        new_w, new_h = width, width * 9 // 16
    left, top = (width - new_w) // 2, (height - new_h) // 2
    return left, top, left + new_w, top + new_h


def main():
    from PIL import Image  # Pillow는 uv run --with로 임시로만 쓴다(계산 함수는 Pillow 없이 테스트)

    source, target = sys.argv[1], sys.argv[2]
    image = Image.open(source)
    mode = "RGBA" if "A" in image.getbands() else "RGB"
    result = image.convert(mode).crop(crop_box(*image.size))
    result.save(target, "PNG")
    print(f"{source} {image.width}x{image.height} -> {target} {result.width}x{result.height} {mode}")


if __name__ == "__main__":
    main()
