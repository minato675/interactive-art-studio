"""PC1 UE 실행 검증 러너 — 전시(PC2)와 같은 Shipping 실행본 · 같은 release.json 교체 경로로 작품을 재생해 판정한다.

central은 검수 폴더(review/delivery)에 버전을 게시하고 verify(version)를 부른다. 러너는
  1. 검수 폴더 release.json을 보는 Shipping 실행본(-ArtworkRelease · -ArtworkProbe)을 띄우거나 이미 떠 있는 것을 쓴다
  2. release.json.status.json에서 이 버전이 이번 프로세스에서 Applied인지 기다린다
  3. 입력 없이 같은 간격으로 여러 장(평소 변화량의 최대값 · 편차) → 합성 손 2개(UDP protocol v2, 127.0.0.1:7001) 궤적 중 → 입력 뒤를 Probe Capture.json으로
     찍는다. 캡처 ID는 매번 새로 만든다(runtime은 같은 ID 반복과 이미 있는 파일을 무시한다)
  4. Probe Trace.jsonl에서 손이 장면 행동에 닿았는지(BehaviorInputs.ActiveContacts)와 입력 구간의 긴 프레임(16.7ms 초과,
     저장분 + DroppedLongFrames)을 본다
  5. 판정(적용 · 준비 시간 · 화면 · 반응 · 잔상 · 복귀 · 긴 프레임 · 프로세스 유지)과 리포트(측정값 · 스크린샷 · 규칙 버전 ·
     번들 해시 · 카탈로그 버전 · GPU 사용량)를 돌려준다.
잔상 · 복귀는 버전 폴더 scene.json의 손 뗀 뒤 화면 반감기 h(ue_contract.persistence)로 정한다. h를 모르면 3초 뒤 복귀만 본다.
비교하는 두 화면의 간격이 평소 변화를 잰 간격과 다르면 배경 흐름만으로 판정이 바뀌므로 간격을 맞춘다(잔상 검사 때만 평소 화면 1장 추가).
central을 import하지 않는다(목표 구조에서는 제작 노드 Node Agent가 같은 러너를 부른다).
GPU는 다른 작업(RL 훈련 등)과 공유될 수 있어, 긴 프레임 기준을 넘으면 측정 전 GPU 사용량과 함께 '환경 의심'으로 표시한다
(실행본을 계속 띄워 두면 그 사용량에는 UE 자신의 부하도 들어간다 — 첫 실행 때만 순수한 외부 부하다).
"""
import hashlib
import json
import math
import os
import socket
import struct
import subprocess
import sys
import time
import uuid
import zlib
from dataclasses import asdict, dataclass, field
from itertools import combinations, pairwise
from pathlib import Path

from interactive_controller.delivery.publisher import replace_with_retry

from studio.verify.png import decode_png
from studio.verify.ue_contract import AFTERIMAGE_HALF_LIFE, SCREEN_MEDIA, STAY_HALF_LIFE, catalog_version, persistence

RULE_VERSION = "ue-runner-v7"
SOURCES = ("td_main/forest_hand", "td_main/hand_tracking_2")   # 조립 템플릿 조건의 TD 소스
UDP_TARGET = ("127.0.0.1", 7001)
SEND_HZ = 40
FINAL_PHASES = ("Applied", "Failed", "Cancelled")
Area = tuple[tuple[float, float], tuple[float, float]]   # 화면 비율 범위 ((u 최소, v 최소), (u 최대, v 최대))


@dataclass
class Thresholds:
    apply_seconds: float = 35          # 게시 → 결과 대기 한도
    max_prepare_seconds: float = 10    # status.PrepareSeconds 상한
    max_prepared_mb: float = 1024      # status.PreparedBytes 상한
    settle_seconds: float = 2.0        # 적용 뒤 첫 화면까지
    input_seconds: float = 3.2         # 손 궤적 길이(평소 변화량도 같은 간격으로 잰다)
    release_seconds: float | None = None   # 입력을 멈춘 뒤 복귀 화면까지. None이면 장면 반감기로 정함(모르면 3초)
    pixel_delta: int = 12              # 한 픽셀이 '바뀜'인 채널 차이(라넬 회귀 시험과 같은 값)
    hand_radius: float = 0.12          # 손 궤적 주변 판정 영역 반경(화면 너비 비율)
    min_reaction: float = 0.02         # 손 주변: 입력 중 변화 − 평소 변화 ≥ 이 값
    recovery_slack: float = 0.05       # 손 주변: 입력 뒤 변화 ≤ 평소 변화 × 2 + 이 값
    screen_judge_limit: float = 0.5    # 손 주변 평소 변화가 이보다 크면 화면으로 판정하지 않고 행동 입력으로만 본다
    review_motion_limit: float = 0.1   # 손 주변 평소 변화가 이보다 크면 화면 판정을 믿기 어려워 사람 확인 필요(needs_review)
    idle_pairs: int = 3                # 평소 변화를 잴 입력 없는 화면 쌍 수(가장 큰 평소 변화를 기준으로 쓴다)
    max_long_frames_per_second: float = 6   # 입력 구간 긴 프레임(초당)
    expect_recovery: bool | None = None    # 입력 뒤 손 주변이 돌아와야 함. None이면 장면 반감기로 정함(남아 있는 장면은 끔)
    expect_afterimage: bool = True         # 장면 반감기가 길면 손 뗀 뒤 잔상을 본다. False면 끈다
    afterimage_seconds: float = 5.0    # 손 뗀 뒤 잔상을 찍는 시점(전시기획서: 잔상 5초 이상)
    min_afterimage: float = 0.02       # 손 주변: 잔상 화면 변화 − 긴 평소 변화 ≥ 이 값
    min_shrink: float = 0.005          # 손 주변 평균 밝기 차이: 손 화면에서 멀어진 정도(잔상 → 복귀) − 긴 평소 차이 ≥ 이 값
    settle_ratio: float = 0.8          # 아직 옅어지는 잔상의 가라앉음: 평소와의 차이가 잔상 화면의 이 비율 이하로 줄어야 함(PC1 paint 0.66)
    afterimage_half_life: float = AFTERIMAGE_HALF_LIFE   # 이 이상인 장면만 잔상을 본다
    stay_half_life: float = STAY_HALF_LIFE               # 이 이상인 장면은 복귀를 보지 않는다
    max_recovery_seconds: float = 20   # 반감기 장면의 복귀 화면 시점 상한(반감기 × 4까지)
    capture_seconds: float = 12

    def __post_init__(self):
        if self.idle_pairs < 1:
            raise ValueError("idle_pairs는 1 이상")


