"""PC1 UE 실행 검증 러너(studio.verify.ue_runner). UE 대신 fakes/fake_shipping.py(같은 파일 계약의 가짜 실행본)로 돌린다."""
import json
import socket
import struct
import sys
import time
import zlib
from pathlib import Path

import pytest

from studio.exhibition.delivery import publish_version
from studio.verify import ue_runner
from studio.verify.png import decode_png
from studio.verify.ue_runner import Hands, Runner, Thresholds, bundle_hash, changed_ratio

FAKE = Path(__file__).parent / "fakes" / "fake_shipping.py"
# Windows 가상환경의 python.exe는 진짜 인터프리터를 자식으로 다시 띄워 PID가 달라진다. 가짜는 표준 라이브러리만 쓴다.
PYTHON = getattr(sys, "_base_executable", sys.executable)
FAST = {"settle_seconds": 0.3, "input_seconds": 0.8, "release_seconds": 0.6, "apply_seconds": 10, "capture_seconds": 8}


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def encode(width, height, data, filters, channels=3):
    """filters[y] 방식으로 거른 PNG(색 형식 2 또는 6). decode_png의 필터 복원을 확인하려고 직접 만든다."""
    stride, out, previous = width * channels, bytearray(), bytearray(width * channels)
    for y in range(height):
        line, kind = data[y * stride:(y + 1) * stride], filters[y % len(filters)]
        filtered = bytearray()
        for i, value in enumerate(line):
            left = line[i - channels] if i >= channels else 0
            up, corner = previous[i], previous[i - channels] if i >= channels else 0
            predictor = [0, left, up, (left + up) // 2, None][kind]
            if kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                predictor = left if pa <= pb and pa <= pc else up if pb <= pc else corner
            filtered.append((value - predictor) & 255)
        out += bytes([kind]) + filtered
        previous = bytearray(line)
    color = 2 if channels == 3 else 6
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(out))) + chunk(b"IEND", b""))


def test_decode_png_all_filters_rgb_and_rgba():
    width, height = 7, 5
    rgb = bytes((x * 37 + y * 11 + c * 5) % 256 for y in range(height) for x in range(width) for c in range(3))
    rgba = bytes(v for i in range(width * height) for v in (*rgb[i * 3:i * 3 + 3], (i * 13) % 256))
    for filters in ([0], [1], [2], [3], [4], [0, 1, 2, 3, 4]):
        assert decode_png(encode(width, height, rgb, filters)) == (width, height, rgb)
        assert decode_png(encode(width, height, rgba, filters, 4)) == (width, height, rgb)   # 알파는 떼어 낸다
        assert decode_png(encode(width, height, rgba, filters, 4), alpha=True)[2] == rgba[3::4]
    assert decode_png(encode(width, height, rgb, [0]), alpha=True)[2] == bytes([255]) * width * height


def test_decode_png_rejects_bad_input():
    with pytest.raises(ValueError):
        decode_png(b"not a png")
    header = struct.pack(">IIBBBBB", 1, 1, 16, 2, 0, 0, 0)
    with pytest.raises(ValueError, match="지원하지 않는"):
        decode_png(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b""))
    bad_filter = bytearray(encode(2, 1, bytes(6), [0]))
    raw = b"\x07" + bytes(6)   # 필터 번호 7
    good = encode(2, 1, bytes(6), [0])
    bad_filter = good.replace(chunk(b"IDAT", zlib.compress(b"\x00" + bytes(6))), chunk(b"IDAT", zlib.compress(raw)))
    with pytest.raises(ValueError, match="모르는 PNG 필터"):
        decode_png(bad_filter)


def test_changed_ratio_checks_every_channel():
    a = bytes([10, 10, 10] * 100)
    assert changed_ratio(a, a, 12) == 0
    for channel in range(3):
        b = bytearray(a)
        b[channel] = 255
        assert changed_ratio(a, bytes(b), 12, step=1) == pytest.approx(0.01)
    assert changed_ratio(a, bytes([20, 10, 10] * 100), 12) == 0   # 기준 이하 차이는 무시


