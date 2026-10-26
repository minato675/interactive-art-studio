"""조회 전용 네이티브 운영 현황. 중앙 API를 호출하며 UE·DB를 직접 제어하지 않는다."""
import json
import re
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .client import ApiError, CentralClient

STAGES = {"background_generate": "배경 생성", "assets_generate": "에셋 생성", "ue_assemble": "UE 조립",
          "ue_verify": "실행 검수", "ue_publish": "화면 적용", "head_decide": "Head 판단", "head_decision": "Head 판단"}
STATES = {"queued": "대기", "running": "실행 중", "failed": "실패", "succeeded": "완료 보고", "cancelled": "취소"}


def outcome(job):
    result = job.get("result") or {}
    if job.get("kind") == "ue_publish":
        applied = (job.get("status") == "succeeded" and result.get("phase") == "Applied"
                   and result.get("runtime_status", {}).get("phase") == "Applied")
        if job.get("approval", {}).get("scope") == "display_preview_only":
            return "미리보기 Applied · 정식 승인 아님" if applied else "미리보기 적용 미확인"
        return "실행기 Applied 보고" if applied else "화면 적용 미확인"
    if job.get("kind") == "ue_verify":
        return {"pass": "검수 통과", "review": "사람 검토 필요", "fail": "검수 미달"}.get(
            result.get("verdict"), "검수 결과 대기")
    return "품질·적용 별도 확인"


def timestamp(value):
    try:
        return datetime.fromtimestamp(value).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return "미확인"


def error_text(error):
    if isinstance(error, ApiError):
        return "운영자 인증 실패 · 앱의 토큰 설정을 확인하세요" if error.status == 401 else f"조회 실패 (HTTP {error.status})"
    return "서버에 연결하지 못했습니다 · 주소·인증서·네트워크를 확인하세요"


class _Bridge(QObject):
    call = Signal(object)


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return widget


def rows(widget, values, ids=None):
    widget.setRowCount(len(values))
    for row, cells in enumerate(values):
        for column, value in enumerate(cells):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            if ids:
                item.setData(Qt.UserRole, ids[row])
            widget.setItem(row, column, item)
    widget.resizeRowsToContents()