@dataclass
class Check:
    name: str
    passed: bool
    value: object = None
    limit: object = None


@dataclass
class Report:
    version: str
    passed: bool = False
    checks: list[Check] = field(default_factory=list)
    screenshots: dict[str, str] = field(default_factory=dict)
    measurements: dict = field(default_factory=dict)
    rule_version: str = RULE_VERSION
    bundle: dict = field(default_factory=dict)
    catalog_version: str = ""
    gpu_before: dict | None = None
    environment_suspect: bool = False
    needs_review: bool = False          # 규칙은 통과했지만 화면으로 반응을 확인하지 못함 → 사람 최종 검수로
    review_reasons: list[str] = field(default_factory=list)
    error: str | None = None

    def add(self, name, passed, value=None, limit=None):
        self.checks.append(Check(name, bool(passed), value, limit))

    @property
    def verdict(self) -> str:
        """게시 판단은 이 값으로 한다: pass(자동 통과) · review(사람 최종 검수 필요) · fail. passed만 보면 review가 새어 나간다."""
        if not self.passed:
            return "fail"
        return "review" if self.needs_review else "pass"

    def summary(self) -> dict:
        data = asdict(self)
        data["failed"] = [c.name for c in self.checks if not c.passed]
        data["verdict"] = self.verdict
        return data


def changed_ratio(a: bytes, b: bytes, delta: int, step: int = 2, pixels=None) -> float:
    """두 RGB 화면에서 어느 채널이든 delta보다 크게 바뀐 픽셀 비율. pixels(바이트 시작 위치 목록)를 주면 그 영역만."""
    if len(a) != len(b):
        raise ValueError("화면 크기가 다름")
    pixels = range(0, len(a) - 2, 3 * step) if pixels is None else pixels
    changed = sum(1 for i in pixels
                  if abs(a[i] - b[i]) > delta or abs(a[i + 1] - b[i + 1]) > delta or abs(a[i + 2] - b[i + 2]) > delta)
    return changed / max(len(pixels), 1)


def mean_difference(a: bytes, b: bytes, pixels) -> float:
    """두 RGB 화면의 픽셀별 가장 큰 채널 차이 평균(0~1). 잔상이 얼마나 옅어졌는지처럼 기준선을 넘은 픽셀 수로는 안 보이는 변화용."""
    if len(a) != len(b):
        raise ValueError("화면 크기가 다름")
    total = sum(max(abs(a[i] - b[i]), abs(a[i + 1] - b[i + 1]), abs(a[i + 2] - b[i + 2])) for i in pixels)
    return total / 255 / max(len(pixels), 1)


def interaction_isolation(quiet, active, target, control, minimum=0.02):
    """비포화 평균 변화의 영역 간 차이를 무입력 범위와 비교한다. 인과의 확정 판정은 아니다."""
    result = {"method": "spatial_temporal_control_v1", "target_pixels": len(target),
              "control_pixels": len(control), "status": "inconclusive", "causal_verification": False}
    if len(quiet) < 3 or len(target) < 32 or len(control) < 32:
        return {**result, "reason": "insufficient_samples"}
    baseline = [(mean_difference(a, b, target), mean_difference(a, b, control)) for a, b in pairwise(quiet)]
    local, remote = mean_difference(quiet[-1], active, target), mean_difference(quiet[-1], active, control)
    gaps = [a - b for a, b in baseline]
    # 전역 움직임이 평소보다 급증한 경우 영역 간 차이도 잘못 커질 수 있다.
    controls = [b for _, b in baseline]
    control_stable = min(controls) - minimum <= remote <= max(controls) + minimum
    signed = local - remote
    excess = max(signed - max(gaps), min(gaps) - signed, 0)
    result.update(idle_target_mean=[a for a, _ in baseline], idle_control_mean=controls,
                  idle_spatial_difference=gaps, active_target_mean=local, active_control_mean=remote,
                  active_spatial_difference=signed, local_excess=excess, minimum=minimum,
                  control_stable=control_stable,
                  direction="increase" if signed > max(gaps) else "decrease" if signed < min(gaps) else "none")
    if control_stable and excess >= minimum:
        result.update(status="localized_change_observed", reason="outside_idle_spatial_range")
    else:
        result["reason"] = "control_motion_shift" if not control_stable else "within_idle_noise"
    return result


