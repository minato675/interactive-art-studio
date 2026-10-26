"""시연 대시보드의 실제 API 연결·편집 보존·명시적 게시와 재전송을 검사한다."""
import gc
import json
import os

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from tests.test_operator import dispose, wait_until
from tests.test_runtime_pipeline import runtime_client, verified  # noqa: F401 — fixture 공유
from tests.test_server_exchange import claim, finish, put
from tests.test_show_dashboard import plan_for

from operator_app.client import ApiError, CentralClient
from operator_app.dashboard import ShowDashboard
from operator_app.window import OperatorWindow


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(runtime_client, qt_app):  # noqa: F811
    widget = ShowDashboard(CentralClient(http=runtime_client))
    widget.show()
    widget.set_active(True)
    wait_until(qt_app, lambda: widget.ready and widget.requests == 0)
    yield widget
    widget.shutdown()
    wait_until(qt_app, lambda: widget.requests == 0)
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()


def test_default_tab_visible_and_can_be_explicitly_hidden(runtime_client, qt_app):  # noqa: F811
    for enabled in (False, True):
        win = OperatorWindow(CentralClient(http=runtime_client), show_dashboard=enabled)
        wait_until(qt_app, lambda win=win: win.busy == 0)
        assert win.tabs.count() == (4 if enabled else 3)
        assert (win.dashboard is not None) is enabled
        if enabled:
            assert win.tabs.tabText(3) == "시연 관리"
            assert win.show_tabs.tabText(0) == "로컬 아카이브"
            assert win.show_tabs.tabText(1) == "공용 런타임"
            assert win.show_tabs.widget(0) is win.native_artworks
            assert win.dashboard.requests == 0  # 선택 전에는 조회하지 않음
        dispose(win, qt_app)


def test_show_subtabs_only_poll_the_selected_source(runtime_client, qt_app, monkeypatch):  # noqa: F811
    from tests.test_operator_native_artworks import state

    client = CentralClient(http=runtime_client)
    monkeypatch.setattr(client, "native_artworks", state)
    win = OperatorWindow(client)
    wait_until(qt_app, lambda: win.busy == 0)
    win.tabs.setCurrentIndex(3)
    wait_until(qt_app, lambda: win.native_artworks.requests == 0)
    assert win.native_artworks.library.rowCount() == 28 and win.native_artworks.active
    assert not win.dashboard.active
    win.show_tabs.setCurrentIndex(1)
    wait_until(qt_app, lambda: win.dashboard.requests == 0)
    assert win.dashboard.active and not win.native_artworks.active
    win.tabs.setCurrentIndex(0)
    assert not win.dashboard.active and not win.native_artworks.active
    dispose(win, qt_app)


def test_empty_registry_report_and_no_fake_artworks(panel):
    assert panel.plan["items"] == [] and panel.library_table.rowCount() == 0
    assert panel.duration.value() == panel.timeline.duration_seconds == panel.plan["duration_seconds"] == 120
    assert "2분 00초" in panel.duration_label.text()
    assert not panel.apply_button.isEnabled()
    assert "미확인" in panel.current.text()


def test_real_plan_save_poll_does_not_replace_dirty_edits(panel, runtime_client, qt_app):  # noqa: F811
    _, verification = verified(runtime_client, "pass")
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    panel.library_table.selectRow(0)
    panel.add()
    assert panel.plan_table.item(0, 1).text() == "120"
    panel.duration.setValue(90)
    panel.edit_item(0, "repeat", 2)
    panel.loop.setChecked(False)
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.plan == plan_for(verification["id"]) and panel.dirty
    panel.save()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.revision == 1 and not panel.dirty
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.duration.value() == panel.timeline.duration_seconds == 90
    assert panel.plan_table.item(0, 1).text() == "90"
    panel.edit_item(0, "repeat", 3)
    runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 1, "plan": {}}).raise_for_status()
    panel.save()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.plan["items"][0]["repeat"] == 3 and panel.dirty
    assert "다른 운영자" in panel.notice.text()


def test_common_duration_changes_all_rows_and_reset_applies_to_new_rows(panel):
    # 화면 검토용 후보이며 실제 API로 등록·게시하지 않는다.
    first = {"verification_job_id": "first", "artwork_id": "art-first", "version": "v1",
             "catalog_version": "v1", "title": "첫 작품", "verdict": "pass"}
    second = {**first, "verification_job_id": "second", "artwork_id": "art-second", "title": "두 번째 작품"}
    panel.refreshed({"nodes": panel.nodes, "library": [first, second], "observed_at": 1,
                     "configuration": {"node_id": panel.node_id, "revision": 0, "plan": {}}, "publication": None})
    panel.library_table.selectRow(0)
    panel.add()
    panel.duration.setValue(180)
    panel.library_table.selectRow(1)
    panel.add()
    assert [panel.plan_table.item(row, 1).text() for row in range(2)] == ["180", "180"]
    assert panel.timeline.duration_seconds == 180 and panel.dirty
    panel.duration_reset.click()
    assert panel.duration.value() == panel.timeline.duration_seconds == 120
    assert [panel.plan_table.item(row, 1).text() for row in range(2)] == ["120", "120"]
    assert all("duration_seconds" not in item for item in panel.plan["items"])


