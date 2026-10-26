"""앱의 계정 입력·세션 헤더·비동기 로그인 검사. 실제 서버·AI 호출 없음."""
import os
import time

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog

from operator_app.client import CentralClient
from operator_app.login import LoginDialog


def test_login_uses_bearer_and_logout_removes_it():
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"access_token": "one-session-only", "username": "owner", "role": "admin"})
        if request.url.path == "/auth/logout":
            return httpx.Response(204)
        return httpx.Response(200, json=[])
    http = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handle))
    client = CentralClient(http=http)
    user = client.login("owner", "test-password")
    assert "access_token" not in user
    client.nodes()
    assert calls[-1].headers["Authorization"] == "Bearer one-session-only"
    assert "X-Operator-Token" not in calls[-1].headers
    client.logout()
    assert "Authorization" not in http.headers
    http.close()


def test_login_dialog_clears_password_and_does_not_block_ui():
    app = QApplication.instance() or QApplication([])
    class Client:
        def login(self, username, password):
            time.sleep(0.1)
            assert (username, password) == ("owner", "test-password")
            return {"username": username, "role": "admin"}
    dialog = LoginDialog(Client(), "https://example.test")
    dialog.username.setText("owner")
    dialog.password.setText("test-password")
    dialog.sign_in()
    assert dialog.running
    assert dialog.password.text() == ""
    deadline = time.monotonic() + 3
    while dialog.running and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert dialog.result() == QDialog.Accepted
    assert dialog.user["username"] == "owner"
    dialog.close()
