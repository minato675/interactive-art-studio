"""실제 시연 상태·수동 적용·중앙 구성. 타임라인은 계획이며 실제 재생 위치를 추정하지 않는다."""
import copy
import json
import threading
import time
import uuid

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from contracts.show_dashboard import ShowItem, ShowPlan

from .client import ApiError
from .monitor import _Bridge, error_text, timestamp
from .show_state import clock_text, state
from .style import QSS

SHOW_STYLE = QSS + """
QLabel#dashboardTitle { font-size:22px; font-weight:700; }
QLabel#timecode { font-size:20px; color:#1A73E8; font-weight:700; }
QLabel[tone="good"] { color:#23734D; }
QLabel[tone="wait"] { color:#9B6215; }
QLabel[tone="error"] { color:#B33C3C; }
QLabel#dashboardNote { color:#766F7D; }
QLabel#preview { background:#F2ECE8; border:1px solid #E9DFDA; border-radius:10px; }
QProgressBar { border:0; border-radius:5px; background:#EEE8E3; color:#342F3B; min-height:20px; text-align:center; }
QProgressBar::chunk { background:#A8C8F5; border-radius:5px; }
QScrollArea { border:0; background:#F8F4F1; }
QSpinBox { background:white; color:#342F3B; padding:5px; border:1px solid #DDD3CF; border-radius:6px; }
QPushButton#applyShow { background:#1A73E8; color:white; }
QPushButton#applyShow:disabled { background:#DDD7D4; color:#928B8A; }
"""


