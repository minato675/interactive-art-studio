"""uv run a504-operator  (central 주소·토큰: CENTRAL_URL · OPERATOR_TOKEN 환경 변수)"""
import os
import sys
from pathlib import Path

import httpx
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from .client import ApiError, CentralClient
from .login import AccountsDialog, LoginDialog, PasswordDialog
from .window import APP_NAME, OperatorWindow

ICON = Path(__file__).with_name("assets") / "artomation.png"


class SessionBridge(QObject):
    expired = Signal()


def main() -> None:
    if sys.platform == "win32":   # 작업 표시줄이 python.exe 대신 이 앱의 아이콘을 쓰게 한다
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("a504.artomation")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    server = os.environ.get("CENTRAL_URL", "https://k15a504.p.ssafy.io")
    client = CentralClient(server, os.environ.get("OPERATOR_TOKEN") or None)
    user = None
    if not os.environ.get("OPERATOR_TOKEN"):
        login = LoginDialog(client, server)
        if login.exec() != QDialog.Accepted:
            client.http.close()
            return
        user = login.user
    window = OperatorWindow(client, show_dashboard=os.environ.get("OPERATOR_SHOW_DASHBOARD", "1") != "0")
    if user:
        window.setWindowTitle(f"{APP_NAME} — 운영 · {user['username']}")
        account_menu = window.menuBar().addMenu("계정")
        if user["role"] == "admin":
            account_menu.addAction("계정 관리", lambda: AccountsDialog(client, window).exec())
        account_menu.addAction("내 비밀번호 변경", lambda: PasswordDialog(client, window).exec())
        account_menu.addAction("로그아웃 후 종료", app.quit)
        bridge = SessionBridge(window)

        def expired():
            client.on_unauthorized = None
            QMessageBox.information(window, "로그인 만료", "세션이 만료되었습니다. 앱을 다시 실행해 로그인하세요.")
            app.quit()

        bridge.expired.connect(expired)
        client.on_unauthorized = bridge.expired.emit
        def cleanup():
            try:
                client.logout()
            except (ApiError, httpx.HTTPError):
                client.http.headers.pop("Authorization", None)
        app.aboutToQuit.connect(cleanup)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
