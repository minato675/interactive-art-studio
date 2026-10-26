import json
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from central.config import Settings
from central.main import create_app
from contracts.infra import Bundle, FileRef, ShowReport
from db import jobs
from show_agent.runtime import activate, observe, prepare
from studio.infra.agent import AgentClient, singleton
from studio.infra.storage import FileStore, release_version, stage_bundle


@pytest.fixture
def infra_client(tmp_path):
    keys = {name: {"token": name * 10, "role": role, "catalog_version": "v1"}
            for name, role in [("p1", "production"), ("p2", "production"), ("show", "show"),
                               ("g1", "generation"), ("g2", "generation")]}
    settings = Settings(data_dir=tmp_path, operator_token="operator", agent_keys_json=json.dumps(keys), lease_seconds=10)
    with TestClient(create_app(settings), headers={"X-Operator-Token": "operator"}) as client:
        yield client


@pytest.fixture
def bundle(tmp_path):
    source = tmp_path / "scene.json"
    source.write_text('{"scene": "test"}', encoding="utf-8")
    store = FileStore(tmp_path / "share")
    ref = store.ingest(source, "scene.json")
    return store, Bundle(artwork_id="art-A", version="v1", catalog_version="v1", files=[ref])


def post_agent(client, node, path, body=None):
    return client.post("/infra/agent/" + path, json=body, headers={"X-Agent-Token": node * 10})


def queue_bundle(client, manifest, request_id="request-1"):
    response = client.post("/infra/jobs", json={"request_id": request_id, "kind": "bundle_verify",
                                               "bundle": manifest.model_dump()})
    assert response.status_code == 202, response.text
    return response.json()


def claim(client, node):
    assert post_agent(client, node, "heartbeat").status_code == 200
    return post_agent(client, node, "claim")


def test_idempotency_and_request_conflict(infra_client, bundle):
    _, manifest = bundle
    first = queue_bundle(infra_client, manifest)
    assert queue_bundle(infra_client, manifest)["id"] == first["id"]
    changed = manifest.model_copy(update={"version": "v2"})
    response = infra_client.post("/infra/jobs", json={
        "request_id": "request-1", "kind": "bundle_verify", "bundle": changed.model_dump()})
    assert response.status_code == 409
    assert "token" not in first


def test_two_producers_share_queue_but_one_slot_each(infra_client, bundle):
    _, manifest = bundle
    a = queue_bundle(infra_client, manifest, "A")
    b = queue_bundle(infra_client, manifest.model_copy(update={"artwork_id": "art-B"}), "B")
    first = claim(infra_client, "p1").json()
    assert post_agent(infra_client, "p1", "claim").status_code == 409
    second = claim(infra_client, "p2").json()
    assert {first["id"], second["id"]} == {a["id"], b["id"]}
    assert first["node_id"] != second["node_id"]
    assert claim(infra_client, "show").status_code == 409


def test_expiry_releases_job_and_rejects_late_result(infra_client, bundle):
    _, manifest = bundle
    job = queue_bundle(infra_client, manifest)
    old = claim(infra_client, "p1").json()
    with Session(infra_client.app.state.engine) as session:
        row = session.get(jobs.Job, job["id"])
        row.lease_until = time.time() - 1
        session.add(row)
        session.commit()
    new = claim(infra_client, "p2").json()
    assert new["id"] == old["id"] and new["attempt"] == 2 and new["token"] != old["token"]
    assert new["attempt_id"] != old["attempt_id"]
    queue_bundle(infra_client, manifest, "next-work")
    assert claim(infra_client, "p1").status_code == 200  # 이전 작업의 새 소유자가 다른 노드라면 슬롯 해제
    assert post_agent(infra_client, "p1", f"jobs/{job['id']}/complete", {"token": old["token"]}).status_code == 409
    assert post_agent(infra_client, "p2", f"jobs/{job['id']}/complete", {"token": new["token"]}).status_code == 200
    status = infra_client.get(f"/infra/jobs/{job['id']}").json()
    assert status["status"] == "succeeded"
    assert status["result"]["publish_approved"] is False
    assert [attempt["status"] for attempt in status["attempts"]] == ["expired", "succeeded"]