class MonitorPanel(QWidget):
    PAGE_SIZE = 200

    def __init__(self, client: CentralClient, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.client, self.offset, self.loaded_offset = client, 0, 0
        self.jobs, self.nodes = [], []
        self.active, self.running, self.closed = False, True, False
        self.epoch, self.requests = 0, 0
        self._operations, self._head_id = {}, ""
        self.bridge = _Bridge(self)
        self.bridge.call.connect(lambda fn: fn())
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.connection = QLabel("운영 현황 탭을 선택하면 조회합니다")
        self.connection.setWordWrap(True)
        self.refresh_button, self.toggle_button = QPushButton("지금 갱신"), QPushButton("조회 중지")
        top.addWidget(self.connection, 1)
        top.addWidget(self.refresh_button)
        top.addWidget(self.toggle_button)
        layout.addLayout(top)
        layout.addWidget(QLabel("앱의 운영자 토큰 사용 · 조회 전용 · 5초 갱신 · FPS·CPU/GPU 시간은 중앙 미수집"))
        self.summary = QLabel("현재 목록 집계: 조회 전")
        layout.addWidget(self.summary)
        sections = QSplitter(Qt.Vertical)
        node_group = QGroupBox("실행기와 시연 화면")
        node_layout = QVBoxLayout(node_group)
        self.node_table = table(["실행기 / 역할", "연결 / 마지막 보고", "현재 작업", "시연 상태", "활성 / 요청 버전", "UE PID / 오류"])
        node_layout.addWidget(self.node_table)
        sections.addWidget(node_group)
        jobs_group = QGroupBox("최근 작업")
        jobs_layout = QVBoxLayout(jobs_group)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("작품·작업·노드 ID 또는 오류 검색 (현재 페이지)")
        self.filter = QComboBox()
        self.filter.addItem("전체 상태", "")
        for state, label in STATES.items():
            self.filter.addItem(label, state)
        self.previous, self.next_button = QPushButton("이전"), QPushButton("다음")
        self.page = QLabel("목록 없음")
        for widget in (self.search, self.filter, self.previous, self.next_button, self.page):
            filters.addWidget(widget)
        jobs_layout.addLayout(filters)
        self.job_table = table(["단계 / 생성 시각", "작품 / 버전", "작업 ID", "진행 / 시도", "검수·적용", "실행기 / 오류"])
        jobs_layout.addWidget(self.job_table)
        jobs_layout.addWidget(QLabel("완료 보고는 품질 통과·실제 화면 적용과 구분합니다. 집계는 현재 페이지 기준입니다."))
        sections.addWidget(jobs_group)
        bottom = QSplitter(Qt.Horizontal)
        head = QGroupBox("Head 감독 판단")
        head_layout = QVBoxLayout(head)
        head_form = QHBoxLayout()
        self.workflow_id = QLineEdit()
        self.workflow_id.setPlaceholderText("Head 흐름 ID")
        self.workflow_button = QPushButton("조회")
        head_form.addWidget(self.workflow_id)
        head_form.addWidget(self.workflow_button)
        head_layout.addLayout(head_form)
        self.workflow = QPlainTextEdit("흐름 ID를 입력하면 보류 이유와 최근 판단을 표시합니다.")
        self.workflow.setReadOnly(True)
        head_layout.addWidget(self.workflow)
        bottom.addWidget(head)
        detail = QGroupBox("선택한 작업 상세")
        detail_layout = QVBoxLayout(detail)
        self.detail = QPlainTextEdit("작업 행을 누르면 시도 이력과 선행 작업을 조회합니다.")
        self.detail.setReadOnly(True)
        detail_layout.addWidget(self.detail)
        bottom.addWidget(detail)
        sections.addWidget(bottom)
        sections.setSizes([170, 280, 200])
        layout.addWidget(sections, 1)
        self.refresh_button.clicked.connect(self.refresh)
        self.toggle_button.clicked.connect(self.toggle)
        self.search.textChanged.connect(self.render_jobs)
        self.filter.currentIndexChanged.connect(self.render_jobs)
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next_button.clicked.connect(lambda: self.change_page(1))
        self.job_table.cellClicked.connect(self.select_job)
        self.workflow_button.clicked.connect(self.query_workflow)
        self.workflow_id.returnPressed.connect(self.query_workflow)
        self.render_jobs()

    def set_active(self, active):
        self.active = active
        if active and self.running:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()

    def toggle(self):
        self.running = not self.running
        self.epoch += 1
        self._operations.clear()
        self.toggle_button.setText("조회 중지" if self.running else "조회 시작")
        if self.running:
            self.set_active(self.active)
        else:
            self.timer.stop()
            self.clear_data()
            self.connection.setText("조회 중지 · 자료를 지웠습니다")

    def clear_data(self):
        self.jobs, self.nodes = [], []
        self.node_table.setRowCount(0)
        self.render_jobs()
        self.summary.setText("현재 목록 집계: 자료 없음")
        self.workflow.setPlainText("인증 후 Head 흐름을 조회하세요.")
        self.detail.setPlainText("인증 후 작업을 선택하세요.")

    def shutdown(self):
        self.closed = True
        self.epoch += 1
        self.timer.stop()

    def _launch(self, kind, function, success, failure=None, replace=False):
        if self.closed or not self.running or (kind in self._operations and not replace):
            return
        token, epoch = object(), self.epoch
        self._operations[kind] = token
        self.requests += 1

        def work():
            value, error = None, None
            try:
                value = function()
            except Exception as exc:  # noqa: BLE001 — 조회 오류는 화면 스레드에서 안전한 문구로 표시
                error = exc
            try:
                self.bridge.call.emit(lambda: self._finish(kind, token, epoch, success, failure, value, error))
            except RuntimeError:  # 창이 먼저 닫혀 Qt 객체가 삭제된 경우
                pass

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, kind, token, epoch, success, failure, value, error):
        self.requests -= 1
        if self.closed or epoch != self.epoch or self._operations.get(kind) is not token:
            return
        del self._operations[kind]
        if error is None:
            success(value)
        elif isinstance(error, ApiError) and error.status == 401:
            self.toggle()
            self.connection.setText(error_text(error))
        elif failure:
            failure(error)
        else:
            self.refresh_failed(error)

    def refresh(self):
        if self.active:
            offset = self.offset
            self._launch("refresh", lambda: self.client.monitor_snapshot(offset, self.PAGE_SIZE),
                         lambda snapshot: self.refreshed(snapshot, offset))

    def refreshed(self, snapshot, offset=0):
        self.loaded_offset = offset
        self.jobs, self.nodes = snapshot["jobs"], snapshot["nodes"]
        self.connection.setText("마지막 수신 " + datetime.now().strftime("%H:%M:%S"))
        self.summary.setText("현재 목록 · 실행 중 {} · 대기 {} · 실패 {} · 온라인 실행기 {}/{}".format(
            *(sum(j["status"] == state for j in self.jobs) for state in ("running", "queued", "failed")),
            sum(bool(n.get("online")) for n in self.nodes), len(self.nodes)))
        self.render_nodes(fresh=True)
        self.render_jobs()
        if self._head_id:
            self._load_workflow(self._head_id)

    def refresh_failed(self, error):
        self.connection.setText(error_text(error) + " · 표시된 값은 마지막 수신 자료")
        self.render_nodes(fresh=False)

    def render_nodes(self, fresh):
        values = []
        for node in self.nodes:
            report = node.get("show_report") or {}
            online = bool(node.get("online")) and fresh
            phase = (report.get("phase") or "미확인") if online else "최근 보고 · 현재 미확인"
            values.append([f"{node['id']} / {node['role']}",
                           f"{'온라인' if online else '오프라인·미확인'} / {timestamp(node.get('heartbeat_at'))}",
                           node.get("active_job") or "없음", phase if node["role"] == "show" else "—",
                           f"{report.get('active_version') or '미확인'} / {report.get('desired_version') or '미확인'}"
                           if node["role"] == "show" else "—",
                           f"{report.get('runtime_pid') or '미확인'} / {report.get('error') or '없음'}"
                           if node["role"] == "show" else "—"])
        rows(self.node_table, values)

    def render_jobs(self):
        query, state = self.search.text().strip().lower(), self.filter.currentData()
        selected = [j for j in self.jobs if (not state or j["status"] == state) and
                    (not query or any(query in str(j.get(k) or "").lower()
                                      for k in ("artwork_id", "id", "node_id", "error")))]
        rows(self.job_table, [[f"{STAGES.get(j['kind'], j['kind'])}\n{timestamp(j.get('created_at'))}",
             f"{j.get('artwork_id') or '미지정'} / {j.get('artwork_version') or ''}", j["id"],
             f"{STATES.get(j['status'], j['status'])} {j.get('progress', 0)}%\n시도 {j.get('attempt', 0)}/{j.get('max_attempts', 0)}",
             outcome(j), f"{j.get('node_id') or '미배정'}\n{j.get('error') or ''}"] for j in selected],
             [j["id"] for j in selected])
        self.page.setText(f"{self.loaded_offset + 1}~{self.loaded_offset + len(self.jobs)} · 검색 {len(selected)}개"
                          if self.jobs else "작업 없음")
        self.previous.setEnabled(self.offset > 0)
        self.next_button.setEnabled(len(self.jobs) == self.PAGE_SIZE)

    def change_page(self, direction):
        self.offset = max(0, self.offset + direction * self.PAGE_SIZE)
        self.epoch += 1
        self._operations.clear()
        self.detail.setPlainText("작업 행을 선택하세요.")
        self.refresh()

    def select_job(self, row, column):
        item = self.job_table.item(row, 0)
        if item is None:
            return
        job_id = item.data(Qt.UserRole)
        self.detail.setPlainText(f"조회 중: {job_id}")
        self._launch("detail", lambda: self.client.job(job_id),
                     lambda value: self.detail.setPlainText(json.dumps(value, ensure_ascii=False, indent=2)),
                     lambda error: self.detail.setPlainText(error_text(error)), replace=True)

    def query_workflow(self):
        workflow_id = self.workflow_id.text().strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", workflow_id):
            self.workflow.setPlainText("흐름 ID는 영문·숫자·점·밑줄·하이픈으로 입력하세요.")
            return
        self._head_id = workflow_id
        self.workflow.setPlainText("Head 조회 중…")
        self._load_workflow(workflow_id, replace=True)

    def _load_workflow(self, workflow_id, replace=False):
        def render(value):
            flow, decisions = value.get("payload", {}).get("_flow", {}), value.get("decisions") or []
            last = decisions[-1] if decisions else {}
            self.workflow.setPlainText("\n".join([
                f"상태: {value['status']}", f"흐름 ID: {value['id']}",
                f"배경 작업: {value.get('background_job') or '없음'}", f"원본 에셋 작업: {len(value.get('asset_jobs') or [])}개",
                f"보류 이유: {flow.get('hold') or '없음'}",
                f"최근 판단: {last.get('plan', {}).get('decision') or last.get('phase') or '없음'}",
                last.get("plan", {}).get("reason") or ""]))
        self._launch("head", lambda: self.client.head_workflow(workflow_id), render,
                     lambda error: self.workflow.setPlainText("Head 조회 실패 · " + error_text(error)), replace=replace)
