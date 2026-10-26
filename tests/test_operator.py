"""운영 앱 화면: 실제 central(TestClient)에 붙여 자동·편집·전시 모드를 offscreen으로 확인한다.
PySide6가 없으면(uv sync --all-packages 전) 건너뛴다."""
import gc
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from tests.test_flow import make_client

from operator_app import window as window_module
from operator_app.client import CentralClient
from operator_app.window import OperatorWindow


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, qt_app):
    with make_client(tmp_path) as http:
        win = OperatorWindow(CentralClient(http=http))
        win.show()
        wait_until(qt_app, lambda: win.family_boxes and win.busy == 0)
        yield win
        dispose(win, qt_app)


def dispose(win, qt_app):
    """Qt 객체는 화면 스레드에서 지운다. 남겨 두면 다른 스레드의 GC가 지우다 프로세스가 죽는다."""
    win.close()
    wait_until(qt_app, lambda: win.busy == 0 and win.monitor.requests == 0 and win.feedback.requests == 0)
    QApplication.instance().removeEventFilter(win.switch)
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()


def wait_until(qt_app, condition, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        qt_app.processEvents()
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("시간 안에 끝나지 않음")


def idle(win):
    return lambda: win.busy == 0


def test_monitor_tab_uses_native_widget_without_replacing_creation(window, qt_app):
    assert window.tabs.tabText(0) == "작품 제작"
    assert window.tabs.tabText(1) == "운영 현황"
    window.tabs.setCurrentIndex(1)
    wait_until(qt_app, lambda: window.monitor.requests == 0)
    assert window.monitor.active and window.monitor.timer.isActive()
    assert "마지막 수신" in window.monitor.connection.text()
    window.tabs.setCurrentIndex(0)
    assert not window.monitor.timer.isActive()


def test_feedback_tab_does_not_require_an_existing_artwork(window, qt_app):
    assert window.tabs.tabText(2) == "작품 피드백"
    window.tabs.setCurrentIndex(2)
    wait_until(qt_app, lambda: window.feedback.requests == 0)
    assert "미확인" in window.feedback.current_info.text()
    assert not window.feedback.pin_button.isEnabled()
    window.tabs.setCurrentIndex(0)
    assert not window.feedback.timer.isActive()


@pytest.mark.parametrize("phase,active,expected", [("Staged", "A", "파일 캐시 준비됨"),
                                                 ("Ready", "A", "활성화 전"),
                                                 ("Applied", "B", "실제 활성화 확인")])
def test_show_report_is_displayed_separately_from_artwork_preparation(window, phase, active, expected):
    window.fill_runtime([{"id": "show-1", "role": "show", "online": True,
                          "show_report": {"phase": phase, "desired_version": "B", "active_version": active}}])
    assert expected in window.runtime_state.text() and f"활성 {active}" in window.runtime_state.text()
    window.fill_runtime([{"id": "show-1", "role": "show", "online": False,
                          "show_report": {"phase": phase, "desired_version": "B", "active_version": active}}])
    assert "마지막 보고" in window.runtime_state.text() and "실제 활성화 확인" not in window.runtime_state.text()


def test_auto_mode_submits_to_distributed_head(window, qt_app):
    window.prompt.setPlainText("바다")
    window.submit()
    wait_until(qt_app, idle(window))
    assert window.current is None and window.head_job["kind"] == "head_intake"
    assert "Head 접수 완료" in window.title.text()
    assert "PC2 적용 완료" not in window.title.text()
    assert not window.draft_group.isVisibleTo(window)
    assert window.client.artworks() == []
    assert window.history.count() == 1


def test_head_submit_retry_after_lost_response_does_not_duplicate(window, qt_app, monkeypatch):
    submit = window.client.submit_head
    attempts = []
    monkeypatch.setattr(window, "show_error", lambda error: None)

    def lost_once(body, request_id):
        attempts.append(request_id)
        result = submit(body, request_id)
        if len(attempts) == 1:
            raise RuntimeError("response lost")
        return result

    monkeypatch.setattr(window.client, "submit_head", lost_once)
    window.prompt.setPlainText("금빛 먼지")
    window.submit()
    wait_until(qt_app, idle(window))
    window.submit()
    wait_until(qt_app, idle(window))
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert len(window.client.head_intakes()) == 1


def test_head_candidate_is_not_displayed_as_applied(window):
    job = {"id": "intake123", "status": "succeeded"}
    window.show_head(job, {"id": "workflow123", "status": "waiting_publication", "jobs": []})
    assert "PC2 적용 확인 중" in window.title.text()
    assert "PC2 적용 완료" not in window.title.text()
    window.show_head(job, {"id": "workflow123", "status": "completed", "jobs": []})
    assert "PC2 적용 완료" in window.title.text()
    assert not window.head_timer.isActive()


def test_window_title_is_app_name(window):
    assert window.windowTitle() == "Artomation"


def test_shift_tab_switches_mode(window):
    assert window.mode == "auto"
    QTest.keyClick(window.prompt, Qt.Key_Tab, Qt.ShiftModifier)
    assert window.mode == "edit" and "편집 모드" in window.submit_button.text()
    QTest.keyClick(window.history, Qt.Key_Backtab)
    assert window.mode == "auto"


def test_edit_mode_shows_prefilled_draft_and_sends_only_changes(window, qt_app):
    window.toggle_mode()
    window.prompt.setPlainText("바다")
    window.submit()
    wait_until(qt_app, idle(window))
    assert window.current.status == "awaiting" and window.draft_group.isVisibleTo(window)
    assert window.draft_families["fluid"].isChecked() and window.draft_subject.text() == ""
    assert "AI 제안" in window.draft_labels["subject"].text()
    assert window.draft_changes() == {}   # 아무것도 안 바꾸면 보낼 것이 없다
    mood = window.draft_chips["mood"]
    mood.setCurrentIndex(mood.findData("mysterious"))
    assert window.draft_changes() == {"mood": "mysterious"}
    window.send_draft()
    wait_until(qt_app, idle(window))
    assert window.current.status == "prepared"
    confirmed = next(s.detail for s in window.current.steps if s.name == "확정")
    assert confirmed["changed"] == ["mood"]
    assert not window.draft_group.isVisibleTo(window)


def test_draft_needs_title_and_family(window, qt_app):
    window.toggle_mode()
    window.prompt.setPlainText("숲")
    window.submit()
    wait_until(qt_app, idle(window))
    assert window.draft_button.isEnabled()
    for box in window.draft_families.values():
        box.setChecked(False)
    assert not window.draft_button.isEnabled()


def test_rejected_confirm_keeps_draft(window, qt_app, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warned.append(args[2]))
    window.toggle_mode()
    window.prompt.setPlainText("바다")
    window.submit()
    wait_until(qt_app, idle(window))
    window.draft_families["flock"].setChecked(False)
    window.draft_families["paint"].setChecked(True)
    window.draft_families["fluid"].setChecked(True)   # 그리기·유체는 한 작품에 하나만 → central이 422
    assert window.draft_changes() == {"families": ["paint", "fluid"]}
    window.send_draft()
    wait_until(qt_app, idle(window))
    assert warned and "422" in warned[0]
    assert window.draft_group.isVisibleTo(window) and window.current.status == "awaiting"


def test_exhibition_checkbox_toggles_central_feed(window, qt_app):
    wait_until(qt_app, idle(window))
    assert not window.show_check.isChecked() and window.show_state.text() == "꺼짐"
    window.show_check.setChecked(True)
    wait_until(qt_app, idle(window))
    assert window.client.exhibition_feed()["enabled"] is True   # 앱이 아니라 중앙 서버가 순환을 돈다
    assert window.show_state.text() == "켜짐 · 진행 중 0/10"
    assert window.client.artworks() == []   # 동기 전시 요청을 보내지 않는다
    window.show_check.setChecked(False)
    wait_until(qt_app, idle(window))
    assert window.client.exhibition_feed()["enabled"] is False and window.show_state.text() == "꺼짐"
    window.client.set_exhibition_feed(True)   # 다른 운영 PC에서 켠 상태도 새로 고침으로 보인다
    window.load_history()
    wait_until(qt_app, idle(window))
    assert window.show_check.isChecked()


def test_exhibition_toggle_failure_restores_server_state(window, qt_app, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warned.append(args[2]))
    wait_until(qt_app, idle(window))

    def refuse(enabled):
        raise RuntimeError("중앙 서버 연결 실패")

    monkeypatch.setattr(window.client, "set_exhibition_feed", refuse)
    window.show_check.setChecked(True)
    wait_until(qt_app, lambda: window.busy == 0 and warned)
    wait_until(qt_app, idle(window))
    assert "연결 실패" in warned[0] and not window.show_check.isChecked()


def test_operator_does_not_import_central():
    source = Path(window_module.__file__).parent
    for path in source.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "import central" not in text and "from central" not in text, path


def test_confirm_locks_form_until_result(window, qt_app):
    window.toggle_mode()
    window.prompt.setPlainText("숲")
    window.submit()
    wait_until(qt_app, idle(window))
    window.send_draft()
    assert not window.draft_group.isEnabled() and not window.draft_button.isEnabled()
    window.draft_title.setText("다른 제목")   # 진행 중에 바꿔도 버튼이 다시 켜지지 않는다
    assert not window.draft_button.isEnabled()
    wait_until(qt_app, idle(window))
    assert window.current.status == "prepared" and window.draft_group.isEnabled()


def test_draft_arriving_before_families_is_redrawn(tmp_path, qt_app):
    with make_client(tmp_path) as http:
        client = CentralClient(http=http)
        win = OperatorWindow(client)
        assert not win.submit_button.isEnabled()   # 목록을 받기 전에는 제출 불가
        artwork = client.draft({"theme": "바다"})
        win.show_artwork(artwork)   # 목록보다 초안이 먼저 옴
        wait_until(qt_app, lambda: win.family_boxes and win.busy == 0)
        assert win.draft_families["fluid"].isChecked() and win.submit_button.isEnabled()
        dispose(win, qt_app)


def test_asset_review_card_shows_thumbnails_and_approves(window, qt_app):
    window.prompt.setPlainText("바다 속 고래")   # 규칙이 '바다'를 읽어 배경이 필요한 구성이 되게
    window.review_assets.setChecked(True)
    assert window.request_body()["review_assets"] is True and "review_final" not in window.request_body()
    window.show_artwork(window.client.create(window.request_body()))  # 기존 기록의 검수 화면 호환
    wait_until(qt_app, idle(window))
    assert window.current.status == "asset_review" and window.gate_group.isVisibleTo(window)
    assert "소재 검수 대기" in window.title.text()
    thumbs = [window.gate_thumbs.itemAt(i).widget() for i in range(window.gate_thumbs.count())]
    thumbs = [thumb for thumb in thumbs if thumb]
    assert any(not thumb.pixmap().isNull() for thumb in thumbs)        # 배경 그림
    assert any("이미지 아님" in thumb.text() for thumb in thumbs)       # 실루엣 JSON
    window.send_review(True)
    assert not window.gate_group.isEnabled()   # 결과가 올 때까지 잠김
    wait_until(qt_app, idle(window))
    assert window.current.status == "prepared" and not window.gate_group.isVisibleTo(window)


def test_reopening_review_before_thumbnail_arrives_is_safe(window, qt_app, monkeypatch):
    errors = []
    monkeypatch.setattr("sys.excepthook", lambda *args: errors.append(args[1]))
    monkeypatch.setattr("sys.unraisablehook", lambda info: errors.append(info.exc_value))
    window.prompt.setPlainText("바다")
    window.review_assets.setChecked(True)
    window.show_artwork(window.client.create(window.request_body()))
    wait_until(qt_app, idle(window))
    window.show_artwork(window.current)   # 썸네일 요청을 띄운 뒤
    window.show_artwork(window.current)   # 도착하기 전에 같은 작품을 다시 그림
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)   # 앞 카드의 칸을 실제로 지운다
    wait_until(qt_app, idle(window))
    assert errors == []
    thumbs = [window.gate_thumbs.itemAt(i).widget() for i in range(window.gate_thumbs.count())]
    assert any(thumb and not thumb.pixmap().isNull() for thumb in thumbs)


