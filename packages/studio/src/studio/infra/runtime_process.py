"""Opt-in Shipping process supervisor. Also copied unchanged into standalone HTTP kits.

Only local configuration supplies executable/arguments. An agent exit leaves UE running;
an explicit persisted stop request stops only the identified process. No UE API is invented.
"""
import ctypes
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import psutil


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".runtime-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(20):
            try:
                os.replace(name, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(.05)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path, default=None):
    if not path.exists():
        return default
    value = json.loads(path.read_text("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("runtime state must be an object")  # noqa: TRY004 — malformed JSON content
    return value


@contextmanager
def process_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def canonical(path):
    return os.path.normcase(str(Path(path).resolve()))


@contextmanager
def show_host_lock():
    """Use the same Windows host mutex as infra/show_supervisor.py, across both managers."""
    if os.name != "nt":
        yield  # Portable process tests retain the executable lock below.
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, "Global\\RanelShowRuntime-v1")
    error = ctypes.get_last_error()
    if not handle or error == 183:
        if handle:
            kernel.CloseHandle(handle)
        raise RuntimeError("another_show_supervisor_is_running")
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


@dataclass(frozen=True)
class RuntimeConfig:
    executable: Path
    working_directory: Path
    arguments: tuple[str, ...] = ()
    autostart: bool = True
    restart_limit: int = 3
    restart_delay_seconds: float = 3
    stable_seconds: float = 60
    stop_timeout_seconds: float = 5

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict):
            raise TypeError("managed_runtime must be an object")
        allowed = set(cls.__dataclass_fields__)
        if set(data) - allowed:
            raise ValueError("unknown managed_runtime setting")
        result = cls(**{**data, "executable": Path(data["executable"]),
                        "working_directory": Path(data["working_directory"]),
                        "arguments": tuple(data.get("arguments", ()))})
        if (not result.executable.is_absolute() or not result.executable.is_file()
                or not result.working_directory.is_absolute() or not result.working_directory.is_dir()):
            raise ValueError("absolute executable and working_directory are required")
        if not isinstance(data.get("arguments", []), list) or any(
            not isinstance(arg, str) or "\0" in arg or arg.lower().startswith(("-artworkrelease", "-artworkscene"))
            for arg in result.arguments
        ):
            raise ValueError("arguments must not override the artwork source")
        if type(result.autostart) is not bool or type(result.restart_limit) is not int:
            raise ValueError("invalid autostart or restart_limit")
        if not 0 <= result.restart_limit <= 10:
            raise ValueError("restart_limit must be between 0 and 10")
        for key in ("restart_delay_seconds", "stable_seconds", "stop_timeout_seconds"):
            value = getattr(result, key)
            if type(value) not in (int, float) or not math.isfinite(value) or not .1 <= value <= 300:
                raise ValueError("invalid process timeout")
        if result.stop_timeout_seconds > 10:
            raise ValueError("stop_timeout_seconds must not exceed 10")
        return result


class ProcessBackend:
    def process(self, pid):
        return psutil.Process(pid)

    def candidates(self, executable):
        found = []
        for process in psutil.process_iter(["pid", "name", "exe", "cmdline", "create_time"], ad_value=None):
            info = process.info
            if info["exe"] and canonical(info["exe"]) == canonical(executable):
                found.append(process)
            elif not info["exe"] and (info["name"] or "").lower() == executable.name.lower():
                raise RuntimeError("matching_process_access_denied")
        return found

    def launch(self, config, release, log_path):
        with log_path.open("ab") as stream:
            child = subprocess.Popen([str(config.executable), *config.arguments, f"-ArtworkRelease={release}"],
                                     cwd=config.working_directory, stdin=subprocess.DEVNULL,
                                     stdout=stream, stderr=stream, shell=False)
        return child

    def close(self, process, timeout):
        """Ask UE's window to close, then use bounded termination if it does not exit."""
        if os.name == "nt":
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]

            @callback_type
            def close_window(window, parameter):
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
                if owner.value == process.pid:
                    user32.PostMessageW(window, 0x0010, 0, 0)  # WM_CLOSE
                return True

            user32.EnumWindows(close_window, 0)
        else:
            process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout)
            return False
        except psutil.TimeoutExpired:
            process.terminate()
        try:
            process.wait(timeout)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout)
        return True