def test_bundle_hash_is_stable_and_includes_subfolders(tmp_path):
    (tmp_path / "scene.json").write_text("{}", encoding="utf-8")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "a.png").write_bytes(b"x")
    first = bundle_hash(tmp_path)
    assert first == bundle_hash(tmp_path) and set(first["files"]) == {"media/a.png", "scene.json"}
    (tmp_path / "media" / "a.png").write_bytes(b"y")
    assert bundle_hash(tmp_path)["sha256"] != first["sha256"]


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def runner(tmp_path):
    made = []

    def make(probe="probe", **fake):
        port = free_port()
        review = tmp_path / "review" / "delivery"
        review.mkdir(parents=True, exist_ok=True)
        options = [f"-Fake{key}={value}" for key, value in fake.items()]
        launcher = [PYTHON, str(FAKE), f"-FakePort={port}", *options]
        item = Runner(tmp_path / "Art-Win64-Shipping.exe", review / "release.json", tmp_path / probe, width=128,
                      height=72, hands=Hands(("127.0.0.1", port)), launcher=launcher, working_directory=tmp_path)
        made.append(item)
        return item
    yield make
    for item in made:
        item.stop()


def publish(runner, version, mode="react", **extra):
    def go():
        publish_version(runner.release.parent, version, "scene.json", {"FakeMode": mode, **extra}, {})
    return go


def failed(report):
    return {c.name for c in report.checks if not c.passed}


def test_reacting_scene_passes(runner):
    item = runner()
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1"))
    assert report.passed, report.summary()
    names = {c.name for c in report.checks}
    assert {"적용(Applied)", "준비 시간", "준비 메모리(MB)", "빈 화면 아님", "입력 중 화면 반응(손 주변, 평소 변화 차감)",
            "입력 뒤 복귀(손 주변)", "두 손 수신", "손이 장면 행동에 닿음", "긴 프레임(16.7ms 초과, 초당)", "실행본 유지"} == names
    assert report.measurements["changed_during"] > report.measurements["changed_after"]
    assert report.measurements["max_behavior_contacts"] == 2
    assert set(report.screenshots) == {"rest", "idle0", "idle1", "idle2", "idle", "hands", "after"}
    assert all(Path(path).exists() for path in report.screenshots.values())
    assert report.bundle["files"].keys() == {"scene.json"} and report.rule_version == "ue-runner-v7"
    assert report.measurements["screen_judged"] and report.measurements["region_pixels"] > 0


def test_rgba_captures_are_read(runner):
    item = runner(Rgba=1)
    assert item.verify("v1", Thresholds(**FAST), publish(item, "v1")).passed


def test_same_process_verifies_next_version(runner):
    item = runner()
    assert item.verify("v1", Thresholds(**FAST), publish(item, "v1")).passed
    pid = item.process.pid
    report = item.verify("v2", Thresholds(**FAST), publish(item, "v2"))
    assert report.passed and item.process.pid == pid   # 교체 경로: 프로세스를 다시 띄우지 않는다


def test_already_published_version_is_accepted(runner):
    """central이 먼저 게시하고 나중에 부르거나, 같은 버전을 다시 검증해도 이미 나온 결과를 받는다."""
    item = runner()
    assert item.verify("v1", Thresholds(**FAST), publish(item, "v1")).passed
    publish(item, "v2")()
    time.sleep(1.5)
    assert item.verify("v2", Thresholds(**FAST)).passed
    assert item.verify("v2", Thresholds(**FAST)).passed


def test_stale_status_of_other_process_is_ignored(runner):
    item = runner(StaleStatus=1, FirstDelay=2)   # 처리 전 2초 동안 다른 PID의 Applied가 남아 있다
    publish(item, "v1", "still")()
    report = item.verify("v1", Thresholds(**FAST))
    assert report.measurements["status"]["Phase"] == "Applied"
    assert report.measurements["status"]["RuntimeSessionId"] != "old" and report.measurements["status"]["RequestId"] != 7
    assert "입력 중 화면 반응(손 주변, 평소 변화 차감)" in failed(report)   # 남은 상태가 아니라 이번 프로세스 결과로 판정


def test_old_capture_files_are_not_reused(runner):
    """같은 Probe 폴더에 예전 통과 캡처가 남아 있어도, 반응 없는 새 빌드는 실패한다."""
    first = runner()
    assert first.verify("v1", Thresholds(**FAST), publish(first, "v1")).passed
    first.stop()
    second = runner()
    second.release.with_suffix(".json.status.json").unlink()
    (second.release.parent / "versions" / "v1" / "scene.json").write_text(json.dumps({"FakeMode": "still"}))
    report = second.verify("v1", Thresholds(**FAST))
    assert not report.passed and "입력 중 화면 반응(손 주변, 평소 변화 차감)" in failed(report)


