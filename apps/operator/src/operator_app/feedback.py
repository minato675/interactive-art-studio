"""시연 작품에 고정된 피드백 편집. 중앙 저장 뒤 다음 Head가 참고한다."""
import copy
import json
import threading
import uuid

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .client import ApiError
from .monitor import _Bridge, error_text, timestamp

CATEGORIES = {"overall": "전체", "look": "색·재질", "composition": "구도·배치",
              "interaction": "인터랙션", "performance": "성능"}
INTENTIONS = {"improve": "개선할 점", "keep": "유지할 장점"}


def label(text):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    return widget


class FeedbackPanel(QWidget):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.client, self.offset = client, 0
        self.shows, self.records, self.draft = [], [], None
        self.active, self.closed, self.running, self.server_ok, self.saving = False, False, True, False, False
        self.requests, self.epoch, self.operations = 0, 0, {}
        self.signature, self.request_id = None, None
        self.bridge = _Bridge(self)
        self.bridge.call.connect(lambda fn: fn())
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.connection, self.refresh_button = label("탭을 선택하면 현재 시연 정보를 조회합니다"), QPushButton("지금 갱신")
        top.addWidget(self.connection, 1)
        top.addWidget(self.refresh_button)
        layout.addLayout(top)
        current_group = QGroupBox("현재 시연 작품")
        current_layout = QVBoxLayout(current_group)
        choose = QHBoxLayout()
        self.node, self.pin_button = QComboBox(), QPushButton("이 작품에 피드백 작성")
        choose.addWidget(self.node, 1)
        choose.addWidget(self.pin_button)
        current_layout.addLayout(choose)
        self.current_info = label("현재 시연 작품 미확인")
        current_layout.addWidget(self.current_info)
        layout.addWidget(current_group)
        lower = QSplitter(Qt.Horizontal)
        form_group = QGroupBox("Head에 전달할 피드백")
        form_layout = QVBoxLayout(form_group)
        self.target_info = label("먼저 시연 작품을 확인하고 작성 대상을 선택하세요.")
        form_layout.addWidget(self.target_info)
        form = QFormLayout()
        self.intention, self.category = QComboBox(), QComboBox()
        for key, value in INTENTIONS.items():
            self.intention.addItem(value, key)
        for key, value in CATEGORIES.items():
            self.category.addItem(value, key)
        form.addRow("의견", self.intention)
        form.addRow("분류", self.category)
        form_layout.addLayout(form)
        self.comment = QPlainTextEdit()
        self.comment.setPlaceholderText("관찰한 문제나 유지할 장점을 구체적으로 적어주세요.\n예: 객체가 과밀해 손 반응이 묻힙니다.")
        form_layout.addWidget(self.comment, 1)
        self.length = label("0 / 1,000자")
        form_layout.addWidget(self.length)
        self.enabled = QCheckBox("다음 작품의 Head 기획에 참고")
        self.enabled.setChecked(True)
        form_layout.addWidget(self.enabled)
        self.save_button = QPushButton("피드백 저장")
        self.save_button.setObjectName("draftAction")
        form_layout.addWidget(self.save_button)
        form_layout.addWidget(label("기획에 참고할 기억을 남깁니다. 모델 재학습이나 현재 작품의 즉시 수정은 수행하지 않습니다."))
        lower.addWidget(form_group)
        history_group = QGroupBox("피드백 기록 · 전체 작품")
        history_layout = QVBoxLayout(history_group)
        self.history = QListWidget()
        self.history.setWordWrap(True)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        history_layout.addWidget(self.history, 1)
        self.record_detail = QPlainTextEdit()
        self.record_detail.setReadOnly(True)
        self.record_detail.setMaximumHeight(150)
        history_layout.addWidget(self.record_detail)
        controls = QHBoxLayout()
        self.previous, self.next_button, self.page = QPushButton("이전"), QPushButton("다음"), label("최근 50개씩")
        controls.addWidget(self.previous)
        controls.addWidget(self.next_button)
        controls.addWidget(self.page, 1)
        history_layout.addLayout(controls)
        self.change_button = QPushButton("기획 참고 중지 / 재개")
        history_layout.addWidget(self.change_button)
        lower.addWidget(history_group)
        lower.setSizes([550, 650])
        layout.addWidget(lower, 1)
        self.notice = label("활성 피드백 최신 8개를 다음 새 기획부터 참고합니다. 작성 중 작품이 바뀌어도 대상은 고정됩니다.")
        layout.addWidget(self.notice)
        self.refresh_button.clicked.connect(self.resume)
        self.node.currentIndexChanged.connect(self.render_current)
        self.pin_button.clicked.connect(self.pin)
        self.comment.textChanged.connect(self.update_controls)
        self.save_button.clicked.connect(self.save)
        self.history.currentItemChanged.connect(self.show_record)
        self.change_button.clicked.connect(self.change_enabled)
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next_button.clicked.connect(lambda: self.change_page(1))
        self.update_controls()

    def set_active(self, active):
        self.active = active
        if active and self.running:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()

    def shutdown(self):
        self.closed = True
        self.epoch += 1
        self.timer.stop()

    def resume(self):
        self.running = True
        self.set_active(self.active)

    def launch(self, kind, function, success, failure=None):
        if self.closed or not self.running or kind in self.operations:
            return
        token, epoch = object(), self.epoch
        self.operations[kind] = token
        self.requests += 1

        def worker():
            value, error = None, None
            try:
                value = function()
            except Exception as exc:  # noqa: BLE001 — 원시 오류·토큰 대신 안전한 문구를 표시
                error = exc
            try:
                self.bridge.call.emit(lambda: self.done(kind, token, epoch, value, error, success, failure))
            except RuntimeError:
                pass  # 창이 닫혀 Qt 객체가 먼저 삭제됨
        threading.Thread(target=worker, daemon=True).start()

    def done(self, kind, token, epoch, value, error, success, failure):
        self.requests -= 1
        if self.closed or epoch != self.epoch or self.operations.get(kind) is not token:
            return
        del self.operations[kind]
        if error:
            if isinstance(error, ApiError) and error.status == 401:
                self.running = False
                self.timer.stop()
                self.shows, self.records = [], []
                self.history.clear()
                self.record_detail.clear()
            (failure or self.failed)(error)
        else:
            success(value)
        self.update_controls()

    def refresh(self):
        if self.active:
            offset = self.offset
            self.launch("refresh", lambda: self.client.feedback_snapshot(offset),
                        lambda value: self.refreshed(value, offset))

    def refreshed(self, value, offset=0):
        self.server_ok = True
        selected_node = self.node.currentData()
        self.shows, self.records = value["current"]["shows"], value["records"]
        self.node.blockSignals(True)
        self.node.clear()
        for show in self.shows:
            self.node.addItem(show["node_id"], show["node_id"])
        preferred = selected_node or next((s["node_id"] for s in self.shows if s["can_review"]), None)
        index = self.node.findData(preferred)
        if index >= 0:
            self.node.setCurrentIndex(index)
        self.node.blockSignals(False)
        self.connection.setText("마지막 수신 " + timestamp(value["current"]["observed_at"]))
        self.render_current()
        self.render_history()
        self.previous.setEnabled(offset > 0)
        self.next_button.setEnabled(len(self.records) == 50)
        self.page.setText(f"{offset + 1}~{offset + len(self.records)}" if self.records else "피드백 없음")

    def failed(self, error):
        self.server_ok = False
        self.connection.setText(error_text(error) + " · 작성 내용은 유지됩니다")
        self.current_info.setText("현재 시연 작품 미확인 · 서버 조회가 필요합니다")
        self.pin_button.setEnabled(False)
        self.update_controls()

    def selected_show(self):
        return next((s for s in self.shows if s["node_id"] == self.node.currentData()), None)

    def render_current(self):
        show = self.selected_show()
        self.pin_button.setEnabled(bool(self.server_ok and show and show["can_review"] and not self.saving))
        if not show or not show["can_review"]:
            self.current_info.setText("현재 시연 작품 미확인 · " + (show["reason"] if show else "시연 노드 보고 대기"))
            return
        a, t = show["artwork"], show["artwork"]["target"]
        self.current_info.setText(
            f"{a['title']} · {'미리보기 / 정식 승인 아님' if a['preview_only'] else 'Applied 게시 근거 확인'}\n"
            f"작품 {t['artwork_id']} / {t['version']} · Runtime {t['runtime_version']}\n"
            f"게시 작업 {t['publication_job_id']}\n"
            f"{a['summary']}\n템플릿 {a['template']} · 객체 {a['object_count']}개 / 층 {a['layer_count']}개 · "
            f"반응 {', '.join(a['interactions']) or '미등록'}\n시연 보고 {timestamp(show['report_at'])}")

    def pin(self):
        show = self.selected_show()
        if not self.server_ok or not show or not show["can_review"] or self.saving:
            return
        artwork = show["artwork"]
        if self.draft and self.comment.toPlainText().strip() and self.draft["target"] != artwork["target"]:
            self.notice.setText("작성 중인 피드백을 먼저 저장하거나 내용을 비운 뒤 대상을 바꿔주세요.")
            return
        self.draft = copy.deepcopy(artwork)
        t = self.draft["target"]
        self.target_info.setText(f"작성 대상 고정: {self.draft['title']}\n{t['artwork_id']} / {t['version']} · {t['node_id']}")
        self.update_controls()
        self.comment.setFocus()

    def update_controls(self):
        available = self.draft is not None and not self.saving
        for widget in (self.comment, self.intention, self.category, self.enabled):
            widget.setEnabled(available)
        count = len(self.comment.toPlainText().strip())
        self.length.setText(f"{count:,} / 1,000자")
        self.save_button.setEnabled(bool(available and self.server_ok and self.running and 0 < count <= 1000))
        self.change_button.setEnabled(bool(self.server_ok and self.running and self.history.currentItem()
                                           and "change" not in self.operations))

    def save(self):
        if not self.save_button.isEnabled():
            return
        body = {"target": self.draft["target"], "intention": self.intention.currentData(),
                "category": self.category.currentData(), "comment": self.comment.toPlainText().strip(),
                "enabled": self.enabled.isChecked()}
        signature = json.dumps(body, sort_keys=True)
        if signature != self.signature:
            self.signature, self.request_id = signature, uuid.uuid4().hex
        body["request_id"] = self.request_id
        self.saving = True
        self.update_controls()

        def saved(value):
            self.saving, self.server_ok = False, True
            self.draft, self.signature = None, None
            self.comment.clear()
            self.target_info.setText("저장 완료 · 다음 작성 대상을 선택하세요")
            self.notice.setText("저장 완료 · 다음 새 기획부터 참고합니다" if value["enabled"] else "기록 저장 완료 · 기획 참고는 꺼져 있습니다")
            self.refresh()

        def failed(error):
            self.saving = False
            self.failed(error)
            self.notice.setText("저장 확인 실패 · 같은 내용으로 재시도하면 동일 요청 ID를 사용합니다.")
        self.launch("save", lambda: self.client.save_feedback(body), saved, failed)

    def render_history(self):
        selected = self.history.currentItem()
        selected_id = selected.data(Qt.UserRole)["id"] if selected else None
        self.history.clear()
        for r in self.records:
            t = r["target"]
            item = QListWidgetItem(f"{'참고 중' if r['enabled'] else '참고 중지'} · {INTENTIONS[r['intention']]} · "
                                   f"{CATEGORIES[r['category']]} · {timestamp(r['created_at'])}\n"
                                   f"{r['artwork_title']} ({t['artwork_id']} / {t['version']}) · {r['comment'][:65]}")
            item.setData(Qt.UserRole, r)
            item.setToolTip(r["comment"])
            self.history.addItem(item)
            if r["id"] == selected_id:
                self.history.setCurrentItem(item)
        self.update_controls()

    def show_record(self, item, previous=None):
        if item:
            r = item.data(Qt.UserRole)
            self.record_detail.setPlainText(r["comment"] + f"\n\n출처: {r['target']['artwork_id']} / {r['target']['version']}"
                                            f"\n의견 ID: {r['id']} · revision {r['revision']}")
            self.change_button.setText("이후 기획 참고 중지" if r["enabled"] else "이후 기획에 다시 참고")
        else:
            self.record_detail.clear()
        self.update_controls()

    def change_enabled(self):
        item = self.history.currentItem()
        if not item or not self.change_button.isEnabled():
            return
        record = item.data(Qt.UserRole)

        def changed(value):
            self.records = [value if r["id"] == value["id"] else r for r in self.records]
            self.render_history()
            self.notice.setText("참고 여부 변경 완료 · 이후 새 기획부터 적용됩니다")
        self.launch("change", lambda: self.client.change_feedback(record["id"], not record["enabled"], record["revision"]), changed)
        self.update_controls()

    def change_page(self, direction):
        if "refresh" in self.operations:
            return
        self.offset = max(0, self.offset + direction * 50)
        self.refresh()