def test_review_options_stay_with_exhibition_and_head_final_request(window, qt_app, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warned.append(args[2]))
    window.show_check.setChecked(True)
    wait_until(qt_app, idle(window))
    # 검수 체크박스는 운영자가 직접 제출하는 요청에만 쓰인다. 자동 순환 작품은 늘 자동 게시다.
    assert window.review_assets.isEnabled() and window.review_final.isEnabled()
    window.prompt.setPlainText("숲")
    window.review_final.setChecked(True)
    window.submit()
    wait_until(qt_app, idle(window))
    assert window.head_job["kind"] == "head_intake" and not warned
    assert window.current is None and window.request_body()["review_final"] is True


def test_final_review_card_points_to_td_candidate_and_rejects(window, qt_app):
    window.prompt.setPlainText("숲")
    window.review_final.setChecked(True)
    window.show_artwork(window.client.create(window.request_body()))
    wait_until(qt_app, idle(window))
    assert window.current.status == "final_review"
    assert "개발 TD 후보 버전" in window.gate_info.text()
    assert "실제 전시 적용은 별도 확인" in window.gate_info.text()
    window.gate_note.setText("발자국이 너무 빠름")
    window.send_review(False)
    wait_until(qt_app, idle(window))
    assert window.current.status == "held"
    assert any("발자국이 너무 빠름" in str(s.detail) for s in window.current.steps)


def test_step_summary_shows_the_input_agent_decision():
    from contracts.schemas import Step

    decision = "메인 캐릭터 없음 → 드러내기 없음으로 기획"
    assert window_module.step_summary(Step(name="작품 명세", detail={"decision": decision, "spec": {}})) == decision
