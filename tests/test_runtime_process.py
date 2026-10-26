import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from studio.infra.runtime_process import ProcessBackend, RuntimeConfig, RuntimeSupervisor, atomic_json, process_lock


class FakeProcess:
    def __init__(self, pid, executable, release, created):
        self.pid, self.executable, self.release, self.created = pid, executable, release, created
        self.alive = True

    def exe(self):
        return str(self.executable)

    def cmdline(self):
        return [str(self.executable), f"-ArtworkRelease={self.release}"]

    def create_time(self):
        return self.created

    def is_running(self):
        return self.alive

    def status(self):
        return psutil.STATUS_RUNNING if self.alive else psutil.STATUS_ZOMBIE

    def poll(self):
        return None if self.alive else 1


class FakeBackend:
    def __init__(self, clock):
        self.clock, self.processes, self.launches, self.stopped = clock, {}, 0, []

    def process(self, pid):
        if pid not in self.processes or not self.processes[pid].alive:
            raise psutil.NoSuchProcess(pid)
        return self.processes[pid]

    def candidates(self, executable):
        return [p for p in self.processes.values() if p.alive and p.executable == executable]

    def launch(self, config, release, log_path):
        self.launches += 1
        process = FakeProcess(100 + self.launches, config.executable, release, self.clock[0])
        self.processes[process.pid] = process
        return process

    def close(self, process, timeout):
        self.stopped.append(process.pid)
        process.alive = False
        return False


@pytest.fixture
def setup(tmp_path):
    executable = tmp_path / "Shipping.exe"
    executable.write_bytes(b"not an actual executable")
    config = RuntimeConfig.parse({"executable": str(executable), "working_directory": str(tmp_path),
                                  "restart_delay_seconds": .1, "stable_seconds": 10,
                                  "stop_timeout_seconds": .1, "restart_limit": 1})
    release = tmp_path / "delivery/release.json"
    version = release.parent / "versions/good"
    version.mkdir(parents=True)
    (version / "scene.json").write_text('{}')
    atomic_json(release, {"SchemaVersion": 1, "Version": "good"})
    clock = [1000.0]
    backend = FakeBackend(clock)
    factory = lambda: RuntimeSupervisor(config, release, backend=backend, clock=lambda: clock[0],
                                       lock_directory=tmp_path / "locks")
    return SimpleNamespace(manager=factory(), factory=factory, backend=backend, clock=clock,
                           config=config, release=release, directory=tmp_path)


def applied(setup, **patch):
    report = {"ProcessId": setup.manager.pid, "Phase": "Applied", "Applied": True,
              "RuntimeSessionId": "test-session", "RequestId": 1, "Error": "",
              "RequestedVersion": "good", "ActiveReleaseVersion": "good",
              "UpdatedAtUtc": datetime.fromtimestamp(setup.clock[0] + .1, UTC).isoformat()}
    atomic_json(Path(str(setup.release) + ".status.json"), {**report, **patch})


def test_start_stop_intent_survives_agent_restart(setup):
    manager = setup.manager
    manager.tick()
    first = manager.pid
    manager.tick()
    assert manager.state["phase"] == "Running" and setup.backend.launches == 1
    assert not (manager.directory / "last-good.json").exists()  # process alive is not Applied
    result = manager.request(False)
    assert result["accepted"] and not result["applied"]
    manager.tick()
    assert manager.pid == 0 and setup.backend.stopped == [first]
    replacement = setup.factory()
    replacement.tick()
    assert replacement.state["phase"] == "Stopped" and setup.backend.launches == 1
    replacement.request(True)
    replacement.tick()
    assert replacement.pid != first and setup.backend.launches == 2


def test_crash_restores_checked_last_good_and_gets_new_pid(setup):
    manager = setup.manager
    manager.tick()
    first = manager.pid
    applied(setup)
    manager.tick()
    assert (manager.directory / "last-good.json").exists()
    atomic_json(setup.release, {"SchemaVersion": 1, "Version": "bad-candidate"})
    setup.backend.process(first).alive = False
    manager.tick()
    assert manager.state["phase"] == "Backoff" and setup.backend.launches == 1
    setup.clock[0] += .2
    manager.tick()
    assert manager.pid != first and manager.pid > 0
    assert json.loads(setup.release.read_text())["Version"] == "good"


def test_corrupted_last_good_is_not_restored_or_launched(setup):
    manager = setup.manager
    manager.tick()
    applied(setup)
    manager.tick()
    setup.backend.process(manager.pid).alive = False
    (setup.release.parent / "versions/good/scene.json").write_text('tampered')
    atomic_json(setup.release, {"SchemaVersion": 1, "Version": "other"})
    manager.tick()
    setup.clock[0] += .2
    manager.tick()
    assert setup.backend.launches == 1 and manager.pid == 0
    assert json.loads(setup.release.read_text())["Version"] == "other"


