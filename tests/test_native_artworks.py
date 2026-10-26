"""Native 별도 큐의 권한·선택·신선도. 기존 검수 작업을 합성하지 않는다."""

import copy
import json
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from infra.native_pc2 import server
from infra.native_pc2.agent import Player

from central.config import Settings
from central.main import create_app
from central.native_artworks_api import NoRedirect


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path)
    server.save(
        "credentials.json",
        {"pc2-installer": {"enabled": True, "token_sha256": server.digest("device-token"), "device_id": "a" * 32}},
    )
    (tmp_path / "operator-token.sha256").write_text(server.digest("service-key"))
    rows = [
        {"id": ident, "title": ident, "sensor_status": "synthetic_only", "quality_status": "local_candidate"}
        for ident in ("moss-v3", "storm-v2")
    ]
    server.save("catalog.json", rows)
    server.save("release.json", {"version": "v2", "packages": []})
    server.save(
        "plan.json",
        {
            "schema": 1,
            "kind": "earth-native-v1",
            "revision": 1,
            "duration_seconds": 120,
            "loop": True,
            "autoplay": True,
            "items": [{**x, "enabled": True} for x in rows],
        },
    )
    http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{http.server_port}"
    http.shutdown()
    http.server_close()


def command(revision=1, request_id="1" * 32):
    return {
        "artwork_id": "storm-v2",
        "expected_revision": revision,
        "request_id": request_id,
        "duration_seconds": 60,
        "loop": False,
        "requested_by": "operator",
    }


def call(base, path, body=None, headers=None):
    request = urllib.request.Request(
        base + path, headers=headers or {}, data=json.dumps(body).encode() if body is not None else None
    )
    return urllib.request.urlopen(request)


def test_operator_scope_is_not_a_device_token(native):
    for headers in ({}, {"Authorization": "Bearer device-token"}):
        with pytest.raises(urllib.error.HTTPError) as error:
            call(native, "/operator/state", headers=headers)
        assert error.value.code == 401
    headers = {"X-Operator-Token": "service-key"}
    with call(native, "/operator/state", headers=headers) as response:
        value = json.load(response)
    assert len(value["library"]) == 2
    assert "token_sha256" not in json.dumps(value)
    assert value["online"] is False and value["playing_confirmed"] is False


def test_select_is_idempotent_and_revision_protected(native):
    headers = {"X-Operator-Token": "service-key"}
    for _ in range(2):
        with call(native, "/operator/play", command(), headers) as response:
            value = json.load(response)
        assert value["plan"]["revision"] == 2
        assert [x["id"] for x in value["plan"]["items"]] == ["storm-v2"]
        assert len(value["library"]) == 2  # Selecting one item does not remove the immutable library.
    with pytest.raises(urllib.error.HTTPError) as error:
        call(native, "/operator/play", command(request_id="2" * 32), headers)
    assert error.value.code == 409
    bad = command(2, "3" * 32)
    bad["artwork_id"] = "cmd.exe /c anything"
    with pytest.raises(urllib.error.HTTPError) as error:
        call(native, "/operator/play", bad, headers)
    assert error.value.code == 400
    with call(
        native, "/operator/stop", {"expected_revision": 2, "request_id": "4" * 32, "requested_by": "operator"}, headers
    ) as response:
        assert json.load(response)["plan"]["autoplay"] is False


def test_test_identity_stale_or_wrong_revision_never_become_playing(native):
    report = {
        "device_id": "a" * 32,
        "received_at": time.time(),
        "state": "playing",
        "ready": True,
        "queue_revision": 1,
        "artwork_id": "moss-v3",
        "session": "b" * 32,
        "runtime_pid": 123,
        "installation_version": "v2",
    }
    server.save("reports.json", {"local-integration-test": report})
    assert server.operator_state()["playing_confirmed"] is False
    server.save("reports.json", {"pc2-installer": report})
    assert server.operator_state()["playing_confirmed"] is True
    assert server.operator_state()["physical_sensor_verified"] is False
    for change in (
        {"queue_revision": 0},
        {"received_at": time.time() - 60},
        {"session": ""},
        {"device_id": "c" * 32},
        {"runtime_pid": None},
    ):
        server.save("reports.json", {"pc2-installer": {**report, **change}})
        assert server.operator_state()["playing_confirmed"] is False


def test_central_requires_account_and_does_not_forward_session_token(native, tmp_path, monkeypatch):
    from central import native_artworks_api as api

    opener = urllib.request.build_opener(NoRedirect())

    def local_open(message, timeout):
        assert message.headers["X-operator-token"] == "service-key"
        assert "Authorization" not in message.headers
        mapped = urllib.request.Request(
            message.full_url.replace("http://earth-pc2-queue:8014", native),
            data=message.data,
            headers=dict(message.headers),
        )
        return opener.open(mapped, timeout=timeout)

    monkeypatch.setattr(
        api.urllib.request, "build_opener", lambda *args: type("Open", (), {"open": staticmethod(local_open)})()
    )
    with TestClient(create_app(Settings(data_dir=tmp_path / "db", operator_token="service-key"))) as client:
        assert client.get("/infra/native-artworks").status_code == 401
        client.headers["X-Operator-Token"] = "service-key"
        assert client.get("/infra/native-artworks").status_code == 200
        body = command()
        body.pop("requested_by")
        assert client.post("/infra/native-artworks/play", json=body).status_code == 200
        assert client.post("/infra/native-artworks/play", json={**body, "request_id": "5" * 32}).status_code == 409
        assert client.post("/infra/native-artworks/play", json={**body, "command": "anything"}).status_code == 422
        from sqlmodel import Session

        from central.accounts import create_account
        from contracts.auth import AccountCreate

        with Session(client.app.state.engine) as db:
            create_account(db, AccountCreate(username="native-owner", password="test-only-password-123!"))
        client.headers.pop("X-Operator-Token")
        login = client.post("/auth/login", json={"username": "native-owner", "password": "test-only-password-123!"})
        assert login.status_code == 200
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        assert len(client.get("/infra/native-artworks").json()["library"]) == 2
        reply = client.post("/infra/native-artworks/stop", json={"expected_revision": 2, "request_id": "6" * 32})
        assert reply.status_code == 200
        assert reply.json()["plan"]["operator_request"]["signature"]["requested_by"] == "native-owner"


def test_agent_restarts_owned_child_for_a_new_selection_and_rejects_downgrade(tmp_path):
    player = Player.__new__(Player)
    player.root = tmp_path
    player.catalog = {"moss-v3": {}, "storm-v2": {}}
    player.plan = {
        "schema": 1,
        "kind": "earth-native-v1",
        "revision": 1,
        "duration_seconds": 120,
        "autoplay": True,
        "loop": True,
        "items": [{"id": "moss-v3", "enabled": True}],
    }
    player.client = Mock()
    changed = {
        **copy.deepcopy(player.plan),
        "revision": 2,
        "play_request_id": "1" * 32,
        "items": [{"id": "storm-v2", "enabled": True}],
    }
    player.client.call.return_value = changed
    player.stop_child = Mock()
    player.index = 8
    player.sync()
    player.stop_child.assert_called_once()
    assert player.index == 0 and player.plan["items"][0]["id"] == "storm-v2"
    player.client.call.return_value = {**changed, "revision": 1}
    player.sync()
    assert player.plan["revision"] == 2 and player.sync_error == "ValueError"
