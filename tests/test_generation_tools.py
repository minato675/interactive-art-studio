"""생성 PC 도구(infra/generation): 16:9 자르기 · 밝기 알파 계산, 생성 전 · 넘기기 전 점검.

점검 두 개는 UE 조립기(studio.assembly.ue_assemble의 select_template · assemble)를 쓴다. 렌더링은 검사하지 않는다.
"""
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
from infra.generation import crop169, luma_alpha

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "infra" / "generation"
SPEC = {
    "template_family": "scene_family", "raw_prompt": "안개 낀 새벽 숲, 손을 움직이면 숲이 휘저어진다",
    "content": {"theme": "숲", "mood": "고요함", "background": "안개 낀 새벽 숲", "dimension": "2.5d",
                "reveal": "field", "style": "realistic"},
    "idle": {"motion": "ignore"},
    "interactions": [{"input": "move", "reaction": "stir", "reaction_mode": "while", "return": "fade"}],
}


@pytest.mark.parametrize("size, box", [
    ((1536, 1024), (0, 80, 1536, 944)),      # 3:2 → 위아래를 자름
    ((2000, 900), (200, 0, 1800, 900)),      # 더 넓음 → 좌우를 자름
    ((1920, 1080), (0, 0, 1920, 1080)),      # 이미 16:9
])
def test_crop_box_keeps_center_16_9(size, box):
    assert crop169.crop_box(*size) == box


def test_luma_alpha_black_is_transparent_and_glow_keeps_color():
    assert luma_alpha.to_rgba(0, 0, 0, 8) == (0, 0, 0, 0)
    assert luma_alpha.to_rgba(6, 5, 4, 8) == (0, 0, 0, 0)          # 배경 잡티
    assert luma_alpha.to_rgba(255, 200, 0, 8) == (255, 200, 0, 255)  # 가장 밝은 빛은 불투명
    r, g, b, a = luma_alpha.to_rgba(120, 90, 0, 8)                    # 어두운 금색: 반투명, 색은 되살림
    assert 0 < a < 255 and (r, g, b) == (255, 191, 0)


def _ue():
    ue = pytest.importorskip("studio.assembly.ue_assemble")
    if not hasattr(ue, "select_template"):
        pytest.skip("UE 조립기 select_template 없음")
    return ue


def _png(path: Path, size=(64, 36), alpha=False):
    """테스트용 최소 PNG(8비트 RGB/RGBA, 단색)."""
    width, height = size
    channels = 4 if alpha else 3
    pixel = bytes([40, 120, 60, 128][:channels])
    raw = b"".join(b"\x00" + pixel * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw))
                     + chunk(b"IEND", b""))


