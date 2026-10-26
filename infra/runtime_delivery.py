"""PC1 검증·PC2 게시 공통: 해시 검증한 불변 버전 준비와 원자 포인터 교체."""
import hashlib
import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from client import atomic_json, digest


def source_bundle(job):
    source_id = job["payload"]["runtime"]["source_job_id"]
    source = next(p for p in job["inputs"] if p["id"] == source_id)
    if source["kind"] != "ue_assemble" or source["status"] != "succeeded":
        raise ValueError("조립 성공 결과가 필요합니다")
    files = {f["name"]: f["sha256"] for f in source["result"]["files"]}
    combined = hashlib.sha256("\n".join(f"{k}:{files[k]}" for k in sorted(files)).encode()).hexdigest()
    return source, combined


def prepare(job, downloaded, delivery):
    source, sha = source_bundle(job)
    version = "ue-" + source["id"]
    refs = source["result"]["files"]
    if "scene.json" not in {r["name"] for r in refs}:
        raise ValueError("scene.json이 없습니다")
    target = delivery / "versions" / version
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        actual = {p.relative_to(target).as_posix(): digest(p) for p in target.rglob("*") if p.is_file()}
        if actual != {r["name"]: (r["sha256"], r["size"]) for r in refs}:
            raise ValueError("기존 불변 버전과 입력이 다릅니다")
        return version, sha
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".prepare-") as temporary:
        stage = Path(temporary) / "version"
        stage.mkdir()
        for ref in refs:
            relative = Path(ref["name"])
            source_path = (downloaded / source["id"] / relative).resolve()
            destination = (stage / relative).resolve()
            if not source_path.is_relative_to(downloaded.resolve()) or not destination.is_relative_to(stage.resolve()):
                raise ValueError("번들 경로 이탈")
            if digest(source_path) != (ref["sha256"], ref["size"]):
                raise ValueError("게시 전 무결성 불일치")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
        # 동일 파일 시스템의 디렉터리 교체. release.json은 아직 바꾸지 않는다.
        os.replace(stage, target)
    return version, sha


def publish(delivery, version, lost):
    if lost.is_set():
        raise RuntimeError("lease_lost")
    for attempt in range(20):
        try:
            atomic_json(delivery / "release.json", {"SchemaVersion": 1, "Version": version})
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.1)


@contextmanager
def singleton(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def observe(delivery, pid, desired=None):
    """기존 show_agent.observe 규칙과 동일한 PID·프로세스 생성 시각·세션 검사."""
    from datetime import datetime

    import psutil

    unknown = {"phase": "Unknown", "desired_version": desired, "active_version": None,
               "runtime_session": None, "runtime_pid": None, "error": "runtime_status_unconfirmed"}
    try:
        data = json.loads((delivery / "release.json.status.json").read_text("utf-8-sig"))
        process = psutil.Process(pid)
        updated = datetime.fromisoformat(data["UpdatedAtUtc"]).timestamp()
        if (data["ProcessId"] != pid or not process.is_running() or updated < process.create_time()
                or not data["RuntimeSessionId"] or not data.get("RequestId")):
            return unknown
        phase = data["Phase"]
        if phase not in ("Ready", "Applied", "Failed"):
            phase = "Staged"
        active = data.get("ActiveReleaseVersion") or None
        if desired and data.get("RequestedVersion") != desired:
            phase = "Unknown"
        if phase in ("Ready", "Applied") and not data.get("RequestedVersion"):
            phase = "Unknown"
        if phase == "Ready" and data.get("Applied"):
            phase = "Unknown"
        if phase == "Applied" and (data.get("Applied") is not True or active != data.get("RequestedVersion")
                                   or data.get("Error")):
            phase = "Unknown"
        return {"phase": phase, "desired_version": desired, "active_version": active,
                "runtime_session": data["RuntimeSessionId"], "runtime_pid": pid,
                "error": "runtime_failed" if phase == "Failed" else ""}
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        return unknown
