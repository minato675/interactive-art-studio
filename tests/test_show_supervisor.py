import copy
import json
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
from client import atomic_json, digest
from runtime_worker import display
from show_check import crash_test, inspect
from show_supervisor import Supervisor, checked_manifest, machine_lock

FAKE = Path(__file__).parent / "fakes" / "fake_show_runtime.py"


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(0.05)
    raise AssertionError("supervisor condition timed out")


def bundle(delivery, version="ue-good", **scene):
    root = delivery / "versions" / version
    root.mkdir(parents=True)
    atomic_json(root / "scene.json", scene)
    sha, size = digest(root / "scene.json")
    return {"version": version, "files": [{"name": "scene.json", "sha256": sha, "size": size}],
            "previous_pc_applied_verified": True}


@pytest.fixture
def manager(tmp_path, monkeypatch):
    delivery = tmp_path / "delivery"
    record = bundle(delivery)
    atomic_json(tmp_path / "bootstrap-manifest.json", record)
    config = {"mode": "show", "auto_launch": True, "delivery": str(delivery),
              "cache": str(tmp_path / "cache"),
              "executable": sys._base_executable, "working_directory": str(tmp_path),
              "launch_arguments": [str(FAKE)], "restart_initial_seconds": 0.2,
              "restart_max_seconds": 0.8, "startup_timeout_seconds": 2, "apply_timeout_seconds": 0.8}
    value = Supervisor(tmp_path, config)
    atomic_json(tmp_path / "runtime.local.json", config)
    # 부모·다른 Python을 UE로 세지 않는다. 실제 자식 프로세스의 소유권 검사는 유지한다.
    monkeypatch.setattr(value, "foreign_processes", list)
    yield value
    value.close()
    with value.lock:
        value._stop_owned()


def start(manager):
    manager.start()
    wait_for(lambda: manager.ready)
    assert manager.snapshot()["phase"] == "Applied"
    return manager.owner["pid"]


def test_start_crash_recovery_offline_and_agent_restart_adopts_one_process(manager, monkeypatch):
    first = start(manager)
    session = manager.snapshot()["runtime_session"]
    # 중앙 HTTP 없이 독립 감시 스레드가 동작한다.
    psutil.Process(first).kill()
    wait_for(lambda: manager.ready and manager.owner["pid"] != first)
    second = manager.owner["pid"]
    assert manager.snapshot()["runtime_session"] != session
    assert manager.snapshot()["active_version"] == "ue-good"
    manager.close()
    assert psutil.Process(second).is_running()  # Agent 종료만으로 화면을 끄지 않는다.
    replacement = Supervisor(manager.folder, manager.config)
    monkeypatch.setattr(replacement, "foreign_processes", list)
    try:
        replacement.start()
        wait_for(lambda: replacement.ready)
        assert replacement.owner["pid"] == second
    finally:
        replacement.close()


def test_applied_candidate_becomes_new_last_good(manager):
    first = start(manager)
    record = bundle(manager.delivery, "ue-next")
    ticket = manager.activate("ue-next", record, threading.Event())
    assert ticket["event"].wait(5)
    assert not ticket["error"]
    assert ticket["status"]["active_version"] == "ue-next"
    assert manager.owner["pid"] == first  # 정상 작품 교체는 UE를 재시작하지 않는다.
    psutil.Process(first).kill()
    wait_for(lambda: manager.ready and manager.owner["pid"] != first)
    assert manager.snapshot()["active_version"] == "ue-next"
    saved = json.loads((manager.folder / "show-last-good.json").read_text("utf-8"))
    assert saved["version"] == "ue-next"


