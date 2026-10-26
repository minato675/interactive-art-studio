"""ID·비밀번호 로그인과 관리자 계정 발급. 비밀번호·세션을 디스크에 저장하지 않는다."""
import threading

import httpx
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .client import ApiError


class Bridge(QObject):
    finished = Signal(object, object)


class AsyncDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bridge = Bridge(self)
        self.bridge.finished.connect(self.completed)
        self.running = False
        self.callback = None

    def run(self, fn, callback):
        if self.running:
            return
        self.running, self.callback = True, callback
        self.setEnabled(False)

        def worker():
            try:
                result, error = fn(), None
            except ApiError as exc:
                result, error = None, exc.detail
            except (httpx.HTTPError, OSError, ValueError):
                result, error = None, "서버에 연결하지 못했습니다. 주소·네트워크·인증서를 확인하세요."
            self.bridge.finished.emit(result, error)

        threading.Thread(target=worker, daemon=True).start()

    def completed(self, result, error):
        self.running = False
        self.setEnabled(True)
        if error:
            self.message.setText(error)
        else:
            self.callback(result)

    def reject(self):
        if not self.running:
            super().reject()


def password_field():
    widget = QLineEdit()
    widget.setEchoMode(QLineEdit.Password)
    widget.setMaxLength(128)
    return widget


class LoginDialog(AsyncDialog):
    def __init__(self, client, server, parent=None):
        super().__init__(parent)
        self.client, self.user = client, None
        self.setWindowTitle("Artomation — 로그인")
        self.resize(420, 260)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("운영 서버: " + server))
        form = QFormLayout()
        self.username, self.password = QLineEdit(), password_field()
        self.username.setMaxLength(64)
        form.addRow("ID", self.username)
        form.addRow("비밀번호", self.password)
        layout.addLayout(form)
        self.message = QLabel("관리자가 발급한 계정으로 로그인하세요.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.submit = QPushButton("로그인")
        self.submit.clicked.connect(self.sign_in)
        self.password.returnPressed.connect(self.sign_in)
        layout.addWidget(self.submit)

    def sign_in(self):
        username, password = self.username.text().strip(), self.password.text()
        self.password.clear()
        if not username or not password:
            self.message.setText("ID와 비밀번호를 입력하세요.")
            return
        self.message.setText("로그인 중…")
        self.run(lambda: self.client.login(username, password), self.signed_in)

    def signed_in(self, user):
        self.user = user
        self.accept()


class AccountsDialog(AsyncDialog):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client, self.rows = client, []
        self.setWindowTitle("관리자 — 계정 관리")
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        self.accounts = QListWidget()
        self.accounts.currentRowChanged.connect(self.select_account)
        layout.addWidget(self.accounts)
        form = QFormLayout()
        self.username, self.password, self.confirm = QLineEdit(), password_field(), password_field()
        self.username.setMaxLength(64)
        self.role = QComboBox()
        self.role.addItems(["operator", "admin"])
        form.addRow("새 계정 ID / 선택한 ID", self.username)
        form.addRow("비밀번호 (12~128자)", self.password)
        form.addRow("비밀번호 확인", self.confirm)
        form.addRow("새 계정 권한", self.role)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        for label, action in [("새 계정 생성", self.create), ("비밀번호 재설정", self.reset),
                              ("사용 중지 / 재개", self.toggle)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.message = QLabel("목록 조회 중…")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.refresh()

    def refresh(self, _=None):
        self.run(self.client.accounts, self.show_accounts)

    def show_accounts(self, rows):
        self.rows = rows
        self.accounts.clear()
        self.accounts.addItems([f"{x['username']} · {x['role']} · {'사용' if x['enabled'] else '중지'}" for x in rows])
        self.message.setText("ID는 영문·숫자·밑줄·점·하이픈 3~64자입니다. 새 ID는 입력란에 직접 적으세요.")

    def select_account(self, index):
        if 0 <= index < len(self.rows):
            self.username.setText(self.rows[index]["username"])

    def secret(self):
        password = self.password.text()
        if password != self.confirm.text() or not 12 <= len(password) <= 128:
            self.message.setText("비밀번호를 12~128자로 입력하고 확인란을 맞춰 주세요.")
            return None
        self.password.clear()
        self.confirm.clear()
        return password

    def create(self):
        password = self.secret()
        if password:
            username, role = self.username.text().strip(), self.role.currentText()
            self.run(lambda: self.client.create_account(username, password, role), self.refresh)

    def reset(self):
        password = self.secret()
        if password:
            username = self.username.text().strip()
            self.run(lambda: self.client.update_account(username, {"password": password}), self.refresh)

    def toggle(self):
        row = next((x for x in self.rows if x["username"] == self.username.text().strip()), None)
        if row and QMessageBox.question(self, "계정 상태 변경", f"{row['username']} 계정의 사용 상태를 변경할까요?") \
                == QMessageBox.Yes:
            self.run(lambda: self.client.update_account(row["username"], {"enabled": not row["enabled"]}), self.refresh)


class PasswordDialog(AsyncDialog):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.setWindowTitle("내 비밀번호 변경")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.current, self.password, self.confirm = password_field(), password_field(), password_field()
        form.addRow("현재 비밀번호", self.current)
        form.addRow("새 비밀번호 (12~128자)", self.password)
        form.addRow("새 비밀번호 확인", self.confirm)
        layout.addLayout(form)
        self.message = QLabel("변경하면 모든 세션이 종료됩니다. 앱을 다시 실행해 로그인하세요.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        button = QPushButton("비밀번호 변경 후 종료")
        layout.addWidget(button)

        def change():
            old, new = self.current.text(), self.password.text()
            if not 12 <= len(new) <= 128 or new != self.confirm.text():
                self.message.setText("새 비밀번호 길이와 확인란을 확인하세요.")
                return
            self.current.clear()
            self.password.clear()
            self.confirm.clear()
            self.run(lambda: client.change_password(old, new), lambda _: QApplication.quit())

        button.clicked.connect(change)
