"""자동 생성 실행기용 handler(infra/generation/handler.py). 실제 Codex 대신 가짜 생성기로 흐름 · 규격 검사 · 재생성을 본다.

Pillow는 프로젝트 의존성이 아니므로 `uv run --with pillow pytest`에서만 이미지 검사가 실행된다. 화풍 품질은 검사하지 않는다.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from infra.generation import handler

HANDLER = Path(handler.__file__)
# 시도마다 FAKE_MODES(쉼표 목록)의 다음 동작을 한다. 목록이 끝나면 마지막 동작을 반복한다. 호출 횟수는 FAKE_STATE에 센다.
FAKE = """
import os, re, sys
from pathlib import Path
from PIL import Image
name = re.search(r"out/(\\S+\\.png)", sys.argv[-1]).group(1)
Path("refs_seen.txt").write_text(",".join(sorted(p.name for p in Path("refs").iterdir())))
state = Path(os.environ["FAKE_STATE"])
calls = int(state.read_text()) if state.exists() else 0
state.write_text(str(calls + 1))
modes = os.environ["FAKE_MODES"].split(",")
mode = modes[min(calls, len(modes) - 1)]
if mode == "background":   # 세부가 있는 배경: 그라데이션 위 잡음
    noise = Image.effect_noise((1536, 1024), 60).convert("RGB")
    Image.blend(Image.linear_gradient("L").resize((1536, 1024)).convert("RGB"), noise, 0.5).save(Path("out") / name)
elif mode == "flat_background":
    Image.new("RGB", (1536, 1024), (20, 60, 40)).save(Path("out") / name)
elif mode == "pink_object":   # 짙은 초록 · 금색 배경과 동떨어진 형광 분홍
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    image.paste((255, 40, 200, 255), (300, 300, 700, 700))
    image.save(Path("out") / name)
elif mode == "green_object":   # 투명 대신 단색 초록 배경 위의 금색 객체
    image = Image.new("RGB", (1024, 1024), (0, 255, 0))
    image.paste((230, 200, 60), (300, 300, 700, 700))
    image.save(Path("out") / name)
elif mode == "black_object":
    image = Image.new("RGB", (1024, 1024), (0, 0, 0))
    image.paste((230, 200, 60), (400, 400, 620, 620))
    image.save(Path("out") / name)
elif mode == "alpha_object":
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    image.paste((90, 200, 120, 255), (300, 300, 700, 700))
    image.save(Path("out") / "wrong-name.png")
elif mode == "opaque":
    Image.new("RGB", (1024, 1024), (200, 30, 30)).save(Path("out") / name)
elif mode == "edge_object":   # 왼쪽 가장자리에서 잘린 객체
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    image.paste((90, 200, 120, 255), (0, 300, 500, 700))
    image.save(Path("out") / name)
elif mode == "tiny_object":   # 화면의 6%밖에 안 되는 객체
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    image.paste((90, 200, 120, 255), (482, 482, 542, 542))
    image.save(Path("out") / name)
elif mode == "hang":   # 자식 프로세스를 띄우고 멈춤(실제 Codex: node가 Codex 본체를 자식으로 띄움)
    import subprocess, time
    beat = os.environ["FAKE_BEAT"]
    subprocess.Popen([sys.executable, "-c", "import pathlib, time\\nwhile True:\\n"
                      f"    pathlib.Path({beat!r}).write_text(str(time.time()))\\n    time.sleep(0.1)"])
    time.sleep(120)
elif mode == "refuse":
    Path("last_message.txt").write_text("제작 보류 지시에 따라 이미지를 생성하지 않았습니다.", encoding="utf-8")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