def test_worker_display_reports_real_managed_process_and_result(manager, tmp_path):
    first = start(manager)
    downloaded, output = tmp_path / "download", tmp_path / "output"
    record = bundle(downloaded, "ue-source")
    source = downloaded / "versions" / "ue-source"
    # 입력 도구의 <source job id>/<files> 배치와 같은 형태다.
    source.rename(downloaded / "source")
    output.mkdir()
    job = {"payload": {"runtime": {"source_job_id": "source"}}, "inputs": [
        {"id": "source", "kind": "ue_assemble", "status": "succeeded", "result": {"files": record["files"]}}]}
    calls = []
    client = SimpleNamespace(folder=tmp_path, renew=lambda _: None,
                             call=lambda path, status, **_: calls.append((path, status)))
    display(client, manager.config, job, downloaded, manager.delivery, output, threading.Event(), manager)
    report = json.loads((output / "applied-report.json").read_text("utf-8"))
    assert report["version"] == "ue-source"
    assert report["status"]["runtime_pid"] == first
    assert report["status"]["runtime_session"]
    assert manager.good["version"] == "ue-source"


@pytest.mark.parametrize("failure", ["crash", "fail", "hang"])
def test_failed_candidate_restores_last_good(manager, failure):
    first = start(manager)
    record = bundle(manager.delivery, "ue-bad", **{failure: True})
    ticket = manager.activate("ue-bad", record, threading.Event())
    assert ticket["event"].wait(5)
    assert ticket["error"]
    wait_for(lambda: manager.ready)
    assert manager.snapshot()["active_version"] == "ue-good"
    assert json.loads((manager.folder / "show-last-good.json").read_text("utf-8"))["version"] == "ue-good"
    if failure in ("crash", "hang"):
        assert manager.owner["pid"] != first
    else:
        assert manager.owner["pid"] == first


def test_lost_lease_never_activates_and_abort_restores(manager):
    start(manager)
    record = bundle(manager.delivery, "ue-pending", hang=True)
    lost = threading.Event()
    lost.set()
    with pytest.raises(RuntimeError, match="lease_lost"):
        manager.activate("ue-pending", record, lost)
    assert manager.snapshot()["active_version"] == "ue-good"
    lost.clear()
    ticket = manager.activate("ue-pending", record, lost)
    manager.abort(ticket)
    assert ticket["event"].is_set() and ticket["error"]
    wait_for(lambda: manager.ready)
    assert manager.good["version"] == "ue-good"


def test_lost_lease_is_detected_even_when_http_worker_is_blocked(manager):
    start(manager)
    record = bundle(manager.delivery, "ue-pending", hang=True)
    lost = threading.Event()
    ticket = manager.activate("ue-pending", record, lost)
    lost.set()
    assert ticket["event"].wait(5)
    assert ticket["error"] == "lease_lost"
    wait_for(lambda: manager.ready)
    assert manager.good["version"] == "ue-good"


def test_stale_status_and_reused_pid_are_not_trusted_or_killed(manager):
    first = start(manager)
    owner = copy.deepcopy(manager.owner)
    manager.owner["create_time"] -= 100
    try:
        with pytest.raises(RuntimeError, match="ownership_mismatch"):
            manager._stop_owned()
        assert psutil.Process(first).is_running()
    finally:
        manager.owner = owner
    stale = json.loads((manager.delivery / "release.json.status.json").read_text("utf-8"))
    stale["ProcessId"] = first + 10000
    atomic_json(manager.delivery / "release.json.status.json", stale)
    assert manager.snapshot()["phase"] == "Unknown"


def test_foreign_ue_blocks_start_without_killing_it(manager, monkeypatch):
    monkeypatch.setattr(manager, "foreign_processes", lambda: [12345])
    manager._tick()
    assert manager.owner is None
    state = json.loads((manager.folder / "supervisor-state.json").read_text("utf-8"))
    assert state["phase"] == "Blocked" and state["error"] == "foreign_ue_running"


def test_shipping_launcher_and_editor_are_detected_as_foreign_ue(manager, monkeypatch):
    names = ["Art-Win64-Shipping.exe", "Other-Win64-Shipping.exe", "Art.exe", "UnrealEditor.exe", "notepad.exe",
             "CrashReportClient-Win64-Shipping.exe"]
    processes = [SimpleNamespace(pid=index + 100, info={"name": name}) for index, name in enumerate(names)]
    monkeypatch.setattr(psutil, "process_iter", lambda _: processes)
    assert Supervisor.foreign_processes(manager) == [100, 101, 102, 103]