def test_no_reaction_fails(runner):
    item = runner()
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1", "still"))
    assert {"입력 중 화면 반응(손 주변, 평소 변화 차감)", "손이 장면 행동에 닿음"} <= failed(report)


def test_background_motion_alone_is_not_a_reaction(runner):
    """손과 무관하게 움직이는 장면(루프 영상 · 유체)은 화면이 바뀌어도 반응으로 치지 않는다."""
    item = runner()
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1", "drift"))
    assert report.measurements["changed_idle"] > 0
    # 화면 판정은 띠 위치에 따라 우연히 넘을 수 있어, 손이 장면 행동에 닿지 않았다는 판정이 최종적으로 막는다
    assert not report.passed and "손이 장면 행동에 닿음" in failed(report)


def test_moving_background_with_reaction_passes(runner):
    """배경이 흐르면서 손에도 반응하는 정상 작품은 통과한다(평소 변화를 빼고 손 주변만 본다)."""
    item = runner()
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1", "driftreact"))
    assert report.passed, report.summary()
    assert report.measurements["changed_idle"] > 0 and report.measurements["screen_judged"]


def test_heavy_motion_is_judged_by_behavior_input(runner):
    """손 주변이 평소에도 절반 넘게 바뀌면 화면으로는 가르지 않고 행동 입력으로만 판정한다."""
    item = runner()
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1", "floodreact"))
    assert report.passed, report.summary()
    assert not report.measurements["screen_judged"] and report.needs_review and report.review_reasons
    assert report.verdict == "review"
    assert "입력 중 화면 반응(손 주변, 평소 변화 차감)" not in {c.name for c in report.checks}


@pytest.mark.parametrize("mode", ["drifthidden", "widehidden", "floodhidden"])
def test_hidden_reaction_is_not_silently_passed(runner, mode):
    """행동 입력은 닿지만 화면에 반응이 안 보이는 장면(앞 층이 가림 등)은 통과하지 않거나 사람 확인 필요로 올라간다."""
    item = runner()
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1", mode))
    assert not report.passed or report.needs_review, report.summary()
    assert report.verdict in ("fail", "review") and report.summary()["verdict"] == report.verdict


@pytest.mark.parametrize("baseline, pairs, touched, doubt", [
    (0.46, [0.43, 0.46, 0.43], 2, True),     # PC1 ripple_lake: 평소 흔들림 0.032 > 반응 기준 0.02, 손은 닿음
    (0.46, [0.45, 0.46, 0.455], 2, False),   # 흔들림이 기준보다 작으면 측정을 믿고 그대로 판정
    (0.05, [0.0, 0.05, 0.02], 2, False),     # 평소 변화가 작으면(≤ 0.1) 그대로
    (0.46, [0.43, 0.46, 0.43], 0, False),    # 손이 장면 행동에 안 닿았으면 반응 없음은 실패
])
def test_reaction_doubt_only_when_idle_noise_exceeds_the_threshold(baseline, pairs, touched, doubt):
    found = ue_runner.reaction_doubt(baseline, pairs, touched, Thresholds())
    assert (found is not None) == doubt


def test_quiet_scene_does_not_need_review(runner):
    item = runner()
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1"))
    assert report.passed and not report.needs_review and report.review_reasons == []
    assert len(report.measurements["changed_idle_pairs"]) == 3 and report.verdict == "pass"
    assert {"rest", "idle0", "idle1", "idle2", "hands", "after"} <= set(report.screenshots)


def test_idle_pairs_must_be_positive():
    with pytest.raises(ValueError, match="idle_pairs"):
        Thresholds(idle_pairs=0)


def test_blank_screen_fails(runner):
    item = runner()
    assert "빈 화면 아님" in failed(item.verify("v1", Thresholds(**FAST), publish(item, "v1", "blank")))


def test_staying_marks_need_recovery_off(runner):
    item = runner()
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1", "stay"))
    assert failed(report) == {"입력 뒤 복귀(손 주변)"}
    report = item.verify("v2", Thresholds(**FAST, expect_recovery=False), publish(item, "v2", "stay"))
    assert report.passed, report.summary()


def test_failed_apply_stops_early(runner):
    item = runner()
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1", FakeFail=True))
    assert failed(report) == {"적용(Applied)"} and report.screenshots == {}