@dataclass
class Plan:
    """장면 반감기로 정한 잔상 · 복귀 검사. recovery: None(안 봄) · "settle"(3초 뒤 평소 수준) · "fade"(가라앉음 · 줄어듦)."""
    half_life: float | None
    medium: str | None
    afterimage: bool
    recovery: str | None
    release_seconds: float
    area: Area | None = None   # 손 궤적을 넣을 화면 범위(물결 범위). None이면 가운데

    @property
    def screen(self) -> bool:   # 물결 · 입자는 화면 잔상이 반감기와 맞지 않아 사람 확인으로만 둔다(PC1 보정 전)
        return self.medium in SCREEN_MEDIA


def plan_checks(limits: Thresholds, scene: dict | None) -> Plan:
    found = persistence(scene) if scene else None
    half_life, medium = found if found else (None, None)
    lasting = half_life is not None and half_life >= limits.afterimage_half_life
    afterimage = lasting and limits.expect_afterimage   # 반감기를 모르거나 짧으면 잔상 시점이 복귀 시점보다 늦어진다
    if limits.expect_recovery is False or (limits.expect_recovery is None and lasting
                                           and half_life >= limits.stay_half_life):
        recovery = None
    else:
        recovery = "fade" if lasting else "settle"
    if limits.release_seconds is not None:   # 잔상을 볼 때는 잔상 화면 뒤에 찍어야 하므로 잔상 시점보다 짧게 두지 않는다
        release = max(limits.release_seconds, limits.afterimage_seconds) if afterimage else limits.release_seconds
    elif recovery == "fade":
        release = min(half_life * 4, limits.max_recovery_seconds)
    else:
        release = 3.0   # 짧은 반감기도 3초: 더 기다리면 평소 변화를 잰 간격과 어긋난다
    return Plan(half_life, medium, afterimage, recovery, release, drop_area(scene) if scene else None)


def reaction_doubt(baseline: float, pairs: list[float], touched: int, limits: Thresholds) -> str | None:
    """손 반응 미달을 사람 확인으로 넘길 사유. 평소 움직임이 크고(> review_motion_limit) 평소 3쌍의 흔들림이 반응 기준보다 커서
    재는 값이 잡음보다 작고, 손이 장면 행동에는 닿았을 때만. 손이 안 닿았으면 반응 없음은 그대로 실패다."""
    jitter = max(pairs) - min(pairs)
    if baseline > limits.review_motion_limit and jitter >= limits.min_reaction and touched > 0:
        return f"손 주변 평소 변화 흔들림 {jitter:.3f}가 반응 기준 {limits.min_reaction}보다 커서 손 반응을 화면으로 가르기 어려움"
    return None


def _uv(text) -> tuple[float, float] | None:
    """UE 문자열 "(X=0.28,Y=0.66)" → (0.28, 0.66)."""
    try:
        parts = dict(item.split("=") for item in str(text).strip("()").split(","))
        return float(parts["X"]), float(parts["Y"])
    except (KeyError, ValueError):
        return None


def drop_area(scene: dict) -> Area | None:
    """그림 위 물결의 물 영역(SurfaceDeformation DropAreaMin/Max, 화면 비율). 물이 화면 일부에만 있어 손이 그 안에 와야 한다."""
    for row in scene.get("Profiles", {}).get("SurfaceDeformation", []):
        low, high = _uv(row.get("DropAreaMin")), _uv(row.get("DropAreaMax"))
        if low and high:
            return low, high
    return None


def hand_region(width: int, height: int, seconds: float, radius: float, step: int = 2,
                area: Area | None = None) -> list[int]:
    """손 궤적이 지나는 화면 영역의 픽셀(바이트 위치). 장면의 좌우 반전 여부를 모르므로 반전 위치도 포함한다."""
    centers = []
    for k in range(13):
        for u, v in trajectory(seconds * k / 12, area):
            centers += [(u * width, v * height), ((1 - u) * width, v * height)]
    r = radius * width
    picked = set()
    for cx, cy in centers:
        for y in range(max(0, int(cy - r)) // step * step, min(height, int(cy + r) + 1), step):
            half = math.sqrt(max(r * r - (y - cy) ** 2, 0))
            for x in range(max(0, int(cx - half)) // step * step, min(width, int(cx + half) + 1), step):
                picked.add((y * width + x) * 3)
    return sorted(picked)


def _spread(rgb: bytes) -> float:
    """밝기 표준편차(빈 · 검정 화면 확인용, 1/16 표본)."""
    values = [rgb[i] + rgb[i + 1] + rgb[i + 2] for i in range(0, len(rgb) - 2, 48)]
    mean = sum(values) / max(len(values), 1)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / max(len(values), 1)) / 3


def _publish_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    replace_with_retry(temporary, path)   # UE가 읽는 순간이면 Windows가 잠깐 거부한다


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def bundle_hash(folder: Path) -> dict:
    """버전 폴더 파일(하위 폴더 포함) 해시 목록과 묶음 해시(정렬된 '경로:해시' 목록의 sha256)."""
    files = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(folder.rglob("*")) if p.is_file()}
    combined = hashlib.sha256("\n".join(f"{k}:{v}" for k, v in files.items()).encode()).hexdigest()
    return {"files": files, "sha256": combined}