def test_apply_cancel_and_lost_reply_use_one_job_until_actual_applied(panel, runtime_client, qt_app, monkeypatch):  # noqa: F811
    ue, _verification = verified(runtime_client, "pass")
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    panel.library_table.selectRow(0)
    panel.reviewer.setText("developer")
    panel.reason.setText("검증된 작품을 선택해 적용")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    panel.apply()
    assert claim(runtime_client, "show").json() is None
    original = panel.client.apply_show_artwork
    seen_ids = []

    def lost_once(verification_id, body):
        value = original(verification_id, body)
        seen_ids.append(body["request_id"])
        if len(seen_ids) == 1:
            raise httpx.ReadTimeout("reply lost")
        return value

    panel.client.apply_show_artwork = lost_once
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    panel.apply()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.publication is None
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    panel.library_table.selectRow(0)
    panel.apply()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert len(seen_ids) == 2 and seen_ids[0] == seen_ids[1]
    assert "Applied 보고 확인" not in panel.apply_status.text()
    publication = claim(runtime_client, "show").json()
    assert publication["id"] == panel.publication
    assert claim(runtime_client, "show").status_code == 409
    assert len([job for job in runtime_client.get("/infra/jobs").json() if job["kind"] == "ue_publish"]) == 1
    status = {"phase": "Applied", "desired_version": "ue-" + ue["id"], "active_version": "ue-" + ue["id"],
              "runtime_session": "test-session", "runtime_pid": 123}
    report = {"version": "ue-" + ue["id"], "source_job_id": ue["id"],
              "source_bundle_sha256": publication["inputs"][1]["result"]["source_bundle_sha256"], "status": status}
    ref = put(runtime_client, publication, "show", "applied-report.json", json.dumps(report).encode())
    finish(runtime_client, publication, "show", [ref]).raise_for_status()
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert "Applied 보고 확인" in panel.apply_status.text()
    assert panel.publication_terminal and not panel.publication_pending
    panel.apply()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert seen_ids[-1] != seen_ids[-2]  # 실제 완료 이후 같은 작품을 다시 요청하는 것은 새 작업
    assert panel.publication != publication["id"]


def test_offline_and_unauthorized_keep_draft_but_clear_current(panel, runtime_client, qt_app):  # noqa: F811
    verified(runtime_client, "pass")
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    panel.library_table.selectRow(0)
    panel.add()
    for error in (httpx.ConnectError("offline"), ApiError(401, "unauthorized")):
        panel.authorized = True
        panel.launch("refresh", lambda error=error: (_ for _ in ()).throw(error), panel.refreshed)
        wait_until(qt_app, lambda: panel.requests == 0)
        assert panel.dirty and len(panel.plan["items"]) == 1
        assert "미확인" in panel.current.text() and not panel.apply_button.isEnabled()
    assert not panel.authorized and panel.library == []


def test_removed_node_never_moves_dirty_plan_to_another_node(panel, runtime_client, qt_app):  # noqa: F811
    verified(runtime_client, "pass")
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    panel.library_table.selectRow(0)
    panel.add()
    snapshot = panel.client.show_dashboard("wrong")
    snapshot["nodes"] = [n for n in snapshot["nodes"] if n["node_id"] != "show"]
    panel.refreshed(snapshot)
    panel.controls()
    assert panel.node_id == "show" and panel.dirty
    assert len(panel.plan["items"]) == 1 and not panel.save_button.isEnabled()


def test_current_preview_is_not_labelled_as_official_approval(panel):
    value = {"nodes": [{"node_id": "show", "online": True, "can_review": True, "phase": "Applied",
                        "catalog_version": "v1", "report_at": 1,
                        "artwork": {"title": "검토 작품", "preview_only": True,
                                    "target": {"artwork_id": "art", "version": "v1", "runtime_version": "ue-source"}}}],
             "library": [], "configuration": {"node_id": "show", "revision": 0, "plan": {"items": [], "loop": True}},
             "observed_at": 1, "publication": None}
    panel.refreshed(value)
    assert "미리보기 / 정식 승인 아님" in panel.current.text()


def test_choose_save_remove_and_search_playlist(panel, runtime_client, qt_app):  # noqa: F811
    _, check = verified(runtime_client, "pass")
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    panel.library_table.selectRow(0)
    panel.add_button.click()
    panel.autoplay.setChecked(True)
    panel.save_button.click()
    wait_until(qt_app, lambda: panel.requests == 0)
    saved = runtime_client.get("/infra/show-dashboard").json()["configuration"]["plan"]
    assert saved["autoplay"] and saved["items"][0]["verification_job_id"] == check["id"]
    panel.search.setText("존재하지 않는 제목")
    panel.search_button.click()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.library_table.rowCount() == 0
    assert panel.plan_table.rowCount() == 1  # 검색 결과가 목록을 지우지 않는다
    panel.plan_table.selectRow(0)
    panel.remove.click()
    panel.save_button.click()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert runtime_client.get("/infra/show-dashboard").json()["configuration"]["plan"]["items"] == []
    panel.search.clear()
    panel.search_button.click()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.library_table.rowCount() == 1  # 재생목록 제거는 원본 삭제가 아니다
