"""운영 앱 창(PySide6 · Qt Widgets). central API만 부른다.

- 자동 모드: POST /infra/head/intakes — 원문을 저장하고 분산 Head 상태를 조회한다.
- 편집 모드: POST /api/drafts로 AI 초안을 받아 폼에 미리 채워 보여 주고(AI가 채운 칸은 보라색),
  사용자가 바꾼 칸만 POST /api/artworks/{id}/confirm으로 보낸다. Shift+Tab으로 모드를 바꾼다.
- 자동 모드 최종 검수는 PC2 게시 승인 대기이며 소재 검수는 미지원이다.
  편집·기존 작품 기록의 검수 카드는 POST /api/artworks/{id}/review로 승인·반려한다.
- 전시 모드(체크박스): central의 전시 자동 순환을 PUT /infra/exhibition/feed로 켜고 끈다. 순환은 중앙 서버에서 돈다.
API 호출은 작업 스레드에서 하고, 결과는 신호로 화면 스레드에 돌려준다(화면 스레드에서 기다리지 않는다).
"""
import json
import threading
import uuid

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from contracts.schemas import CHIPS, ArtworkOut, Reveal

from .client import CentralClient
from .dashboard import ShowDashboard
from .feedback import FeedbackPanel
from .monitor import MonitorPanel
from .native_artworks import NativeArtworkPanel
from .style import EDIT, INK, MUTED, QSS

APP_NAME = "Artomation"
MODE_STYLE = {
    "auto": ("자동 모드", "중앙 Head에 접수해 분산 생성·UE 조립·검증·PC2 게시를 진행합니다.", "#1A73E8", "#E8F0FE"),
    "edit": ("편집 모드", "AI가 채운 초안을 보여 주고, 필요한 칸만 고친 뒤 진행합니다.", "#9334E6", "#F3E8FD"),
}
STATUS_TEXT = {"prepared": ("필수 후보 파일 준비 완료 · Ready·Applied 미확인", "#188038"),
               "published": ("과거 파일 게시 기록 · Ready·Applied 미확인", "#B36B16"),
               "held": ("보류 · 지금 작품을 이어서 상영해요", "#B36B16"),
               "failed": ("완성하지 못했어요 · 지금 작품을 유지해요", "#C34F43"),
               "running": ("진행 중 · 작품을 차근차근 만들고 있어요", "#1A73E8"),
               "awaiting": ("입력 대기 · AI 초안을 살펴보고 이어가 주세요", "#9334E6"),
               "asset_review": ("소재 검수 대기 · 만든 소재를 보고 승인해 주세요", "#9334E6"),
               "final_review": ("최종 검수 대기 · 후보 파일을 확인해 주세요", "#9334E6")}
RENDERER_LABEL = {"ue": "UE", "td": "TD"}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
THUMB_SIZE = 180
CHIP_LABEL = {
    "mood": {"calm": "차분함", "dreamy": "몽환적", "mysterious": "신비로움", "joyful": "경쾌함"},
    "palette": {"cool": "차가운 색", "warm": "따뜻한 색", "night": "밤", "pastel": "파스텔"},
    "intensity": {"gentle": "약하게", "normal": "보통", "strong": "강하게"},
}
REVEAL_LABEL = {"formation": "형상 — 작은 것들이 모여 모양이 됨", "trace": "흔적 — 지나간 자국만 남음",
                "none": "없음 — 주인공 없이 장(field)만"}
FIELD_LABEL = {"title": "제목", "families": "표현 방식", "subject": "보이지 않는 주인공", "reveal": "드러내는 방식",
               "mood": "분위기", "palette": "색", "intensity": "반응 세기"}


def step_summary(step) -> str:
    """진행 기록을 한 줄로 읽을 수 있게 줄인다. 원문은 원본 데이터에 남는다."""
    detail = step.detail
    if isinstance(detail, str):
        return detail.replace("\n", " ")[:110]
    if isinstance(detail, list):
        if not detail:
            return "이 단계를 마쳤어요"
        names = [str(value.get("name") or value.get("kind") or value.get("label") or "항목")
                 if isinstance(value, dict) else str(value) for value in detail[:3]]
        return f"{len(detail)}개 항목 · {', '.join(names)}"[:110]
    if not isinstance(detail, dict):
        return "이 단계를 마쳤어요"
    if step.name == "파일 준비":
        return " · ".join(f"{name.upper()}: " + ("준비 완료" if item["status"] == "prepared" else item["error"])
                          for name, item in detail["targets"].items())
    if step.name == "상영":
        return str(detail.get("message") or detail.get("status") or "상영 장치에 전달했어요")[:110]
    if step.name == "작품 명세":   # Input Agent 판단은 문장 그대로 보여 준다
        return str(detail.get("decision") or "세부 내용은 원본 데이터에서 볼 수 있어요")[:110]
    keys = ("title", "subject", "reveal") if step.name in ("의도", "초안", "확정") else (
        "status", "result", "message", "reason")
    parts = [f"{FIELD_LABEL.get(key, key)} "
             f"{REVEAL_LABEL.get(detail[key], detail[key]) if key == 'reveal' else detail[key]}"
             for key in keys if isinstance(detail.get(key), str) and detail.get(key)]
    return " · ".join(parts)[:110] if parts else "세부 내용은 원본 데이터에서 볼 수 있어요"