def test_restart_limit_persists_and_explicit_start_resets_it(setup):
    manager = setup.manager
    manager.tick()
    for _ in range(2):
        setup.backend.process(manager.pid).alive = False
        manager.tick()
        setup.clock[0] += 1
        manager.tick()
    assert manager.state["phase"] == "Failed" and setup.backend.launches == 2
    replacement = setup.factory()
    replacement.tick()
    assert replacement.state["phase"] == "Failed"
    replacement.request(True)
    replacement.tick()
    assert replacement.pid > 0 and setup.backend.launches == 3


def test_applied_alone_does_not_reset_crash_budget(setup):
    manager = setup.manager
    manager.tick()
    manager.state["failures"] = 1
    applied(setup)
    manager.tick()
    assert manager.state["failures"] == 1
    setup.clock[0] += 11
    manager.tick()
    assert manager.state["failures"] == 0


@pytest.mark.parametrize("change", [{"ProcessId": 999}, {"RequestId": 0}, {"Error": "bad"},
                                   {"Applied": False}, {"UpdatedAtUtc": "invalid"},
                                   {"UpdatedAtUtc": "1970-01-01T00:00:00+00:00"},
                                   {"RequestedVersion": "other"}])
def test_bad_or_stale_status_does_not_become_last_good(setup, change):
    setup.manager.tick()
    applied(setup, **change)
    setup.manager.tick()
    assert not (setup.manager.directory / "last-good.json").exists()


def test_adopt_matching_runtime_without_duplicate_launch(setup):
    setup.backend.processes[77] = FakeProcess(77, setup.config.executable, setup.release, 999)
    setup.manager.tick()
    assert setup.manager.pid == 77 and setup.backend.launches == 0
    replacement = setup.factory()
    replacement.tick()
    assert replacement.pid == 77 and setup.backend.launches == 0


@pytest.mark.parametrize("count", [1, 2])
def test_conflicting_runtime_is_not_launched_or_killed(setup, count):
    for index in range(count):
        setup.backend.processes[77 + index] = FakeProcess(77 + index, setup.config.executable,
                                                        setup.release.parent / "other.json", 999)
    setup.manager.tick()
    assert setup.manager.state["phase"] == "Blocked"
    assert setup.backend.launches == 0 and not setup.backend.stopped


def test_reused_pid_cannot_be_stopped_as_original(setup):
    setup.manager.tick()
    pid = setup.manager.pid
    setup.backend.processes[pid] = FakeProcess(pid, setup.config.executable, setup.release.parent / "other.json", 1001)
    setup.manager.request(False)
    setup.manager.tick()
    assert setup.manager.state["phase"] == "Blocked" and not setup.backend.stopped


def test_configuration_change_and_malformed_intent_fail_closed(setup):
    setup.manager.tick()
    path = setup.manager.directory / "intent.json"
    atomic_json(path, {"config_id": "other", "running": True})
    with pytest.raises(ValueError, match="configuration changed"):
        setup.manager.tick()
    assert setup.backend.launches == 1


def test_initial_missing_release_waits_without_exhausting_restart_budget(setup):
    setup.release.unlink()
    for _ in range(8):
        setup.manager.tick()
        setup.clock[0] += 1
    assert setup.manager.state["phase"] == "WaitingForRelease"
    assert setup.manager.state["failures"] == 0 and setup.backend.launches == 0
    setup.manager.activate(lambda: atomic_json(setup.release, {"SchemaVersion": 1, "Version": "good"}))
    setup.manager.tick()
    assert setup.manager.pid > 0


def test_new_publication_waits_until_last_good_restore_finishes(setup):
    manager = setup.manager
    manager.tick()
    applied(setup)
    manager.tick()
    setup.backend.process(manager.pid).alive = False
    manager.tick()
    original = setup.release.read_bytes()
    with pytest.raises(TimeoutError, match="recovery_pending"):
        manager.activate(lambda: atomic_json(setup.release, {"SchemaVersion": 1, "Version": "next"}), timeout=.04)
    assert setup.release.read_bytes() == original
    setup.clock[0] += 1
    manager.tick()
    manager.activate(lambda: atomic_json(setup.release, {"SchemaVersion": 1, "Version": "next"}))
    assert json.loads(setup.release.read_text())["Version"] == "next"
    manager.request(False)
    with pytest.raises(RuntimeError, match="explicitly_stopped"):
        manager.activate(lambda: pytest.fail("stop must prevent publication"))