def _task(tmp_path, kind="background_generate", conditions=None, with_background=False, real_background=False):
    inputs_root = tmp_path / "inputs"
    inputs_root.mkdir(exist_ok=True)
    inputs = []
    if with_background:
        (inputs_root / "bg-job").mkdir()
        target = inputs_root / "bg-job" / "background.png"
        if real_background:   # 대표 색을 뽑을 수 있는 실제 PNG: 왼쪽 3/4 짙은 초록, 오른쪽 1/4 금색
            from PIL import Image
            image = Image.new("RGB", (160, 90), (10, 60, 30))
            image.paste((220, 180, 60), (120, 0, 160, 90))
            image.save(target)
        else:
            target.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        inputs = [{"id": "bg-job", "result": {"files": [{"name": "background.png", "sha256": "0" * 64, "size": 12}]}}]
    background = kind == "background_generate"
    task = {"job_id": "job-1", "kind": kind, "input_root": str(inputs_root), "output_dir": "", "inputs": inputs,
            "generation": {"artwork_id": "art", "version": "v1", "prompt": "안개 낀 새벽 숲" if background else "반딧불",
                           "conditions": conditions or {
                               "template": "image_stir", "role": "background" if background else "object",
                               "output_name": "background.png" if background else "asset-001.png",
                               "recommended_size": [1920, 1080] if background else [512, 512],
                               "alpha_required": not background, "background_usage": "canvas"}}}
    path = tmp_path / "task.json"
    path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    return path


def _run(tmp_path, task, modes, monkeypatch, exit_code=0, extra=()):
    fake = tmp_path / "fake_codex.py"
    fake.write_text(FAKE, encoding="utf-8")
    monkeypatch.setenv("RANEL_CODEX_CMD", json.dumps([sys.executable, str(fake)]))
    monkeypatch.setenv("FAKE_MODES", modes)
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "fake-calls.txt"))
    monkeypatch.setenv("FAKE_EXIT", str(exit_code))
    output = tmp_path / "output"
    output.mkdir()
    code = handler.main(["--request", str(task), "--output", str(output), "--records", str(tmp_path / "records"),
                         *extra])
    record = next((tmp_path / "records").iterdir(), None) if (tmp_path / "records").exists() else None
    report = json.loads((record / "report.json").read_text("utf-8")) if record else None
    return code, output, record, report


def test_prompt_carries_head_instruction_output_path_and_reference(tmp_path):
    task = json.loads(_task(tmp_path, "assets_generate", with_background=True).read_text(encoding="utf-8"))
    prompt = handler.build_prompt(task, has_reference=True)
    assert "out/asset-001.png" in prompt and "반딧불" in prompt and "TRANSPARENT" in prompt
    assert "reference image is the actual background" in prompt and "RETRY" not in prompt
    (tmp_path / "b").mkdir()
    background = json.loads(_task(tmp_path / "b", "background_generate").read_text(encoding="utf-8"))
    text = handler.build_prompt(background, has_reference=False)
    assert "out/background.png" in text and "stir" in text and "16:9" in text


def test_prompt_leaves_style_to_head_and_forbids_only_defects(tmp_path):
    task = json.loads(_task(tmp_path, "assets_generate").read_text(encoding="utf-8"))
    prompt = handler.build_prompt(task, has_reference=False)
    assert "Luminous" not in prompt and "glass" not in prompt and "people" not in prompt
    assert "watermarks" in prompt and "Follow its subject and style" in prompt


def test_background_palette_lists_dominant_colors_first(tmp_path):
    pytest.importorskip("PIL.Image")
    task = json.loads(_task(tmp_path, "assets_generate", with_background=True, real_background=True)
                      .read_text(encoding="utf-8"))
    palette = handler.background_palette(handler.reference_files(task)[0])
    assert palette[0] == ("#0a3c1e", 0.75) and ("#dcb43c", 0.25) in palette
    prompt = handler.build_prompt(task, has_reference=True, palette=palette)
    assert "Colors of the actual background" in prompt and "#0a3c1e 75%" in prompt
    assert handler.background_palette(tmp_path / "missing.png") == []


def test_retry_note_explains_previous_failure():
    refused = handler.retry_note("asset-001.png", {"result": "codex_failed", "last_message": "생성하지 않았습니다",
                                                   "problems": []})
    assert "did not save an image at out/asset-001.png" in refused and "생성하지 않았습니다" in refused
    no_alpha = handler.retry_note("asset-001.png", {"result": "spec_failed", "problems": ["object_without_alpha"]})
    assert "object_without_alpha" in no_alpha and "transparent" in no_alpha


