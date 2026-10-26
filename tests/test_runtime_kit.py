import hashlib
import io
import json
import ssl
import subprocess
import sys
import threading
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_runtime_pipeline import assembled, runtime_client  # noqa: F401 — fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
from client import Client
from runtime_delivery import prepare, publish
from runtime_worker import run_one


class LocalOpener:
    def __init__(self, http):
        self.http = http

    def open(self, request, timeout=10):
        data = request.data
        if data is not None and not isinstance(data, bytes):
            data = b"".join(data)
        response = self.http.request(request.get_method(), request.full_url.replace("https://kit.test", ""),
                                     headers=dict(request.header_items()), content=data)
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code, "rejected", {}, None)
        return io.BytesIO(response.content)


def kit(http, node, folder):
    folder.mkdir()
    client = Client.__new__(Client)
    client.folder = folder
    client.node = {"node_id": node, "token": node * 20}
    client.url = "https://kit.test"
    client.opener = LocalOpener(http)
    return client


@pytest.mark.parametrize("managed", [False, True])
def test_pc1_to_central_to_pc2_with_real_kit_transport(runtime_client, tmp_path, monkeypatch, managed):  # noqa: F811
    http = runtime_client
    ue, verification, sha = assembled(http)
    pc1 = kit(http, "ue-review-1", tmp_path / "pc1kit")
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "rest.png").write_bytes(b"screenshot bytes")
    delivery = tmp_path / "review-delivery"

    class FakeRunner:
        def __init__(self):
            self.probe = probe

        def verify(self, version, publish):
            publish()
            assert json.loads((delivery / "release.json").read_text())["Version"] == version
            report = {"version": version, "verdict": "pass", "passed": True, "needs_review": False,
                      "checks": [{"name": "applied", "passed": True}], "bundle": {"sha256": sha},
                      "screenshots": {"rest": str(probe / "rest.png")}}
            return SimpleNamespace(summary=lambda: report)

    config = {"mode": "verify", "cache": str(tmp_path / "pc1cache"), "delivery": str(delivery)}
    assert run_one(pc1, config, FakeRunner())["event"] == "result_registered"
    assert http.get(f"/infra/jobs/{verification['id']}").json()["result"]["verdict"] == "pass"
    response = http.post(f"/infra/jobs/{verification['id']}/approve-publication", json={
        "request_id": "kit-publish", "target_node": "show", "reviewer": "test", "reason": "synthetic check"})
    assert response.status_code == 202
    pc2 = kit(http, "show", tmp_path / "pc2kit")
    show = tmp_path / "show-delivery"
    supervisor = SimpleNamespace(pid=123, running_requested=True)
    activations = []

    def activate(callback, timeout):
        activations.append(True)
        return callback()

    supervisor.activate = activate

    def observe(delivery, pid, desired):
        assert json.loads((delivery / "release.json").read_text())["Version"] == desired
        if managed and pid == 123:
            supervisor.pid = 456  # crash recovery changes PID while waiting for actual Applied
            return {"phase": "Unknown", "desired_version": desired, "runtime_pid": pid}
        assert pid == (456 if managed else 123)
        return {"phase": "Applied", "desired_version": desired, "active_version": desired,
                "runtime_pid": pid, "runtime_session": "synthetic-session"}

    monkeypatch.setattr("runtime_worker.observe", observe)
    monkeypatch.setattr("runtime_worker.time.sleep", lambda seconds: None)
    assert run_one(pc2, {"mode": "show", "cache": str(tmp_path / "pc2cache"), "delivery": str(show),
                         "runtime_pid": 123}, supervisor=supervisor if managed else None)["event"] == "result_registered"
    assert bool(activations) == managed
    assert (show / "versions" / ("ue-" + ue["id"]) / "scene.json").read_bytes() == b'{}'
    assert http.get(f"/infra/jobs/{response.json()['id']}").json()["result"]["phase"] == "Applied"
    assert not (pc2.folder / "assignment.secret.json").exists()