def test_stop_only_owned_runtime(manager):
    first = start(manager)
    manager.close()
    manager.stop_owned()
    assert not psutil.pid_exists(first)
    assert not manager.owner_path.exists()


def test_field_recovery_test_records_old_new_process_and_session(manager, monkeypatch):
    monkeypatch.setattr(Supervisor, "foreign_processes", lambda self: [])
    start(manager)
    result = crash_test(manager.folder, timeout=10)
    assert result["passed"]
    assert result["before"]["runtime_pid"] != result["after"]["runtime_pid"]
    assert result["before"]["runtime_session"] != result["after"]["runtime_session"]
    assert result["after"]["ue_process_count"] == 1
    assert (manager.folder / "recovery-test-result.json").exists()
    manager.close()
    _, stopped = inspect(manager.folder)
    assert not stopped["passed"]  # UE만 남고 감시가 꺼진 상태는 복구 시험 준비 완료가 아니다.


def test_saved_last_good_requires_real_applied_fields(manager):
    atomic_json(manager.folder / "show-last-good.json", manager.good)
    with pytest.raises(ValueError, match="applied_evidence"):
        Supervisor(manager.folder, manager.config)


def test_worker_cli_check_is_read_only_and_show_can_start_offline(manager, monkeypatch, capsys):
    import runtime_worker
    import show_supervisor

    class OfflineClient:
        def __init__(self, folder):
            self.folder = folder
            self.node = {"role": "show", "job_kinds": ["ue_publish"]}

        def check(self):
            raise OSError("synthetic offline")

        def call(self, *args, **kwargs):
            return None

        def claim(self):
            pass

        def current(self):
            return None

    monkeypatch.setattr(runtime_worker, "Client", OfflineClient)
    monkeypatch.setattr(Supervisor, "foreign_processes", lambda self: [])
    monkeypatch.setattr(Supervisor, "validate_windows", lambda self: None)
    monkeypatch.setattr(show_supervisor, "machine_lock", nullcontext)
    monkeypatch.setattr(sys, "argv", ["worker", "--folder", str(manager.folder), "--check"])
    with pytest.raises(OSError):
        runtime_worker.main()
    assert not manager.owner_path.exists()
    monkeypatch.setattr(sys, "argv", ["worker", "--folder", str(manager.folder), "--once"])
    try:
        runtime_worker.main()
    finally:
        if manager.owner_path.exists():
            manager.owner = json.loads(manager.owner_path.read_text("utf-8"))
    assert manager.owned_process()
    assert "offline_local_recovery" in capsys.readouterr().out
    assert json.loads((manager.folder / "supervisor-state.json").read_text("utf-8"))["phase"] == "Paused"


def test_corrupt_bundle_or_manifest_never_used(manager):
    (manager.delivery / "versions" / "ue-good" / "scene.json").write_text("corrupted", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        manager._spawn()
    assert manager.owner is None
    bad = {**manager.good, "version": "../../other"}
    with pytest.raises(ValueError, match="version"):
        checked_manifest(manager.delivery, bad)


def test_backoff_grows_for_repeated_crashes(manager):
    start(manager)
    for count in (1, 2):
        psutil.Process(manager.owner["pid"]).kill()
        wait_for(lambda expected=count: manager.failures == expected)
        assert manager.next_start - time.monotonic() <= min(manager.maximum, manager.initial * 2 ** (count - 1))
        wait_for(lambda: manager.ready)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 전역 mutex")
def test_machine_lock_rejects_second_instance():
    with machine_lock(), pytest.raises(RuntimeError, match="another_show_supervisor"), machine_lock():
        pytest.fail("second supervisor acquired host lock")
    with machine_lock():
        pass
