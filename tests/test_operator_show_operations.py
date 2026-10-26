"""운영자가 보고 성공과 요청 접수를 혼동하지 않도록 실제 상태·제어 경로를 확인한다."""
import copy

import pytest
from tests.test_operator import wait_until
from tests.test_operator_dashboard import panel, qt_app  # noqa: F401
from tests.test_runtime_pipeline import runtime_client, verified  # noqa: F401
from tests.test_server_exchange import headers

from operator_app.show_state import state


def evidence():
    artworks = {"a": {"verification_job_id": "a", "source_job_id": "source-a", "title": "물결"},
                "b": {"verification_job_id": "b", "source_job_id": "source-b", "title": "숲"}}
    config = {"revision": 8, "plan": {"autoplay": True, "duration_seconds": 25, "loop": True,
              "items": [{"verification_job_id": x, "repeat": 1, "enabled": True} for x in artworks]}}
    node = {"node_id": "show", "online": True, "report_at": 1000, "phase": "Applied", "runtime_pid": 55,
            "runtime_session": "session", "active_version": "ue-source-a", "playback": {
                "revision": 8, "state": "Playing", "current_verification_job_id": "a",
                "next_verification_job_id": "a", "duration_seconds": 25, "elapsed_seconds": 10,
                "cached_count": 2}}
    return node, config, artworks


def test_actual_progress_uses_report_and_next_is_not_legacy_stale_current():
    node, config, artworks = evidence()
    view = state(node, config, artworks, 1002, True)
    assert view["title"] == "물결" and view["progress"] == 40 and view["remaining"] == 15
    assert view["next_id"] == "b" and view["cached"] is None
    assert state(node, config, artworks, 1040, True)["elapsed"] == 10  # UI 시계로 증가시키지 않음


@pytest.mark.parametrize("change", ["offline", "expired", "query_failed", "version", "pid", "phase"])
def test_no_progress_from_stale_or_inconsistent_evidence(change):
    node, config, artworks = evidence()
    now, connected = 1002, True
    if change == "offline":
        node["online"] = False
    elif change == "expired":
        now = 1090
    elif change == "query_failed":
        connected = False
    elif change == "version":
        node["active_version"] = "ue-other"
    elif change == "pid":
        node["runtime_pid"] = None
    else:
        node["phase"] = "Staged"
    view = state(node, config, artworks, now, connected)
    assert view["elapsed"] is None and view["progress"] == 0


def test_dirty_plan_or_revision_mismatch_does_not_claim_new_plan_playing():
    node, config, artworks = evidence()
    config["revision"] = 9
    config["plan"]["items"].reverse()
    view = state(node, config, artworks, 1002, True)
    assert not view["synced"] and view["next_id"] is None
    assert view["current_id"] == "a"  # 새 구성 동기화 전에도 실제 UE 보고는 유지


def test_report_contract_preserves_process_and_per_artwork_cache(runtime_client):  # noqa: F811
    report = {"phase": "Applied", "runtime_pid": 55, "runtime_session": "session",
              "active_version": "ue-source-a", "desired_version": "ue-source-a",
              "playback": {"revision": 8, "state": "Playing", "cached_verification_job_ids": ["a"],
                           "cache_errors": {"b": "prepare_ValueError"}, "current_repeat": 2}}
    runtime_client.post("/infra/agent/heartbeat", headers=headers("show"), json=report).raise_for_status()
    node = next(n for n in runtime_client.get("/infra/show-dashboard").json()["nodes"] if n["node_id"] == "show")
    assert node["runtime_pid"] == 55 and node["runtime_session"] == "session"
    assert node["playback"]["cached_verification_job_ids"] == ["a"]
    assert node["playback"]["cache_errors"] == {"b": "prepare_ValueError"}


def test_preview_download_rejects_corrupt_bytes_without_other_requests():
    import hashlib

    import httpx

    from operator_app.client import CentralClient

    value = b"original PNG bytes"
    ref = {"job_id": "assembly", "sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    http = httpx.Client(base_url="https://test.invalid", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=value)))
    with http:
        client = CentralClient(http=http)
        assert client.show_preview(ref) == value
        with pytest.raises(ValueError, match="integrity"):
            client.show_preview({**ref, "sha256": "0" * 64})


def test_operating_controls_preserve_draft_and_keep_unsupported_disabled(panel, runtime_client, qt_app, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QMessageBox

    source, check = verified(runtime_client, "pass")
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    panel.library_table.selectRow(0)
    panel.add()
    panel.autoplay.setChecked(True)
    panel.save()
    wait_until(qt_app, lambda: panel.requests == 0)
    report = {"phase": "Applied", "runtime_pid": 55, "runtime_session": "session",
              "active_version": "ue-" + source["id"], "desired_version": "ue-" + source["id"],
              "playback": {"revision": 1, "state": "Playing", "current_verification_job_id": check["id"],
                           "duration_seconds": 120, "elapsed_seconds": 10}}
    runtime_client.post("/infra/agent/heartbeat", headers=headers("show"), json=report).raise_for_status()
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.editor.isHidden()
    assert panel.stop_auto.isEnabled()
    assert all(not button.isEnabled() for button in panel.unsupported)
    assert not panel.pause_button.isEnabled()
    assert panel.progress.value() == 8
    assert "미보고" in panel.health["sensor"].text()
    panel.duration.setValue(40)
    assert not panel.stop_auto.isEnabled()  # 기존 편집을 운영 제어가 덮지 않음
    assert panel.live_timeline.duration_seconds == 120 and panel.timeline.duration_seconds == 40
    panel.load_plan(panel.server_config)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    panel.set_autoplay(False)
    assert panel.server_config["revision"] == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    panel.set_autoplay(False)
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.server_config["revision"] == 2
    assert not panel.server_config["plan"]["autoplay"]
    assert "동기화 대기" in panel.stages["synced"].text()
    panel.reviewer.setText("operator")
    panel.reason.setText("복구 시험")
    original = copy.deepcopy(panel.server_config)
    panel.operate_artwork(check["id"], "복구 작품 적용")
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.publication_pending and "Applied 보고 확인" not in panel.apply_status.text()
    assert panel.server_config == original
    # 완료된 이전 작업 이후 새 요청의 응답이 유실돼도 재시도 ID가 유지된다.
    import httpx

    seen = []
    panel.publication_terminal, panel.publication_pending = True, False
    panel.client.apply_show_artwork = lambda key, body: seen.append(body["request_id"]) or (
        (_ for _ in ()).throw(httpx.ReadTimeout("lost")))
    for _ in range(2):
        panel.ready = True
        panel.render_state()
        panel.operate_artwork(check["id"], "복구 작품 적용")
        wait_until(qt_app, lambda: panel.requests == 0)
    assert len(seen) == 2 and seen[0] == seen[1]
    panel.launch("refresh", lambda: (_ for _ in ()).throw(RuntimeError("offline")), panel.refreshed)
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.progress.value() == 0 and "미확인" in panel.stages["playing"].text()