def test_wrong_token_and_catalog_do_not_claim(infra_client, bundle):
    _, manifest = bundle
    manifest = manifest.model_copy(update={"catalog_version": "different"})
    queue_bundle(infra_client, manifest)
    assert claim(infra_client, "p1").json() is None
    assert infra_client.get("/infra/nodes", headers={"X-Operator-Token": "bad"}).status_code == 401
    assert infra_client.post("/infra/agent/heartbeat", headers={"X-Agent-Token": "bad"}).status_code == 401
    assert infra_client.get("/ready").json()["ok"]


def test_celery_redelivery_does_not_create_second_draft(infra_client, monkeypatch):
    from central import worker

    monkeypatch.setattr(worker, "engine", infra_client.app.state.engine)
    monkeypatch.setattr(worker, "settings", infra_client.app.state.settings)
    response = infra_client.post("/infra/jobs", json={"request_id": "draft-1", "kind": "draft",
                                                     "draft": {"mode": "manual", "theme": "바다"}})
    job_id = response.json()["id"]
    worker.execute.run(job_id)
    worker.execute.run(job_id)
    status = infra_client.get(f"/infra/jobs/{job_id}").json()
    assert status["status"] == "succeeded" and status["attempt"] == 1
    assert status["result"]["status"] == "awaiting"


def test_dispatch_outage_keeps_job_and_retries(infra_client, monkeypatch):
    from central import worker

    monkeypatch.setattr(worker, "engine", infra_client.app.state.engine)
    job = infra_client.post("/infra/jobs", json={"request_id": "probe", "kind": "diagnostic"}).json()

    def unavailable(*args, **kwargs):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(worker.celery, "send_task", unavailable)
    with pytest.raises(ConnectionError):
        worker.dispatch_once()
    assert infra_client.get("/infra/jobs/" + job["id"]).json()["status"] == "queued"
    delivered = []
    monkeypatch.setattr(worker.celery, "send_task", lambda *a, **k: delivered.append(k["args"][0]))
    worker.dispatch_once()
    assert delivered == [job["id"]]


def test_cache_survives_share_loss_and_detects_tampering(tmp_path, bundle):
    store, manifest = bundle
    cache = tmp_path / "cache"
    target = stage_bundle(store, cache, manifest, reserve_bytes=0)
    store.path(manifest.files[0]).unlink()
    assert stage_bundle(store, cache, manifest, reserve_bytes=0) == target
    (target / "scene.json").write_text("tampered")
    with pytest.raises(ValueError, match="무결성"):
        stage_bundle(store, cache, manifest, reserve_bytes=0)


def test_partial_failure_does_not_replace_release(tmp_path, bundle):
    store, manifest = bundle
    cache = tmp_path / "cache"
    prepare(store, cache, manifest, "v1")
    activate(cache, manifest)
    previous = (cache / "release.json").read_bytes()
    store.path(manifest.files[0]).write_text("broken")
    next_bundle = manifest.model_copy(update={"version": "v2"})
    # 새 캐시가 없도록 새로운 별도 해시를 가진 손상 파일을 요청한다.
    bad_ref = FileRef(name="scene.json", sha256="f" * 64, size=10)
    bad_bundle = next_bundle.model_copy(update={"files": [bad_ref]})
    with pytest.raises(FileNotFoundError):
        prepare(store, cache, bad_bundle, "v1")
    assert (cache / "release.json").read_bytes() == previous
    assert not (cache / "versions" / release_version(bad_bundle)).exists()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/path", "a\\b", "CON", "a.", "a//b", "a/../b"])
def test_bundle_paths_cannot_escape(name):
    with pytest.raises(ValueError):
        FileRef(name=name, sha256="a" * 64, size=0)


def test_single_agent_lock(tmp_path):
    with singleton(tmp_path / "agent.lock"), pytest.raises(OSError), singleton(tmp_path / "agent.lock"):
        pytest.fail("중복 Agent가 잠금을 획득함")


def test_remote_plain_http_is_rejected():
    with pytest.raises(ValueError, match="HTTPS"):
        AgentClient("http://example.org", "token")


@pytest.mark.parametrize("path", ["/api/artworks", "/api/pool", "/api/families"])
def test_operator_reads_require_authentication(infra_client, path):
    assert infra_client.get(path, headers={"X-Operator-Token": ""}).status_code == 401
    assert infra_client.get(path, headers={"X-Operator-Token": "wrong"}).status_code == 401


