"""계정 로그인은 운영자 비밀을 배포하지 않고 기존 운영 API만 허용한다."""
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from central.accounts import create_account
from central.config import Settings
from central.main import create_app
from contracts.auth import AccountCreate
from db.accounts import LoginLimit, OperatorAccount, OperatorSession

PASSWORD = "test-only-password-123!"


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, operator_token="server-only-test-token"))
    with TestClient(app) as client:
        with Session(app.state.engine) as db:
            create_account(db, AccountCreate(username="owner", password=PASSWORD, role="admin"))
            create_account(db, AccountCreate(username="worker", password=PASSWORD))
        yield client


def sign_in(client, username="owner", password=PASSWORD):
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_login_hashes_and_authorizes_both_api_paths_without_server_token(client):
    assert client.get("/api/artworks").status_code == 401
    assert client.get("/infra/jobs").status_code == 401
    headers = sign_in(client, "OWNER")
    assert client.get("/api/artworks", headers=headers).status_code == 200
    assert client.get("/infra/jobs", headers=headers).status_code == 200
    assert client.get("/auth/me", headers=headers).json()["username"] == "owner"
    with Session(client.app.state.engine) as db:
        account = db.get(OperatorAccount, "owner")
        assert PASSWORD not in account.password_hash
        session = db.exec(select(OperatorSession)).one()
        assert headers["Authorization"][7:] not in session.digest
    assert "server-only-test-token" not in client.get("/auth/me", headers=headers).text
    assert client.get("/infra/jobs", headers={"X-Operator-Token": "server-only-test-token"}).status_code == 200
    # 계정 세션으로 Agent 전용 입구에 접근할 수 없다.
    assert client.get("/infra/agent/identity", headers=headers).status_code == 401


def test_operator_cannot_issue_accounts_and_no_public_signup(client):
    body = {"username": "new-user", "password": PASSWORD}
    assert client.post("/auth/accounts", json=body).status_code == 401
    headers = sign_in(client, "worker")
    assert client.post("/auth/accounts", json=body, headers=headers).status_code == 403
    headers = sign_in(client)
    assert client.post("/auth/accounts", json=body, headers=headers).status_code == 201
    assert client.post("/auth/accounts", json=body, headers=headers).status_code == 409
    result = client.get("/auth/accounts", headers=headers)
    assert "password" not in result.text
    assert {x["username"] for x in result.json()} == {"owner", "worker", "new-user"}


def test_logout_expiry_disable_and_reset_revoke_sessions(client):
    admin = sign_in(client)
    worker = sign_in(client, "worker")
    assert client.post("/auth/logout", headers=worker).status_code == 204
    assert client.get("/infra/jobs", headers=worker).status_code == 401
    worker = sign_in(client, "worker")
    with Session(client.app.state.engine) as db:
        row = db.exec(select(OperatorSession).where(OperatorSession.username == "worker")).one()
        row.expires_at = time.time() - 1
        db.add(row)
        db.commit()
    assert client.get("/infra/jobs", headers=worker).status_code == 401
    worker = sign_in(client, "worker")
    assert client.put("/auth/accounts/worker", headers=admin, json={"password": PASSWORD + "new"}).status_code == 200
    assert client.get("/infra/jobs", headers=worker).status_code == 401
    worker = sign_in(client, "worker", PASSWORD + "new")
    assert client.put("/auth/accounts/worker", headers=admin, json={"enabled": False}).status_code == 200
    assert client.get("/infra/jobs", headers=worker).status_code == 401
    assert client.post("/auth/login", json={"username": "worker", "password": PASSWORD + "new"}).status_code == 401
    assert client.put("/auth/accounts/owner", headers=admin, json={"enabled": False}).status_code == 409


def test_password_change_requires_current_password_and_revokes_all(client):
    first, second = sign_in(client), sign_in(client)
    assert client.post("/auth/password", headers=first, json={
        "current_password": "wrong", "new_password": PASSWORD + "new"}).status_code == 401
    assert client.post("/auth/password", headers=first, json={
        "current_password": PASSWORD, "new_password": PASSWORD + "new"}).status_code == 204
    assert client.get("/infra/jobs", headers=first).status_code == 401
    assert client.get("/infra/jobs", headers=second).status_code == 401
    sign_in(client, password=PASSWORD + "new")


def test_failure_limit_persists_and_does_not_echo_credentials(client):
    for _ in range(5):
        response = client.post("/auth/login", json={"username": "missing", "password": "bad-password"})
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert "bad-password" not in response.text
    assert client.post("/auth/login", json={"username": "missing", "password": "bad-password"}).status_code == 429
    with Session(client.app.state.engine) as db:
        assert any(x.locked_until > time.time() for x in db.exec(select(LoginLimit)))
    # 유효성 오류도 Pydantic 기본 input 필드를 통해 원문을 반환하지 않는다.
    response = client.post("/auth/login", json={"username": "x", "password": "sensitive-123"})
    assert response.status_code == 422
    assert "sensitive-123" not in response.text