def test_preserve_pointer_on_corruption_and_lost_lease(tmp_path):
    raw = b'{}'
    source_id = "assembly"
    sha = hashlib.sha256(raw).hexdigest()
    job = {"payload": {"runtime": {"source_job_id": source_id}}, "inputs": [{"id": source_id,
           "kind": "ue_assemble", "status": "succeeded", "result": {"files": [
               {"name": "scene.json", "sha256": sha, "size": len(raw)}]}}]}
    downloaded = tmp_path / "download"
    (downloaded / source_id).mkdir(parents=True)
    scene = downloaded / source_id / "scene.json"
    scene.write_bytes(b"corrupt")
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    pointer = delivery / "release.json"
    pointer.write_text('{"Version":"last-good"}')
    with pytest.raises(ValueError):
        prepare(job, downloaded, delivery)
    assert json.loads(pointer.read_text())["Version"] == "last-good"
    scene.write_bytes(raw)
    version, _ = prepare(job, downloaded, delivery)
    assert prepare(job, downloaded, delivery)[0] == version  # 재시작 시 불변 폴더 재사용
    lost = threading.Event()
    lost.set()
    with pytest.raises(RuntimeError):
        publish(delivery, version, lost)
    assert json.loads(pointer.read_text())["Version"] == "last-good"