def label(text, name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return widget


class PlanTimeline(QWidget):
    def __init__(self):
        super().__init__()
        self.items = []
        self.current_id = None
        self.duration_seconds = ShowPlan().duration_seconds
        self.setMinimumHeight(92)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#FFFCFA"))
        painter.setPen(QColor("#766F7D"))
        active = [item for item in self.items if item["enabled"]]
        total = sum(self.duration_seconds * item["repeat"] for item in active)
        if not total:
            painter.drawText(self.rect(), Qt.AlignCenter, "재생 구성이 비어 있습니다 · 작품을 추가하세요")
            return
        width, x = self.width() - 24, 12.0
        for index, item in enumerate(active):
            span = width * self.duration_seconds * item["repeat"] / total
            painter.fillRect(int(x), 35, max(1, int(span) - 2), 55, QColor("#1A73E8" if item["verification_job_id"] == self.current_id else "#E5D9EB" if index % 2 == 0 else "#DCE7EF"))
            if span > 65:
                painter.setPen(QColor("white" if item["verification_job_id"] == self.current_id else "#342F3B"))
                painter.drawText(int(x) + 7, 58, f"{index + 1:02d} · {self.duration_seconds}초")
                painter.drawText(int(x) + 7, 78, f"× {item['repeat']}")
            x += span
        painter.setPen(QColor("#766F7D"))
        painter.drawText(12, 22, "구성 시작 00:00")
        painter.drawText(max(12, self.width() - 165), 22, f"총 {total // 60:02d}:{total % 60:02d}")


class ShowDashboard(QWidget):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client, self.node_id = client, None
        self.library, self.nodes, self.plan, self.revision = [], [], ShowPlan().model_dump(), 0
        self.library_offset, self.library_search, self.artworks = 0, "", {}
        self.observed_at = 0
        self.received_at = time.monotonic()
        self.server_config, self.view_state = None, {}
        self.preview_key, self.last_log = None, None
        self.preview_loaded_key = None
        self.operation_signature, self.operation_request_id = None, None
        self.dirty, self.ready, self.active, self.closed, self.authorized = False, False, False, False, True
        self.requests, self.operations, self.publication = 0, set(), None
        self.publication_pending, self.publication_terminal = False, False
        self.request_signature, self.request_id = None, None
        self.bridge = _Bridge(self)
        self.bridge.call.connect(lambda fn: fn())
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.expiry_timer = QTimer(self)
        self.expiry_timer.setInterval(1000)
        self.expiry_timer.timeout.connect(self.expire_report)
        self.setObjectName("page")
        self.setStyleSheet(SHOW_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        header = QHBoxLayout()
        header.addWidget(label("시연 관리 · 공용 런타임", "dashboardTitle"), 1)
        self.node = QComboBox()
        self.node.setMinimumWidth(160)
        self.refresh_button = QPushButton("상태 새로고침")
        header.addWidget(self.node)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        self.connection = label("탭을 열면 중앙 서버에 연결합니다", "helper")
        layout.addWidget(self.connection)
        self.live_notice = label("실제 PC2 보고를 기다립니다", "helper")
        layout.addWidget(self.live_notice)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("page")
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(10)
        self.health = {}
        health_row = QHBoxLayout()
        for key, title in (("controller", "Controller"), ("runtime", "UE Runtime"),
                           ("sensor", "TouchDesigner · Sensor"), ("pc2", "PC2 Agent")):
            card = QGroupBox(title)
            card_layout = QVBoxLayout(card)
            self.health[key] = label("보고 대기", "helper")
            card_layout.addWidget(self.health[key])
            health_row.addWidget(card, 1)
        body.addLayout(health_row)
        self.stages = {}
        stages_row = QHBoxLayout()
        for key, title in (("saved", "1  중앙 저장"), ("synced", "2  PC2 동기화"),
                           ("applied", "3  UE 적용"), ("playing", "4  실제 재생")):
            card = QGroupBox(title)
            card_layout = QVBoxLayout(card)
            self.stages[key] = label("미확인")
            card_layout.addWidget(self.stages[key])
            stages_row.addWidget(card, 1)
        body.addLayout(stages_row)
        main_row = QHBoxLayout()
        current_group = QGroupBox("현재 시연 · PC2 보고 기준")
        current_layout = QVBoxLayout(current_group)
        current_header = QHBoxLayout()
        self.current = label("현재 작품 미확인", "sectionTitle")
        current_header.addWidget(self.current, 1)
        self.time_label = label("--:-- / --:--", "timecode")
        current_header.addWidget(self.time_label)
        current_layout.addLayout(current_header)
        self.preview = label("작품 이미지 보고 대기", "preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(145)
        self.preview.setMinimumWidth(280)
        current_layout.addWidget(self.preview)
        current_layout.addWidget(label("배경/소재 원본 미리보기 · 실시간 전시 화면 아님", "helper"))
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFormat("재생 보고 대기")
        current_layout.addWidget(self.progress)
        self.next_status = label("다음 작품 · 준비 상태 미확인", "helper")
        current_layout.addWidget(self.next_status)
        main_row.addWidget(current_group, 3)
        operations = QGroupBox("전시 운영")
        operations_layout = QVBoxLayout(operations)
        self.operations_note = label("저장된 구성 기준 · 출력 경로 현장 확인", "helper")
        operations_layout.addWidget(self.operations_note)
        self.start_auto = QPushButton("자동 전환 시작")
        self.stop_auto = QPushButton("자동 전환 멈춤 · 화면 유지")
        self.next_button = QPushButton("다음 작품 적용")
        self.reapply_button = QPushButton("현재 작품 재적용")
        for button in (self.start_auto, self.stop_auto, self.next_button, self.reapply_button):
            operations_layout.addWidget(button)
        self.unsupported = []
        runtime_row = QHBoxLayout()
        for text in ("UE 실행", "UE 종료", "UE 재시작"):
            button = QPushButton(text)
            button.setEnabled(False)
            button.setToolTip("공용 런타임의 원격 프로세스 제어 계약이 아직 없습니다. PC2 실행기로 관리합니다.")
            runtime_row.addWidget(button)
            self.unsupported.append(button)
        operations_layout.addLayout(runtime_row)
        self.pause_button = QPushButton("영상 일시정지 · 미지원")
        self.pause_button.setEnabled(False)
        self.pause_button.setToolTip("자동 전환 멈춤은 영상 일시정지가 아닙니다.")
        operations_layout.addWidget(self.pause_button)
        operations_layout.addWidget(label("UE 원격 제어 미연결 · 기존 PC2 자동 복구는 유지", "helper"))
        recovery_row = QHBoxLayout()
        self.recovery_choice = QComboBox()
        self.recovery_choice.setMinimumWidth(140)
        self.recovery_button = QPushButton("복구 작품 적용")
        recovery_row.addWidget(self.recovery_choice, 1)
        recovery_row.addWidget(self.recovery_button)
        operations_layout.addLayout(recovery_row)
        operations_layout.addWidget(label("승인자·사유는 아래 적용 승인 정보에 입력합니다.", "helper"))
        main_row.addWidget(operations, 2)
        body.addLayout(main_row)
        live_group = QGroupBox("실행 타임라인 · 저장된 목록 / 실제 보고")
        live_layout = QVBoxLayout(live_group)
        self.live_timeline = PlanTimeline()
        self.live_timeline.setMinimumHeight(96)
        live_layout.addWidget(self.live_timeline)
        self.execution_table = table(["순서 · 작품", "파일 준비", "실제 실행"])
        self.execution_table.setMaximumHeight(155)
        live_layout.addWidget(self.execution_table)
        self.overall = label("전체 진행률·예상 종료 미확인", "helper")
        live_layout.addWidget(self.overall)
        body.addWidget(live_group)
        self.editor_toggle = QPushButton("재생목록 편집 열기 · 라이브러리 / 순서 / 시간")
        self.editor_toggle.setCheckable(True)
        body.addWidget(self.editor_toggle)
        self.editor = QWidget()
        editor_layout = QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        timeline_group = QGroupBox("구성 타임라인 · 저장 전 편집 미리보기")
        timeline_layout = QVBoxLayout(timeline_group)
        self.timeline = PlanTimeline()
        timeline_layout.addWidget(self.timeline)
        editor_layout.addWidget(timeline_group)
        library_group = QGroupBox("검증 작품 라이브러리 · 과거 작품 검색 / 선택")
        library_layout = QVBoxLayout(library_group)
        library_controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("작품 제목 또는 ID 검색")
        self.search.setMaxLength(100)
        self.search_button = QPushButton("검색")
        self.previous_page, self.next_page = QPushButton("이전"), QPushButton("다음")
        for widget in (self.search, self.search_button, self.previous_page, self.next_page):
            library_controls.addWidget(widget)
        library_layout.addLayout(library_controls)
        self.library_table = table(["작품 / 버전", "제목", "검수"])
        self.library_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.library_table.setMinimumHeight(90)
        library_layout.addWidget(self.library_table)
        self.add_button = QPushButton("선택 작품을 재생목록에 추가")
        library_layout.addWidget(self.add_button)
        editor_layout.addWidget(library_group)
        plan_group = QGroupBox("중앙 재생 구성 · 순서 / 시간 / 반복")
        plan_layout = QVBoxLayout(plan_group)
        duration_row = QHBoxLayout()
        duration_row.addWidget(label("작품당 시연 시간"))
        self.duration = QSpinBox()
        self.duration.setRange(5, 3600)
        self.duration.setSuffix(" 초")
        self.duration.setValue(self.plan["duration_seconds"])
        self.duration.setToolTip("모든 작품의 1회 시연에 같은 시간을 사용합니다. 중앙 저장 후 유지됩니다.")
        duration_row.addWidget(self.duration)
        self.duration_label = label("2분 00초 · 모든 작품 공통", "dashboardNote")
        duration_row.addWidget(self.duration_label, 1)
        self.duration_reset = QPushButton("2분으로 복원")
        duration_row.addWidget(self.duration_reset)
        plan_layout.addLayout(duration_row)
        self.plan_table = table(["작품 / 버전", "시간(초)", "반복", "사용"])
        self.plan_table.setMinimumHeight(90)
        plan_layout.addWidget(self.plan_table)
        controls = QHBoxLayout()
        self.up, self.down, self.remove = QPushButton("위로"), QPushButton("아래로"), QPushButton("제거")
        for button in (self.up, self.down, self.remove):
            controls.addWidget(button)
        self.loop = QCheckBox("전체 반복")
        controls.addWidget(self.loop)
        self.autoplay = QCheckBox("자동 순환 재생")
        controls.addWidget(self.autoplay)
        controls.addStretch()
        plan_layout.addLayout(controls)
        self.plan_status = label("빈 구성 · 아직 저장하지 않음", "dashboardNote")
        plan_layout.addWidget(self.plan_status)
        save_row = QHBoxLayout()
        self.reload_button, self.save_button = QPushButton("서버 설정 다시 불러오기"), QPushButton("중앙에 구성 저장")
        save_row.addWidget(self.reload_button)
        save_row.addWidget(self.save_button)
        plan_layout.addLayout(save_row)
        plan_layout.addWidget(label("중앙 저장 후 PC2가 동기화합니다. 다음 작품이 미준비이면 현재 화면을 유지합니다.", "dashboardNote"))
        editor_layout.addWidget(plan_group)
        apply_group = QGroupBox("선택 작품 수동 적용")
        apply_layout = QVBoxLayout(apply_group)
        self.selection = label("검증 작품 목록에서 작품을 선택하세요")
        apply_layout.addWidget(self.selection)
        self.reviewer, self.reason = QLineEdit(), QLineEdit()
        self.reviewer.setMaxLength(100)
        self.reason.setMaxLength(1000)
        fields = QFormLayout()
        fields.addRow("승인자", self.reviewer)
        fields.addRow("적용 사유", self.reason)
        apply_layout.addLayout(fields)
        self.apply_button = QPushButton("선택 작품 적용 요청")
        self.apply_button.setObjectName("applyShow")
        apply_layout.addWidget(self.apply_button)
        self.apply_status = label("적용 요청 없음", "dashboardNote")
        apply_layout.addWidget(self.apply_status)
        editor_layout.addWidget(apply_group)
        body.addWidget(self.editor)
        self.editor.hide()
        # Approval fields remain visible during operations, independent of playlist editing.
        editor_layout.removeWidget(apply_group)
        body.addWidget(apply_group)
        self.device = label("노드 보고 대기", "helper")
        self.playback_status = label("PC2 재생 보고 대기", "helper")
        self.notice = label("중앙 저장과 실제 재생 보고를 구분합니다.", "helper")
        log_group = QGroupBox("시스템 상태 · 최근 변화")
        log_layout = QVBoxLayout(log_group)
        log_layout.addWidget(self.device)
        log_layout.addWidget(self.playback_status)
        log_layout.addWidget(self.notice)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(80)
        self.logs.setFixedHeight(90)
        log_layout.addWidget(self.logs)
        body.addWidget(log_group)
        body.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.editor_toggle.toggled.connect(self.editor.setVisible)
        self.editor_toggle.toggled.connect(lambda opened: self.editor_toggle.setText(
            "재생목록 편집 닫기" if opened else "재생목록 편집 열기 · 라이브러리 / 순서 / 시간"))
        self.start_auto.clicked.connect(lambda: self.set_autoplay(True))
        self.stop_auto.clicked.connect(lambda: self.set_autoplay(False))
        self.next_button.clicked.connect(lambda: self.operate_artwork(self.view_state.get("next_id"), "다음 작품 적용"))
        self.reapply_button.clicked.connect(lambda: self.operate_artwork(self.view_state.get("current_id"), "현재 작품 재적용"))
        self.recovery_button.clicked.connect(lambda: self.operate_artwork(self.recovery_choice.currentData(), "복구 작품 적용"))
        self.recovery_choice.currentIndexChanged.connect(self.controls)
        self.refresh_button.clicked.connect(self.resume)
        self.node.currentIndexChanged.connect(self.choose_node)
        self.library_table.itemSelectionChanged.connect(self.controls)
        self.add_button.clicked.connect(self.add)
        self.up.clicked.connect(lambda: self.move(-1))
        self.down.clicked.connect(lambda: self.move(1))
        self.remove.clicked.connect(self.remove_item)
        self.loop.toggled.connect(self.change_loop)
        self.autoplay.toggled.connect(self.change_autoplay)
        self.search_button.clicked.connect(self.search_library)
        self.search.returnPressed.connect(self.search_library)
        self.previous_page.clicked.connect(lambda: self.page(-1))
        self.next_page.clicked.connect(lambda: self.page(1))
        self.duration.valueChanged.connect(self.change_duration)
        self.duration_reset.clicked.connect(lambda: self.duration.setValue(ShowPlan().duration_seconds))
        self.reload_button.clicked.connect(self.reload_plan)
        self.save_button.clicked.connect(self.save)
        self.apply_button.clicked.connect(self.apply)
        self.reviewer.textChanged.connect(self.controls)
        self.reason.textChanged.connect(self.controls)
        self.controls()

    def set_active(self, active):
        self.active = active
        if active and self.authorized:
            self.timer.start()
            self.expiry_timer.start()
            self.refresh()
        else:
            self.timer.stop()
            self.expiry_timer.stop()

    def shutdown(self):
        self.closed = True
        self.timer.stop()
        self.expiry_timer.stop()

    def launch(self, kind, fn, success):
        if self.closed or not self.authorized or kind in self.operations:
            return
        self.operations.add(kind)
        self.requests += 1

        def work():
            value, error = None, None
            try:
                value = fn()
            except Exception as caught:  # noqa: BLE001 — UI에는 원시 응답 대신 안전한 문구
                error = caught
            try:
                self.bridge.call.emit(lambda: self.done(kind, value, error, success))
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()
        self.controls()

    def done(self, kind, value, error, success):
        self.requests -= 1
        self.operations.discard(kind)
        if self.closed:
            return
        if kind == "preview":
            if error:
                self.preview.setText("이미지 조회 실패 · 재생 상태와 별도")
            else:
                success(value)
            self.controls()
            return
        if error:
            self.ready = False
            self.render_state()
            self.current.setText("현재 시연 작품 미확인 · 다시 조회가 필요합니다")
            self.playback_status.setText("마지막 재생 보고 · 중앙 조회 실패로 현재 상태 미확인")
            self.notice.setText(error_text(error) + " · 편집 내용은 유지됩니다")
            if isinstance(error, ApiError) and error.status == 401:
                self.authorized = False
                self.timer.stop()
                self.expiry_timer.stop()
                self.library = []
                self.library_table.setRowCount(0)
                self.device.setText("운영자 인증 필요")
            if isinstance(error, ApiError) and error.status == 409:
                self.notice.setText("다른 운영자가 변경했거나 검수 근거가 바뀌었습니다. 서버 설정을 다시 확인하세요.")
        else:
            success(value)
        self.controls()

    def resume(self):
        self.authorized = True
        self.set_active(self.active)

    def refresh(self):
        if self.active and self.authorized:
            node_id, publication_id = self.node_id, self.publication
            offset, search = self.library_offset, self.library_search

            def refreshed(value):
                if node_id != self.node_id:
                    self.refresh()
                elif (offset, search) == (self.library_offset, self.library_search):
                    self.refreshed(value)
                else:
                    self.refresh()
            self.launch("refresh", lambda: self.client.show_dashboard(node_id, publication_id, offset, search), refreshed)

    def search_library(self):
        self.library_search, self.library_offset = self.search.text().strip(), 0
        self.refresh()

    def page(self, direction):
        self.library_offset = max(0, self.library_offset + direction * 50)
        self.refresh()

    def refreshed(self, value):
        self.ready = True
        self.observed_at = value["observed_at"]
        self.received_at = time.monotonic()
        selected = self.node_id
        selected_id = self.selected_artwork().get("verification_job_id")
        self.nodes, self.library = value["nodes"], value["library"]
        self.artworks.update({item["verification_job_id"]: item for item in
                              self.library + value.get("plan_artworks", []) + value.get("current_artworks", [])})
        self.previous_page.setEnabled(self.library_offset > 0)
        self.next_page.setEnabled(value.get("library_has_more", False))
        self.node.blockSignals(True)
        self.node.clear()
        for node in self.nodes:
            self.node.addItem(node["node_id"], node["node_id"])
        index = self.node.findData(selected)
        if selected and index < 0 and self.dirty:
            self.node.addItem(selected + " · 미등록", selected)
            index = self.node.count() - 1
        self.node.setCurrentIndex(max(0, index))
        self.node_id = self.node.currentData()
        self.node.blockSignals(False)
        self.connection.setText("마지막 수신 " + timestamp(value["observed_at"]))
        self.library_table.setRowCount(len(self.library))
        for row, item in enumerate(self.library):
            for column, text in enumerate((item["artwork_id"] + " / " + item["version"], item["title"], item["verdict"])):
                self.library_table.setItem(row, column, QTableWidgetItem(str(text)))
            if item["verification_job_id"] == selected_id:
                self.library_table.selectRow(row)
        config = value.get("configuration")
        self.server_config = config
        if not self.dirty and not self.operations.intersection({"save", "operate"}) and config and config["node_id"] == self.node_id:
            self.load_plan(config)
        self.render_state()
        publication = value.get("publication")
        if publication:
            self.publication_pending = publication.get("status") in ("queued", "running")
            self.publication_terminal = publication.get("status") in ("succeeded", "failed", "cancelled")
            applied = (publication.get("status") == "succeeded" and publication.get("result", {}).get("phase") == "Applied")
            state = "Applied 보고 확인"
            self.apply_status.setText(f"{publication['id']}\n{state if applied else publication.get('status', '미확인')}"
                                      "\n실제 전시 화면 품질은 별도 확인")

    def expire_report(self):
        node = next((n for n in self.nodes if n["node_id"] == self.node_id), {})
        age = self.observed_at + time.monotonic() - self.received_at - (node.get("report_at") or 0)
        if self.active and self.view_state.get("fresh") and age >= 90:
            self.render_state()
            self.controls()

    def render_state(self):
        node = next((n for n in self.nodes if n["node_id"] == self.node_id), {})
        now = self.observed_at + max(0, time.monotonic() - self.received_at)
        view = state(node, self.server_config, self.artworks, now, self.ready and self.authorized)
        self.view_state = view
        self.live_notice.setText(("확인 필요 · " + view["error"] + " · " if view["error"] else "") + view["playback"])
        self.tone(self.live_notice, "error" if view["error"] else "good" if view["duration"] else "wait")
        playback = node.get("playback") or {}
        config = self.server_config or {}
        saved_plan = config.get("plan") or {}
        self.health["pc2"].setText("연결됨 · 최근 보고 " + timestamp(node.get("report_at"))
                                    if view["fresh"] else "연결/보고 미확인")
        self.health["runtime"].setText(f"프로세스 보고 · PID {node.get('runtime_pid')}\n{view['phase']}"
                                        if view["process"] else "프로세스 보고 미확인\n" + view["phase"])
        self.health["controller"].setText("원격 상태 미보고\nPC2 Agent 연결과 별도")
        self.health["sensor"].setText("연결·손 좌표 수신 미보고\n마지막 입력 / 추적 인원 미확인")
        self.stages["saved"].setText(f"{'저장 완료' if self.ready else '마지막 저장'} · Rev.{config.get('revision', 0)}"
                                      + ("\n미저장 편집 있음" if self.dirty else ""))
        self.stages["synced"].setText(f"동기화 완료 · Rev.{playback.get('revision')}" if view["synced"] else
            f"동기화 대기 · PC2 Rev.{playback.get('revision', '—')}" if view["fresh"] else "동기화 미확인")
        self.stages["applied"].setText(view["phase"])
        playing = view["playback"]
        if playing == "재생 중" and not view["duration"]:
            playing = "재생 보고 · 작품/프로세스 대조 대기"
        self.stages["playing"].setText(playing)
        self.tone(self.stages["saved"], "good" if self.ready and not self.dirty else "wait")
        self.tone(self.stages["synced"], "good" if view["synced"] else "wait")
        self.tone(self.stages["applied"], "good" if view["applied"] else "wait")
        self.tone(self.stages["playing"], "good" if view["duration"] else "wait")
        if view["title"]:
            artwork = node.get("artwork") or {}
            preview = " · 미리보기 / 정식 승인 아님" if artwork.get("preview_only") else ""
            self.current.setText(view["title"] + preview + "\n" + (node.get("active_version") or ""))
        else:
            explanation = ("새 작품의 UE 적용 완료 보고를 기다립니다." if view["fresh"] and
                           node.get("phase") in ("Staged", "Ready") else node.get("reason") or playing)
            self.current.setText("현재 작품 미확인\n" + explanation)
        if view["duration"]:
            self.time_label.setText(clock_text(view["elapsed"]) + " / " + clock_text(view["duration"]))
            self.progress.setValue(view["progress"])
            self.progress.setFormat("남은 " + clock_text(view["remaining"]) + " · 마지막 PC2 보고 기준")
        else:
            self.time_label.setText("--:-- / --:--")
            self.progress.setValue(0)
            self.progress.setFormat("재생 시간 미확인")
        next_item = self.artworks.get(view["next_id"]) or {}
        cache = view["cached"]
        next_ready = ("파일 준비 보고" if cache is not None and view["next_id"] in cache else
                      "준비 실패" if view["next_id"] in view["errors"] else
                      "파일 준비 대기" if cache is not None else "파일 준비 미보고")
        self.next_status.setText("다음 · " + next_item.get("title", "미확인/마지막 작품") + " · " + next_ready
                                + "\nUE 사전 로딩 완료와 파일 준비는 별도입니다.")
        items = [item for item in saved_plan.get("items", []) if item.get("enabled")]
        self.live_timeline.items = items
        self.live_timeline.duration_seconds = saved_plan.get("duration_seconds", 120)
        self.live_timeline.current_id = view["current_id"]
        self.live_timeline.update()
        self.execution_table.setRowCount(len(items))
        for row, item in enumerate(items):
            key = item["verification_job_id"]
            title = self.artworks.get(key, {}).get("title", key)
            preparation = ("실패 · " + view["errors"][key] if key in view["errors"] else "파일 준비됨"
                           if cache is not None and key in cache else "준비 대기" if cache is not None else "미보고")
            actual = ("현재 · " + playing if key == view["current_id"] else "다음 (구성 기준)"
                      if key == view["next_id"] else "—")
            for col, text in enumerate((f"{row + 1:02d} · {title}", preparation, actual)):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                if key == view["current_id"]:
                    cell.setBackground(QColor("#E5EFFD"))
                    cell.setForeground(QColor("#174D9A"))
                self.execution_table.setItem(row, col, cell)
        total = sum(item["repeat"] * saved_plan.get("duration_seconds", 120) for item in items)
        self.overall.setText(f"구성 1회 {clock_text(total)} · " +
                            ("전체 반복 ON · 종료 시각 없음" if saved_plan.get("loop") else
                             "전체 진행률·종료 시각 미확인 (실행 회차·전환 지연 보고 필요)"))
        self.operations_note.setText(f"중앙 자동 전환 {'ON' if saved_plan.get('autoplay') else 'OFF'} · "
                                     f"반복 {'ON' if saved_plan.get('loop') else 'OFF'}\n출력: PC2 · LED 매핑 현장 확인")
        selected = self.recovery_choice.currentData()
        self.recovery_choice.blockSignals(True)
        self.recovery_choice.clear()
        self.recovery_choice.addItem("복구 작품 선택", None)
        for item in self.artworks.values():
            if item.get("catalog_version") == node.get("catalog_version"):
                self.recovery_choice.addItem(item["title"], item["verification_job_id"])
        self.recovery_choice.setCurrentIndex(max(0, self.recovery_choice.findData(selected)))
        self.recovery_choice.blockSignals(False)
        self.device.setText(f"{self.node_id or '미등록'} · {view['phase']} · 최근 보고 {timestamp(node.get('report_at'))}")
        self.playback_status.setText(f"{'PC2 보고' if view['fresh'] else '마지막 PC2 보고 · 현재 미확인'} · "
            f"Rev.{playback.get('revision', '—')} / 중앙 {config.get('revision', 0)} · {playing} · "
            f"캐시 {playback.get('cached_count', '—')}개" + (f"\n오류: {view['error']}" if view["error"] else ""))
        log_key = (self.node_id, view["fresh"], view["phase"], playing, view["current_id"], view["error"])
        if log_key != self.last_log:
            self.logs.appendPlainText(f"[{timestamp(now)}] {self.node_id or '노드 없음'} · {playing} · {view['phase']}"
                                      + (" · " + view["error"] if view["error"] else ""))
            self.last_log = log_key
        self.update_preview(self.artworks.get(view["current_id"], {}).get("preview") if view["fresh"] else None)

    @staticmethod
    def tone(widget, value):
        if widget.property("tone") != value:
            widget.setProperty("tone", value)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def update_preview(self, ref):
        key = ref.get("sha256") if ref else None
        if key != self.preview_key:
            self.preview_key, self.preview_loaded_key = key, None
            self.preview.clear()
            self.preview.setText("이미지 조회 중" if ref else "작품 이미지 미보고 · 현재 화면은 PC2에서 확인")
        if ref and self.preview_loaded_key != key and "preview" not in self.operations:
            self.preview_loaded_key = key
            def show(data):
                if self.preview_key != key or not self.ready:
                    return
                pixmap = QPixmap()
                if pixmap.loadFromData(data):
                    self.preview.setPixmap(pixmap.scaled(self.preview.width(), 145, Qt.KeepAspectRatio,
                                                        Qt.SmoothTransformation))
                else:
                    self.preview.setText("이미지를 읽을 수 없습니다")
            self.launch("preview", lambda: self.client.show_preview(ref), show)

    def set_autoplay(self, enabled):
        button = self.start_auto if enabled else self.stop_auto
        if not button.isEnabled():
            return
        config = copy.deepcopy(self.server_config)
        plan = config["plan"]
        plan["autoplay"] = enabled
        action = "자동 전환 시작" if enabled else "자동 전환 멈춤 (현재 화면 유지)"
        if QMessageBox.question(self, action, self.node_id + " · " + action + "을 요청할까요?\nPC2 동기화 후 반영됩니다.") != QMessageBox.Yes:
            return
        def saved(value):
            self.server_config = value
            self.load_plan(value)
            self.notice.setText(action + " 저장 · 실제 PC2 보고 대기")
            self.render_state()
            self.refresh()
        self.launch("operate", lambda: self.client.save_show_plan(self.node_id, config["revision"], plan), saved)

    def operate_artwork(self, verification_id, action):
        item = self.artworks.get(verification_id)
        if not item or not self.operation_available():
            return
        if QMessageBox.question(self, action, f"{self.node_id} · {item['title']}\n자동 전환을 끈 뒤 적용을 요청합니다.\n현재 화면이 바뀔 수 있습니다. 계속할까요?") != QMessageBox.Yes:
            return
        node_id, config = self.node_id, copy.deepcopy(self.server_config)
        body = {"target_node": node_id, "reviewer": self.reviewer.text().strip(),
                "reason": (action + " · " + self.reason.text().strip())[:1000]}
        signature = json.dumps({"id": verification_id, **body}, sort_keys=True)
        if signature != self.operation_signature or self.publication_terminal:
            self.operation_signature, self.operation_request_id = signature, uuid.uuid4().hex
            self.publication_terminal = False
        body["request_id"] = self.operation_request_id
        def request():
            if config["plan"]["autoplay"]:
                config["plan"]["autoplay"] = False
                self.client.save_show_plan(node_id, config["revision"], config["plan"])
            return self.client.apply_show_artwork(verification_id, body)
        def requested(value):
            self.publication = value["id"]
            self.publication_pending = value.get("status") in ("queued", "running")
            self.publication_terminal = value.get("status") in ("succeeded", "failed", "cancelled")
            self.apply_status.setText(action + " 요청 접수 · 실제 Applied 보고 대기\n" + value["id"])
            self.notice.setText("자동 전환 꺼짐 · 적용 실패 시 상태 새로고침 후 복구 작품을 확인하세요.")
            self.refresh()
        self.launch("operate", request, requested)

    def operation_available(self):
        return (self.ready and self.authorized and self.view_state.get("fresh") and not self.dirty
                and not self.operations.intersection({"save", "apply", "operate"}) and not self.publication_pending
                and bool(self.reviewer.text().strip() and self.reason.text().strip()))

    def selected_artwork(self):
        row = self.library_table.currentRow()
        return self.library[row] if 0 <= row < len(self.library) else {}

    def controls(self):
        item = self.selected_artwork()
        if item:
            self.selection.setText(f"{item['title']}\n{item['artwork_id']} / {item['version']}")
        idle = not self.operations.intersection({"save", "apply", "operate"})
        node = next((n for n in self.nodes if n["node_id"] == self.node_id), None)
        available = self.ready and self.authorized and node is not None and idle
        compatible = node is not None and item and item["catalog_version"] == node.get("catalog_version")
        self.add_button.setEnabled(bool(available and compatible))
        self.save_button.setEnabled(bool(available and self.dirty))
        self.reload_button.setEnabled(bool(available))
        self.apply_button.setEnabled(bool(available and compatible and not self.publication_pending and self.reviewer.text().strip()
                                          and self.reason.text().strip()))
        operational = self.operation_available()
        self.next_button.setEnabled(bool(operational and self.view_state.get("next_id")))
        self.reapply_button.setEnabled(bool(operational and self.view_state.get("current_id")))
        self.recovery_button.setEnabled(bool(operational and self.recovery_choice.currentData()))
        plan = (self.server_config or {}).get("plan") or {}
        auto_ready = bool(available and not self.dirty and self.view_state.get("fresh") and node.get("playback"))
        self.start_auto.setEnabled(auto_ready and not plan.get("autoplay") and any(x["enabled"] for x in plan.get("items", [])))
        self.stop_auto.setEnabled(auto_ready and bool(plan.get("autoplay")))
        self.node.setEnabled(not self.operations)
        for widget in (self.plan_table, self.loop, self.autoplay, self.duration, self.duration_reset,
                       self.up, self.down, self.remove):
            widget.setEnabled(idle)

    def choose_node(self):
        new_node = self.node.currentData()
        if self.dirty:
            self.node.blockSignals(True)
            self.node.setCurrentIndex(self.node.findData(self.node_id))
            self.node.blockSignals(False)
            self.notice.setText("작성한 구성을 저장하거나 서버 설정을 다시 불러온 뒤 노드를 바꿔주세요.")
            return
        self.node_id, self.publication, self.ready = new_node, None, False
        self.publication_pending, self.publication_terminal = False, False
        self.refresh()

    def load_plan(self, value):
        self.revision = value["revision"]
        self.plan = ShowPlan.model_validate(value["plan"]).model_dump()
        self.dirty = False
        self.render_plan()

    def render_plan(self):
        names = {key: f"{r['title']}\n{r['artwork_id']} / {r['version']}" for key, r in self.artworks.items()}
        self.plan_table.setRowCount(len(self.plan["items"]))
        for row, item in enumerate(self.plan["items"]):
            title = names.get(item["verification_job_id"], item["verification_job_id"])
            cell = QTableWidgetItem(f"{row + 1:02d} · {title}")
            cell.setToolTip(title + "\n검수 작업 " + item["verification_job_id"])
            self.plan_table.setItem(row, 0, cell)
            self.plan_table.setItem(row, 1, QTableWidgetItem(str(self.plan["duration_seconds"])))
            repeat = QSpinBox()
            repeat.setRange(1, 100)
            repeat.setValue(item["repeat"])
            repeat.valueChanged.connect(lambda value, row=row: self.edit_item(row, "repeat", value))
            self.plan_table.setCellWidget(row, 2, repeat)
            enabled = QCheckBox("사용")
            enabled.setChecked(item["enabled"])
            enabled.toggled.connect(lambda value, row=row: self.edit_item(row, "enabled", value))
            self.plan_table.setCellWidget(row, 3, enabled)
        self.loop.blockSignals(True)
        self.loop.setChecked(self.plan["loop"])
        self.loop.blockSignals(False)
        self.autoplay.blockSignals(True)
        self.autoplay.setChecked(self.plan["autoplay"])
        self.autoplay.blockSignals(False)
        self.duration.blockSignals(True)
        self.duration.setValue(self.plan["duration_seconds"])
        self.duration.blockSignals(False)
        self.plan_table.resizeRowsToContents()
        self.update_plan_view()

    def update_plan_view(self):
        self.timeline.items = copy.deepcopy(self.plan["items"])
        self.timeline.duration_seconds = self.plan["duration_seconds"]
        self.timeline.update()
        duration = self.plan["duration_seconds"]
        self.duration_label.setText(f"{duration // 60}분 {duration % 60:02d}초 · 모든 작품 공통")
        self.plan_status.setText(f"revision {self.revision} · {len(self.plan['items'])}개 · "
                                 + ("저장되지 않은 편집" if self.dirty else "중앙 구성 동기화"))
        self.render_state()
        self.controls()

    def edit_item(self, row, key, value):
        self.plan["items"][row][key] = value
        self.dirty = True
        self.update_plan_view()

    def change_loop(self, value):
        self.plan["loop"], self.dirty = value, True
        self.update_plan_view()

    def change_autoplay(self, value):
        self.plan["autoplay"], self.dirty = value, True
        self.update_plan_view()

    def change_duration(self, value):
        self.plan["duration_seconds"], self.dirty = value, True
        for row in range(self.plan_table.rowCount()):
            self.plan_table.item(row, 1).setText(str(value))
        self.update_plan_view()

    def add(self):
        rows = sorted(index.row() for index in self.library_table.selectionModel().selectedRows())
        existing = {item["verification_job_id"] for item in self.plan["items"]}
        node = next((node for node in self.nodes if node["node_id"] == self.node_id), {})
        for row in rows:
            item = self.library[row]
            key = item["verification_job_id"]
            if key in existing or item["catalog_version"] != node.get("catalog_version"):
                continue
            if len(self.plan["items"]) >= 200:
                self.notice.setText("구성은 최대 200개입니다.")
                break
            self.plan["items"].append(ShowItem(verification_job_id=key).model_dump())
            existing.add(key)
            self.dirty = True
        self.render_plan()

    def move(self, direction):
        row, items = self.plan_table.currentRow(), self.plan["items"]
        other = row + direction
        if 0 <= row < len(items) and 0 <= other < len(items):
            items[row], items[other] = items[other], items[row]
            self.dirty = True
            self.render_plan()
            self.plan_table.selectRow(other)

    def remove_item(self):
        row = self.plan_table.currentRow()
        if 0 <= row < len(self.plan["items"]):
            self.plan["items"].pop(row)
            self.dirty = True
            self.render_plan()

    def reload_plan(self):
        if self.dirty and QMessageBox.question(self, "편집 교체", "저장하지 않은 편집을 버리고 중앙 구성을 불러올까요?") != QMessageBox.Yes:
            return
        if getattr(self, "server_config", None):
            self.load_plan(self.server_config)
        self.refresh()

    def save(self):
        if not self.save_button.isEnabled():
            return
        plan, revision, node_id = copy.deepcopy(self.plan), self.revision, self.node_id

        def saved(value):
            self.ready = True
            self.load_plan(value)
            self.server_config = value
            self.render_state()
            self.notice.setText("중앙에 저장했습니다 · PC2의 적용 revision과 실제 재생 보고를 확인하세요.")
        self.launch("save", lambda: self.client.save_show_plan(node_id, revision, plan), saved)

    def apply(self):
        if not self.apply_button.isEnabled():
            return
        item = copy.deepcopy(self.selected_artwork())
        node_id = self.node_id
        if QMessageBox.question(self, "시연 작품 적용 요청", f"{node_id}에 {item['title']} / {item['version']}을 적용할까요?\n"
                                "현재 작품의 교체를 요청하며 실제 Applied 보고를 별도로 확인합니다.") != QMessageBox.Yes:
            return
        body = {"target_node": node_id, "reviewer": self.reviewer.text().strip(), "reason": self.reason.text().strip()}
        signature = json.dumps({"verification_job_id": item["verification_job_id"], **body}, sort_keys=True)
        if signature != self.request_signature or self.publication_terminal:
            self.request_signature, self.request_id = signature, uuid.uuid4().hex
            self.publication_terminal = False
        body["request_id"] = self.request_id

        def requested(value):
            self.publication = value["id"]
            self.publication_pending = value.get("status") in ("queued", "running")
            self.apply_status.setText(f"요청 접수 · {value['id']}\n실제 Applied 보고 대기")
            self.refresh()
        self.launch("apply", lambda: self.client.apply_show_artwork(item["verification_job_id"], body), requested)