def test_generation_identity_is_read_only_and_cannot_claim_ue_work(tmp_path):
    registry = {"generation-1": {"token": "generation-secret-1234", "role": "generation", "catalog_version": "v1"}}
    settings = Settings(data_dir=tmp_path, agent_keys_json=json.dumps(registry), operator_token="operator")
    with TestClient(create_app(settings)) as client:
        header = {"X-Agent-Token": "generation-secret-1234"}
        assert client.get("/infra/agent/identity").status_code == 401
        identity = client.get("/infra/agent/identity", headers=header).json()
        assert identity == {"node_id": "generation-1", "role": "generation", "catalog_version": "v1"}
        assert client.get("/infra/nodes", headers={"X-Operator-Token": "operator"}).json() == []
        assert client.post("/infra/agent/heartbeat", headers=header).status_code == 200
        assert client.post("/infra/agent/claim", headers=header).json() is None
        assert client.get("/infra/jobs/unknown", headers=header).status_code == 401


class LiveProcess:
    def __init__(self, pid):
        self.pid = pid

    def is_running(self):
        return True

    def create_time(self):
        return 100


def test_applied_needs_actual_active_version_and_live_session(tmp_path):
    path = tmp_path / "status.json"
    data = {"ProcessId": 123, "RuntimeSessionId": "session-new", "UpdatedAtUtc": datetime.now(UTC).isoformat(),
            "Phase": "Ready", "Applied": False, "ActiveReleaseVersion": "A", "RequestedVersion": "B"}
    path.write_text(json.dumps(data))
    report = observe(path, 123, "B", LiveProcess)
    assert report.phase == "Ready" and report.active_version == "A"
    data.update(Phase="Applied", Applied=True, ActiveReleaseVersion="B")
    path.write_text(json.dumps(data))
    assert observe(path, 123, "B", LiveProcess).phase == "Applied"
    assert observe(path, 456, "B", LiveProcess).phase == "Unknown"
    data["UpdatedAtUtc"] = datetime.fromtimestamp(99, UTC).isoformat()
    path.write_text(json.dumps(data))
    assert observe(path, 123, "B", LiveProcess).phase == "Unknown"


def test_reconnect_reports_actual_version_even_when_request_differs(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({
        "ProcessId": 123, "RuntimeSessionId": "session", "UpdatedAtUtc": datetime.now(UTC).isoformat(),
        "Phase": "Applied", "Applied": True, "ActiveReleaseVersion": "A", "RequestedVersion": "A"}))
    report = observe(path, 123, "B", LiveProcess)
    assert report.phase == "Unknown" and report.active_version == "A"


def test_expired_final_attempt_stays_failed(infra_client, bundle):
    _, manifest = bundle
    queued = queue_bundle(infra_client, manifest)
    claimed = claim(infra_client, "p1").json()
    with Session(infra_client.app.state.engine) as session:
        row = session.get(jobs.Job, queued["id"])
        row.max_attempts, row.lease_until = 1, time.time() - 1
        session.add(row)
        session.commit()
    assert claim(infra_client, "p2").json() is None
    assert infra_client.get("/infra/jobs/" + claimed["id"]).json()["status"] == "failed"
    with Session(infra_client.app.state.engine) as session:
        assert len(session.exec(select(jobs.Attempt)).all()) == 1


@pytest.mark.parametrize("broken", [False, True])
def test_show_agent_preserves_active_version_while_staged_or_failed(tmp_path, bundle, monkeypatch, broken):
    from show_agent import __main__ as show

    store, manifest = bundle
    path = tmp_path / "manifest.json"
    path.write_text(manifest.model_dump_json())
    monkeypatch.setattr("sys.argv", ["show_agent", "--bundle", str(path), "--once"])
    monkeypatch.setenv("SHOW_CACHE", str(tmp_path / "show"))
    monkeypatch.setenv("ARTIFACT_ROOT", str(store.root))
    monkeypatch.setenv("AGENT_TOKEN", "test-token")
    monkeypatch.setenv("UE_RUNTIME_PID", "123")
    reports = []

    class Client:
        def post(self, route, body):
            reports.append(body)

        def close(self):
            pass

    monkeypatch.setattr(show, "AgentClient", lambda *a: Client())
    monkeypatch.setattr(show, "observe", lambda *a: ShowReport(
        phase="Applied", active_version="current-A", runtime_pid=123, runtime_session="real-session"))
    if broken:
        store.path(manifest.files[0]).unlink()
        with pytest.raises(SystemExit):
            show.main()
    else:
        show.main()
    assert reports[-1]["active_version"] == "current-A"
    assert reports[-1]["phase"] == ("Failed" if broken else "Staged")
    assert not (tmp_path / "show" / "release.json").exists()


