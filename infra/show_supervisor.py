"""PC2 전용: 로컬 Shipping 한 개의 소유권·실행·Applied 기반 복구 관리."""
import ctypes
import json
import os
import re
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import psutil
from client import atomic_json, digest
from runtime_delivery import observe, publish, singleton


@contextmanager
def machine_lock():
    """다른 폴더의 자동 실행기도 같은 Windows 호스트에서 동시에 실행하지 않는다."""
    if os.name != "nt":
        raise RuntimeError("automatic_show_requires_windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, "Global\\RanelShowRuntime-v1")
    error = ctypes.get_last_error()
    if not handle or error == 183:  # ERROR_ALREADY_EXISTS
        if handle:
            kernel.CloseHandle(handle)
        raise RuntimeError("another_show_supervisor_is_running")
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


def checked_manifest(delivery, record):
    version = record["version"]
    if not isinstance(version, str) or not re.fullmatch(r"ue-[A-Za-z0-9_-]+", version):
        raise ValueError("invalid_recovery_version")
    root = (delivery / "versions" / version).resolve(strict=True)
    if not root.is_relative_to((delivery / "versions").resolve()):
        raise ValueError("recovery_path_escape")
    expected = {}
    for ref in record["files"]:
        name = ref["name"]
        relative = Path(name)
        path = (root / relative).resolve(strict=True)
        if (relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(root)
                or name in expected or path.is_symlink()):
            raise ValueError("invalid_recovery_file")
        expected[name] = (ref["sha256"], ref["size"])
        if digest(path) != expected[name]:
            raise ValueError("recovery_integrity_failed")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if "scene.json" not in expected or actual != set(expected):
        raise ValueError("incomplete_recovery_bundle")
    return record


class Supervisor:
    def __init__(self, folder, config):
        self.folder, self.config = Path(folder).resolve(), config
        self.delivery = Path(config["delivery"]).resolve()
        self.executable = Path(config["executable"]).resolve(strict=True)
        self.cwd = Path(config["working_directory"]).resolve(strict=True)
        self.arguments = config.get("launch_arguments", ["Art", "-dx12", "-unattended", "-windowed",
                                                         "-ResX=1920", "-ResY=1080"])
        if not self.executable.is_file() or not self.cwd.is_dir():
            raise ValueError("invalid_shipping_path")
        if not isinstance(self.arguments, list) or not all(isinstance(x, str) for x in self.arguments):
            raise ValueError("invalid_launch_arguments")
        if any(x.lower().startswith(("-artworkrelease", "-ranelsupervisor")) for x in self.arguments):
            raise ValueError("supervisor_arguments_are_reserved")
        self.initial = float(config.get("restart_initial_seconds", 2))
        self.maximum = float(config.get("restart_max_seconds", 60))
        self.timeout = float(config.get("startup_timeout_seconds", 120))
        if not (0 < self.initial <= self.maximum <= 600 and 0 < self.timeout <= 600):
            raise ValueError("invalid_supervisor_timeout")
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.owner = None
        self.pending = None
        self.ready = False
        self.failures = 0
        self.next_start = 0
        self.deadline = 0
        self.started = 0
        self.last_error = ""
        self.supervisor_created = psutil.Process().create_time()
        path = self.folder / "show-last-good.json"
        self.good = checked_manifest(self.delivery, json.loads(
            (path if path.exists() else self.folder / "bootstrap-manifest.json").read_text("utf-8-sig")))
        if path.exists():
            status = self.good.get("status", {})
            if (status.get("phase") != "Applied" or status.get("active_version") != self.good["version"]
                    or not status.get("runtime_session") or not status.get("runtime_pid")):
                raise ValueError("last_good_requires_applied_evidence")
        if not path.exists() and self.good.get("previous_pc_applied_verified") is not True:
            raise ValueError("bootstrap_requires_historical_applied")

    def validate_windows(self):
        if os.name != "nt" or not self.executable.name.lower().endswith("-win64-shipping.exe"):
            raise ValueError("use_direct_windows_shipping_executable")

    @property
    def owner_path(self):
        return self.folder / "runtime-process.json"

    def owned_process(self, owner=None):
        row = owner or self.owner
        if not row:
            return None
        try:
            process = psutil.Process(row["pid"])
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return None
            command = process.cmdline()
            if (abs(process.create_time() - row["create_time"]) > 0.01
                    or Path(process.exe()).resolve() != self.executable
                    or row["executable"] != str(self.executable)
                    or row["release"] != str(self.delivery / "release.json")
                    or f"-RanelSupervisor={row['launch_id']}" not in command
                    or f"-ArtworkRelease={row['release']}" not in command):
                raise RuntimeError("runtime_ownership_mismatch")
            return process
        except psutil.NoSuchProcess:
            return None

    def foreign_processes(self):
        own = self.owner["pid"] if self.owned_process() else None
        found = []
        for proc in psutil.process_iter(["pid", "name"]):
            name = (proc.info["name"] or "").lower()
            if (proc.pid != own and not name.startswith("crashreportclient")
                    and (name == self.executable.name.lower() or name.endswith("-win64-shipping.exe")
                    or name in ("art.exe", "unrealeditor.exe", "ue4editor.exe"))):
                found.append(proc.pid)
        return found

    def _state(self, phase, error=""):
        self.last_error = error
        atomic_json(self.folder / "supervisor-state.json", {
            "phase": phase, "error": error, "runtime_pid": self.owner["pid"] if self.owner else None,
            "last_good_version": self.good["version"], "restart_count": self.failures,
            "supervisor_pid": os.getpid(), "supervisor_create_time": self.supervisor_created,
            "updated_at": time.time(),
        })

    def _restore(self):
        checked_manifest(self.delivery, self.good)
        publish(self.delivery, self.good["version"], threading.Event())

    def _spawn(self):
        self._restore()
        if self.foreign_processes():
            raise RuntimeError("foreign_ue_running")
        launch_id = uuid.uuid4().hex
        arguments = [str(self.executable), *self.arguments, f"-ArtworkRelease={self.delivery / 'release.json'}",
                     f"-RanelSupervisor={launch_id}"]
        logs = self.folder / "logs"
        logs.mkdir(exist_ok=True)
        log = logs / "ue-runtime.log"
        if log.exists() and log.stat().st_size > 10 * 1024 * 1024:
            os.replace(log, logs / "ue-runtime.previous.log")
        with log.open("ab") as output:
            process = subprocess.Popen(arguments, cwd=self.cwd, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        try:
            created = psutil.Process(process.pid).create_time()
        except psutil.NoSuchProcess:
            process.wait(timeout=5)
            raise RuntimeError("runtime_exited_during_start") from None
        self.owner = {"pid": process.pid, "create_time": created, "executable": str(self.executable),
                      "release": str(self.delivery / "release.json"), "launch_id": launch_id}
        # 기록 실패 시에도 메모리의 소유권으로 추적한다. 다음 tick에서 중복 실행하지 않는다.
        atomic_json(self.owner_path, self.owner)
        self.ready = False
        self.started = time.monotonic()
        self.deadline = self.started + self.timeout
        self._state("Starting")

    def _stop_owned(self):
        process = self.owned_process()
        if process:
            process.terminate()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                # 생성 시각·실행 식별자를 다시 확인한 자신 소유 프로세스만 강제 종료한다.
                self.owned_process().kill()
                process.wait(timeout=5)
        self.owner = None
        self.owner_path.unlink(missing_ok=True)
        self.ready = False

    def stop_owned(self):
        with self.lock:
            self._adopt()
            self._stop_owned()
            self._state("Stopped")

    def _adopt(self):
        if self.owner_path.exists():
            row = json.loads(self.owner_path.read_text("utf-8"))
            process = self.owned_process(row)
            if process:
                self.owner = row
                self._restore()
                self.started = time.monotonic()
                self.deadline = self.started + self.timeout
            else:
                self.owner_path.unlink(missing_ok=True)

    def start(self):
        with self.lock:
            self._adopt()
        self.thread = threading.Thread(target=self._watch, name="show-supervisor", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            # 대용량 복구 파일 검사 중에도 감시 스레드 종료 전에 호스트 잠금을 풀지 않는다.
            self.thread.join()
            with self.lock:
                self._state("Paused")
        # UE는 유지한다. 다음 Agent가 동일 소유권 기록을 검증해 인계한다.

    def _reject_pending(self, reason):
        if self.pending:
            self.pending["error"] = reason
            self.pending["event"].set()
            self.pending = None

    def _recover(self, reason, *, restart):
        self._reject_pending(reason)
        self.ready = False
        self._restore()
        if restart:
            self._stop_owned()
            self.failures += 1
            self.next_start = time.monotonic() + min(self.maximum, self.initial * 2 ** min(self.failures - 1, 16))
        else:
            self.deadline = time.monotonic() + self.timeout
        self._state("Recovering", reason)

    def activate(self, version, record, lost):
        with self.lock:
            if lost.is_set():
                raise RuntimeError("lease_lost")
            if not self.ready or not self.owned_process() or self.pending or self.foreign_processes():
                raise RuntimeError("runtime_not_ready")
            if version != record["version"]:
                raise ValueError("candidate_version_mismatch")
            checked_manifest(self.delivery, record)
            ticket = {"event": threading.Event(), "error": "", "record": record, "status": None, "lost": lost,
                      "deadline": time.monotonic() + self.config.get("apply_timeout_seconds", 120)}
            publish(self.delivery, version, lost)
            self.pending, self.ready = ticket, False
            self._state("Applying")
            return ticket

    def abort(self, ticket):
        with self.lock:
            if self.pending is ticket:
                self._recover("publication_interrupted", restart=False)

    def snapshot(self):
        with self.lock:
            desired = self.pending["record"]["version"] if self.pending else self.good["version"]
            pid = self.owner["pid"] if self.owned_process() else None
            return observe(self.delivery, pid, desired) if pid else {
                "phase": "Unknown", "desired_version": desired, "active_version": None,
                "runtime_session": None, "runtime_pid": None, "error": "runtime_status_unconfirmed"}

    def _tick(self):
        with self.lock:
            if self.foreign_processes():
                self._reject_pending("foreign_ue_running")
                # 수동으로 켠 UE는 건드리지 않는다. 우리 UE는 종료하여 추가 중복을 줄인다.
                self._stop_owned()
                self._state("Blocked", "foreign_ue_running")
                return
            process = self.owned_process()
            if not process and self.owner:
                self._recover("runtime_exited", restart=True)
                return
            if not process:
                if time.monotonic() >= self.next_start:
                    self._spawn()
                return
            if self.pending and self.pending["lost"].is_set():
                self._recover("lease_lost", restart=False)
                return
            status = self.snapshot()
            if status["phase"] == "Applied":
                if self.pending:
                    ticket = self.pending
                    record = {**ticket["record"], "status": status, "new_pc_applied_verified": True}
                    checked_manifest(self.delivery, record)
                    atomic_json(self.folder / "show-last-good.json", record)
                    self.good = record
                    ticket["status"] = status
                    self.pending = None
                    ticket["event"].set()
                elif self.good.get("status") != status:
                    self.good = {**self.good, "status": status, "new_pc_applied_verified": True}
                    atomic_json(self.folder / "show-last-good.json", self.good)
                if time.monotonic() - self.started >= 30:
                    self.failures = 0
                self._state("Running")
                self.ready = True
            elif status["phase"] == "Failed":
                self._recover("runtime_failed", restart=not bool(self.pending))
            elif time.monotonic() >= (self.pending["deadline"] if self.pending else self.deadline) and not self.ready:
                self._recover("runtime_apply_timeout", restart=True)

    def _watch(self):
        while not self.stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 — 원문·설정·토큰을 로그에 출력하지 않는다.
                with self.lock:
                    self.ready = False
                    self._reject_pending("supervisor_blocked")
                    self._state("Blocked", type(exc).__name__)
                    self.next_start = time.monotonic() + self.maximum
            self.stop_event.wait(0.5)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--stop", action="store_true", required=True)
    args = parser.parse_args()
    config = json.loads((args.folder / "runtime.local.json").read_text("utf-8-sig"))
    if config.get("auto_launch") is not True or config["mode"] != "show":
        raise ValueError("automatic_show_configuration_required")
    with machine_lock(), singleton(args.folder / "runtime-worker.lock"):
        manager = Supervisor(args.folder, config)
        manager.validate_windows()
        manager.stop_owned()
    print('{"event":"owned_runtime_stopped"}', flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("show_supervisor_failed: " + type(exc).__name__) from None
