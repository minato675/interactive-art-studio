"""네이티브 피드백 화면: 시연 작품 전환·통신 실패·중복 클릭에도 작성 대상을 보존한다."""
import copy
import gc
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from sqlmodel import Session
from tests.test_artwork_feedback import seed_show
from tests.test_infra import infra_client  # noqa: F401 — pytest fixture 공유
from tests.test_operator import wait_until

from operator_app.client import ApiError, CentralClient
from operator_app.feedback import FeedbackPanel


def artwork(suffix="a"):
    return {"target": {"publication_job_id": f"publish-{suffix}", "node_id": "show", "artwork_id": f"art-{suffix}",
                       "version": "v1", "runtime_version": f"ue-source-{suffix}"},
            "title": f"숲의 호흡 {suffix}", "summary": "손이 지나가면 잎의 흐름이 번지는 작품", "template": "flow",
            "object_count": 4, "layer_count": 1, "interactions": ["move · push · fade"], "preview_only": False}


class FakeClient:
    def __init__(self):
        self.artwork = artwork()
        self.records, self.calls = [], []
        self.unavailable, self.unauthorized, self.fail_after_save = False, False, False

    def feedback_snapshot(self, offset=0):
        if self.unauthorized:
            raise ApiError(401, "private server details")
        if self.unavailable:
            raise OSError("private transport details")
        return {"current": {"observed_at": time.time(), "shows": [
            {"node_id": "show", "online": True, "phase": "Applied", "heartbeat_at": time.time(),
             "report_at": time.time(), "can_review": True, "reason": "확인", "artwork": copy.deepcopy(self.artwork)}]},
            "records": copy.deepcopy(self.records[offset:offset + 50])}

    def save_feedback(self, body):
        self.calls.append(copy.deepcopy(body))
        previous = next((r for r in self.records if r["request_id"] == body["request_id"]), None)
        if not previous:
            previous = {**copy.deepcopy(body), "id": f"note-{len(self.records)}", "revision": 1,
                        "created_at": time.time(), "updated_at": time.time(), "artwork_title": "숲의 호흡",
                        "preview_only": False}
            self.records.insert(0, previous)
        if self.fail_after_save:
            self.fail_after_save = False
            raise OSError("response was lost")
        return previous

    def change_feedback(self, feedback_id, enabled, revision):
        record = next(r for r in self.records if r["id"] == feedback_id)
        assert record["revision"] == revision
        record.update(enabled=enabled, revision=revision + 1)
        return copy.deepcopy(record)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qt_app):
    widget = FeedbackPanel(FakeClient())
    widget.show()
    widget.set_active(True)
    wait_until(qt_app, lambda: widget.requests == 0)
    yield widget
    widget.shutdown()
    wait_until(qt_app, lambda: widget.requests == 0)
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()


def test_real_api_client_reads_and_saves_feedback(infra_client):  # noqa: F811
    with Session(infra_client.app.state.engine) as session:
        target = seed_show(session)
    api = CentralClient(http=infra_client)
    assert api.feedback_snapshot()["current"]["shows"][0]["can_review"]
    record = api.save_feedback({"request_id": "qt-api", "target": target, "comment": "반응이 더 잘 보이면 좋겠습니다"})
    assert record["enabled"]
    assert not api.change_feedback(record["id"], False, record["revision"])["enabled"]
    assert len(api.feedback_snapshot()["records"]) == 1


def test_writing_target_stays_pinned_after_current_artwork_changes(panel, qt_app):
    panel.pin()
    panel.comment.setPlainText("객체 밀도를 낮춰주세요")
    target = copy.deepcopy(panel.draft["target"])
    panel.client.artwork = artwork("b")
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert "art-b" in panel.current_info.text() and "art-a" in panel.target_info.text()
    panel.pin()
    assert panel.draft["target"] == target and "먼저 저장" in panel.notice.text()
    panel.save()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.client.calls[0]["target"] == target
    assert panel.comment.toPlainText() == "" and panel.draft is None


def test_lost_response_retains_draft_and_identical_retry_does_not_duplicate(panel, qt_app):
    panel.pin()
    panel.comment.setPlainText("잔상이 더 오래 남으면 좋겠습니다")
    panel.client.fail_after_save = True
    panel.save()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert "잔상" in panel.comment.toPlainText() and panel.draft
    assert not panel.save_button.isEnabled()
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert len(panel.client.calls) == 1  # 조회 재개가 자동 POST를 만들지 않는다.
    panel.save()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert panel.client.calls[0]["request_id"] == panel.client.calls[1]["request_id"]
    assert len(panel.client.records) == 1 and panel.comment.toPlainText() == ""


def test_offline_and_auth_failure_keep_user_draft_but_do_not_claim_current_display(panel, qt_app):
    panel.pin()
    panel.comment.setPlainText("직접 입력한 의견")
    panel.client.unavailable = True
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert "미확인" in panel.current_info.text() and not panel.pin_button.isEnabled()
    assert "private" not in panel.connection.text()
    assert panel.comment.toPlainText() == "직접 입력한 의견"
    panel.client.unauthorized = True
    panel.refresh()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert not panel.running and not panel.timer.isActive() and panel.history.count() == 0
    assert not panel.save_button.isEnabled() and panel.comment.toPlainText() == "직접 입력한 의견"


def test_can_suspend_future_context_without_losing_another_draft(panel, qt_app):
    panel.pin()
    panel.comment.setPlainText("유지할 장점")
    panel.save()
    wait_until(qt_app, lambda: panel.requests == 0)
    panel.pin()
    panel.comment.setPlainText("아직 저장하지 않은 추가 의견")
    panel.history.setCurrentRow(0)
    panel.change_enabled()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert not panel.client.records[0]["enabled"]
    assert panel.comment.toPlainText() == "아직 저장하지 않은 추가 의견"
    assert "이후 새 기획" in panel.notice.text()


def test_double_click_during_pending_save_starts_only_one_mutation(panel, qt_app):
    entered, release = threading.Event(), threading.Event()
    original = panel.client.save_feedback

    def delayed(body):
        entered.set()
        release.wait(5)
        return original(body)

    panel.client.save_feedback = delayed
    panel.pin()
    panel.comment.setPlainText("명암 대비 유지")
    panel.save()
    try:
        wait_until(qt_app, entered.is_set)
        assert not panel.save_button.isEnabled() and not panel.comment.isEnabled()
        panel.save()
        assert "save" in panel.operations
    finally:
        release.set()
    wait_until(qt_app, lambda: panel.requests == 0)
    assert len(panel.client.calls) == 1