def test_managed_kit_is_self_contained_and_local_stop_needs_no_server_secrets(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = tmp_path / "test.env"
    environment.write_text('AGENT_KEYS_JSON={}\n')
    certificate = tmp_path / "test.crt"
    certificate.write_text(ssl.DER_cert_to_PEM_cert(b"synthetic certificate"))
    folder = tmp_path / "kit"
    result = subprocess.run([sys.executable, str(root / "infra/issue_client.py"), "--node", "test-show",
        "--role", "show", "--job-kind", "ue_publish", "--env", str(environment), "--url", "https://kit.invalid",
        "--ca", str(certificate), "--output", str(folder)], capture_output=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert (folder / "runtime_process.py").read_bytes() == (
        root / "packages/studio/src/studio/infra/runtime_process.py").read_bytes()
    assert (folder / "LIFECYCLE.md").is_file()
    assert (folder / "show_supervisor.py").is_file()
    assert (folder / "show_playback.py").is_file()
    assert (folder / "show_check.py").is_file()
    assert "../apps/show_agent/LIFECYCLE.md" not in (folder / "UE_RUNTIME_SETUP.md").read_text("utf-8")
    local = json.loads((folder / "runtime.local.example.json").read_text())
    assert local["managed_runtime"] is None  # issuing a new kit does not opt a PC into process control
    executable = tmp_path / "fake.exe"
    executable.write_bytes(b"not executable")
    local["delivery"], local["cache"] = str(tmp_path / "delivery"), str(tmp_path / "cache")
    local["managed_runtime"] = {"executable": str(executable), "working_directory": str(tmp_path)}
    (folder / "runtime.local.json").write_text(json.dumps(local))
    (folder / "node.secret.json").unlink()
    (folder / "server.json").unlink()
    result = subprocess.run([sys.executable, str(folder / "runtime_worker.py"), "--folder", str(folder),
                             "--runtime-action", "stop"], capture_output=True, check=False, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    reply = json.loads(result.stdout)
    assert reply["accepted"] and reply["applied"] is False and reply["running"] is False


def test_managed_check_does_not_construct_supervisor_or_start_runtime(tmp_path, monkeypatch):
    import runtime_worker

    executable = tmp_path / "fake.exe"
    executable.write_bytes(b"not executable")
    config = {"mode": "show", "delivery": str(tmp_path / "delivery"), "cache": str(tmp_path / "cache"),
              "managed_runtime": {"executable": str(executable), "working_directory": str(tmp_path)}}
    (tmp_path / "runtime.local.json").write_text(json.dumps(config))
    client = SimpleNamespace(node={"role": "show", "job_kinds": ["ue_publish"]}, check=lambda: {"synthetic": True})
    monkeypatch.setattr(runtime_worker, "Client", lambda folder: client)
    monkeypatch.setattr(runtime_worker, "RuntimeSupervisor", lambda *a, **k: pytest.fail("--check started manager"))
    monkeypatch.setattr(sys, "argv", ["runtime_worker", "--folder", str(tmp_path), "--check"])
    runtime_worker.main()
    assert not (tmp_path / "delivery").exists()


def test_two_runtime_manager_settings_are_rejected_before_any_side_effect(tmp_path, monkeypatch):
    import runtime_worker

    config = {"mode": "show", "auto_launch": True, "managed_runtime": {},
              "delivery": str(tmp_path / "delivery"), "cache": str(tmp_path / "cache")}
    (tmp_path / "runtime.local.json").write_text(json.dumps(config))
    monkeypatch.setattr(runtime_worker, "Client", lambda folder: pytest.fail("client constructed"))
    monkeypatch.setattr(sys, "argv", ["runtime_worker", "--folder", str(tmp_path), "--check"])
    with pytest.raises(ValueError, match="choose_one_runtime_manager"):
        runtime_worker.main()
    assert not (tmp_path / "delivery").exists()


def test_managed_watchdog_starts_offline_and_survives_connection_retries(tmp_path, monkeypatch):
    import runtime_worker
    import show_playback

    executable = tmp_path / "fake.exe"
    executable.write_bytes(b"not executable")
    config = {"mode": "show", "delivery": str(tmp_path / "delivery"), "cache": str(tmp_path / "cache"),
              "managed_runtime": {"executable": str(executable), "working_directory": str(tmp_path)}}
    (tmp_path / "runtime.local.json").write_text(json.dumps(config))
    monitoring, attempts = [], []

    @contextmanager
    def monitor():
        monitoring.append(True)
        try:
            yield SimpleNamespace(pid=123, running_requested=True)
        finally:
            monitoring.pop()

    def offline(*args, **kwargs):
        assert monitoring  # local recovery must be active before any central call
        attempts.append(True)
        if len(attempts) == 2:
            raise KeyboardInterrupt
        raise OSError("synthetic connection outage")

    client = SimpleNamespace(node={"role": "show", "job_kinds": ["ue_publish"]}, call=offline,
                             check=lambda: pytest.fail("central preflight gated local recovery"))
    monkeypatch.setattr(runtime_worker, "Client", lambda folder: client)
    monkeypatch.setattr(runtime_worker, "RuntimeSupervisor", lambda *a, **k: SimpleNamespace(monitor=monitor))
    monkeypatch.setattr(runtime_worker, "observe", lambda *a: {"phase": "Unknown"})
    # 이 검사는 프로세스 감시의 시작 순서를 검사한다. 독립 재생 스레드는 별도 통합 검사 대상이다.
    playback = SimpleNamespace(report=dict, close=lambda: None)
    playback.start = lambda: playback
    monkeypatch.setattr(show_playback, "PlaylistPlayer", lambda *a: playback)
    monkeypatch.setattr(runtime_worker.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sys, "argv", ["runtime_worker", "--folder", str(tmp_path)])
    with pytest.raises(KeyboardInterrupt):
        runtime_worker.main()
    assert len(attempts) == 2 and not monitoring


def test_cached_playlist_observer_can_start_while_central_is_offline(tmp_path, monkeypatch):
    import runtime_worker
    import show_playback

    config = {"mode": "show", "delivery": str(tmp_path / "delivery"), "cache": str(tmp_path / "cache"),
              "runtime_pid": 123}
    (tmp_path / "runtime.local.json").write_text(json.dumps(config))
    playback = SimpleNamespace(value={"revision": 1}, report=dict, close=lambda: None)
    playback.start = lambda: playback
    checks = []

    def offline():
        checks.append(True)
        raise OSError("offline")

    def report(*args, **kwargs):
        assert checks
        raise KeyboardInterrupt

    client = SimpleNamespace(node={"role": "show", "job_kinds": ["ue_publish"]}, check=offline, call=report)
    monkeypatch.setattr(runtime_worker, "Client", lambda folder: client)
    monkeypatch.setattr(runtime_worker, "observe", lambda *a: {"phase": "Unknown"})
    monkeypatch.setattr(show_playback, "PlaylistPlayer", lambda *a: playback)
    monkeypatch.setattr(sys, "argv", ["runtime_worker", "--folder", str(tmp_path)])
    with pytest.raises(KeyboardInterrupt):
        runtime_worker.main()
    assert checks