def test_reference_files_are_the_downloaded_background(tmp_path):
    task = json.loads(_task(tmp_path, "assets_generate", with_background=True).read_text(encoding="utf-8"))
    assert handler.reference_files(task) == [tmp_path / "inputs" / "bg-job" / "background.png"]


def test_rejects_non_empty_output_and_bad_name(tmp_path):
    task = _task(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    (output / "old.png").write_bytes(b"x")
    assert handler.main(["--request", str(task), "--output", str(output)]) == handler.EXIT_INPUT
    bad = tmp_path / "bad"
    bad.mkdir()
    bad_task = _task(bad, conditions={"role": "background", "output_name": "../escape.png"})
    (bad / "output").mkdir()
    assert handler.main(["--request", str(bad_task), "--output", str(bad / "output")]) == handler.EXIT_INPUT


def test_background_is_cropped_to_16_9_and_recommended_size(tmp_path, monkeypatch):
    pil = pytest.importorskip("PIL.Image")
    code, output, record, report = _run(tmp_path, _task(tmp_path), "background", monkeypatch)
    assert code == 0
    assert [p.name for p in output.iterdir()] == ["background.png"]
    with pil.open(output / "background.png") as image:
        assert image.size == (1920, 1080)
    assert (record / "attempt-1" / "prompt.md").is_file()
    assert report["result"] == "ok" and len(report["attempts"]) == 1 and report["problems"] == []


def test_object_on_black_is_keyed_to_alpha_and_gets_background_reference(tmp_path, monkeypatch):
    pil = pytest.importorskip("PIL.Image")
    task = _task(tmp_path, "assets_generate", with_background=True)
    code, output, record, report = _run(tmp_path, task, "black_object", monkeypatch)
    assert code == 0
    with pil.open(output / "asset-001.png") as image:
        assert image.mode == "RGBA" and max(image.size) <= 512 and handler.transparent_ratio(image) >= 0.01
    assert (record / "attempt-1" / "refs_seen.txt").read_text() == "background.png"
    assert report["alpha_method"] == "black_key"


def test_generated_alpha_with_wrong_name_is_saved_under_output_name(tmp_path, monkeypatch):
    pil = pytest.importorskip("PIL.Image")
    code, output, _, report = _run(tmp_path, _task(tmp_path, "assets_generate"), "alpha_object", monkeypatch)
    assert code == 0 and [p.name for p in output.iterdir()] == ["asset-001.png"]
    with pil.open(output / "asset-001.png") as image:
        assert handler.transparent_ratio(image) >= 0.01
    assert report["alpha_method"] == "generated_alpha"


def test_missing_image_is_regenerated_with_the_reason(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    code, output, record, report = _run(tmp_path, _task(tmp_path), "refuse,background", monkeypatch)
    assert code == 0 and [p.name for p in output.iterdir()] == ["background.png"]
    assert [a["result"] for a in report["attempts"]] == ["codex_failed", "ok"]
    retry_prompt = (record / "attempt-2" / "prompt.md").read_text(encoding="utf-8")
    assert "RETRY" in retry_prompt and "생성하지 않았습니다" in retry_prompt


def test_spec_failure_is_regenerated_with_alpha_hint(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    code, output, record, report = _run(tmp_path, _task(tmp_path, "assets_generate"), "opaque,black_object",
                                        monkeypatch)
    assert code == 0 and [p.name for p in output.iterdir()] == ["asset-001.png"]
    assert [a["result"] for a in report["attempts"]] == ["spec_failed", "ok"]
    assert "transparent" in (record / "attempt-2" / "prompt.md").read_text(encoding="utf-8")


def test_cropped_asset_is_regenerated_with_margin_hint(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    task = _task(tmp_path, "assets_generate", with_background=True, real_background=True)
    code, output, record, report = _run(tmp_path, task, "edge_object,alpha_object", monkeypatch)
    assert code == 0 and [p.name for p in output.iterdir()] == ["asset-001.png"]
    assert report["attempts"][0]["problems"] == ["asset_touches_edge"]
    retry_prompt = (record / "attempt-2" / "prompt.md").read_text(encoding="utf-8")
    assert "8% empty margin" in retry_prompt and "Colors of the actual background" in retry_prompt


def test_flat_background_is_regenerated_and_scores_recorded(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    code, output, _, report = _run(tmp_path, _task(tmp_path), "flat_background,background", monkeypatch)
    assert code == 0 and [p.name for p in output.iterdir()] == ["background.png"]
    first, second = report["attempts"]
    assert first["problems"] == ["background_too_flat"] and first["scores"]["spread"] < handler.MIN_BACKGROUND_SPREAD
    assert second["scores"]["detail"] >= handler.MIN_CANVAS_DETAIL and report["scores"] == second["scores"]


def test_asset_far_from_background_colors_is_rejected(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    task = _task(tmp_path, "assets_generate", with_background=True, real_background=True)
    code, output, record, report = _run(tmp_path, task, "pink_object", monkeypatch)
    assert code == handler.EXIT_SPEC and list(output.iterdir()) == []
    assert report["problems"] == ["asset_color_mismatch"]
    assert report["scores"]["color_distance"] > handler.MAX_COLOR_DISTANCE
    assert "belong to the background's palette" in (record / "attempt-2" / "prompt.md").read_text(encoding="utf-8")


def test_matching_asset_passes_color_check(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    task = _task(tmp_path, "assets_generate", with_background=True, real_background=True)
    code, _, _, report = _run(tmp_path, task, "black_object", monkeypatch)   # 금색 객체 · 짙은 초록과 금색 배경
    assert code == 0 and report["scores"]["color_distance"] <= handler.MAX_COLOR_DISTANCE


def test_green_screen_object_is_chroma_keyed_without_green_fringe(tmp_path, monkeypatch):
    pil = pytest.importorskip("PIL.Image")
    code, output, _, report = _run(tmp_path, _task(tmp_path, "assets_generate"), "green_object", monkeypatch)
    assert code == 0 and report["alpha_method"] == "chroma_key"
    with pil.open(output / "asset-001.png") as image:
        assert handler.transparent_ratio(image) >= 0.01
        data = image.tobytes()
        visible = [data[i:i + 4] for i in range(0, len(data), 4) if data[i + 3] > 0]
        assert visible and all(g <= max(r, b) or a == 255 for r, g, b, a in visible)


def test_black_key_restores_color_of_soft_edges():
    pil = pytest.importorskip("PIL.Image")
    keyed = handler.key_black(pil.new("RGB", (1, 1), (20, 16, 4)))   # 검정이 섞인 반투명 가장자리
    r, g, b, a = keyed.getpixel((0, 0))
    assert 0 < a < 255 and (r, g, b) == (20 * 255 // a, 16 * 255 // a, 4 * 255 // a)


def test_tiny_asset_fails_after_all_attempts(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    code, output, record, report = _run(tmp_path, _task(tmp_path, "assets_generate"), "tiny_object", monkeypatch)
    assert code == handler.EXIT_SPEC and list(output.iterdir()) == []
    assert report["problems"] == ["asset_too_small"]
    assert "50-80% of the canvas" in (record / "attempt-2" / "prompt.md").read_text(encoding="utf-8")


def test_opaque_object_fails_after_all_attempts(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    code, output, _, report = _run(tmp_path, _task(tmp_path, "assets_generate"), "opaque", monkeypatch)
    assert code == handler.EXIT_SPEC and list(output.iterdir()) == []
    assert len(report["attempts"]) == handler.MAX_ATTEMPTS and report["problems"] == ["object_without_alpha"]
    assert int((tmp_path / "fake-calls.txt").read_text()) == handler.MAX_ATTEMPTS


@pytest.mark.parametrize("modes, exit_code", [("none", 0), ("background", 1), ("refuse", 0)])
def test_codex_failure_reports_codex_error_after_all_attempts(tmp_path, monkeypatch, modes, exit_code):
    pytest.importorskip("PIL.Image")
    code, output, _, report = _run(tmp_path, _task(tmp_path), modes, monkeypatch, exit_code)
    assert code == handler.EXIT_CODEX and list(output.iterdir()) == []
    assert len(report["attempts"]) == handler.MAX_ATTEMPTS and report["result"] == "codex_failed"


def test_attempts_option_limits_regeneration(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    code, _, _, report = _run(tmp_path, _task(tmp_path), "none", monkeypatch, extra=("--attempts", "1"))
    assert code == handler.EXIT_CODEX and len(report["attempts"]) == 1


def test_no_new_attempt_when_budget_is_nearly_spent(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    budget = str(handler.MIN_ATTEMPT_SECONDS - 1)   # 첫 시도 뒤 남은 시간이 최소 시도 시간보다 적다
    code, _, _, report = _run(tmp_path, _task(tmp_path), "none,background", monkeypatch,
                              extra=("--budget-seconds", budget))
    assert code == handler.EXIT_CODEX and len(report["attempts"]) == 1


def test_timeout_stops_generator_children_before_retry(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    import time

    beat = tmp_path / "child-beat.txt"
    monkeypatch.setenv("FAKE_BEAT", str(beat))
    monkeypatch.setattr(handler, "ATTEMPT_TIMEOUT", 3)          # 한 번 3초 뒤 시간 초과
    monkeypatch.setattr(handler, "MIN_ATTEMPT_SECONDS", 0)      # 시간 초과 뒤 다음 시도까지 이어지게
    code, _, _, report = _run(tmp_path, _task(tmp_path), "hang,background", monkeypatch)
    assert code == 0 and [a.get("error") for a in report["attempts"]] == ["TimeoutExpired", None]
    assert beat.exists()                                        # 자식이 실제로 돌았다
    last = beat.read_text()
    time.sleep(1.0)
    assert beat.read_text() == last                             # 시간 초과 뒤에는 자식도 멈췄다


def test_openai_codex_is_chosen_over_same_named_program(tmp_path, monkeypatch):
    other = tmp_path / "miniforge3" / "Scripts"   # 먼저 잡히는 동명의 다른 codex(예: conda 패키지)
    other.mkdir(parents=True)
    (other / "codex.exe").write_bytes(b"not openai")
    npm = tmp_path / "npm"
    script = npm / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    script.parent.mkdir(parents=True)
    script.write_text("// codex", encoding="utf-8")
    node = tmp_path / "node" / ("node.exe" if sys.platform == "win32" else "node")
    node.parent.mkdir()
    node.write_bytes(b"")
    node.chmod(0o755)
    _no_installed_codex(monkeypatch)
    monkeypatch.setattr(handler.shutil, "which", lambda name: str(node) if name == "node" else str(other / "codex.exe"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(other), str(npm)]))
    assert handler.openai_codex() == [str(node), str(script)]
    monkeypatch.setenv("PATH", str(other))
    with pytest.raises(FileNotFoundError):
        handler.openai_codex()


def _no_installed_codex(monkeypatch):
    """이 PC에 실제로 깔린 Codex(npm · Codex 앱 · 지정 경로)가 테스트에 끼어들지 않게 한다."""
    for name in ("RANEL_CODEX_CMD", "RANEL_CODEX_PATH", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(handler, "_codex_path", "")


def _fake_codex_app(tmp_path, monkeypatch, versions: dict[str, str | None]):
    """%LOCALAPPDATA%/OpenAI/Codex/bin/<폴더>/codex.exe 설치(폴더 이름은 해시). versions: 폴더 → --version 결과."""
    root = tmp_path / "LocalAppData"
    for folder in versions:
        exe = root / "OpenAI" / "Codex" / "bin" / folder / "codex.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(root))
    monkeypatch.setattr(handler, "codex_version", lambda command: versions.get(Path(command[-1]).parent.name))
    return root / "OpenAI" / "Codex" / "bin"


def test_codex_app_install_is_used_without_npm(tmp_path, monkeypatch):
    _no_installed_codex(monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    bin_dir = _fake_codex_app(tmp_path, monkeypatch, {
        "896a0166b774037a": "codex-cli 0.163.0-alpha.10.2",
        "c0ffee0000000001": "codex-cli 0.163.0-alpha.11.1",   # 가장 높은 버전
        "deadbeef00000002": None,                              # --version이 codex-cli가 아님 → 제외
    })
    assert handler.openai_codex() == [str(bin_dir / "c0ffee0000000001" / "codex.exe")]


def test_explicit_codex_path_is_used_only_when_verified(tmp_path, monkeypatch):
    _no_installed_codex(monkeypatch)
    exe = tmp_path / "custom" / "codex.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setenv("RANEL_CODEX_PATH", str(exe))
    monkeypatch.setattr(handler, "codex_version", lambda command: "codex-cli 0.163.0")
    assert handler.openai_codex() == [str(exe)]
    monkeypatch.setattr(handler, "codex_version", lambda command: None)   # 이름만 같은 다른 프로그램
    with pytest.raises(FileNotFoundError, match="codex_path_not_verified"):
        handler.openai_codex()
    monkeypatch.setenv("RANEL_CODEX_PATH", "codex.exe")                 # 절대 경로가 아님
    with pytest.raises(FileNotFoundError, match="codex_path_not_verified"):
        handler.openai_codex()


def test_check_reports_chosen_codex_and_codex_path_option_wins(tmp_path, monkeypatch, capsys):
    _no_installed_codex(monkeypatch)
    exe = tmp_path / "custom" / "codex.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setenv("RANEL_CODEX_PATH", str(tmp_path / "other.exe"))   # 인자가 환경 변수보다 우선
    monkeypatch.setattr(handler, "codex_version", lambda command: "codex-cli 0.163.0")
    assert handler.main(["--check", "--codex-path", str(exe)]) == 0
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["event"] == "codex_check" and event["command"] == [str(exe)] and event["version"] == "codex-cli 0.163.0"
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert handler.main(["--check"]) == handler.EXIT_INPUT                   # 지정 경로 없음 · 설치도 없음


def test_codex_version_rejects_program_that_is_not_codex_cli(tmp_path):
    other = tmp_path / "other.py"
    other.write_text("print('Codex comic server 1.0')", encoding="utf-8")
    real = tmp_path / "real.py"
    real.write_text("print('codex-cli 0.163.0-alpha.11.1')", encoding="utf-8")
    assert handler.codex_version([sys.executable, str(other)]) is None
    assert handler.codex_version([sys.executable, str(real)]) == "codex-cli 0.163.0-alpha.11.1"


def test_missing_codex_cli_fails_once_without_retry(tmp_path, monkeypatch):
    pytest.importorskip("PIL.Image")
    monkeypatch.delenv("RANEL_CODEX_CMD", raising=False)
    monkeypatch.setattr(handler, "openai_codex", lambda: (_ for _ in ()).throw(FileNotFoundError("missing")))
    output = tmp_path / "output"
    output.mkdir()
    code = handler.main(["--request", str(_task(tmp_path)), "--output", str(output),
                         "--records", str(tmp_path / "records")])
    report = json.loads(next((tmp_path / "records").iterdir()).joinpath("report.json").read_text("utf-8"))
    assert code == handler.EXIT_CODEX and len(report["attempts"]) == 1
    assert report["attempts"][0]["error"] == "FileNotFoundError"


def test_runs_as_script_from_repository_root(tmp_path):
    root = HANDLER.parents[2]
    done = subprocess.run([sys.executable, str(HANDLER), "--request", str(tmp_path / "missing.json"),
                           "--output", str(tmp_path)], cwd=root, capture_output=True, text=True, check=False)
    assert done.returncode == handler.EXIT_INPUT and "input_invalid" in done.stdout