@pytest.mark.parametrize("fake, limits, check", [
    ({"PrepareSeconds": 12}, {}, "준비 시간"),
    ({"PreparedBytes": 2 * 2**30}, {}, "준비 메모리(MB)"),
    ({"LongFrames": 3}, {"max_long_frames_per_second": 5}, "긴 프레임(16.7ms 초과, 초당)"),
    ({"Dropped": 3}, {"max_long_frames_per_second": 5}, "긴 프레임(16.7ms 초과, 초당)"),   # 버린 것도 센다
])
def test_limits(runner, fake, limits, check):
    item = runner(**fake)
    report = item.verify("v1", Thresholds(**FAST, **limits), publish(item, "v1"))
    assert failed(report) == {check}


def test_long_frames_are_counted_per_second(runner):
    """행마다 1개(초당 약 4개)면 초당 5개 기준은 통과한다. 총수로 세면 2초 구간에서 기준을 넘는다."""
    item = runner(LongFrames=1)
    limits = Thresholds(**{**FAST, "input_seconds": 2.0, "max_long_frames_per_second": 5})
    report = item.verify("v1", limits, publish(item, "v1"))
    assert report.measurements["long_frames"] > 5 and report.passed, report.summary()


def test_missing_capture_is_requested_again(runner):
    item = runner(SkipFirstCapture=1)
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1"))
    assert report.passed, report.summary()


def test_long_frames_flag_environment_when_gpu_busy(runner, monkeypatch):
    monkeypatch.setattr(ue_runner, "gpu_snapshot", lambda: {"utilization_percent": 90.0})
    item = runner(LongFrames=3)
    report = item.verify("v1", Thresholds(**FAST, max_long_frames_per_second=5), publish(item, "v1"))
    assert report.environment_suspect
    monkeypatch.setattr(ue_runner, "gpu_snapshot", lambda: {"utilization_percent": 5.0})
    quiet = runner()
    assert not quiet.verify("v1", Thresholds(**FAST), publish(quiet, "v1")).environment_suspect


def test_wrong_capture_size_fails(runner):
    item = runner(Size="32x18")
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1"))
    assert not report.passed and "캡처 크기" in report.error


def test_dead_process_is_reported(runner):
    item = runner()
    assert item.verify("v1", Thresholds(**FAST), publish(item, "v1")).passed
    item.process.kill()
    item.process.wait()
    report = item.verify("v2", Thresholds(**{**FAST, "apply_seconds": 5}), publish(item, "v2"))
    assert report.passed   # 죽어 있으면 다시 띄워 검증한다
    assert item.alive()


def test_stop_then_reuse(runner):
    item = runner()
    assert item.verify("v1", Thresholds(**FAST), publish(item, "v1")).passed
    item.stop()
    assert not item.alive()
    assert item.verify("v2", Thresholds(**FAST), publish(item, "v2")).passed   # 손 송신 소켓도 다시 연다


def test_missing_executable_is_reported(tmp_path):
    review = tmp_path / "delivery"
    review.mkdir()
    item = Runner(tmp_path / "missing.exe", review / "release.json", tmp_path / "probe", working_directory=tmp_path)
    report = item.verify("v1", Thresholds(**FAST), lambda: publish_version(review, "v1", "scene.json", {}, {}))
    item.stop()
    assert not report.passed and report.error and failed(report) == {"검증 진행"}


def test_start_failure_cleans_up(tmp_path, monkeypatch):
    """계측이 시작되지 않으면 실행본을 남기지 않는다(실기에서는 UDP 7001을 잡은 채 남는다)."""
    review = tmp_path / "delivery"
    review.mkdir()
    silent = tmp_path / "silent.py"
    silent.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    item = Runner(tmp_path / "x.exe", review / "release.json", tmp_path / "probe", launcher=[PYTHON, str(silent)],
                  working_directory=tmp_path)
    real = time.monotonic
    monkeypatch.setattr(ue_runner.time, "monotonic", lambda: real() + (45 if item.process else 0))
    with pytest.raises(RuntimeError, match="계측"):
        item.start()
    assert not item.alive()


