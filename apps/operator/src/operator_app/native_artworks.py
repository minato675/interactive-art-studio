"""최근 native 아카이브 목록과 실제 PC2 보고. 요청 저장을 재생 완료로 표시하지 않는다."""

import threading
import uuid

import httpx
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .client import ApiError
from .dashboard import SHOW_STYLE, table
from .monitor import _Bridge, error_text

QUALITY = {"reference": "기준작", "local_candidate": "후보", "needs_revision": "개선 필요"}
SENSOR = {"user_confirmed": "기존 로컬 손 확인", "synthetic_only": "모의 입력 확인", "weak_original": "손 반응 약함"}


class NativeArtworkPanel(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client, self.active, self.closed = client, False, False
        self.requests, self.snapshot = 0, None
        self.bridge = _Bridge()
        self.bridge.call.connect(lambda fn: fn())
        self.setObjectName("page")
        self.setStyleSheet(SHOW_STYLE)
        layout = QVBoxLayout(self)
        self.title = QLabel("최근 아카이브 · PC2 선택 재생")
        self.title.setObjectName("dashboardTitle")
        layout.addWidget(self.title)
        note = QLabel("28개용 PC2 원클릭 설치가 한 번 필요합니다. 요청 저장과 실제 PC2 실행 보고를 구분합니다.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.connection, self.playback = QLabel("아카이브 조회 대기"), QLabel("PC2 실행 상태 미확인")
        self.connection.setWordWrap(True)
        self.playback.setWordWrap(True)
        layout.addWidget(self.connection)
        layout.addWidget(self.playback)
        self.library = table(["작품 ID", "제목", "기존 품질", "기존 입력 확인"])
        self.library.itemSelectionChanged.connect(self.buttons)
        layout.addWidget(self.library)
        controls = QHBoxLayout()
        self.duration = QSpinBox()
        self.duration.setRange(5, 3600)
        self.duration.setValue(120)
        self.duration.setSuffix(" 초")
        self.loop = QCheckBox("선택 작품 반복")
        self.loop.setChecked(True)
        self.play_button = QPushButton("선택 작품 재생 요청")
        self.play_button.setObjectName("applyShow")
        self.stop_button, self.refresh_button = QPushButton("PC2 재생 정지 요청"), QPushButton("새로고침")
        self.play_button.clicked.connect(self.play)
        self.stop_button.clicked.connect(self.stop)
        self.refresh_button.clicked.connect(self.refresh)
        for widget in (self.duration, self.loop, self.play_button, self.stop_button, self.refresh_button):
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.message = QLabel("원본 28개만 표시합니다. 반려본·다른 수정본을 임의로 실행 대상에 넣지 않습니다.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.buttons()

    def selected_id(self):
        row = self.library.currentRow()
        item = self.library.item(row, 0)
        return item.text() if item else None

    def buttons(self):
        available = self.snapshot is not None and self.requests == 0 and not self.closed
        self.play_button.setEnabled(available and self.selected_id() is not None)
        self.stop_button.setEnabled(available)
        self.refresh_button.setEnabled(self.requests == 0)

    def set_active(self, active):
        self.active = active
        if active:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()

    def run(self, fn, after=None):
        if self.requests or self.closed:
            return
        self.requests += 1
        self.buttons()

        def worker():
            try:
                result, error = fn(), None
            except (ApiError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                result, error = None, error_text(exc)
            self.bridge.call.emit(lambda: self.completed(result, error, after))

        threading.Thread(target=worker, daemon=True).start()

    def completed(self, result, error, after):
        self.requests -= 1
        if self.closed:
            return
        if error:
            self.snapshot = None
            self.connection.setText("아카이브 조회/요청 실패 · 현재 실행 상태 미확인")
            self.playback.setText("마지막 화면 자료로 재생 중이라고 판단하지 않습니다.")
            self.message.setText(error)
        else:
            self.render(result)
            if after:
                self.message.setText(after)
        self.buttons()

    def render(self, value):
        selected = self.selected_id()
        self.snapshot = value
        rows = value["library"]
        self.library.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = [
                row["id"],
                row["title"],
                QUALITY.get(row["quality_status"], row["quality_status"]),
                SENSOR.get(row["sensor_status"], row["sensor_status"]),
            ]
            for col, text in enumerate(values):
                self.library.setItem(index, col, QTableWidgetItem(str(text)))
            if row["id"] == selected:
                self.library.selectRow(index)
        plan = value["plan"]
        self.connection.setText(
            f"아카이브 {len(rows)}개 · 요청 revision {plan['revision']} · "
            + ("PC2 연결됨" if value["online"] else "PC2 미연결 · 설치/접속 보고 대기")
        )
        wanted = next((x["id"] for x in plan["items"] if x["enabled"]), "없음")
        if value["playing_confirmed"]:
            report = value["device"]
            self.playback.setText(
                f"PC2 실행 보고: {report['artwork_id']} · PID {report['runtime_pid']} · 센서·현장 LED 검증은 별도"
            )
        elif plan["autoplay"]:
            self.playback.setText(f"재생 요청: {wanted} · 실제 PC2 실행 확인 대기")
        else:
            report = value.get("device") or {}
            stopped = (
                value["online"]
                and report.get("queue_revision") == plan["revision"]
                and report.get("state") in ("paused", "stopped")
            )
            self.playback.setText("PC2 정지 보고 확인" if stopped else "정지 요청 상태 · PC2 확인 대기")

    def refresh(self):
        self.run(self.client.native_artworks)

    def play(self):
        ident = self.selected_id()
        if not self.snapshot or not ident:
            return
        body = {
            "artwork_id": ident,
            "expected_revision": self.snapshot["plan"]["revision"],
            "request_id": uuid.uuid4().hex,
            "duration_seconds": self.duration.value(),
            "loop": self.loop.isChecked(),
        }
        self.run(lambda: self.client.native_play(body), "재생 요청 저장 · PC2가 연결되어 동기화한 뒤 실행합니다.")

    def stop(self):
        if not self.snapshot:
            return
        body = {"expected_revision": self.snapshot["plan"]["revision"], "request_id": uuid.uuid4().hex}
        self.run(lambda: self.client.native_stop(body), "정지 요청 저장 · PC2의 정지 보고를 기다립니다.")

    def shutdown(self):
        self.closed = True
        self.timer.stop()