def gpu_snapshot() -> dict | None:
    """GPU 사용량(nvidia-smi). 없으면 None."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5, check=True)
        utilization, used, total = (float(v) for v in out.stdout.strip().splitlines()[0].split(","))
        return {"utilization_percent": utilization, "memory_used_mb": used, "memory_total_mb": total}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def mirror_safe(area: Area) -> bool:
    """좌우 반전된 범위와 가로로 겹치는지(겹치는 구간이 있어야 반전 여부와 상관없이 손이 범위 안에 온다)."""
    (u0, _), (u1, _) = area
    return max(u0, 1 - u1) < min(u1, 1 - u0)


def trajectory(elapsed: float, area: Area | None = None) -> list[list[float]]:
    """두 손이 화면 가운데 근처에서 엇갈려 움직인다(라넬 통합 회귀 시험과 같은 궤적, u 0.28~0.72 · v 0.34~0.66).
    area가 있으면 같은 모양을 그 범위 안으로 옮기고 줄인다. 좌우 반전돼도 안에 있도록 반전 범위와 겹치는 가로 구간을 쓰고,
    겹치지 않으면(범위가 한쪽에만 있음) 원래 범위를 쓴다(반전되면 손이 범위 밖 — 리포트 hand_area_mirror_safe=false)."""
    offset = 0.08 * math.sin(elapsed * 1.3)
    points = [[0.36 + offset, 0.4 + 0.06 * math.sin(elapsed)], [0.64 - offset, 0.6 - 0.06 * math.sin(elapsed)]]
    if area is None:
        return points
    (u0, v0), (u1, v1) = area
    if mirror_safe(area):
        u0, u1 = max(u0, 1 - u1), min(u1, 1 - u0)
    su, sv = min(1, (u1 - u0) / 0.44) * 0.95, min(1, (v1 - v0) / 0.32) * 0.95   # 0.95: 경계에 딱 붙지 않게
    return [[(u0 + u1) / 2 + (u - 0.5) * su, (v0 + v1) / 2 + (v - 0.5) * sv] for u, v in points]


class Hands:
    """합성 손 송신기(UDP protocol v2). 같은 세션에서 순번을 늘려 보낸다. close() 뒤에 보내면 다시 연다."""

    def __init__(self, target=UDP_TARGET):
        self.target, self.session, self.sequence, self.socket = target, str(uuid.uuid4()), 0, None

    def send(self, points):
        if self.socket is None:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sequence += 1
        frames = []
        for index, source in enumerate(SOURCES):
            point = points[index] if points else None
            samples = [] if point is None else [{
                "subject_id": f"hand:{index + 1}", "sample_id": "position_uv", "kind": "position2d",
                "semantic": "index_fingertip_position", "space": "mediapipe_image_uv", "value": point}]
            frames.append({"source_id": source, "source_session_id": self.session, "sequence": self.sequence,
                           "captured_at_seconds": time.monotonic(), "mode": "snapshot", "status": "ok",
                           "samples": samples})
        self.socket.sendto(json.dumps({"protocol_version": 2, "frames": frames}).encode(), self.target)

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None


class Runner:
    """검수 폴더의 release.json을 보는 Shipping 실행본 하나를 맡는다. 프로세스는 검증 사이에도 계속 띄워 둔다."""

    def __init__(self, executable: Path, release: Path, probe: Path, catalog: Path | None = None,
                 width: int = 1280, height: int = 720, hands: Hands | None = None,
                 launcher: list[str] | None = None, working_directory: Path | None = None, max_fps: int = 0):
        """launcher: 실행 파일 앞에 붙일 명령(시험용 가짜 실행본을 python으로 띄울 때).
        max_fps: 0이면 제한 없음(라넬 Shipping 검사 기본값). 60으로 묶으면 프레임 간격이 16.7ms 기준 근처에 몰려
        긴 프레임이 과대 집계될 수 있다."""
        self.executable, self.release, self.probe = Path(executable), Path(release), Path(probe)
        self.catalog, self.width, self.height, self.max_fps = catalog, width, height, max_fps
        self.hands = hands or Hands()
        self.launcher = launcher or []
        # Shipping 패키지 루트(<패키지>/Art/Binaries/Win64/Art-Win64-Shipping.exe의 네 단계 위)
        self.working_directory = working_directory or self.executable.parents[3]
        self.process: subprocess.Popen | None = None
        self._trace_offset, self._trace_rows = 0, []   # Trace.jsonl에서 이미 읽은 바이트 수와 이번 프로세스 행

    @property
    def status_path(self) -> Path:
        return self.release.with_suffix(".json.status.json")

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        """release.json이 이미 있어야 한다(central이 먼저 게시한다). 계측이 시작되지 않으면 프로세스를 정리하고 실패한다."""
        self.probe.mkdir(parents=True, exist_ok=True)
        commands = "t.IdleWhenNotForeground 0,Slate.bAllowThrottling 0"
        if self.max_fps:
            commands += f",t.MaxFPS {self.max_fps},r.VSync 0"
        arguments = [*self.launcher, str(self.executable), "Art", f"-ArtworkRelease={self.release}",
                     f"-ArtworkProbe={self.probe}", "-dx12", "-unattended", "-windowed", "-ForceRes",
                     f"-ResX={self.width}", f"-ResY={self.height}", "-RenderOffscreen", f"-ExecCmds={commands}"]
        options = {}
        if sys.platform == "win32":   # 창 숨김은 Windows 전용 설정. 다른 OS(Linux CI의 가짜 실행본)는 그냥 띄운다
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            options["startupinfo"] = startup
        self.process = subprocess.Popen(arguments, cwd=self.working_directory, **options)
        self._trace_rows = []   # 이전 프로세스 행이 남아 있으면 새 계측을 기다리지 않고 넘어간다
        deadline = time.monotonic() + 40
        while not self.trace():
            if not self.alive() or time.monotonic() > deadline:
                self._terminate()
                raise RuntimeError("Shipping 실행본이 계측(Trace.jsonl)을 쓰지 않음")
            time.sleep(0.2)

    def _terminate(self) -> None:
        if self.alive():
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=15)

    def stop(self) -> None:
        self._terminate()
        self.hands.close()

    def rotate_trace(self) -> None:
        """Trace.jsonl을 Trace.previous.jsonl로 바꿔 직전 것 하나만 남긴다. 실행본을 계속 띄워 두면 계측이 끝없이 쌓여
        읽기만으로 검증이 멈춘다(PC1에서 2.26GB). runtime은 기록마다 파일을 열고 닫으므로 다음 기록은 새 파일에 쓴다."""
        path = self.probe / "Trace.jsonl"
        self._trace_rows = []   # 판정은 검증마다 새로 센 구간만 쓴다
        try:
            replace_with_retry(path, self.probe / "Trace.previous.jsonl")
        except FileNotFoundError:
            pass
        except PermissionError:   # 새 Runner도 기존 대용량 계측을 다시 읽지 않는다.
            try:
                self._trace_offset = path.stat().st_size
            except FileNotFoundError:
                self._trace_offset = 0
            return
        self._trace_offset = 0

    def trace_resumes(self, seconds: float = 3.0) -> bool:
        """교체 뒤 이번 프로세스 계측이 다시 붙는지. runtime은 쓰기가 한 번 실패하면 계측 · 캡처 처리를 끝까지 멈추는데
        (ArtworkRuntimeProbeSubsystem.cpp:165 · 337), 이름 바꾸기 순간과 쓰기가 겹치면 그럴 수 있다."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.trace():
                return True
            time.sleep(0.1)
        return False

    def trace(self) -> list[dict]:
        """이번 프로세스의 계측 행. 지난번 읽은 위치 뒤에 붙은 완성된 줄만 읽는다(쓰는 중인 마지막 반 줄은 다음에)."""
        path = self.probe / "Trace.jsonl"
        for attempt in range(5):
            try:
                with path.open("rb") as file:
                    if os.fstat(file.fileno()).st_size < self._trace_offset:   # 교체됐다
                        self._trace_offset = 0
                    file.seek(self._trace_offset)
                    chunk = file.read()
                break
            except FileNotFoundError:
                self._trace_offset = 0
                return self._trace_rows
            except PermissionError:   # UE가 append 중이면 잠깐 읽히지 않는다
                if attempt == 4:
                    raise
                time.sleep(0.02)
        complete = chunk[:chunk.rfind(b"\n") + 1]
        self._trace_offset += len(complete)
        for line in complete.decode("utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if self.process is not None and row.get("ProcessId") == self.process.pid:
                self._trace_rows.append(row)
        return self._trace_rows

    def wait(self, seconds: float, points=None) -> None:
        began = time.monotonic()
        while time.monotonic() - began < seconds:
            if not self.alive():
                raise RuntimeError(f"Shipping 실행본 종료: {self.process.returncode}")
            if points is not None:
                self.hands.send(points(time.monotonic() - began) if callable(points) else points)
            time.sleep(1 / SEND_HZ)

    def capture(self, name: str, seconds: float, points=None) -> tuple[Path, bytes]:
        """새 캡처 ID로 요청한다. 6초 안에 오지 않으면 다른 ID로 한 번 더 요청한다(라넬 회귀 시험과 같은 방식)."""
        capture_id = f"{name}-{uuid.uuid4().hex[:8]}"
        target = self.probe / f"{capture_id}.png"
        _publish_json(self.probe / "Capture.json", {"CaptureId": capture_id})
        began, retried = time.monotonic(), False
        while time.monotonic() - began < seconds:
            self.wait(0.1, points)
            if not retried and time.monotonic() - began > 6 and not target.exists():
                capture_id, retried = f"{name}-{uuid.uuid4().hex[:8]}", True
                target = self.probe / f"{capture_id}.png"
                _publish_json(self.probe / "Capture.json", {"CaptureId": capture_id})
            try:
                width, height, rgb = decode_png(target.read_bytes())
            except ValueError as error:
                if "지원하지 않는" in str(error):
                    raise   # 다 쓴 파일인데 읽을 수 없는 형식이면 원인을 그대로 알린다
                continue   # 아직 쓰는 중
            except (OSError, zlib.error, struct.error, TypeError):
                continue   # 아직 쓰는 중
            if (width, height) != (self.width, self.height):
                raise ValueError(f"캡처 크기 {width}×{height}가 요청한 {self.width}×{self.height}와 다름")
            return target, rgb
        raise TimeoutError(f"화면 캡처가 오지 않음: {target}")

    def wait_applied(self, version: str, previous_request, seconds: float) -> dict:
        """이번 프로세스의 이 버전 최종 결과. previous_request가 None이면 이미 나온 결과도 받는다."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            status = _read_json(self.status_path)
            if status and status.get("ProcessId") == self.process.pid and status.get("RequestedVersion") == version \
                    and status.get("Phase") in FINAL_PHASES \
                    and (previous_request is None or status.get("RequestId") != previous_request):
                return status
            self.wait(0.1)
        raise TimeoutError(f"{seconds}초 안에 {version} 적용 결과가 오지 않음")

    def verify(self, version: str, thresholds: Thresholds | None = None, publish=None) -> Report:
        """publish(선택)는 검수 폴더에 버전을 올리는 함수. 주지 않으면 이미 올라가 있다고 보고 그 결과를 받는다."""
        limits = thresholds or Thresholds()
        report = Report(version=version, gpu_before=gpu_snapshot())
        try:
            self.rotate_trace()
            if self.alive() and not self.trace_resumes():
                self._terminate()   # 계측이 멈춘 실행본은 끄고 아래에서 새로 띄운다
                report.measurements["restarted"] = "trace_stalled"
            previous = None
            if publish is not None:
                previous = (_read_json(self.status_path) or {}).get("RequestId") if self.alive() else None
                publish()
            folder = self.release.parent / "versions" / version
            report.bundle = bundle_hash(folder)
            plan = plan_checks(limits, _read_json(folder / "scene.json"))
            if self.catalog is not None:
                report.catalog_version = catalog_version(self.catalog)
            if not self.alive():
                self.start()
            status = self.wait_applied(version, previous, limits.apply_seconds)
            self._judge(report, status, limits, plan)
        except Exception as error:  # noqa: BLE001 — 검증 실패는 보고서로 돌려준다
            report.error = f"{type(error).__name__}: {error}"
            report.add("검증 진행", False, report.error)
        report.passed = bool(report.checks) and all(c.passed for c in report.checks)
        return report

    def _judge(self, report: Report, status: dict, limits: Thresholds, plan: Plan) -> None:
        version = report.version
        report.measurements["status"] = {k: status.get(k) for k in (
            "RuntimeSessionId", "RequestId", "Phase", "Applied", "ActiveReleaseVersion", "ActiveSceneId", "Error",
            "FailedStage", "PreparedBytes",
            "PrepareSeconds")}
        applied = status.get("Applied") is True and status.get("ActiveReleaseVersion") == version \
            and not status.get("Error")
        report.add("적용(Applied)", applied, status.get("Error") or status.get("Phase"))
        if not applied:
            return
        report.add("준비 시간", (status.get("PrepareSeconds") or 0) <= limits.max_prepare_seconds,
                   status.get("PrepareSeconds"), limits.max_prepare_seconds)
        prepared_mb = (status.get("PreparedBytes") or 0) / 2**20
        report.add("준비 메모리(MB)", prepared_mb <= limits.max_prepared_mb, round(prepared_mb, 1), limits.max_prepared_mb)
        self.wait(limits.settle_seconds, [None, None])
        rest_path, rest = self.capture(f"{version}_rest", limits.capture_seconds, [None, None])
        report.add("빈 화면 아님", _spread(rest) > 0.5, round(_spread(rest), 2), 0.5)
        # 입력 없이 input_seconds 간격으로 찍은 화면들(평소 변화 기준). 잔상을 볼 때는 1장 더 찍어 rest를 뺀 긴 간격도 잰다.
        quiet, idle_paths, quiet_at, extra = [rest], {}, [time.monotonic()], {}
        for index in range(limits.idle_pairs + plan.afterimage):
            self.wait(limits.input_seconds, [None, None])
            idle_path, idle = self.capture(f"{version}_idle{index}", limits.capture_seconds, [None, None])
            quiet.append(idle)
            quiet_at.append(time.monotonic())
            (idle_paths if index < limits.idle_pairs else extra)[f"idle{index}"] = str(idle_path)
        first = len(self.trace())
        began = time.monotonic()
        self.wait(limits.input_seconds, lambda elapsed: trajectory(elapsed, plan.area))
        window, seconds = self.trace()[first:], time.monotonic() - began   # 캡처 프레임이 섞이기 전에 구간을 끊는다
        active_path, active = self.capture(f"{version}_hands", limits.capture_seconds,
                                           trajectory(limits.input_seconds, plan.area))
        released = time.monotonic()
        lingering = after = settled = None
        if plan.afterimage:
            self.wait(max(released + limits.afterimage_seconds - time.monotonic(), 0))
            lingering_path, lingering = self.capture(f"{version}_afterimage", limits.capture_seconds)
            lingering_at = time.monotonic()
            extra["afterimage"] = str(lingering_path)
        if plan.recovery is not None or not plan.afterimage:
            self.wait(max(released + plan.release_seconds - time.monotonic(), 0))
            after_path, after = self.capture(f"{version}_after", limits.capture_seconds)
        if plan.recovery == "fade":
            self.wait(limits.input_seconds)
            settled_path, settled = self.capture(f"{version}_settled", limits.capture_seconds)
            extra["settled"] = str(settled_path)
        # 키트가 순서대로 captures/NNN.png로 복사하므로 기존 라벨 순서를 지키고 새 라벨은 맨 뒤에 둔다
        report.screenshots = {"rest": str(rest_path), **idle_paths, "idle": str(idle_path), "hands": str(active_path),
                              **({"after": str(after_path)} if after is not None else {}), **extra}
        # 손 반응은 화면 일부에서 일어나므로 손 궤적 주변만 비교하고, 같은 간격의 평소 변화(배경 흐름)를 빼서 본다.
        region = hand_region(self.width, self.height, limits.input_seconds, limits.hand_radius, area=plan.area)
        report.measurements.update(hand_area=plan.area, input_seconds=limits.input_seconds,
                                   hand_radius=limits.hand_radius,
                                   hand_area_mirror_safe=None if plan.area is None else mirror_safe(plan.area))
        if plan.area is not None and not mirror_safe(plan.area):
            report.needs_review = True
            report.review_reasons.append(f"물결 범위 {plan.area}가 한쪽에만 있어 화면이 좌우 반전되면 합성 손이 범위 밖")
        # 잔상 검사 때 더 찍은 평소 화면은 긴 간격에만 쓴다(쌍이 늘면 기준선이 올라 반응 판정이 달라진다)
        pairs = [changed_ratio(a, b, limits.pixel_delta, pixels=region)
                 for a, b in pairwise(quiet[:limits.idle_pairs + 1])]
        baseline = max(pairs)   # 배경 흐름은 쌍마다 달라 가장 큰 평소 변화를 뺀다(이미 보수적이라 편차는 더 요구하지 않음)
        during = changed_ratio(idle, active, limits.pixel_delta, pixels=region)
        back = None if after is None else changed_ratio(idle, after, limits.pixel_delta, pixels=region)
        report.measurements.update(changed_idle=baseline, changed_idle_pairs=pairs,
                                   changed_idle_jitter=baseline - min(pairs), changed_during=during,
                                   changed_after=back, region_pixels=len(region))
        # 손 영향 경계의 번짐을 대조 영역으로 오인하지 않도록 더 넓은 궤적 영역을 제외한다.
        excluded = set(hand_region(self.width, self.height, limits.input_seconds, limits.hand_radius * 1.75,
                                   area=plan.area))
        control = [3 * (y * self.width + x) for y in range(0, self.height, 2)
                   for x in range(0, self.width, 2) if 3 * (y * self.width + x) not in excluded]
        # quiet의 마지막 화면이 실제 입력 직전 프레임이어야 한다(잔상용 추가 프레임 포함).
        report.measurements["interaction_isolation"] = interaction_isolation(quiet, active, region, control,
                                                                            limits.min_reaction)
        half_life = plan.half_life if plan.half_life is None or math.isfinite(plan.half_life) else "inf"
        report.measurements.update(persistence_half_life=half_life, persistence_medium=plan.medium,
                                   recovery_check=plan.recovery,
                                   release_seconds=None if after is None else plan.release_seconds)
        if plan.afterimage:
            # rest는 적용 직후 시작 변화가 섞이므로 빼고 idle0 ~ 마지막 평소 화면의 모든 쌍 중 가장 큰 변화를 쓴다.
            # 한 쌍만 보면 주기적으로 흐르는 배경이 두 시점에 우연히 손 주변을 비켜 가 0으로 잡힐 수 있다.
            long = max(changed_ratio(a, b, limits.pixel_delta, pixels=region) for a, b in combinations(quiet[1:], 2))
            lingered = changed_ratio(idle, lingering, limits.pixel_delta, pixels=region)
            long_seconds, lingering_seconds = quiet_at[-1] - quiet_at[1], lingering_at - quiet_at[-1]
            report.measurements.update(changed_idle_long=long, idle_long_seconds=round(long_seconds, 2),
                                       changed_afterimage=lingered, afterimage_gap_seconds=round(lingering_seconds, 2))
        contacts = max((sum(1 for c in row.get("Contacts", []) if c.get("SourceId") in SOURCES) for row in window),
                       default=0)
        touched = max((i.get("ActiveContacts", 0) for row in window for h in row.get("Hosts", [])
                       for i in h.get("BehaviorInputs", [])), default=0)
        report.measurements.update(max_contacts=contacts, max_behavior_contacts=touched)
        report.add("두 손 수신", contacts == len(SOURCES), contacts, len(SOURCES))
        report.add("손이 장면 행동에 닿음", touched > 0, touched, 1)
        judged = baseline <= limits.screen_judge_limit
        report.measurements["screen_judged"] = judged
        if not judged:
            report.needs_review = True
            report.review_reasons.append(f"손 주변이 평소에도 {baseline:.0%} 바뀌어 화면으로 반응을 가르지 못함")
        elif baseline > limits.review_motion_limit:
            report.needs_review = True
            report.review_reasons.append(f"손 주변 평소 변화 {baseline:.0%}가 커서 화면 판정을 믿기 어려움")
        if judged:   # 손 주변이 평소에도 절반 넘게 바뀌면 화면으로는 가를 수 없어 행동 입력(위)으로만 본다
            doubt = reaction_doubt(baseline, pairs, touched, limits)
            self._soft(report, "입력 중 화면 반응(손 주변, 평소 변화 차감)", during - baseline >= limits.min_reaction,
                       round(during - baseline, 5), limits.min_reaction, [doubt] if doubt else [])
            mismatch = [] if plan.screen else [f"{plan.medium}는 화면 잔상이 반감기와 맞지 않음(PC1 기준 보정 전)"]
            if mismatch and (plan.afterimage or plan.recovery == "fade"):   # 통과해도 자동 통과로 두지 않는다
                report.needs_review = True
                report.review_reasons.append(f"잔상 · 복귀는 사람 확인: {mismatch[0]}")
            if plan.afterimage:
                doubts = list(mismatch)
                if long > limits.review_motion_limit:
                    doubts.append(f"손 주변 긴 평소 변화 {long:.0%}가 커서 잔상 판정을 믿기 어려움")
                if lingering_seconds > long_seconds:
                    doubts.append(f"잔상 비교 간격 {lingering_seconds:.1f}초가 평소 간격 {long_seconds:.1f}초보다 김(캡처 지연)")
                self._soft(report, f"손 뗀 뒤 {limits.afterimage_seconds:g}초 잔상(손 주변, 긴 평소 변화 차감)",
                           lingered - long >= limits.min_afterimage, round(lingered - long, 5), limits.min_afterimage,
                           doubts)
            if plan.recovery == "settle":
                # 입력 뒤 화면은 평소 변화를 잰 간격보다 오래 뒤에 찍혀 배경 흐름이 최대 두 배까지 쌓인다.
                allowed = baseline * 2 + limits.recovery_slack
                report.add("입력 뒤 복귀(손 주변)", back <= allowed, round(back, 5), round(allowed, 5))
            elif plan.recovery == "fade":
                # 평소 화면과는 20초 넘게 떨어져 직접 비교하지 않는다. 같은 간격끼리(복귀 → 3.2초 뒤) 가라앉았는지 보고,
                # 손 화면에서 시간이 갈수록 멀어지는지(잔상이 옅어짐)는 평균 밝기 차이로 본다.
                calm = changed_ratio(after, settled, limits.pixel_delta, pixels=region)
                allowed = baseline * 2 + limits.recovery_slack
                faded = (mean_difference(active, after, region) - mean_difference(active, lingering, region)
                         if lingering is not None else None)
                # 긴 평소 변화와 같은 이유로 idle0 ~ 마지막 평소 화면의 모든 쌍 중 가장 큰 차이를 뺀다
                drift = (max(mean_difference(a, b, region) for a, b in combinations(quiet[1:], 2))
                         if lingering is not None else 0)
                # 진한 잔상은 복귀 화면에서도 아직 옅어지는 중이라 같은 간격 변화가 크다(PC1 fluid 20초 뒤 0.32).
                # 잔상 → 복귀 → 3.2초 뒤로 갈수록 평소 화면에 단계마다 가까워지면 가라앉는 중으로 본다(깜빡이면 어느 단계에서 멀어짐).
                # 세 시점이 다 있어야 보고(두 시점은 깜빡임도 절반은 줄어드는 순서로 찍힌다), 끝이 처음의 settle_ratio 이하여야
                # 거의 안 옅어지는 잔상을 거른다.
                gaps = [mean_difference(idle, frame, region) for frame in (lingering, after, settled) if frame is not None]
                approaching = lingering is not None and all(a - b > drift for a, b in pairwise(gaps)) \
                    and gaps[0] - gaps[-1] - drift >= limits.min_shrink and gaps[-1] <= gaps[0] * limits.settle_ratio
                report.measurements.update(changed_settle=calm, faded=faded, idle_long_mean=drift, settle_gaps=gaps,
                                           settle_approaching=approaching)
                doubts = list(mismatch)
                if plan.afterimage and long > limits.review_motion_limit:
                    doubts.append(f"손 주변 긴 평소 변화 {long:.0%}가 커서 복귀 판정을 믿기 어려움")
                self._soft(report, "입력 뒤 가라앉음(손 주변, 같은 간격 평소 변화 기준)", calm <= allowed or approaching,
                           round(calm, 5), round(allowed, 5), doubts)
                if faded is not None:   # 픽셀 차이로 옅어짐을 재는 기준은 PC1에서 보정하기 전까지 사람 확인으로만 둔다
                    self._soft(report, "잔상이 옅어짐(손 주변 평균 밝기, 긴 평소 차이 차감)",
                               faded - drift >= limits.min_shrink, round(faded - drift, 5), limits.min_shrink,
                               doubts + ["옅어짐 기준은 PC1 보정 전"])
        long_frames = sum(len(row.get("LongFrames", [])) + row.get("DroppedLongFrames", 0) for row in window)
        per_second = long_frames / max(seconds, 1e-6)
        report.measurements.update(long_frames=long_frames, long_frames_per_second=round(per_second, 2))
        report.add("긴 프레임(16.7ms 초과, 초당)", per_second <= limits.max_long_frames_per_second,
                   round(per_second, 2), limits.max_long_frames_per_second)
        if per_second > limits.max_long_frames_per_second and (report.gpu_before or {}).get("utilization_percent", 0) > 30:
            report.environment_suspect = True   # 측정 전부터 GPU가 바빴다
        report.add("실행본 유지", self.alive())

    @staticmethod
    def _soft(report: Report, name: str, passed: bool, value, limit, doubts: list[str]) -> None:
        """미달이어도 화면 판정을 믿기 어려운 사유가 있으면 실패 대신 사람 확인(needs_review)으로 넘긴다."""
        if passed or not doubts:
            report.add(name, passed, value, limit)
            return
        report.needs_review = True
        report.review_reasons.append(f"{name} 기준 밖(값 {value}, 기준 {limit}): {'; '.join(doubts)}")