def queue_generation(client, name, kind="background_generate", parents=None, artwork="art-A"):
    body = {"request_id": name, "kind": kind,
            "generation": {"artwork_id": artwork, "version": "v1", "prompt": "test generation request"},
            "depends_on": parents or []}
    response = client.post("/infra/jobs", json=body)
    assert response.status_code == 202, response.text
    return response.json()


def generation_result(lease):
    spec = lease["payload"]["generation"]
    return {"token": lease["token"], "result": {
        "artwork_id": spec["artwork_id"], "version": spec["version"],
        "files": [{"name": "image.png", "sha256": "a" * 64, "size": 100}]}}


def test_generation_dependencies_parallel_artworks_and_roles(infra_client, bundle):
    bg = queue_generation(infra_client, "bg-A")
    assets = queue_generation(infra_client, "assets-A", "assets_generate", [bg["id"]])
    other = queue_generation(infra_client, "bg-B", artwork="art-B")
    assembly = queue_bundle(infra_client, bundle[1])
    first, second = claim(infra_client, "g1").json(), claim(infra_client, "g2").json()
    assert first["id"] == bg["id"] and second["id"] == other["id"]
    assert claim(infra_client, "p1").json()["id"] == assembly["id"]
    assert claim(infra_client, "g1").status_code == 409
    assert post_agent(infra_client, "g1", f"jobs/{first['id']}/complete", generation_result(first)).status_code == 200
    following = claim(infra_client, "g1").json()
    assert following["id"] == assets["id"]
    assert following["inputs"][0]["result"]["files"][0]["name"] == "image.png"
    assert not following["inputs"][0]["result"]["publish_approved"]
    assert "_receipt" not in str(following)


def test_generation_completion_replay_and_old_attempt_rejected(infra_client):
    queued = queue_generation(infra_client, "expire-generation")
    old = claim(infra_client, "g1").json()
    with Session(infra_client.app.state.engine) as session:
        row = session.get(jobs.Job, queued["id"])
        row.lease_until = time.time() - 1
        session.add(row)
        session.commit()
    new = claim(infra_client, "g2").json()
    assert post_agent(infra_client, "g1", f"jobs/{old['id']}/complete", generation_result(old)).status_code == 409
    body = generation_result(new)
    for _ in range(2):
        assert post_agent(infra_client, "g2", f"jobs/{new['id']}/complete", body).status_code == 200
    body["result"]["files"][0]["sha256"] = "b" * 64
    assert post_agent(infra_client, "g2", f"jobs/{new['id']}/complete", body).status_code == 409
    status = infra_client.get("/infra/jobs/" + queued["id"]).json()
    assert [attempt["status"] for attempt in status["attempts"]] == ["expired", "succeeded"]
    assert "_receipt" not in str(status)
    nodes = infra_client.get("/infra/nodes").json()
    assert all(node["active_job"] is None for node in nodes)


def test_failed_dependency_waits_until_bounded_retry_succeeds(infra_client):
    bg = queue_generation(infra_client, "retry-bg")
    asset = queue_generation(infra_client, "retry-assets", "assets_generate", [bg["id"]])
    lease = claim(infra_client, "g1").json()
    body = {"token": lease["token"], "error": "provider_failed"}
    assert post_agent(infra_client, "g1", f"jobs/{lease['id']}/complete", body).status_code == 200
    assert claim(infra_client, "g2").json() is None
    assert infra_client.post(f"/infra/jobs/{bg['id']}/retry").status_code == 200
    again = claim(infra_client, "g2").json()
    assert again["attempt"] == 2 and again["attempt_id"] != lease["attempt_id"]
    assert post_agent(infra_client, "g1", f"jobs/{lease['id']}/complete", body).status_code == 409
    assert post_agent(infra_client, "g2", f"jobs/{again['id']}/complete", generation_result(again)).status_code == 200
    assert claim(infra_client, "g2").json()["id"] == asset["id"]
    assert infra_client.post(f"/infra/jobs/{bg['id']}/retry").status_code == 409