class RuntimeSupervisor:
    def __init__(self, config, release, *, backend=None, clock=time.time, lock_directory=None):
        self.config = config
        self.release = Path(release).resolve()
        if not Path(release).is_absolute():
            raise ValueError("release path must be absolute")
        self.directory = self.release.parent / ".runtime-process"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.backend, self.clock = backend or ProcessBackend(), clock
        self.identity = hashlib.sha256(json.dumps([canonical(config.executable), canonical(config.working_directory),
                                                 str(self.release),
                                                 config.arguments]).encode()).hexdigest()
        lock_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "Ranel/ue-locks"
        self.lock = Path(lock_directory or lock_root) / (hashlib.sha256(
            canonical(config.executable).encode()).hexdigest() + ".lock")
        self.state = read_json(self.directory / "state.json", {})
        self._check_identity(self.state)
        saved = self.state.get("process")
        if saved and (not isinstance(saved, dict) or type(saved.get("pid")) is not int or saved["pid"] <= 0
                      or type(saved.get("created_at")) not in (int, float)
                      or not math.isfinite(saved["created_at"])):
            raise ValueError("invalid saved runtime identity")
        if (type(self.state.get("failures", 0)) is not int or self.state.get("failures", 0) < 0
                or type(self.state.get("next_start_at", 0)) not in (int, float)
                or not math.isfinite(self.state.get("next_start_at", 0))):
            raise ValueError("invalid saved restart policy")
        self.state.setdefault("config_id", self.identity)
        self.state.setdefault("failures", 0)
        self.state.setdefault("next_start_at", 0)
        self._children = []  # retain Popen handles until reaped
        self.activation_lock = threading.RLock()

    def _check_identity(self, value):
        if value and value.get("config_id") != self.identity:
            raise ValueError("managed runtime configuration changed; inspect saved state first")

    def request(self, running):
        if type(running) is not bool:
            raise ValueError("running must be boolean")
        value = {"config_id": self.identity, "command_id": uuid.uuid4().hex,
                 "running": running, "issued_at": self.clock()}
        atomic_json(self.directory / "intent.json", value)
        return {"accepted": True, "applied": False, **value}

    def snapshot(self):
        value = read_json(self.directory / "state.json", {})
        self._check_identity(value)
        return value

    @property
    def pid(self):
        return self.state.get("process", {}).get("pid", 0)

    @property
    def running_requested(self):
        value = read_json(self.directory / "intent.json", {"config_id": self.identity,
                                                          "running": self.config.autostart})
        self._check_identity(value)
        if type(value.get("running")) is not bool:
            raise ValueError("invalid persisted runtime intent")
        return value["running"]

    def _publish(self, phase, error="", **fields):
        self.state.update(phase=phase, error=error, updated_at=self.clock(), **fields)
        atomic_json(self.directory / "state.json", self.state)

    def _record(self, process):
        return {"pid": process.pid, "created_at": process.create_time()}

    def _matches(self, process):
        releases = [arg.split("=", 1)[1] for arg in process.cmdline()
                    if arg.lower().startswith("-artworkrelease=")]
        return (process.is_running() and process.status() != psutil.STATUS_ZOMBIE
                and canonical(process.exe()) == canonical(self.config.executable)
                and len(releases) == 1 and canonical(releases[0]) == canonical(self.release)
                and not any(arg.lower().startswith("-artworkscene=") for arg in process.cmdline()))

    def _current(self):
        saved = self.state.get("process")
        if not saved:
            return None
        try:
            process = self.backend.process(saved["pid"])
            if abs(process.create_time() - saved["created_at"]) < .001 and self._matches(process):
                return process
        except psutil.NoSuchProcess:
            pass
        return None

    def _failure(self, reason):
        self.state["failures"] += 1
        delay = min(60, self.config.restart_delay_seconds * 2 ** (self.state["failures"] - 1))
        self.state["next_start_at"] = self.clock() + delay
        self._publish("Backoff", reason)

    def _version_folder(self, version):
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", version):
            raise ValueError("invalid release version")
        versions = self.release.parent / "versions"
        folder = versions / version
        if versions.is_symlink() or folder.is_symlink() or not folder.resolve().is_relative_to(versions.resolve()):
            raise ValueError("release path escape")
        if (folder / "scene.json").is_symlink() or not (folder / "scene.json").is_file():
            raise ValueError("release scene missing")
        return folder

    def _files(self, version):
        folder = self._version_folder(version)
        result = {}
        for path in folder.rglob("*"):
            if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError("release symlink or path escape")
            if path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                result[path.relative_to(folder).as_posix()] = {"sha256": digest.hexdigest(),
                                                               "size": path.stat().st_size}
        return result

    def _remember_good(self, process):
        report = read_json(Path(str(self.release) + ".status.json"), {})
        if (report.get("ProcessId") != process.pid or report.get("Phase") != "Applied"
                or report.get("Applied") is not True or not report.get("RuntimeSessionId")
                or not report.get("RequestId") or report.get("Error")
                or report.get("RequestedVersion") != report.get("ActiveReleaseVersion")):
            return
        from datetime import datetime
        try:
            updated = datetime.fromisoformat(report["UpdatedAtUtc"]).timestamp()
        except (KeyError, TypeError, ValueError):
            return
        if updated < process.create_time() or updated > self.clock() + 5:
            return
        version = report["ActiveReleaseVersion"]
        previous = read_json(self.directory / "last-good.json", {})
        if previous.get("version") != version:
            atomic_json(self.directory / "last-good.json", {"version": version, "files": self._files(version)})

    def _restore(self):
        good = read_json(self.directory / "last-good.json", {})
        if good:
            if good.get("files") != self._files(good["version"]):
                raise ValueError("last good release integrity mismatch")
            atomic_json(self.release, {"SchemaVersion": 1, "Version": good["version"]})
        else:
            pointer = read_json(self.release, {})
            if pointer.get("SchemaVersion") != 1:
                raise ValueError("initial release pointer missing")
            self._version_folder(pointer.get("Version"))

    def activate(self, callback, timeout=120):
        """Do not race a last-good restore with a new publication on the same pointer."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.activation_lock:
                if not self.running_requested:
                    raise RuntimeError("runtime_explicitly_stopped")
                if self._current() or not (self.directory / "last-good.json").exists():
                    return callback()
                if self.state.get("phase") in ("Blocked", "Failed"):
                    raise RuntimeError("runtime_recovery_unavailable")
            time.sleep(.02)
        raise TimeoutError("runtime_recovery_pending")

    def tick(self):
        self._children = [child for child in self._children if child.poll() is None]
        intent = read_json(self.directory / "intent.json", {"config_id": self.identity,
                           "command_id": "initial", "running": self.config.autostart})
        self._check_identity(intent)
        if type(intent.get("running")) is not bool:
            raise ValueError("invalid persisted runtime intent")
        if intent["command_id"] != self.state.get("command_id"):
            self.state.update(command_id=intent["command_id"], failures=0, next_start_at=0)
        process = self._current()
        if not process and self.state.pop("process", None) and intent["running"]:
            self._failure("runtime_exited_or_identity_changed")
        if not process:
            candidates = self.backend.candidates(self.config.executable)
            if len(candidates) > 1 or any(not self._matches(item) for item in candidates):
                self._publish("Blocked", "conflicting_runtime_process")
                return
            if candidates:
                process = candidates[0]
                self.state["process"] = self._record(process)
        if process:
            if not intent["running"]:
                # Recheck identity immediately before sending any termination request.
                if self._current() is None:
                    raise RuntimeError("process_identity_changed_before_stop")
                forced = self.backend.close(process, self.config.stop_timeout_seconds)
                self.state.pop("process", None)
                self._publish("Stopped", forced_stop=forced)
            else:
                self._remember_good(process)
                if self.clock() - process.create_time() >= self.config.stable_seconds:
                    self.state.update(failures=0, next_start_at=0)
                self._publish("Running")
            return
        if not intent["running"]:
            self._publish("Stopped")
        elif self.state["failures"] > self.config.restart_limit:
            self._publish("Failed", "restart_limit_exceeded")
        elif self.clock() < self.state["next_start_at"]:
            self._publish("Backoff", self.state.get("error", ""))
        elif not self.release.exists() and not (self.directory / "last-good.json").exists():
            self._publish("WaitingForRelease")
        else:
            try:
                with self.activation_lock:
                    self._restore()
                    child = self.backend.launch(self.config, self.release, self.directory / "runtime.log")
                    self._children.append(child)
                    process = self.backend.process(child.pid)
                    self.state["process"] = self._record(process)
                    self._publish("Starting")
            except (OSError, ValueError, psutil.Error) as exc:
                self._failure("launch_" + type(exc).__name__)

    @contextmanager
    def monitor(self):
        """The lock also prevents two delivery folders from starting the same binary."""
        with show_host_lock(), process_lock(self.lock):
            stop = threading.Event()

            def watch():
                while not stop.is_set():
                    try:
                        self.tick()
                    except (OSError, ValueError, KeyError, TypeError, RuntimeError, psutil.Error) as exc:
                        try:
                            self._publish("Blocked", type(exc).__name__)
                        except OSError:
                            # Keep supervising; a read-only/full disk must not silently kill the thread.
                            print(json.dumps({"event": "runtime_state_write_failed"}), flush=True)
                    stop.wait(.5)

            thread = threading.Thread(target=watch, name="ue-process-supervisor", daemon=True)
            thread.start()
            try:
                yield self
            finally:
                stop.set()
                thread.join()  # never release ownership while its thread can still launch UE