def test_non_object_persisted_state_is_rejected(setup):
    (setup.manager.directory / "intent.json").write_text('[]')
    with pytest.raises(ValueError, match="must be an object"):
        setup.manager.tick()
    assert setup.backend.launches == 0


def test_backend_discovery_blocks_matching_executable_when_access_is_denied(setup, monkeypatch):
    inaccessible = SimpleNamespace(info={"pid": 1, "name": "Shipping.exe", "exe": None,
                                         "cmdline": None, "create_time": None})
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: [inaccessible])
    with pytest.raises(RuntimeError, match="access_denied"):
        ProcessBackend().candidates(setup.config.executable)


@pytest.mark.parametrize("patch", [{"arguments": "text"}, {"arguments": ["-ArtworkRelease=other"]},
                                  {"restart_limit": True}, {"restart_limit": -1}, {"autostart": "yes"},
                                  {"restart_delay_seconds": float('inf')}, {"unknown": 1}])
def test_invalid_config_is_rejected(setup, patch):
    with pytest.raises((TypeError, ValueError)):
        RuntimeConfig.parse({"executable": str(setup.config.executable),
                             "working_directory": str(setup.directory), **patch})


def test_executable_lock_is_shared_across_delivery_folders(setup):
    other = RuntimeSupervisor(setup.config, setup.directory / "other/release.json", backend=setup.backend,
                              lock_directory=setup.directory / "locks")
    assert other.lock == setup.manager.lock
    with process_lock(setup.manager.lock):
        with pytest.raises(OSError), process_lock(other.lock):
            pytest.fail("second owner acquired lock")
        script = ("from pathlib import Path; from studio.infra.runtime_process import process_lock; "
                  f"lock=process_lock(Path({str(other.lock)!r})); lock.__enter__()")
        result = subprocess.run([sys.executable, "-c", script], check=False, capture_output=True, timeout=15)
        assert result.returncode != 0


def test_watchdog_continues_without_central_calls_and_leaves_ue_on_exit(setup):
    with setup.manager.monitor():
        deadline = time.monotonic() + 3
        while not setup.manager.pid and time.monotonic() < deadline:
            time.sleep(.02)
        first = setup.manager.pid
        assert first
        setup.backend.process(first).alive = False
        setup.clock[0] += 1
        deadline = time.monotonic() + 3
        while setup.backend.launches < 2 and time.monotonic() < deadline:
            setup.clock[0] += .2
            time.sleep(.05)
        assert setup.backend.launches == 2
    assert setup.backend.process(setup.manager.pid).alive  # agent exit is not explicit UE stop


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 호스트 관리자 간 mutex")
@pytest.mark.parametrize("first", ["auto_launch", "managed_runtime"])
def test_managed_and_pc2_auto_modes_reject_each_other_on_same_host(setup, first, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "infra"))
    from show_supervisor import machine_lock

    contexts = (machine_lock(), setup.manager.monitor()) if first == "auto_launch" else (
        setup.manager.monitor(), machine_lock())
    with contexts[0], pytest.raises(RuntimeError, match="another_show_supervisor"), contexts[1]:
        pytest.fail("two runtime managers acquired the same host")


def test_real_process_start_crash_recovery_and_explicit_stop(tmp_path):
    class TrackedBackend(ProcessBackend):
        """Limit discovery to test children; never touch the developer's Python processes."""
        def __init__(self):
            self.children = []

        def candidates(self, executable):
            return [psutil.Process(child.pid) for child in self.children if child.poll() is None]

        def launch(self, config, release, log_path):
            child = super().launch(config, release, log_path)
            self.children.append(child)
            return child

    executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    config = RuntimeConfig.parse({"executable": str(executable), "working_directory": str(tmp_path),
        "arguments": ["-c", "import time; time.sleep(120)"],
        "restart_delay_seconds": .1, "stop_timeout_seconds": .1})
    folder = tmp_path / "versions/real-test"
    folder.mkdir(parents=True)
    (folder / "scene.json").write_text('{}')
    release = tmp_path / "release.json"
    atomic_json(release, {"SchemaVersion": 1, "Version": "real-test"})
    backend = TrackedBackend()
    manager = RuntimeSupervisor(config, release, backend=backend, lock_directory=tmp_path / "locks")
    try:
        manager.tick()
        first = manager.pid
        manager.tick()
        assert manager.state["phase"] == "Running" and len(backend.children) == 1
        psutil.Process(first).kill()
        backend.children[0].wait(timeout=5)
        manager.tick()
        time.sleep(.15)
        manager.tick()
        assert manager.pid and manager.pid != first
        manager.request(False)
        manager.tick()
        assert manager.state["phase"] == "Stopped" and not manager.pid
        assert all(child.poll() is not None for child in backend.children)
    finally:
        for child in backend.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