def test_starts_without_windows_only_options(runner, monkeypatch):
    """Linux CI처럼 STARTUPINFO가 없는 환경에서도 가짜 실행본으로 검증이 돈다(창 숨김은 Windows에서만)."""
    real, seen = ue_runner.subprocess.Popen, []

    def popen(*args, **kwargs):
        seen.append(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(ue_runner.sys, "platform", "linux")
    monkeypatch.setattr(ue_runner.subprocess, "Popen", popen)
    item = runner()
    assert item.verify("v1", Thresholds(**FAST), publish(item, "v1")).passed
    assert seen and "startupinfo" not in seen[0]


# 잔상 시험: 실제 5초 · 20초 대신 같은 비율로 줄인 시간. 장면 반감기 0.8초면 잔상 0.8초, 복귀 3초(반감기 × 4, 상한 3초).
# 평소 간격(입력 1.2초 × 3)을 잔상 비교 간격(입력 + 잔상 0.8초)보다 넉넉히 길게 둔다. 함께 도는 시험으로 캡처가 늦어져도
# '잔상 간격이 평소 간격보다 김'(사람 확인)으로 바뀌지 않게.
QUICK = {**{k: v for k, v in FAST.items() if k != "release_seconds"}, "input_seconds": 1.2}
AFTER = {**QUICK, "afterimage_seconds": 0.8, "afterimage_half_life": 0.5, "stay_half_life": 5, "max_recovery_seconds": 3}
LIQUID = {"Profiles": {"FluidField": [{"Name": "Ink", "Field": {"DyeHalfLifeSeconds": 0.8}}]}, "FakeHalfLife": 0.8}
WATER = {"Profiles": {"SurfaceDeformation": [{"Name": "Water", "Surface": {"DampingPerSecond": 2}}]}}   # 반감기 0.69초
AFTERIMAGE = "손 뗀 뒤 0.8초 잔상(손 주변, 긴 평소 변화 차감)"
SETTLE = "입력 뒤 가라앉음(손 주변, 같은 간격 평소 변화 기준)"


@pytest.mark.parametrize("half_life, afterimage, recovery, release", [
    (None, False, "settle", 3.0),       # 모르는 장면: 지금처럼
    (1.5, False, "settle", 3.0),        # 짧은 반감기도 3초(더 기다리면 평소 변화 간격과 어긋남)
    (5, True, "fade", 20),
    (10, True, "fade", 20),             # 반감기 × 4가 상한 20초에 묶임
    (14, True, None, 3.0),              # 남아 있는 장면은 복귀를 보지 않음
    (0, True, None, 3.0),               # 그리기 자국 0 = 사라지지 않음
])
def test_plan_follows_scene_half_life(half_life, afterimage, recovery, release):
    scene = None if half_life is None else {"Profiles": {"SurfacePaint": [{"FadeHalfLifeSeconds": half_life}]}}
    plan = ue_runner.plan_checks(Thresholds(), scene)
    assert (plan.afterimage, plan.recovery, plan.release_seconds) == (afterimage, recovery, release)


@pytest.mark.parametrize("limits, scene, expected", [
    ({"expect_recovery": True}, {"FluidField": [{"Field": {"DyeHalfLifeSeconds": 14}}]}, (True, "fade", 20)),
    ({"expect_afterimage": False}, {"FluidField": [{"Field": {"DyeHalfLifeSeconds": 5}}]}, (False, "fade", 20)),
    ({}, {"MaterialScalar": [{"Name": "Glow"}]}, (False, "settle", 3.0)),   # 반감기 모름: 잔상 안 봄
    ({"release_seconds": 1}, {"FluidField": [{"Field": {"DyeHalfLifeSeconds": 5}}]}, (True, "fade", 5.0)),   # 잔상 뒤
])
def test_plan_combinations(limits, scene, expected):
    plan = ue_runner.plan_checks(Thresholds(**limits), {"Profiles": scene})
    assert (plan.afterimage, plan.recovery, plan.release_seconds) == expected


def test_plan_explicit_thresholds_win():
    scene = {"Profiles": {"FluidField": [{"Field": {"DyeHalfLifeSeconds": 5}}]}}
    plan = ue_runner.plan_checks(Thresholds(expect_recovery=False, expect_afterimage=False, release_seconds=1), scene)
    assert (plan.afterimage, plan.recovery, plan.release_seconds) == (False, None, 1)


@pytest.mark.parametrize("template", ["ripple_lake", "ripple_star"])
def test_water_scene_moves_hands_inside_drop_area(template):
    """그림 위 물결은 물이 화면 아래에만 있어 가운데 궤적이 물에 안 닿는다(PC1 10/25 fail) → 손 궤적을 물 영역 안으로."""
    from studio.assembly.ue_assemble import template_scene
    plan = ue_runner.plan_checks(Thresholds(), template_scene(template))
    (u0, v0), (u1, v1) = plan.area
    for k in range(40):
        for u, v in ue_runner.trajectory(k * 0.2, plan.area):
            assert u0 <= u <= u1 and v0 <= v <= v1
            assert u0 <= 1 - u <= u1   # 좌우 반전돼도 물 안


def test_scene_without_drop_area_keeps_center_trajectory():
    plan = ue_runner.plan_checks(Thresholds(), {"Profiles": {"FluidField": [{"Field": {"DyeHalfLifeSeconds": 5}}]}})
    assert plan.area is None
    assert ue_runner.trajectory(0.0, plan.area) == [[0.36, 0.4], [0.64, 0.6]]   # 라넬 회귀 시험 궤적 그대로


def test_one_sided_drop_area_stays_inside_without_mirror():
    """물이 화면 한쪽에만 있으면 반전 범위와 겹치지 않는다: 원래 범위 안에 두고 사람 확인 사유가 된다."""
    area = ((0.0, 0.6), (0.4, 0.9))
    assert not ue_runner.mirror_safe(area)
    for k in range(40):
        for u, v in ue_runner.trajectory(k * 0.2, area):
            assert 0.0 <= u <= 0.4 and 0.6 <= v <= 0.9


def test_fading_afterimage_passes(runner):
    """손자국이 반감기대로 옅어지는 장면: 잔상 · 가라앉음 · 옅어짐 모두 통과(자동 pass)."""
    item = runner()
    report = item.verify("v1", Thresholds(**AFTER), publish(item, "v1", "fade", **LIQUID))
    assert report.verdict == "pass", report.summary()
    assert {AFTERIMAGE, SETTLE, "잔상이 옅어짐(손 주변 평균 밝기, 긴 평소 차이 차감)"} <= {c.name for c in report.checks}
    assert "입력 뒤 복귀(손 주변)" not in {c.name for c in report.checks}
    # 기존 라벨 순서를 지키고 새 라벨은 맨 뒤(키트가 순서대로 captures/NNN.png로 복사한다)
    assert list(report.screenshots) == ["rest", "idle0", "idle1", "idle2", "idle", "hands", "after", "idle3",
                                        "afterimage", "settled"]
    assert report.measurements["persistence_medium"] == "FluidField" and report.measurements["release_seconds"] == 3


def test_reaction_that_vanishes_fails_afterimage(runner):
    """배경이 흐르고 손 반응은 바로 사라지는 장면: 배경 흐름만으로 잔상이 통과하면 안 된다."""
    item = runner()
    report = item.verify("v1", Thresholds(**AFTER), publish(item, "v1", "driftreact", **LIQUID))
    # 가짜 띠는 주기적으로 흘러 타이밍에 따라 잔상 실패 · 사람 확인(긴 평소 변화 > 0.1)이 갈린다. 지킬 성질은 '자동 통과 없음'.
    assert AFTERIMAGE not in {c.name for c in report.checks if c.passed} and report.verdict != "pass", report.summary()


def test_reaction_without_afterimage_fails(runner):
    """배경이 흐르지 않고 손 반응이 바로 사라지는 장면인데 반감기가 길게 적혀 있으면 잔상 실패."""
    item = runner()
    report = item.verify("v1", Thresholds(**AFTER), publish(item, "v1", "react", **LIQUID))
    assert AFTERIMAGE in failed(report) and report.verdict == "fail", report.summary()


def test_afterimage_that_never_fades_goes_to_review(runner):
    """잔상이 줄지 않는 장면: 옅어짐 기준은 PC1 보정 전이라 실패가 아니라 사람 확인."""
    item = runner()
    report = item.verify("v1", Thresholds(**AFTER), publish(item, "v1", "stay", **LIQUID))
    assert "잔상이 옅어짐(손 주변 평균 밝기, 긴 평소 차이 차감)" not in {c.name for c in report.checks if c.passed}
    assert any(r.startswith("잔상이 옅어짐") for r in report.review_reasons), report.summary()


def test_slow_fade_still_fading_passes_settle(runner):
    """복귀 화면에서도 아직 옅어지는 중인 진한 잔상: 같은 간격 변화는 크지만 평소 화면에 계속 가까워지면 가라앉음 통과.
    PC1 fluid(색 반감기 5초)는 20초 뒤에도 검은 바탕의 색이 옅어지는 중이라 픽셀이 바뀌어 실패했다."""
    item = runner()
    slow = {**LIQUID, "FakeHalfLife": 3}   # 장면 반감기 0.8초로 복귀 시점을 잡지만 화면은 3초 반감기로 천천히 옅어진다
    report = item.verify("v1", Thresholds(**AFTER), publish(item, "v1", "fade", **slow))
    assert report.measurements["changed_settle"] > 0.05   # 같은 간격 변화만 보면 실패하는 장면
    assert report.measurements["settle_approaching"] is True
    assert SETTLE not in failed(report) and report.verdict == "pass", report.summary()


def test_slow_fade_without_afterimage_frame_keeps_strict_settle(runner):
    """잔상 화면을 안 찍으면(expect_afterimage=False) 두 시점만으로 '가까워짐'을 보지 않는다(깜빡임도 절반은 통과하므로)."""
    item = runner()
    slow = {**LIQUID, "FakeHalfLife": 3}
    report = item.verify("v1", Thresholds(**AFTER, expect_afterimage=False), publish(item, "v1", "fade", **slow))
    assert report.measurements["settle_approaching"] is False
    assert SETTLE in failed(report), report.summary()


def test_flickering_afterimage_fails_settle(runner):
    """잔상이 남아도 계속 크게 바뀌면 가라앉음 실패."""
    item = runner()
    report = item.verify("v1", Thresholds(**AFTER), publish(item, "v1", "fadeflicker", **LIQUID))
    assert SETTLE in failed(report) and report.verdict == "fail"


def test_water_afterimage_is_review_only(runner):
    """물결 매질은 화면 잔상이 반감기와 맞지 않아 미달이어도 실패가 아니라 사람 확인."""
    item = runner()
    report = item.verify("v1", Thresholds(**AFTER), publish(item, "v1", "react", **WATER))
    assert report.passed and report.verdict == "review", report.summary()
    assert any("SurfaceDeformation" in r for r in report.review_reasons)


def test_short_half_life_keeps_old_recovery(runner):
    """반감기가 짧으면 지금처럼 3초 뒤 복귀만 본다(평소 화면 추가 없음)."""
    item = runner()
    short = {"Profiles": {"FluidField": [{"Field": {"DyeHalfLifeSeconds": 0.3}}]}}
    report = item.verify("v1", Thresholds(**AFTER), publish(item, "v1", "react", **short))
    assert report.passed, report.summary()
    assert set(report.screenshots) == {"rest", "idle0", "idle1", "idle2", "idle", "hands", "after"}
    assert "입력 뒤 복귀(손 주변)" in {c.name for c in report.checks}


def test_strong_background_flow_sends_afterimage_to_review(runner):
    """손 주변 배경이 크게 흐르면(긴 평소 변화 > 0.1) 잔상 미달이어도 실패가 아니라 사람 확인."""
    item = runner()
    report = item.verify("v1", Thresholds(**AFTER), publish(item, "v1", "driftreact", FakeBand=6, **LIQUID))
    assert AFTERIMAGE not in failed(report), report.summary()
    assert report.measurements["changed_idle_long"] > 0.1
    assert any("긴 평소 변화" in r for r in report.review_reasons), report.summary()


def test_water_afterimage_passing_still_needs_review(runner):
    """물결 매질은 잔상이 보여도 반감기와 화면이 맞지 않아 자동 통과로 두지 않는다."""
    item = runner()
    water = {"Profiles": {"SurfaceDeformation": [{"Surface": {"DampingPerSecond": 2}}]}, "FakeHalfLife": 0.8}
    report = item.verify("v1", Thresholds(**AFTER), publish(item, "v1", "fade", **water))
    assert report.passed and report.verdict == "review", report.summary()
    assert any(r.startswith("잔상 · 복귀는 사람 확인") for r in report.review_reasons)


def test_trace_reads_only_new_complete_lines(tmp_path):
    """이어 읽기: 지난번 위치 뒤의 완성된 줄만 읽고, 다른 프로세스 행은 빼고, 파일이 바뀌면(작아지면) 처음부터 읽는다."""
    item = Runner(tmp_path / "Art-Win64-Shipping.exe", tmp_path / "release.json", tmp_path, working_directory=tmp_path)
    item.process = type("Process", (), {"pid": 7})()
    trace = tmp_path / "Trace.jsonl"
    trace.write_bytes(b'{"ProcessId": 7, "n": 1}\n{"ProcessId": 9, "n": 2}\n{"ProcessId": 7, "n"')   # 마지막은 쓰는 중
    assert [r["n"] for r in item.trace()] == [1]
    with trace.open("ab") as file:
        file.write(b': 3}\n')
    assert [r["n"] for r in item.trace()] == [1, 3]
    trace.write_bytes(b'{"ProcessId": 7, "n": 4}\n')   # runtime 쪽에서 새 파일로 바뀜
    assert [r["n"] for r in item.trace()] == [1, 3, 4]
    item.rotate_trace()
    assert not trace.exists() and (tmp_path / "Trace.previous.jsonl").exists() and item.trace() == []


def test_large_old_trace_is_rotated_before_verify(runner, tmp_path):
    """실행본을 오래 띄워 두면 계측이 쌓인다(PC1에서 2.26GB로 멈춤). 검증 시작 때 직전 것으로 넘기고 이어서 판정한다."""
    item = runner()
    item.probe.mkdir(parents=True, exist_ok=True)
    old = json.dumps({"ProcessId": 1, "Contacts": [], "Hosts": [], "Padding": "x" * 1000}) + "\n"
    (item.probe / "Trace.jsonl").write_text(old * 20000, encoding="utf-8")   # 약 20MB
    report = item.verify("v1", Thresholds(**FAST), publish(item, "v1"))
    assert report.passed, report.summary()
    assert (item.probe / "Trace.previous.jsonl").stat().st_size > 10 * 2**20
    assert (item.probe / "Trace.jsonl").stat().st_size < 2 * 2**20
    report = item.verify("v2", Thresholds(**FAST), publish(item, "v2"))   # 같은 프로세스 다음 검증도 교체 뒤 판정
    assert report.passed and report.measurements["max_behavior_contacts"] == 2, report.summary()


def test_probe_stopped_by_rotation_restarts_shipping(runner):
    """runtime은 계측 쓰기가 한 번 실패하면 끝까지 멈춘다. 교체 뒤 계측이 안 붙으면 실행본을 새로 띄워 검증한다."""
    item = runner(StickyTrace=1)
    assert item.verify("v1", Thresholds(**FAST), publish(item, "v1")).passed
    first = item.process.pid
    report = item.verify("v2", Thresholds(**FAST), publish(item, "v2"))
    assert report.passed, report.summary()
    assert item.process.pid != first and report.measurements["restarted"] == "trace_stalled"


def test_rotation_refused_keeps_reading_where_it_was(tmp_path, monkeypatch):
    """교체가 계속 거부되면 파일은 그대로 이어 읽고, 지난 검증 행만 비운다."""
    item = Runner(tmp_path / "Art-Win64-Shipping.exe", tmp_path / "release.json", tmp_path, working_directory=tmp_path)
    item.process = type("Process", (), {"pid": 7})()
    trace = tmp_path / "Trace.jsonl"
    trace.write_bytes(b'{"ProcessId": 7, "n": 1}\n')
    assert len(item.trace()) == 1

    def refuse(source, target):
        raise PermissionError("UE가 쓰는 중")
    monkeypatch.setattr(ue_runner, "replace_with_retry", refuse)
    item.rotate_trace()
    assert trace.exists() and item.trace() == []
    with trace.open("ab") as file:
        file.write(b'{"ProcessId": 7, "n": 2}\n')
    assert [r["n"] for r in item.trace()] == [2]


def test_first_rotation_refused_skips_large_old_trace(tmp_path, monkeypatch):
    """새 Runner의 첫 교체가 거부되어도 큰 기존 파일은 읽지 않고 새 완성 행만 판정한다."""
    item = Runner(tmp_path / "Art-Win64-Shipping.exe", tmp_path / "release.json", tmp_path, working_directory=tmp_path)
    item.process = type("Process", (), {"pid": 7})()
    trace = tmp_path / "Trace.jsonl"
    old = b'{"ProcessId": 7, "n": 1, "padding": "' + b'x' * 1000 + b'"}\n'
    trace.write_bytes(old * 20000 + b'{"ProcessId": 7, "n"')
    assert item._trace_offset == 0

    def refuse(source, target):
        raise PermissionError("UE가 쓰는 중")

    monkeypatch.setattr(ue_runner, "replace_with_retry", refuse)
    item.rotate_trace()
    assert item._trace_offset == trace.stat().st_size > 10 * 2**20
    assert item.trace() == []
    with trace.open("ab") as file:
        file.write(b': 2}\n{"ProcessId": 7, "n": 3}\n')
    assert [row["n"] for row in item.trace()] == [3]
