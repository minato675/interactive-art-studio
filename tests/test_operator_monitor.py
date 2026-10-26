"""운영 현황 API와 네이티브 Qt 화면: 비동기 조회·낡은 결과·인증 실패를 검증한다."""
import gc
import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from tests.test_infra import infra_client, post_agent  # noqa: F401 — fixture 공유
from tests.test_operator import wait_until

from operator_app.client import ApiError, CentralClient
from operator_app.monitor import MonitorPanel, outcome


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class FakeClient:
    def monitor_snapshot(self, offset, limit):
        return {"jobs": [{"id": f"job-{offset}", "kind": "ue_verify", "status": "running", "artwork_id": "art-A",
                          "progress": 50, "created_at": 1700000000, "node_id": "p1"}],
                "nodes": [{"id": "show", "role": "show", "online": True, "heartbeat_at": 1700000000,
                           "show_report": {"phase": "Applied", "active_version": "v1", "desired_version": "v1"}}]}

    def job(self, job_id):
        return {"id": job_id, "attempts": [{"id": "attempt-1"}], "depends_on": ["background-1"]}

    def head_workflow(self, workflow_id):
        return {"id": workflow_id, "status": "held", "payload": {"_flow": {"hold": "검토 필요"}},
                "background_job": "bg-1", "asset_jobs": ["asset-1"],
                "decisions": [{"plan": {"decision": "hold", "reason": "스타일 검사 보류"}}]}


@pytest.fixture
def panel(qt_app):
    widget = MonitorPanel(FakeClient())
    widget.show()
    yield widget
    widget.shutdown()
    wait_until(qt_app, lambda: widget.requests == 0)
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()


def test_monitor_client_uses_existing_authenticated_read_apis(infra_client):  # noqa: F811 — imported pytest fixture
    post_agent(infra_client, "show", "heartbeat")
    api = CentralClient(http=infra_client)
    snapshot = api.monitor_snapshot()
    assert snapshot["jobs"] == [] and snapshot["nodes"][0]["id"] == "show"
    with pytest.raises(ApiError) as exc:
        api.head_workflow("missing")
    assert exc.value.status == 404
    infra_client.headers["X-Operator-Token"] = "bad"
    with pytest.raises(ApiError) as exc:
        api.monitor_snapshot()
    assert exc.value.status == 401


def test_monitor_lists_filters_details_and_head_without_mutating_work(panel, qt_app):
    panel.set_active(True)
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.job_table.rowCount() == 1 and "실행 중 1" in panel.summary.text()
    panel.search.setText("not-present")
    assert panel.job_table.rowCount() == 0
    panel.search.clear()
    panel.filter.setCurrentIndex(panel.filter.findData("failed"))
    assert panel.job_table.rowCount() == 0
    panel.filter.setCurrentIndex(0)
    panel.select_job(0, 0)
    panel.workflow_id.setText("head-1")
    panel.query_workflow()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert "attempt-1" in panel.detail.toPlainText() and "background-1" in panel.detail.toPlainText()
    assert "검토 필요" in panel.workflow.toPlainText() and "스타일 검사 보류" in panel.workflow.toPlainText()
    panel.change_page(1)
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.job_table.item(0, 2).text() == "job-200"
    assert "201~201" in panel.page.text()


def test_network_failure_marks_applied_as_last_report_not_current(panel, qt_app):
    panel.set_active(True)
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.node_table.item(0, 3).text() == "Applied"

    def unavailable(*args):
        raise OSError("secret raw transport details")

    panel.client.monitor_snapshot = unavailable
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert "현재 미확인" in panel.node_table.item(0, 3).text()
    assert "마지막 수신" in panel.connection.text()
    assert "secret" not in panel.connection.text()


def test_authentication_failure_clears_data_and_stops_polling(panel, qt_app):
    panel.refreshed(FakeClient().monitor_snapshot(0, 200))

    def unauthorized(*args):
        raise ApiError(401, "do not expose server details")

    panel.client.monitor_snapshot = unauthorized
    panel.set_active(True)
    wait_until(qt_app, lambda: panel.requests == 0)
    assert not panel.running and not panel.timer.isActive()
    assert panel.job_table.rowCount() == panel.node_table.rowCount() == 0
    assert "인증 실패" in panel.connection.text()


def test_pending_query_does_not_block_ui_and_late_response_cannot_restore_cleared_data(panel, qt_app):
    entered, release = threading.Event(), threading.Event()
    original = panel.client.monitor_snapshot

    def delayed(*args):
        entered.set()
        release.wait(5)
        return original(*args)

    panel.client.monitor_snapshot = delayed
    panel.set_active(True)
    try:
        wait_until(qt_app, entered.is_set)
        panel.refresh()  # 겹치는 갱신은 새 스레드로 쌓이지 않는다.
        assert panel.requests == 1
        panel.search.setText("responsive")
        assert panel.search.text() == "responsive"
        panel.toggle()
    finally:
        release.set()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.job_table.rowCount() == 0 and not panel.running


def test_preview_applied_is_not_formal_publication():
    job = {"kind": "ue_publish", "status": "succeeded", "result": {"phase": "Applied",
           "runtime_status": {"phase": "Applied"}}, "approval": {"scope": "display_preview_only"}}
    assert "정식 승인 아님" in outcome(job)
    job["result"]["runtime_status"]["phase"] = "Ready"
    assert "미확인" in outcome(job)
