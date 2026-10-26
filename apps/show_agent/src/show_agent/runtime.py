"""라넬 UE의 실제 status 파일을 확인한다. 파일 복사 성공을 Applied로 추측하지 않는다."""
import json
from datetime import datetime
from pathlib import Path

import psutil

from contracts.infra import Bundle, ShowReport
from studio.infra.storage import FileStore, atomic_json, release_version, stage_bundle


def observe(status_path: Path, expected_pid: int, desired: str | None = None, process_factory=psutil.Process) -> ShowReport:
    try:
        data = json.loads(status_path.read_text("utf-8"))
        process = process_factory(expected_pid)
        updated = datetime.fromisoformat(data["UpdatedAtUtc"]).timestamp()
        if (data["ProcessId"] != expected_pid or not process.is_running() or
                updated < process.create_time() or not data["RuntimeSessionId"]):
            raise ValueError("stale_runtime")
        phase = data["Phase"]
        active = data.get("ActiveReleaseVersion") or None
        if phase not in ("Ready", "Applied", "Failed"):
            phase = "Staged"
        if desired and data.get("RequestedVersion") != desired:
            phase = "Unknown"  # 실제 활성 버전은 보고하되 새 요청 완료로 처리하지 않는다.
        if phase in ("Ready", "Applied") and not data.get("RequestedVersion"):
            phase = "Unknown"
        if phase == "Ready" and data.get("Applied"):
            phase = "Unknown"  # 서로 모순되는 보고를 준비 완료로 승격하지 않는다.
        if phase == "Applied" and (not data.get("Applied") or active != data.get("RequestedVersion")):
            phase = "Unknown"
        return ShowReport(phase=phase, desired_version=desired, active_version=active,
                          runtime_session=data["RuntimeSessionId"], runtime_pid=expected_pid,
                          error="runtime_failed" if phase == "Failed" else "")
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        return ShowReport(phase="Unknown", desired_version=desired, error="runtime_status_unconfirmed")


def prepare(store: FileStore, cache: Path, bundle: Bundle, catalog: str) -> Path:
    if bundle.catalog_version != catalog:
        raise ValueError("기능 카탈로그 버전이 다릅니다")
    if bundle.kind != "runtime_data":
        raise ValueError("사전 쿠킹 콘텐츠 마운트 방식은 아직 구현하지 않았습니다")
    if "scene.json" not in {ref.name for ref in bundle.files}:
        raise ValueError("scene.json이 필요합니다")
    return stage_bundle(store, cache, bundle)


def activate(cache: Path, bundle: Bundle):
    """명시적 활성화 요청. 실행 중 UE의 watcher가 준비 후 자동 commit한다."""
    target = cache / "versions" / release_version(bundle)
    if json.loads((target / "bundle.json").read_text("utf-8")) != bundle.model_dump(mode="json"):
        raise ValueError("준비한 번들이 다릅니다")
    atomic_json(cache / "release.json", {"SchemaVersion": 1, "Version": release_version(bundle)})