class ModeSwitch(QObject):
    """Shift+Tab을 어디에 초점이 있든 모드 전환으로 쓴다(입력창의 기본 초점 이동보다 먼저 잡는다)."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def eventFilter(self, obj, event):
        if event.type() not in (QEvent.ShortcutOverride, QEvent.KeyPress):
            return False
        shift_tab = event.key() == Qt.Key_Backtab or (
            event.key() == Qt.Key_Tab and event.modifiers() & Qt.ShiftModifier)
        if not shift_tab or not isinstance(obj, QWidget) or obj.window() is not self.window:
            return False
        if event.type() == QEvent.KeyPress:
            self.window.toggle_mode()
        event.accept()
        return True


class _Bridge(QObject):
    """작업 스레드에서 화면 스레드로 할 일을 넘긴다."""
    call = Signal(object)


class OperatorWindow(QMainWindow):
    def __init__(self, client: CentralClient, show_dashboard: bool = True):
        super().__init__()
        self.client = client
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 840)
        self.setStyleSheet(QSS)
        self.mode = "auto"
        self.current: ArtworkOut | None = None
        self.head_job: dict | None = None
        self.head_epoch = 0
        self.head_pending: tuple[dict, str] | None = None
        self.intake_history: list[dict] = []
        self.artwork_history: list[ArtworkOut] = []
        self.head_timer = QTimer(self)
        self.head_timer.setSingleShot(True)
        self.head_timer.timeout.connect(self.poll_head)
        self.families: list[dict] = []
        self.draft_base: dict = {}
        self.confirming = False
        self.reviewing = False
        self.gate_seq = 0          # 검수 카드를 다시 그릴 때마다 증가: 늦게 온 썸네일이 지운 칸을 만지지 않게
        self.final_waiting: list[int] = []   # 최종 검수 대기 작품(기록 기준)
        self.busy = 0
        self.feed_epoch = 0
        self.bridge = _Bridge()
        self.bridge.call.connect(lambda fn: fn())

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_request())
        splitter.addWidget(self._build_result())
        splitter.setSizes([440, 840])
        self.tabs = QTabWidget()
        self.tabs.addTab(splitter, "작품 제작")
        self.monitor = MonitorPanel(client)
        self.tabs.addTab(self.monitor, "운영 현황")
        self.tabs.currentChanged.connect(lambda index: self.monitor.set_active(index == 1))
        self.feedback = FeedbackPanel(client)
        self.tabs.addTab(self.feedback, "작품 피드백")
        self.tabs.currentChanged.connect(lambda index: self.feedback.set_active(index == 2))
        self.dashboard = ShowDashboard(client) if show_dashboard else None
        self.native_artworks = NativeArtworkPanel(client) if show_dashboard else None
        self.show_tabs = QTabWidget() if show_dashboard else None
        if self.dashboard:
            self.show_tabs.addTab(self.native_artworks, "로컬 아카이브")
            self.show_tabs.addTab(self.dashboard, "공용 런타임")
            self.tabs.addTab(self.show_tabs, "시연 관리")
            self.tabs.currentChanged.connect(self.activate_show_panel)
            self.show_tabs.currentChanged.connect(self.activate_show_panel)
        self.setCentralWidget(self.tabs)
        self.switch = ModeSwitch(self)
        QApplication.instance().installEventFilter(self.switch)
        self.apply_mode()
        self.submit_button.setEnabled(False)   # 표현 방식 목록을 받은 뒤 켠다
        self.load_families()
        self.load_history()

    def load_families(self):
        def retry(error):
            self.set_title(f"central에 연결하지 못함 — 3초 뒤 다시 시도 ({error})", "#D93025")
            QTimer.singleShot(3000, self.load_families)

        self.background(self.client.families, self.set_families, retry)

    # ---------- 작업 스레드 ----------
    def background(self, fn, on_done, on_error=None):
        """fn을 작업 스레드에서 부르고 결과를 화면 스레드의 on_done(결과)·on_error(예외)로 넘긴다."""
        self.busy += 1

        def work():
            try:
                result = fn()
            except Exception as error:  # noqa: BLE001 — 어떤 실패든 화면에 알린다
                self.bridge.call.emit(lambda error=error: self._finish(on_error or self.show_error, error))
                return
            self.bridge.call.emit(lambda: self._finish(on_done, result))

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, callback, value):
        self.busy -= 1
        try:
            callback(value)
        finally:
            self.submit_button.setEnabled(bool(self.family_boxes) and self.busy == 0)

    def show_error(self, error):
        QMessageBox.warning(self, "요청 실패", str(error))

    # ---------- 왼쪽: 모드 · 요청 · 전시 · 기록 ----------
    def _build_request(self) -> QWidget:
        box = QWidget()
        box.setObjectName("page")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(22, 22, 14, 22)
        layout.setSpacing(16)
        self.mode_badge = QLabel()
        self.mode_badge.setWordWrap(True)
        self.mode_badge.setCursor(Qt.PointingHandCursor)
        self.mode_badge.mousePressEvent = lambda _event: self.toggle_mode()
        layout.addWidget(self.mode_badge)

        group = QGroupBox("원하는 작품")
        form = QVBoxLayout(group)
        form.setSpacing(12)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("예: 비 온 뒤 고요한 밤바다처럼 차분하게 (비워 두면 AI가 테마를 고름)")
        self.prompt.setMinimumHeight(90)
        form.addWidget(self.prompt)
        form.addWidget(QLabel("표현 방식 (비워 두면 AI가 고름 · 자동 모드는 최대 1개)"))
        self.family_row = QGridLayout()
        self.family_row.setHorizontalSpacing(14)
        self.family_boxes: dict[str, QCheckBox] = {}
        form.addLayout(self.family_row)
        self.review_assets = QCheckBox("소재 검수 — 배경·소재를 만든 뒤 멈추고 확인해요")
        self.review_final = QCheckBox("최종 검수 — 전시 전에 PC1 화면에서 확인해요")
        for check in (self.review_assets, self.review_final):
            form.addWidget(check)
        self.submit_button = QPushButton()
        self.submit_button.setObjectName("submit")
        self.submit_button.setMinimumHeight(48)
        self.submit_button.clicked.connect(self.submit)
        form.addWidget(self.submit_button)
        layout.addWidget(group)

        show = QGroupBox("전시 모드")
        show_layout = QVBoxLayout(show)
        self.show_check = QCheckBox("전시 모드 — 중앙 서버에서 AI가 작품을 계속 만들어요 (앱을 꺼도 계속돼요)")
        self.show_check.toggled.connect(self.toggle_exhibition)
        self.show_state = QLabel("상태 확인 중…")
        self.show_state.setStyleSheet("color:#5F6368;")
        for widget in (self.show_check, self.show_state):
            show_layout.addWidget(widget)
        history = QGroupBox("작품 기록")
        history_layout = QVBoxLayout(history)
        self.history = QListWidget()
        self.history.setAlternatingRowColors(False)
        self.history.addItem("아직 작품 기록이 없어요. 첫 작품을 만들어 볼까요?")
        self.history.itemClicked.connect(self.open_history_item)
        history_layout.addWidget(self.history)
        layout.addWidget(history, 1)
        show.setObjectName("showCard")
        layout.addWidget(show)
        return box

    def set_families(self, families: list[dict]):
        if self.family_boxes:
            return
        self.families = [f for f in families if f["status"] != "todo"]
        for spec in self.families:
            box = QCheckBox(spec["label"])
            box.setProperty("code", spec["key"])
            self.family_boxes[spec["key"]] = box
            index = len(self.family_boxes) - 1
            self.family_row.addWidget(box, index // 2, index % 2)
        for index, spec in enumerate(families):
            check = QCheckBox(spec["label"] + (" (연결 예정)" if spec["status"] == "todo" else ""))
            check.toggled.connect(self.validate_draft)
            self.draft_families[spec["key"]] = check
            self.draft_family_row.addWidget(check, index // 2, index % 2)
        if self.title.text().startswith("central에 연결하지 못함"):
            self.set_title("작품을 요청하면 여기에서 함께 살펴볼 수 있어요.", INK)
        if self.current and self.current.status == "awaiting" and self.current.draft:
            self.build_draft(self.current)

    # ---------- 오른쪽: 상태 · 편집 초안 · 단계 ----------
    def _build_result(self) -> QWidget:
        box = QWidget()
        box.setObjectName("page")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 22, 22, 22)
        layout.setSpacing(14)
        self.title = QLabel("작품을 요청하면 여기에서 함께 살펴볼 수 있어요.")
        self.title.setObjectName("status")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.runtime_state = QLabel("전시 상태: 미확인 · 파일 준비와 실제 적용은 별도입니다.")
        self.runtime_state.setWordWrap(True)
        layout.addWidget(self.runtime_state)

        self.draft_group = QGroupBox("AI 초안 · 마음에 드는 방향으로 다듬어 주세요 (편집 모드)")
        self.draft_group.setObjectName("draftCard")
        draft_layout = QVBoxLayout(self.draft_group)
        self.draft_form = QFormLayout()
        self.draft_form.setSpacing(10)
        draft_layout.addLayout(self.draft_form)
        self.draft_labels: dict[str, QLabel] = {}
        self.draft_title = QLineEdit()
        self.draft_title.textChanged.connect(self.validate_draft)
        self.draft_family_row = QGridLayout()
        self.draft_families: dict[str, QCheckBox] = {}
        family_widget = QWidget()
        family_widget.setLayout(self.draft_family_row)
        self.draft_subject = QLineEdit()
        self.draft_subject.setPlaceholderText("비우면 주인공 없이 장(field)만")
        self.draft_reveal = QComboBox()
        for reveal in Reveal:
            self.draft_reveal.addItem(REVEAL_LABEL[reveal.value], reveal.value)
        self.draft_chips = {name: QComboBox() for name in ("mood", "palette", "intensity")}
        widgets = {"title": self.draft_title, "families": family_widget, "subject": self.draft_subject,
                   "reveal": self.draft_reveal, **self.draft_chips}
        for name, widget in widgets.items():
            label = QLabel(FIELD_LABEL[name])
            self.draft_labels[name] = label
            self.draft_form.addRow(label, widget)
        self.draft_button = QPushButton("이 내용으로 진행")
        self.draft_button.setObjectName("draftAction")
        self.draft_button.clicked.connect(self.send_draft)
        draft_layout.addWidget(self.draft_button, 0, Qt.AlignRight)
        self.draft_group.hide()
        layout.addWidget(self.draft_group)

        self.gate_group = QGroupBox("사람 검수")
        self.gate_group.setObjectName("draftCard")
        gate_layout = QVBoxLayout(self.gate_group)
        self.gate_info = QLabel()
        self.gate_info.setWordWrap(True)
        gate_layout.addWidget(self.gate_info)
        self.gate_thumbs = QHBoxLayout()
        gate_layout.addLayout(self.gate_thumbs)
        self.gate_note = QLineEdit()
        self.gate_note.setMaxLength(200)
        self.gate_note.setPlaceholderText("메모 (선택) — 반려 이유 등")
        gate_layout.addWidget(self.gate_note)
        gate_buttons = QHBoxLayout()
        gate_buttons.addStretch(1)
        self.gate_reject = QPushButton("반려")
        self.gate_reject.clicked.connect(lambda: self.send_review(False))
        self.gate_approve = QPushButton("승인하고 이어가기")
        self.gate_approve.setObjectName("draftAction")
        self.gate_approve.clicked.connect(lambda: self.send_review(True))
        gate_buttons.addWidget(self.gate_reject)
        gate_buttons.addWidget(self.gate_approve)
        gate_layout.addLayout(gate_buttons)
        self.gate_group.hide()
        layout.addWidget(self.gate_group)

        steps_title = QLabel("작품이 만들어지는 길")
        steps_title.setObjectName("sectionTitle")
        layout.addWidget(steps_title)
        self.steps = QListWidget()
        self.steps.addItem("아직 진행 기록이 없어요. 작품을 요청해 볼까요?")
        layout.addWidget(self.steps, 2)
        self.raw_toggle = QPushButton("▸ 원본 데이터 보기")
        self.raw_toggle.setObjectName("rawToggle")
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.toggled.connect(self.toggle_raw)
        layout.addWidget(self.raw_toggle)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.hide()
        layout.addWidget(self.detail, 1)
        return box

    def toggle_raw(self, checked: bool):
        self.raw_toggle.setText("▾ 원본 데이터 접기" if checked else "▸ 원본 데이터 보기")
        self.detail.setVisible(checked)

    # ---------- 모드 ----------
    def toggle_mode(self):
        self.mode = "edit" if self.mode == "auto" else "auto"
        self.apply_mode()

    def apply_mode(self):
        name, desc, color, background = MODE_STYLE[self.mode]
        self.mode_badge.setText(f"<b style='color:{color}'>{name}</b> · {desc}<br>"
                                "<span style='color:#5F6368'>Shift+Tab으로 모드 전환</span>")
        self.mode_badge.setStyleSheet(f"background:{background}; border:1px solid {color}; border-radius:12px; "
                                      "padding:13px;")
        self.submit_button.setText(f"제출 — {name}")
        self.submit_button.setProperty("mode", self.mode)
        self.submit_button.style().unpolish(self.submit_button)
        self.submit_button.style().polish(self.submit_button)

    # ---------- 요청 ----------
    def request_body(self) -> dict:
        body = {"theme": self.prompt.toPlainText().strip(),
                "families": [code for code, box in self.family_boxes.items() if box.isChecked()]}
        if self.review_assets.isChecked() and self.review_assets.isEnabled():
            body["review_assets"] = True
        if self.review_final.isChecked() and self.review_final.isEnabled():
            body["review_final"] = True
        return body

    def submit(self):
        body = self.request_body()
        if self.mode == "auto":
            if not body["theme"] or body.get("review_assets") or len(body["families"]) > 1:
                QMessageBox.warning(self, "제출할 수 없음", "원문을 입력하고 표현 방식은 한 개까지만 선택하세요. "
                                    "분산 제작의 소재 중간 검수는 아직 지원하지 않습니다.")
                return
            if self.head_pending is None or self.head_pending[0] != body:
                self.head_pending = (body, "operator-" + uuid.uuid4().hex)
            request_id = self.head_pending[1]
            self.head_epoch += 1
            self.head_timer.stop()
            epoch = self.head_epoch
            self.set_title("분산 Head에 접수 중…", "#1A73E8")
            self.submit_button.setEnabled(False)

            def accepted(job):
                if self.head_pending and self.head_pending[1] == request_id:
                    self.head_pending = None
                if epoch == self.head_epoch:
                    self.show_head(job)
                self.load_history()

            self.background(lambda: self.client.submit_head(body, request_id), accepted)
            return
        if len(body["families"]) > 3:
            QMessageBox.warning(self, "제출할 수 없음", "표현 방식은 3개까지 고를 수 있어요.")
            return
        if body.get("review_final") and self.final_waiting:
            waiting = ", ".join(f"#{artwork_id}" for artwork_id in self.final_waiting)
            QMessageBox.warning(self, "최종 검수 대기 중", f"{waiting}이(가) 최종 검수를 기다리고 있어요. "
                                "새 작품이 PC1 검수 화면을 바꾸므로, 그 작품은 PC1에서 다시 볼 수 없어요.")
        call = self.client.create if self.mode == "auto" else self.client.draft
        self.set_title("AI 초안을 만드는 중…" if self.mode == "edit" else "만드는 중… (생성이 끝날 때까지 기다립니다)",
                       "#1A73E8")
        self.submit_button.setEnabled(False)
        self.background(lambda: call(body), self.show_artwork)

    def set_title(self, text: str, color: str):
        self.title.setText(text)
        self.title.setStyleSheet(f"color:{color};")

    def show_artwork(self, artwork: ArtworkOut):
        self.head_epoch += 1
        self.head_timer.stop()
        self.head_job = None
        self.current = artwork
        text, color = STATUS_TEXT.get(artwork.status, (artwork.status, "#000"))
        self.set_title(f"#{artwork.id} {artwork.title} · {text}", color)
        self.steps.clear()
        for index, step in enumerate(artwork.steps, 1):
            item = QListWidgetItem(f"{index:02d}  {step.name}  ·  {step_summary(step)}")
            item.setToolTip(str(step.detail))
            if step.name in ("초안", "확정"):
                item.setForeground(QColor(EDIT))
            elif step.name in ("상영", "전달"):
                item.setForeground(QColor("#C34F43" if artwork.status == "failed" else "#188038"))
            else:
                item.setForeground(QColor(MUTED))
            self.steps.addItem(item)
        self.detail.setPlainText(json.dumps(artwork.model_dump(mode="json"), ensure_ascii=False, indent=2))
        if artwork.status == "awaiting" and artwork.draft:
            self.build_draft(artwork)
        else:
            self.draft_group.hide()
        if artwork.gate:
            self.build_gate(artwork)
        else:
            self.gate_group.hide()
        self.load_history()

    def show_head(self, job: dict, workflow: dict | None = None):
        self.head_timer.stop()
        self.current = None
        self.head_job = job
        self.draft_group.hide()
        self.gate_group.hide()
        status = (workflow or job)["status"]
        labels = {"queued": "Head 접수 완료 · 처리 대기", "running": "Head 명세 해석 중",
                  "waiting_ue": "UE 템플릿 지문 확인 대기", "failed": "실패 · 운영 현황에서 원인 확인",
                  "completed": "PC2 적용 완료", "needs_attention": "검수·확인 필요",
                  "awaiting_publication_approval": "PC1 검증 완료 · 게시 승인 대기",
                  "planning_background": "배경 기획 중", "waiting_background": "배경 생성 중",
                  "planning_assets": "배경 분석·에셋 기획 중", "waiting_assets": "에셋 생성 중",
                  "waiting_assembly": "UE 조립 중", "waiting_verification": "PC1 실행 검증 중",
                  "planning_review": "Head 검수 중", "waiting_publication": "PC2 적용 확인 중"}
        label = labels.get(status, status)
        self.set_title(f"분산 제작 {job['id'][:8]} · {label}", "#D93025" if status == "failed" else "#1A73E8")
        self.steps.clear()
        self.steps.addItem(f"접수 번호: {job['id']} · {job['status']}")
        if workflow:
            self.steps.addItem(f"Head: {workflow['id']} · {workflow['status']}")
            for child in workflow.get("jobs", []):
                self.steps.addItem(f"{child['kind']} · {child['status']} · {child['id'][:8]}")
        self.detail.setPlainText(json.dumps({"intake": job, "workflow": workflow}, ensure_ascii=False, indent=2))
        if status not in ("failed", "completed", "needs_attention", "cancelled", "awaiting_publication_approval"):
            self.head_timer.start(3000)

    def poll_head(self):
        if not self.head_job:
            return
        job_id, epoch = self.head_job["id"], self.head_epoch

        def fetch():
            job = self.client.job(job_id)
            workflow_id = (job.get("result") or {}).get("workflow_id")
            return job, self.client.head_workflow(workflow_id) if workflow_id else None

        def done(result):
            if epoch == self.head_epoch:
                self.show_head(*result)

        def failed(error):
            if epoch == self.head_epoch:
                self.set_title(f"분산 제작 상태 조회 실패 · 재시도 중 ({error})", "#D93025")
                self.head_timer.start(5000)

        self.background(fetch, done, failed)

    # ---------- 사람 검수 ----------
    def build_gate(self, artwork: ArtworkOut):
        self.gate_seq += 1
        while self.gate_thumbs.count():
            widget = self.gate_thumbs.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        self.gate_note.clear()
        if artwork.gate == "final":
            targets = " · ".join(f"개발 {RENDERER_LABEL.get(name, name)} 후보 버전 {version}"
                                 for name, version in artwork.gate_targets.items())
            self.gate_info.setText(f"최종 검수 — {targets}을 확인한 뒤 승인하면 개발 후보 파일을 게시해요. "
                                   "실제 전시 적용은 별도 확인이 필요해요. 반려하면 현재 후보를 유지해요.")
        else:
            self.gate_info.setText("소재 검수 — 새로 만든 소재를 확인해 주세요. 승인하면 조립부터 이어가고, "
                                   "반려하면 이번 소재를 지워요.")
            for name in artwork.gate_files:
                thumb = QLabel(name if not name.lower().endswith(IMAGE_SUFFIXES) else "불러오는 중…")
                thumb.setToolTip(name)
                thumb.setFixedSize(THUMB_SIZE, THUMB_SIZE)
                thumb.setAlignment(Qt.AlignCenter)
                thumb.setWordWrap(True)
                self.gate_thumbs.addWidget(thumb)
                if name.lower().endswith(IMAGE_SUFFIXES):
                    self.load_thumb(artwork.id, name, thumb, self.gate_seq)
                else:
                    thumb.setText(f"{name}\n(이미지 아님 · 최종 결과에서 확인)")
            self.gate_thumbs.addStretch(1)
        self.gate_group.setEnabled(not self.reviewing)
        self.gate_group.show()

    def load_thumb(self, artwork_id: int, name: str, thumb: QLabel, seq: int):
        def shown(data: bytes):
            if seq != self.gate_seq:
                return   # 그사이 카드를 다시 그렸다(다른 작품이거나 같은 작품을 다시 엶)
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                thumb.setPixmap(pixmap.scaled(THUMB_SIZE, THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                thumb.setText(f"{name}\n(그림을 읽지 못함)")

        def failed(error):
            if seq == self.gate_seq:
                thumb.setText(f"{name}\n(불러오지 못함)")

        self.background(lambda: self.client.review_file(artwork_id, name), shown, failed)

    def send_review(self, approve: bool):
        artwork_id, note = self.current.id, self.gate_note.text().strip()
        self.reviewing = True   # 결과가 올 때까지 다시 보내지 못하게 잠근다
        self.gate_group.setEnabled(False)
        self.set_title(f"#{artwork_id} {'승인 — 이어서 만드는 중…' if approve else '반려하는 중…'}", "#1A73E8")

        def done(artwork):
            self.reviewing = False
            self.show_artwork(artwork)

        def rejected(error):
            self.reviewing = False
            self.gate_group.setEnabled(True)
            self.show_error(error)
            self.background(lambda: self.client.artwork(artwork_id), self.show_artwork)   # 상태가 바뀌었을 수 있다

        self.background(lambda: self.client.review(artwork_id, approve, note), done, rejected)

    # ---------- 편집 모드 초안 ----------
    def build_draft(self, artwork: ArtworkOut):
        values, review = artwork.draft, set(artwork.review)
        self.draft_base = dict(values)
        self.draft_title.setText(values["title"])
        for code, box in self.draft_families.items():
            box.setChecked(code in values["families"])
        self.draft_subject.setText(values["subject"])
        self.draft_reveal.setCurrentIndex(self.draft_reveal.findData(values["reveal"]))
        for name, combo in self.draft_chips.items():
            combo.clear()
            if values[name] is None:   # LLM이 목록 밖 조합을 골랐으면 그대로 두는 선택지를 맨 위에
                names = values.get("palette_names") if name == "palette" else None
                combo.addItem(f"AI 선택 그대로 ({', '.join(names)})" if names else "— 비어 있음 —", None)
            for code in CHIPS[name]:
                combo.addItem(CHIP_LABEL[name].get(code, code), code)
            combo.setCurrentIndex(max(combo.findData(values[name]), 0))
        for name, label in self.draft_labels.items():
            ai = name in review
            label.setText(FIELD_LABEL[name] + ("  · AI 제안" if ai else ""))
            label.setStyleSheet("color:#9334E6; font-weight:600;" if ai else "")
        self.draft_group.show()
        self.validate_draft()

    def draft_changes(self) -> dict:
        """초안과 다른 칸만 보낸다(보내지 않은 칸은 central이 초안 값을 그대로 쓴다)."""
        current = {"title": self.draft_title.text().strip(),
                   "families": [code for code, box in self.draft_families.items() if box.isChecked()],
                   "subject": self.draft_subject.text().strip(), "reveal": self.draft_reveal.currentData(),
                   **{name: combo.currentData() for name, combo in self.draft_chips.items()}}
        changes = {}
        for name, value in current.items():
            base = self.draft_base.get(name)
            if value is None:
                continue   # 목록 밖 AI 값을 그대로 둠
            same = sorted(value) == sorted(base) if name == "families" else value == base
            if not same:
                changes[name] = value
        return changes

    def validate_draft(self, *_):
        if not hasattr(self, "draft_button"):
            return
        chosen = [box for box in self.draft_families.values() if box.isChecked()]
        self.draft_button.setEnabled(not self.confirming and bool(self.draft_title.text().strip())
                                     and 1 <= len(chosen) <= 3)

    def send_draft(self):
        artwork_id, changes = self.current.id, self.draft_changes()
        self.confirming = True   # 결과가 올 때까지 다시 보내지 못하게 잠근다
        self.draft_group.setEnabled(False)
        self.set_title(f"#{artwork_id} 확정 — 만드는 중…", "#1A73E8")

        def unlock():
            self.confirming = False
            self.draft_group.setEnabled(True)

        def confirmed(artwork):
            unlock()
            self.show_artwork(artwork)

        def rejected(error):
            unlock()
            self.show_error(error)   # 422면 초안은 계속 기다린다
            self.validate_draft()

        self.background(lambda: self.client.confirm(artwork_id, changes), confirmed, rejected)

    # ---------- 기록 ----------
    def load_history(self):
        self.background(self.client.artworks, self.fill_history, lambda _error: None)
        self.background(self.client.head_intakes, self.fill_intakes, lambda _error: None)
        self.background(self.client.nodes, self.fill_runtime,
                        lambda _error: self.runtime_state.setText("전시 상태: 조회 실패 · 실제 적용 미확인"))
        self.load_feed()

    def fill_runtime(self, nodes: list[dict]):
        labels = {"Staged": "파일 캐시 준비됨", "Ready": "Ready · 다음 작품 준비됨 (활성화 전)",
                  "Applied": "Applied · 실제 활성화 확인", "Failed": "준비·적용 실패", "Idle": "대기"}
        reports = []
        for node in nodes:
            if node.get("role") != "show":
                continue
            report = node.get("show_report") or {}
            phase = labels.get(report.get("phase"), "실제 적용 미확인") if node.get("online") else "연결 끊김 · 마지막 보고"
            reports.append(f"{node['id']}: {phase} / 요청 {report.get('desired_version') or '없음'} / "
                           f"활성 {report.get('active_version') or '미확인'}" +
                           (f" / 원인: {report['error']}" if report.get("error") else ""))
        self.runtime_state.setText("전시 상태: " + ("\n".join(reports) or "시연 노드 보고 없음 · 실제 적용 미확인"))

    def open_history_item(self, item: QListWidgetItem):
        artwork_id = item.data(Qt.UserRole)   # 작업 스레드에서 Qt 객체를 만지지 않게 여기서 꺼낸다
        if artwork_id is None:
            return
        if isinstance(artwork_id, dict):
            self.head_epoch += 1
            self.head_timer.stop()
            self.head_job = artwork_id
            self.poll_head()
            return
        self.background(lambda: self.client.artwork(artwork_id), self.show_artwork)

    def fill_history(self, rows: list[ArtworkOut]):
        self.artwork_history = rows
        self.render_history()

    def fill_intakes(self, rows: list[dict]):
        self.intake_history = rows
        self.render_history()

    def render_history(self):
        rows = self.artwork_history
        self.history.clear()
        self.final_waiting = [row.id for row in rows if row.status == "final_review"]
        for job in self.intake_history:
            raw = (job.get("payload", {}).get("request") or {}).get("theme", "")
            item = QListWidgetItem(f"●  분산 {job['status']}  {job['id'][:8]}  {raw[:35]}")
            item.setData(Qt.UserRole, job)
            self.history.addItem(item)
        if not rows and not self.intake_history:
            empty = QListWidgetItem("아직 작품 기록이 없어요. 첫 작품을 만들어 볼까요?")
            empty.setForeground(QColor(MUTED))
            self.history.addItem(empty)
            return
        for row in sorted(rows, key=lambda artwork: artwork.id, reverse=True):
            text, color = STATUS_TEXT.get(row.status, (row.status, "#000"))
            item = QListWidgetItem(f"●  {text.split(' ·')[0]}   #{row.id}  {row.title}")
            item.setForeground(QColor(color))
            item.setData(Qt.UserRole, row.id)
            self.history.addItem(item)

    # ---------- 전시 모드 ----------
    def load_feed(self):
        epoch = self.feed_epoch
        self.background(self.client.exhibition_feed, lambda state: epoch == self.feed_epoch and self.fill_feed(state),
                        lambda _error: epoch == self.feed_epoch and self.show_state.setText("상태 조회 실패"))

    def fill_feed(self, state: dict):
        self.show_check.blockSignals(True)   # 서버 상태를 보여 줄 뿐 다시 켜기·끄기를 보내지 않는다
        self.show_check.setChecked(state["enabled"])
        self.show_check.blockSignals(False)
        reasons = {"gms_unavailable": "GMS 연결 실패", "head_unavailable": "분산 Head의 GMS 연결 실패",
                   "nodes_offline": "필요한 PC 꺼짐", "submit_rejected": "접수 거부(노드)", "feed_error": "디렉터 작업 오류"}
        if not state["enabled"]:
            text, style = "꺼짐", "color:#5F6368;"
        elif state["paused"]:
            text = f"켜짐 · 쉬는 중({reasons.get(state['pause_reason'], state['pause_reason'])}) · 잠시 뒤 다시 시도해요"
            style = "color:#B36B16; font-weight:600;"
        else:
            text, style = f"켜짐 · 진행 중 {state['in_flight']}/{state['max_in_flight']}", "color:#188038; font-weight:600;"
        self.show_state.setText(text)
        self.show_state.setStyleSheet(style)

    def toggle_exhibition(self, on: bool):
        self.feed_epoch += 1   # 켜기·끄기 전에 보낸 상태 조회의 늦은 응답은 버린다
        epoch = self.feed_epoch
        self.show_state.setText("켜는 중…" if on else "끄는 중…")

        def failed(error):
            self.show_error(error)
            if epoch == self.feed_epoch:
                self.load_feed()   # 체크박스를 서버 상태로 되돌린다

        self.background(lambda: self.client.set_exhibition_feed(on),
                        lambda state: epoch == self.feed_epoch and self.fill_feed(state), failed)

    def activate_show_panel(self, _index):
        selected = self.tabs.currentWidget() is self.show_tabs
        self.native_artworks.set_active(selected and self.show_tabs.currentIndex() == 0)
        self.dashboard.set_active(selected and self.show_tabs.currentIndex() == 1)

    def closeEvent(self, event):
        if self.native_artworks:
            self.native_artworks.shutdown()
        self.head_epoch += 1
        self.head_timer.stop()
        self.monitor.shutdown()
        self.feedback.shutdown()
        if self.dashboard:
            self.dashboard.shutdown()
        QApplication.instance().removeEventFilter(self.switch)
        super().closeEvent(event)