def test_generation_bad_dependencies_and_results(infra_client):
    body = {"request_id": "bad-deps", "kind": "assets_generate",
            "generation": {"artwork_id": "a", "version": "v1", "prompt": "test"}, "depends_on": ["missing"]}
    assert infra_client.post("/infra/jobs", json=body).status_code == 409
    parent = queue_generation(infra_client, "parent")
    body["depends_on"] = [parent["id"]]
    assert infra_client.post("/infra/jobs", json=body).status_code == 409  # 다른 작품
    lease = claim(infra_client, "g1").json()
    result = generation_result(lease)
    result["result"]["version"] = "wrong-version"
    assert post_agent(infra_client, "g1", f"jobs/{lease['id']}/complete", result).status_code == 422
    assert post_agent(infra_client, "g1", f"jobs/{lease['id']}/complete", {"token": lease["token"]}).status_code == 422
    assert infra_client.get("/infra/agent/current", headers={"X-Agent-Token": "g1" * 10}).json()["id"] == lease["id"]
    assert infra_client.get("/infra/agent/current", headers={"X-Agent-Token": "g2" * 10}).json() is None


def test_client_file_transfer_and_completion_response_loss(infra_client, tmp_path):
    import urllib.error

    from infra.client import Client, digest

    queued = queue_generation(infra_client, "kit-file-transfer")
    agent = Client.__new__(Client)
    agent.folder, agent.node = tmp_path, {"node_id": "g1", "role": "generation"}
    lose_response = True

    def call(path, body=None, heartbeat=False, post=False):
        nonlocal lose_response
        header = {"X-Agent-Token": "g1" * 10}
        response = (infra_client.post(path, json=body, headers=header) if heartbeat or post
                    else infra_client.get(path, headers=header))
        response.raise_for_status()
        if path.endswith("/complete") and lose_response:
            lose_response = False
            raise urllib.error.URLError("response lost")
        return response.json()

    agent.call = call
    assert agent.claim()["job_id"] == queued["id"]
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir()
    path = source / "sample.bin"
    path.write_bytes(b"transport-only-fixture" * 10000)
    with pytest.raises(urllib.error.URLError):
        agent.complete(source, store)
    assert agent.assignment_path.exists()
    assert agent.complete(source, store)["ok"]
    assert not agent.assignment_path.exists()
    sha256, size = digest(path)
    ref = FileRef(name="sample.bin", sha256=sha256, size=size)
    assert FileStore(store).path(ref).read_bytes() == path.read_bytes()


def test_show_reporting_outage_does_not_replace_last_good(tmp_path, bundle, monkeypatch):
    from show_agent import __main__ as show

    cache = tmp_path / "show-offline"
    prepare(bundle[0], cache, bundle[1], "v1")
    activate(cache, bundle[1])
    previous = (cache / "release.json").read_bytes()
    monkeypatch.setenv("SHOW_CACHE", str(cache))
    monkeypatch.setenv("UE_RUNTIME_PID", "123")
    monkeypatch.setenv("AGENT_TOKEN", "test-only")
    monkeypatch.setattr("sys.argv", ["show_agent", "--once"])
    monkeypatch.setattr(show, "observe", lambda *args: ShowReport(phase="Applied", active_version="still-A"))

    class Offline:
        def post(self, *args):
            raise ConnectionError("central offline")

        def close(self):
            pass

    monkeypatch.setattr(show, "AgentClient", lambda *args: Offline())
    with pytest.raises(ConnectionError):
        show.main()
    assert (cache / "release.json").read_bytes() == previous
    assert json.loads((cache / "last-good.json").read_text())["active_version"] == "still-A"

def test_head_http_diagnostic_reaches_persistent_job(infra_client, monkeypatch):
    from central import worker
    from studio.head.responses import HeadAPIUnavailable

    monkeypatch.setattr(worker, 'engine', infra_client.app.state.engine)
    monkeypatch.setattr(worker, 'settings', infra_client.app.state.settings)
    def unavailable(*args):
        raise HeadAPIUnavailable('safe', {'http_status': 429, 'category': 'quota', 'request_bytes': 123})
    monkeypatch.setattr(worker.head_dispatch, 'decide', unavailable)
    with Session(worker.engine) as session:
        job = jobs.enqueue(session, {'request_id': 'safe-head-error', 'kind': 'head_decide',
                                    'workflow_id': 'unneeded', 'phase': 'review'})
        job_id = job.id
    worker.execute.run(job_id)
    status = infra_client.get(f'/infra/jobs/{job_id}').json()
    assert status['status'] == 'failed' and status['error'] == 'LLMUnavailable'
    assert status['result']['error_detail']['http_status'] == 429
    record = worker.settings.data_dir / 'head-errors' / job_id / 'attempt-1.json'
    assert json.loads(record.read_text())['error_detail']['category'] == 'quota'