def _run(tool, *args, cwd):
    return subprocess.run([sys.executable, str(TOOLS / tool), *map(str, args)], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", check=False)


def test_preflight_picks_template_and_writes_choice(tmp_path):
    _ue()
    (tmp_path / "spec.json").write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
    done = _run("preflight.py", "spec.json", "--choice", "choice.json", cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["template"] == "image_stir" and report["holds"] == [] and len(report["generate"]) == 1
    assert json.loads((tmp_path / "choice.json").read_text(encoding="utf-8"))["template"] == "image_stir"


def test_preflight_holds_unsupported_reaction_without_choice(tmp_path):
    _ue()
    spec = {**SPEC, "interactions": [{**SPEC["interactions"][0], "reaction": "bloom"}]}
    (tmp_path / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    done = _run("preflight.py", "spec.json", "--choice", "choice.json", cwd=tmp_path)
    assert done.returncode == 1 and json.loads(done.stdout)["holds"]
    assert not (tmp_path / "choice.json").exists()


def _without(path: str) -> dict:
    """SPEC에서 한 칸을 비운(null) 사본. path 예: content.theme, interactions.0.input"""
    spec = json.loads(json.dumps(SPEC))
    *parents, last = path.split(".")
    node = spec
    for key in parents:
        node = node[int(key)] if key.isdigit() else node[key]
    node[last] = None
    return spec


@pytest.mark.parametrize("path", [
    "content.theme", "content.mood", "content.background", "content.dimension",
    "interactions.0.input", "interactions.0.reaction", "interactions.0.reaction_mode", "interactions.0.return",
])
def test_preflight_holds_missing_required_before_choosing(tmp_path, path):
    _ue()
    (tmp_path / "spec.json").write_text(json.dumps(_without(path), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "choice.json").write_text("{}", encoding="utf-8")   # 이전 실행이 남긴 선택 결과
    done = _run("preflight.py", "spec.json", "--choice", "choice.json", cwd=tmp_path)
    report = json.loads(done.stdout)
    assert done.returncode == 1, done.stdout
    assert report["template"] is None and report["generate"] == [] and report["unfilled_required"]
    assert any("필수 칸" in hold for hold in report["holds"])
    assert not (tmp_path / "choice.json").exists()


@pytest.mark.parametrize("path", ["content.style", "content.reveal", "idle.motion"])
def test_preflight_allows_missing_optional(tmp_path, path):
    _ue()
    (tmp_path / "spec.json").write_text(json.dumps(_without(path), ensure_ascii=False), encoding="utf-8")
    done = _run("preflight.py", "spec.json", "--choice", "choice.json", cwd=tmp_path)
    assert done.returncode == 0, done.stdout
    assert json.loads(done.stdout)["unfilled_required"] == [] and (tmp_path / "choice.json").exists()


def test_handoff_check_passes_layer_and_glowing_object(tmp_path):
    _ue()
    (tmp_path / "spec.json").write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
    assert _run("preflight.py", "spec.json", "--choice", "choice.json", cwd=tmp_path).returncode == 0
    out = tmp_path / "out"
    out.mkdir()
    _png(out / "far.png")
    _png(out / "firefly.png", size=(64, 64), alpha=True)
    (out / "assets.json").write_text(json.dumps({
        "format": "artwork-assets", "version": 1,
        "layers": [{"id": "far", "image": "far.png", "depth": "far"}],
        "objects": [{"id": "firefly", "image": "firefly.png", "position": [0.6, 0.4], "size": 0.08,
                     "behaviors": ["float"]}]}), encoding="utf-8")
    done = _run("handoff_check.py", "spec.json", "choice.json", "out", "--scene", "scene.json", cwd=tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    report = json.loads(done.stdout)
    assert report["passed"] and report["files"] == ["far.png", "firefly.png"] and report["unlisted_files"] == []
    scene = json.loads((tmp_path / "scene.json").read_text(encoding="utf-8"))
    assert {row["ImageFile"] for row in scene["ExternalAssets"]} == {"far.png", "firefly.png"}


def test_handoff_check_rejects_missing_file_and_flags_unlisted(tmp_path):
    _ue()
    (tmp_path / "spec.json").write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
    assert _run("preflight.py", "spec.json", "--choice", "choice.json", cwd=tmp_path).returncode == 0
    out = tmp_path / "out"
    out.mkdir()
    _png(out / "extra.png")
    (out / "assets.json").write_text(json.dumps({
        "format": "artwork-assets", "version": 1,
        "layers": [{"id": "far", "image": "far.png", "depth": "far"}]}), encoding="utf-8")
    done = _run("handoff_check.py", "spec.json", "choice.json", "out", cwd=tmp_path)
    report = json.loads(done.stdout)
    assert done.returncode == 1 and not report["passed"]
    assert any("far.png" in error for error in report["errors"]) and report["unlisted_files"] == ["extra.png"]


@pytest.mark.parametrize("scene", ["out/scene.json", "out/debug/scene.json", "out/a/b/scene.json",
                                   "out/debug/../scene.json", "other/../out/debug/scene.json"])
def test_handoff_check_refuses_preview_anywhere_inside_result_folder(tmp_path, scene):
    _ue()
    (tmp_path / "out" / "debug").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    done = _run("handoff_check.py", "spec.json", "choice.json", "out", "--scene", scene, cwd=tmp_path)
    assert done.returncode == 1 and "결과 폴더 밖" in done.stderr
    assert not list((tmp_path / "out").rglob("scene.json"))
