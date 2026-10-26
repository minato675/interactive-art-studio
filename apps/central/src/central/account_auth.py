"""계정 로그인과 기존 운영 도구 인증. Agent 인증에는 사용하지 않는다."""
import hashlib
import secrets
import time

from fastapi import HTTPException, Request
from sqlmodel import Session

from db.accounts import OperatorAccount, OperatorSession


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def identity(request: Request, *, allow_local: bool = False) -> dict:
    expected = request.app.state.settings.operator_token
    legacy = request.headers.get("x-operator-token", "")
    if expected and legacy and secrets.compare_digest(expected.encode(), legacy.encode()):
        return {"username": "server-token", "role": "admin", "legacy": True}
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer ") and 20 <= len(authorization) <= 200:
        with Session(request.app.state.engine) as db:
            session = db.get(OperatorSession, token_digest(authorization[7:]))
            if session and session.expires_at > time.time():
                account = db.get(OperatorAccount, session.username)
                if account and account.enabled:
                    return {"username": account.username, "role": account.role, "legacy": False}
    if allow_local and not expected and not authorization:
        return {"username": "local-development", "role": "admin", "legacy": True}
    raise HTTPException(401, "로그인이 필요하거나 세션이 만료되었습니다", headers={"WWW-Authenticate": "Bearer"})


def admin(request: Request) -> dict:
    user = identity(request)
    if user["role"] != "admin":
        raise HTTPException(403, "관리자 권한이 필요합니다")
    return user
