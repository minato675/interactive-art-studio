"""자동 생성 실행기(generation_worker.py)용 생성 프로그램: task.json → Codex 이미지 생성 → 규격 가공 · 검사 → 결과 폴더.

실행(키트의 generation.local.json handler 배열이 부른다):
  uv run --no-project --python 3.11 --with pillow python infra/generation/handler.py \
      [--records C:/Ranel/generation/records] --request <task.json> --output <빈 결과 폴더>
결과 폴더에는 conditions.output_name 파일 하나만 둔다. 지시문 · Codex 원본 · 로그는 기록 폴더에 시도별로 남긴다.
그림이 안 나오거나 규격에 걸리면 실패 이유를 지시문에 덧붙여 다시 만든다(최대 --attempts번, 전체 --budget-seconds 안).
종료 코드: 0 성공, 2 입력 오류, 3 Codex 실패 · 결과 없음, 4 규격 검사 실패(마지막 시도 기준). 화풍 · 내용 검수는 하지 않는다.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from crop169 import crop_box  # 스크립트로 실행할 때
except ImportError:   # 테스트에서 패키지로 불러올 때
    from infra.generation.crop169 import crop_box

EXIT_INPUT, EXIT_CODEX, EXIT_SPEC = 2, 3, 4
ASPECT = 16 / 9
ASPECT_TOLERANCE = 0.02   # UE 조립기가 층 비율 경고를 내는 기준과 같음
MAX_SIDE, MAX_BYTES = 4096, 32 * 2**20   # UE 외부 그림 로더 제한(controller validation)
CLEAR_MIN = 0.01   # 객체 PNG의 투명 픽셀 최소 비율(조립기 CLEAR_ALPHA)
MARGIN = 0.06      # 객체 둘레에 남길 투명 여백(긴 변 대비)
BLACK_FLOOR, BLACK_RAMP = 8, 24   # 검은 배경 키: floor 이하 완전 투명, 그 위 ramp 구간에서 불투명으로
CODEX_MODEL = "gpt-6-astra"
# 재생성 한도. 실행기가 handler를 timeout_seconds(기본 1800초)까지만 기다리고, 같은 지시로 계속 실패하는 경우가 있으며
# 시도마다 Codex 사용량이 나가므로 횟수와 전체 시간을 함께 제한한다.
MAX_ATTEMPTS = 3
BUDGET_SECONDS = 1500        # 실행기 기본 timeout(1800초)보다 짧게
ATTEMPT_TIMEOUT = 600        # Codex 한 번의 상한(보통 1~2분)
MIN_ATTEMPT_SECONDS = 120    # 남은 예산이 이보다 적으면 새 시도를 시작하지 않는다

PREAMBLE = """You are an image worker for a real-time interactive media art installation.
Rules:
- Work ONLY inside the current directory. Reference images (if any) are in ./refs/ and are attached.
- Use your built-in image generation tool. Generate exactly ONE image.
- Save it at EXACTLY this relative path: {output}
- Do not create or modify code files. Reply with only the saved file path.
"""
# 화풍 · 내용은 Head 지시(마스터 담당)를 따른다. 여기에는 화풍과 무관하게 UE 소재로 쓰면 안 되는 결함만 금지한다.
AVOID = "Do not include text, letters, logos, watermarks, signatures, frames, borders or UI elements."
# 에셋 잘림 · 크기 검사. 생성 이미지는 가장자리까지 아주 옅은 알파(1~31)가 퍼져 있어서, 어느 정도 불투명한 픽셀만 센다.
SOLID_ALPHA = 32
EDGE_BAND = 0.01           # 가장자리 띠 두께(짧은 변 대비)
EDGE_TOUCH_LIMIT = 0.02    # 띠 픽셀 중 불투명 비율이 이보다 크면 객체가 잘린 것으로 본다
MIN_OBJECT_SIDE = 0.2      # 불투명 영역의 긴 변이 이미지의 이 비율보다 작으면 너무 작다
PALETTE_COLORS = 5         # 에셋 지시에 넣을 배경 대표 색 수
# 배경 세부 · 에셋 색 조화. 2026-10-23 실제 결과(숲 배경 4장, 에셋 3장)로 정한 느슨한 기준이며 명백한 불량만 거른다.
# 배경: 세부(가장자리 변화량) 22.9~33.9 / 단색 0.6 · 단순 그라데이션 3.4. 밝기 변화 25.1~57.3 / 단색 0.0
# 에셋: 배경과의 색감 거리(Lab a·b, 밝기 제외) 3.7~5.2 / 숲 위 형광 분홍 93.4 · 금빛 숲 위 파랑 90.3
MIN_CANVAS_DETAIL = 8.0         # 그림 휘젓기 캔버스 배경: 휘저어도 변화가 보일 만큼의 세부
MIN_BACKGROUND_SPREAD = 5.0     # 모든 배경: 거의 단색이면 불량
MAX_COLOR_DISTANCE = 40.0       # 에셋 색감이 배경 대표 색 어디에서도 이만큼 멀면 불량
CHROMA_KEY = (0, 255, 0)        # 투명 배경이 안 나올 때 재생성에서 요청하는 단색 초록
CHROMA_NEAR, CHROMA_FAR = 60, 140   # 초록과의 거리: near 이하 완전 투명, far 이상 불투명
USAGE = {
    "canvas": "The WHOLE image is the artwork itself: visitors' hands will stir and smear it like liquid paint, "
              "so keep rich forms, flowing gradients and fine detail across the entire frame.",
    "display": "This plate is shown behind real-time particles: keep a calm, readable composition with clear depth.",
    "style_reference": "This plate is mostly hidden behind an effect canvas and is used as the color and lighting "
                       "reference for the objects: make the palette and light direction clear.",
}


def role_of(task: dict) -> str:
    conditions = task["generation"].get("conditions") or {}
    return conditions.get("role") or ("background" if task["kind"] == "background_generate" else "object")


def output_name(task: dict) -> str:
    conditions = task["generation"].get("conditions") or {}
    return conditions.get("output_name") or ("background.png" if role_of(task) == "background" else "asset-001.png")


def reference_files(task: dict) -> list[Path]:
    """선행 작업 결과 중 이미지 파일(에셋 작업의 실제 배경). 위치는 <input_root>/<선행 작업 ID>/<파일 이름>."""
    root, found = Path(task["input_root"]), []
    for parent in task.get("inputs", []):
        for ref in (parent.get("result") or {}).get("files", []):
            path = root / parent["id"] / ref["name"]
            if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and path.is_file():
                found.append(path)
    return found


def retry_note(name: str, failure: dict) -> str:
    """이전 시도가 왜 거부됐는지 다음 지시문에 붙일 문장. 같은 실수를 되풀이하지 않게 구체적으로 적는다."""
    if failure["result"] == "codex_failed":
        said = f' Your previous reply was: "{failure["last_message"]}".' if failure.get("last_message") else ""
        return (f"RETRY: the previous attempt did not save an image at out/{name}.{said} "
                f"You must generate exactly one image with your image tool and save it at out/{name}.")
    hints, problems = [], failure["problems"]
    if any("alpha" in p or "알파" in p for p in problems):
        hints.append("The background must be fully transparent. If that is impossible, use a flat pure green "
                     "#00FF00 background with nothing else on it, and do not use that green in the object.")
    if "background_too_flat" in problems:
        hints.append("Fill the whole frame with rich, clearly visible forms and fine detail; avoid flat or empty areas.")
    if "asset_color_mismatch" in problems:
        hints.append("Use colors that clearly belong to the background's palette listed above.")
    if any("16:9" in p for p in problems):
        hints.append("Use a landscape 1536x1024 canvas.")
    if "asset_touches_edge" in problems:
        hints.append("Keep the WHOLE object inside the frame with at least 8% empty margin on every side; "
                     "no part may touch or cross the image edges.")
    if "asset_too_small" in problems:
        hints.append("Make the object larger: it should span about 50-80% of the canvas.")
    if "asset_empty" in problems:
        hints.append("Draw a clearly visible object; the image must not be empty or almost fully transparent.")
    return ("RETRY: the previous image was rejected by the automatic check: " + "; ".join(failure["problems"])
            + ". " + " ".join(hints)).strip()


def background_palette(path: Path, count: int = PALETTE_COLORS) -> list[tuple[str, float]]:
    """배경 이미지의 대표 색(많은 순)과 비율. 읽을 수 없으면 빈 목록."""
    from PIL import Image

    try:
        with Image.open(path) as image:
            small = image.convert("RGB").resize((160, 90))
    except OSError:
        return []
    quantized = small.quantize(colors=count, method=Image.Quantize.MEDIANCUT)
    palette, total = quantized.getpalette(), small.width * small.height
    found = sorted(quantized.getcolors(), reverse=True)
    return [("#{:02x}{:02x}{:02x}".format(*palette[index * 3:index * 3 + 3]), round(share / total, 2))
            for share, index in found]


def build_prompt(task: dict, has_reference: bool, retry: str = "", palette=()) -> str:
    generation = task["generation"]
    conditions = generation.get("conditions") or {}
    name = output_name(task)
    lines = [PREAMBLE.format(output=f"out/{name}")]
    if role_of(task) == "background":
        lines.append("BACKGROUND plate, landscape 1536x1024, filling the frame edge to edge. Keep everything important "
                     "inside the central horizontal band: the top and bottom 80 px will be cropped to 16:9. "
                     "One far background only: do not paint separate foreground objects or characters on it.")
        lines.append(USAGE.get(conditions.get("background_usage"), USAGE["display"]))
    else:
        lines.append("ONE independent object only, centered, with at least 6% empty space on every side, on a fully "
                     "TRANSPARENT background (keep the alpha channel). If a transparent background is impossible, "
                     "use a PURE BLACK background (#000000) with nothing else on it. No ground, cast shadow, scenery "
                     "or frame. Square 1024x1024. The object will be placed on a flat sprite in front of the background.")
        if has_reference:
            lines.append("The attached reference image is the actual background of this artwork. Match its lighting, "
                         "palette and perspective, but do NOT draw the background itself.")
        if palette:
            lines.append("Colors of the actual background (most common first): "
                         + ", ".join(f"{color} {round(share * 100)}%" for color, share in palette)
                         + ". Make the object's colors and lighting harmonize with them.")
    lines += ["", "Production instruction from the art director (Korean). Follow its subject and style:",
              generation["prompt"], "", "Fixed conditions (must follow): " + json.dumps(conditions, ensure_ascii=False),
              AVOID]
    if retry:
        lines += ["", retry]
    return "\n".join(lines)


CODEX_PATH_ENV = "RANEL_CODEX_PATH"   # 공식 Codex 실행 파일(또는 @openai/codex의 codex.js) 절대 경로를 직접 지정
_codex_path = ""                       # --codex-path 인자 값(환경 변수보다 우선)


def codex_version(command: list[str]) -> str | None:
    """`<command> --version`이 'codex-cli <버전>'을 내면 그 문자열, 아니면 None. 이름만 같은 다른 프로그램을 거른다."""
    try:
        done = subprocess.run([*command, "--version"], capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (done.stdout or "").strip().splitlines()[0] if (done.stdout or "").strip() else ""
    return line if done.returncode == 0 and line.startswith("codex-cli ") else None


def _version_key(version: str) -> tuple:
    """'codex-cli 0.163.0-alpha.10.2' → 비교용 숫자 묶음(높을수록 최신)."""
    return tuple(int(part) for part in re.findall(r"\d+", version.removeprefix("codex-cli ")))


def _as_command(path: Path) -> list[str] | None:
    if path.suffix == ".js":
        node = shutil.which("node")
        return [node, str(path)] if node else None
    return [str(path)]


def openai_codex() -> list[str]:
    """OpenAI Codex CLI 실행 명령. PATH의 첫 'codex'가 이름만 같은 다른 프로그램일 수 있어(예: conda base가 켜진 터미널의
    miniforge3/Scripts/codex.exe — 만화책 서버) PATH 이름으로는 고르지 않고, 확인된 설치만 이 순서로 쓴다.
    1) --codex-path 또는 RANEL_CODEX_PATH로 지정한 절대 경로(`--version`이 codex-cli여야 함. 지정하면 이것만 씀)
    2) npm 전역 설치의 @openai/codex/bin/codex.js를 node로 직접 실행(cmd 셸을 거치지 않아 긴 프롬프트가 깨지지 않음)
    3) Codex 앱에 포함된 공식 실행 파일 %LOCALAPPDATA%/OpenAI/Codex/bin/*/codex.exe(`--version` 확인, 가장 높은 버전)"""
    explicit = _codex_path or os.environ.get(CODEX_PATH_ENV, "")
    if explicit:
        command = _as_command(Path(explicit))
        if not Path(explicit).is_absolute() or not Path(explicit).is_file() or not command or not codex_version(command):
            raise FileNotFoundError("codex_path_not_verified")
        return command
    node = shutil.which("node")
    folders = [folder for folder in os.environ.get("PATH", "").split(os.pathsep) if folder]
    if os.name == "nt" and os.environ.get("APPDATA"):
        folders.append(str(Path(os.environ["APPDATA"]) / "npm"))   # npm 전역 설치 기본 위치
    for folder in folders:
        candidates = [Path(folder) / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"]   # Windows npm
        link = Path(folder) / "codex"
        if link.is_symlink():   # Linux · macOS npm: bin/codex → lib/node_modules/@openai/codex/bin/codex.js
            candidates.append(link.resolve())
        for script in candidates:
            if script.is_file() and script.name == "codex.js" and "@openai" in script.parts and node:
                return [node, str(script)]
    if os.environ.get("LOCALAPPDATA"):   # Codex 앱 설치: 하위 폴더 이름은 버전이 아닐 수 있어(해시) --version으로 고른다
        found = []
        for path in (Path(os.environ["LOCALAPPDATA"]) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"):
            version = codex_version([str(path)])
            if version:
                found.append((_version_key(version), str(path)))
        if found:
            return [max(found)[1]]
    raise FileNotFoundError("openai_codex_cli_not_found")


def codex_command(workdir: Path, prompt: str, refs: list[str]) -> list[str]:
    """Codex 실행 명령. RANEL_CODEX_CMD(JSON argv)가 있으면 그 명령에 프롬프트만 붙인다(테스트 · 대체 생성기)."""
    override = os.environ.get("RANEL_CODEX_CMD")
    if override:
        return [*json.loads(override), prompt]
    images = [arg for ref in refs for arg in ("-i", ref)]
    return [*openai_codex(), "exec", "--skip-git-repo-check", "--ephemeral", "-C", str(workdir), "-s",
            "workspace-write", "-m", CODEX_MODEL, *images, "-o", "last_message.txt", prompt]


def transparent_ratio(image) -> float:
    if "A" not in image.getbands():
        return 0.0
    histogram = image.getchannel("A").histogram()
    return sum(histogram[:250]) / max(sum(histogram), 1)


class SpecError(ValueError):
    """가공 · 품질 검사 불통과. 문제 코드와 그때 잰 점수를 함께 기록에 남긴다."""

    def __init__(self, problems: list[str], scores: dict | None = None):
        super().__init__(";".join(problems))
        self.problems, self.scores = problems, scores or {}


def key_black(image):
    """순수 검정 배경을 투명으로. 밝기가 floor 이하면 완전 투명, floor~floor+ramp 구간은 서서히 불투명.
    반투명 가장자리는 검정이 섞여 어두우므로 알파로 나눠 원래 색을 되살린다(검은 테두리 제거)."""
    from PIL import Image

    rgb = image.convert("RGB")
    data, pixels = rgb.tobytes(), bytearray()
    for i in range(0, len(data), 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        peak = max(r, g, b)
        alpha = 0 if peak <= BLACK_FLOOR else min(255, round((peak - BLACK_FLOOR) * 255 / BLACK_RAMP))
        if 0 < alpha < 255:
            r, g, b = (min(255, c * 255 // alpha) for c in (r, g, b))
        pixels.extend((r, g, b, alpha))
    return Image.frombytes("RGBA", rgb.size, bytes(pixels))


def key_chroma(image):
    """단색 초록(CHROMA_KEY) 배경을 투명으로. 반투명 가장자리의 초록 번짐은 초록을 빨강 · 파랑 중 큰 값으로 낮춰 지운다."""
    from PIL import Image

    rgb = image.convert("RGB")
    data, pixels = rgb.tobytes(), bytearray()
    kr, kg, kb = CHROMA_KEY
    for i in range(0, len(data), 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        distance = ((r - kr) ** 2 + (g - kg) ** 2 + (b - kb) ** 2) ** 0.5
        alpha = 0 if distance <= CHROMA_NEAR else min(255, round((distance - CHROMA_NEAR) * 255 / (CHROMA_FAR - CHROMA_NEAR)))
        if alpha < 255:
            g = min(g, max(r, b))
        pixels.extend((r, g, b, alpha))
    return Image.frombytes("RGBA", rgb.size, bytes(pixels))


def keyed_background(rgb) -> str:
    """알파가 없는 객체 이미지의 바깥 배경색: 가장자리 띠의 평균이 초록이면 chroma, 아니면 black."""
    from PIL import ImageStat

    width, height = rgb.size
    band = max(1, round(min(width, height) * EDGE_BAND))
    strips = [rgb.crop(box) for box in ((0, 0, width, band), (0, height - band, width, height),
                                         (0, 0, band, height), (width - band, 0, width, height))]
    r, g, b = (sum(ImageStat.Stat(strip).mean[c] for strip in strips) / len(strips) for c in range(3))
    return "chroma" if g > 150 and r < 110 and b < 110 else "black"


def layout_scores(rgba) -> dict:
    """에셋 원본(여백을 붙이기 전)의 잘림 · 크기 점수. 어느 정도 불투명한 픽셀(SOLID_ALPHA 이상)만 본다."""
    solid = rgba.getchannel("A").point(lambda v: 255 if v >= SOLID_ALPHA else 0)
    box = solid.getbbox()
    if box is None:
        return {"edge_touch": 0.0, "object_side": 0.0}
    width, height = solid.size
    band = max(1, round(min(width, height) * EDGE_BAND))
    strips = [(0, 0, width, band), (0, height - band, width, height), (0, 0, band, height), (width - band, 0, width, height)]
    touched = sum(solid.crop(strip).histogram()[255] for strip in strips)
    edge_pixels = sum((right - left) * (bottom - top) for left, top, right, bottom in strips)
    side = max((box[2] - box[0]) / width, (box[3] - box[1]) / height)
    return {"edge_touch": round(touched / edge_pixels, 4), "object_side": round(side, 3)}


def layout_problems(rgba) -> list[str]:
    scores = layout_scores(rgba)
    if scores["object_side"] == 0:
        return ["asset_empty"]
    problems = []
    if scores["edge_touch"] > EDGE_TOUCH_LIMIT:
        problems.append("asset_touches_edge")
    if scores["object_side"] < MIN_OBJECT_SIDE:
        problems.append("asset_too_small")
    return problems


def lab_ab(color) -> tuple[float, float]:
    """sRGB → CIE Lab의 a · b(색감). 밝기(L)는 버린다. 어두운 배경 위의 빛나는 에셋을 밝기 차이로 오판하지 않게."""
    def linear(value):
        value /= 255
        return ((value + 0.055) / 1.055) ** 2.4 if value > 0.04045 else value / 12.92

    def curve(value):
        return value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116

    r, g, b = (linear(c) for c in color)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.9505
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.089
    return 500 * (curve(x) - curve(y)), 200 * (curve(y) - curve(z))


def object_colors(rgba, count: int = 4) -> list[tuple[tuple[int, int, int], float]]:
    """에셋의 불투명한 부분(알파 128 이상)의 대표 색과 비율."""
    from PIL import Image

    small = rgba.resize((128, 128))
    data = small.tobytes()
    opaque = [data[i:i + 3] for i in range(0, len(data), 4) if data[i + 3] >= 128]
    if not opaque:
        return []
    strip = Image.frombytes("RGB", (len(opaque), 1), b"".join(opaque))
    quantized = strip.quantize(colors=count, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()
    return [(tuple(palette[index * 3:index * 3 + 3]), share / len(opaque))
            for share, index in sorted(quantized.getcolors(), reverse=True)]


def color_distance(rgba, palette) -> float | None:
    """에셋 대표 색마다 배경 대표 색 중 가장 가까운 색감 거리를 재고, 에셋 색 비율로 평균낸다. 배경이 없으면 None."""
    if not palette:
        return None
    background = [lab_ab(tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))) for hex_color, _ in palette]
    total = 0.0
    for color, share in object_colors(rgba):
        a, b = lab_ab(color)
        total += share * min(((a - x) ** 2 + (b - y) ** 2) ** 0.5 for x, y in background)
    return round(total, 1)


def background_scores(image, usage: str | None) -> tuple[dict, list[str]]:
    """배경의 밝기 변화(표준편차)와 세부(가장자리 변화량 평균). 휘저어지는 캔버스는 세부까지 본다."""
    from PIL import ImageFilter, ImageStat

    gray = image.convert("L").resize((320, 180))
    scores = {"spread": round(ImageStat.Stat(gray).stddev[0], 1),
              "detail": round(ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0], 1)}
    flat = scores["spread"] < MIN_BACKGROUND_SPREAD or (usage == "canvas" and scores["detail"] < MIN_CANVAS_DETAIL)
    return scores, ["background_too_flat"] if flat else []


def finish_background(source: Path, target: Path, conditions: dict) -> dict:
    """16:9로 자르고 권장 크기로 맞춘 뒤 세부 점수를 잰다. 너무 단조로우면 SpecError."""
    from PIL import Image

    image = Image.open(source).convert("RGB")
    image = image.crop(crop_box(*image.size))
    width, height = conditions.get("recommended_size") or (1920, 1080)
    if abs(width / height - ASPECT) / ASPECT <= ASPECT_TOLERANCE and image.size != (width, height):
        image = image.resize((width, height), Image.LANCZOS)
    scores, problems = background_scores(image, conditions.get("background_usage"))
    if problems:
        raise SpecError(problems, scores)
    image.save(target, "PNG")
    return scores


def finish_object(source: Path, target: Path, conditions: dict, palette=()) -> tuple[str, dict]:
    """알파를 보장하고(없으면 검정 · 초록 배경 제거) 잘림 · 크기 · 배경과의 색감을 검사한 뒤,
    둘레에 투명 여백을 두고 권장 크기 이하로 줄인다. 사용한 알파 방식과 점수를 돌려준다."""
    from PIL import Image

    image = Image.open(source)
    rgba = image.convert("RGBA")
    method = "generated_alpha"
    if transparent_ratio(rgba) < CLEAR_MIN:
        method = keyed_background(rgba.convert("RGB")) + "_key"
        rgba = key_chroma(rgba) if method == "chroma_key" else key_black(rgba)
    # 여백을 붙이기 전에 판단한다. 붙인 뒤에 재면 꽉 찬 불투명 그림도 여백 덕에 통과해 UE에서 네모 판으로 보인다.
    if transparent_ratio(rgba) < CLEAR_MIN:
        raise SpecError(["object_without_alpha"], {"alpha_method": method})
    scores = {**layout_scores(rgba), "color_distance": color_distance(rgba, palette)}
    problems = layout_problems(rgba)
    if scores["color_distance"] is not None and scores["color_distance"] > MAX_COLOR_DISTANCE:
        problems.append("asset_color_mismatch")
    if problems:
        raise SpecError(problems, scores)
    box = rgba.getchannel("A").getbbox()
    if box:
        rgba = rgba.crop(box)
    pad = max(2, round(max(rgba.size) * MARGIN))
    canvas = Image.new("RGBA", (rgba.width + 2 * pad, rgba.height + 2 * pad), (0, 0, 0, 0))
    canvas.alpha_composite(rgba, (pad, pad))
    limit = max(conditions.get("recommended_size") or (512, 512))
    canvas.thumbnail((limit, limit), Image.LANCZOS)
    canvas.save(target, "PNG")
    return method, scores


def validate(path: Path, task: dict) -> list[str]:
    """중앙 조건 · UE 파일 규칙 대비 기계 검사. 문제 목록(비면 통과)."""
    from PIL import Image

    conditions = task["generation"].get("conditions") or {}
    problems = []
    if path.name != output_name(task):
        problems.append(f"파일 이름 {path.name} ≠ {output_name(task)}")
    if path.stat().st_size > MAX_BYTES:
        problems.append("32MiB 초과")
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG":
                problems.append(f"PNG가 아님({image.format})")
            width, height = image.size
            if max(width, height) > MAX_SIDE:
                problems.append(f"한 변이 {MAX_SIDE}px 초과({width}x{height})")
            if role_of(task) == "background" and abs(width / height - ASPECT) / ASPECT > ASPECT_TOLERANCE:
                problems.append(f"배경 비율이 16:9가 아님({width}x{height})")
            if conditions.get("alpha_required", role_of(task) == "object") and transparent_ratio(image) < CLEAR_MIN:
                problems.append("알파 필요: 투명 픽셀 1% 미만")
    except OSError as exc:
        problems.append(f"이미지를 열 수 없음: {type(exc).__name__}")
    return problems


def produced_file(workdir: Path, name: str) -> Path | None:
    exact = workdir / "out" / name
    if exact.is_file():
        return exact
    images = sorted(p for p in (workdir / "out").glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    return images[0] if len(images) == 1 else None   # 이름만 틀린 한 장은 받아 준다


def log(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def stop_tree(process) -> None:
    """생성기와 그 자식까지 끝낸다. 실제 Codex는 node가 Codex 본체를 자식으로 띄우므로, 직접 띄운 프로세스만 끄면
    이전 생성이 계속 돌면서 다음 시도와 겹친다. 끝났는지 기다려 확인한다."""
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
        else:
            import signal
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def run_generator(command: list[str], cwd: Path, log: Path, timeout: float) -> int:
    """생성기를 별도 프로세스 그룹으로 실행해 종료 코드를 돌려준다. 시간 초과면 자식까지 끝낸 뒤 TimeoutExpired."""
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    with log.open("wb") as stream:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stream,
                                   stderr=subprocess.STDOUT, **group)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_tree(process)
            raise
        except BaseException:   # 실행기가 handler를 끝낼 때(Ctrl+C 등)도 생성기를 남기지 않는다
            stop_tree(process)
            raise


def attempt(task: dict, workdir: Path, references: list[Path], retry: str, timeout: float) -> dict:
    """Codex 한 번 생성 → 가공 → 검사. 결과(ok · codex_failed · spec_failed)와 통과한 최종 파일 경로를 돌려준다."""
    name, role = output_name(task), role_of(task)
    (workdir / "out").mkdir(parents=True)
    (workdir / "refs").mkdir()
    refs = []
    for path in references:
        shutil.copy2(path, workdir / "refs" / path.name)
        refs.append(f"refs/{path.name}")
    palette = background_palette(references[0]) if references else []
    prompt = build_prompt(task, bool(refs), retry, palette)
    (workdir / "prompt.md").write_text(prompt, encoding="utf-8")
    try:
        exit_code = run_generator(codex_command(workdir, prompt, refs), workdir, workdir / "codex.log", timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"result": "codex_failed", "error": type(exc).__name__, "problems": []}
    raw = produced_file(workdir, name)
    if exit_code or raw is None:
        message = workdir / "last_message.txt"
        said = message.read_text(encoding="utf-8", errors="replace").strip()[:300] if message.is_file() else ""
        return {"result": "codex_failed", "exit_code": exit_code, "last_message": said, "problems": []}
    final = workdir / name
    conditions = task["generation"].get("conditions") or {}
    try:
        if role == "background":
            method, scores = None, finish_background(raw, final, conditions)
        else:
            method, scores = finish_object(raw, final, conditions, palette)
    except SpecError as exc:
        return {"result": "spec_failed", "problems": exc.problems, "scores": exc.scores}
    except (OSError, ValueError) as exc:
        return {"result": "spec_failed", "problems": [f"가공 실패: {type(exc).__name__}"], "scores": {}}
    problems = validate(final, task)
    if problems:
        return {"result": "spec_failed", "alpha_method": method, "problems": problems, "scores": scores}
    return {"result": "ok", "alpha_method": method, "problems": [], "scores": scores, "final": str(final)}


def run(task: dict, output: Path, records: Path, attempts: int = MAX_ATTEMPTS, budget: float = BUDGET_SECONDS) -> int:
    job_id, name, role = task["job_id"], output_name(task), role_of(task)
    record = records / f"{job_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    record.mkdir(parents=True)
    references = reference_files(task) if role == "object" else []
    started, history, retry = time.monotonic(), [], ""
    for number in range(1, attempts + 1):
        remaining = budget - (time.monotonic() - started)
        if number > 1 and remaining < MIN_ATTEMPT_SECONDS:
            log("retry_skipped", job_id=job_id, attempt=number, reason="budget", remaining_seconds=round(remaining))
            break
        log("attempt_start", job_id=job_id, attempt=number, role=role, output=name,
            references=[p.name for p in references])
        outcome = attempt(task, record / f"attempt-{number}", references, retry, min(ATTEMPT_TIMEOUT, max(remaining, 1)))
        outcome["attempt"] = number
        history.append(outcome)
        if outcome["result"] == "ok":
            break
        log("attempt_failed", job_id=job_id, attempt=number, result=outcome["result"], error=outcome.get("error", ""),
            problems=outcome["problems"], last_message=outcome.get("last_message", ""))
        if outcome.get("error") == "FileNotFoundError":   # 생성기가 없으면 다시 해도 같다
            break
        retry = retry_note(name, outcome)
    last = history[-1]
    report = {"job_id": job_id, "kind": task["kind"], "role": role, "output": name,
              "references": [p.name for p in references], "result": last["result"], "attempts": history,
              "alpha_method": last.get("alpha_method"), "scores": last.get("scores", {}), "problems": last["problems"]}
    (record / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if last["result"] != "ok":
        log("generation_failed", job_id=job_id, attempts=len(history), result=last["result"])
        return EXIT_CODEX if last["result"] == "codex_failed" else EXIT_SPEC
    shutil.copy2(last["final"], output / name)
    log("generated", job_id=job_id, output=name, attempts=len(history), record=str(record))
    return 0


def check() -> int:
    """실행 전 점검: 어떤 Codex를 쓸지와 그 버전. 못 찾거나 확인되지 않으면 종료 코드 2."""
    try:
        command = openai_codex()
    except FileNotFoundError as exc:
        log("codex_check_failed", error=str(exc), codex_path=_codex_path or os.environ.get(CODEX_PATH_ENV, ""))
        return EXIT_INPUT
    version = codex_version(command)
    log("codex_check", command=command, version=version)
    return 0 if version else EXIT_INPUT


def main(argv=None) -> int:
    global _codex_path
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--records", type=Path, default=Path("C:/Ranel/generation/records"))
    parser.add_argument("--attempts", type=int, choices=range(1, 6), default=MAX_ATTEMPTS, metavar="1-5",
                        help=f"한 작업에서 생성할 최대 횟수(기본 {MAX_ATTEMPTS})")
    parser.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS,
                        help=f"모든 시도를 합친 시간 상한(기본 {BUDGET_SECONDS}초, 실행기 timeout보다 짧게)")
    parser.add_argument("--codex-path", default="",
                        help=f"공식 Codex 실행 파일(또는 @openai/codex의 codex.js) 절대 경로. 환경 변수 {CODEX_PATH_ENV}보다 우선")
    parser.add_argument("--check", action="store_true", help="작업 없이 사용할 Codex와 버전만 확인")
    args = parser.parse_args(argv)
    _codex_path = args.codex_path
    if args.check:
        return check()
    if not args.request or not args.output:
        parser.error("--request와 --output이 필요합니다(점검만 할 때는 --check)")
    try:
        task = json.loads(args.request.read_text(encoding="utf-8-sig"))
        if task["kind"] not in ("background_generate", "assets_generate") or not task["generation"].get("prompt"):
            raise ValueError("generation_task_required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.png", output_name(task)):
            raise ValueError("invalid_output_name")
        if not args.output.is_dir() or any(args.output.iterdir()):
            raise ValueError("empty_output_dir_required")
    except (OSError, ValueError, KeyError) as exc:
        log("input_invalid", error=str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
        return EXIT_INPUT
    return run(task, args.output, args.records, args.attempts, args.budget_seconds)


if __name__ == "__main__":
    sys.exit(main())
