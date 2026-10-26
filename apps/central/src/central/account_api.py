"""공개 가입 없이 관리자 발급 계정으로 운영 앱을 인증한다."""
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from contracts.auth import AccountCreate, AccountUpdate, LoginRequest, PasswordChange
from db.accounts import LoginLimit, OperatorAccount, OperatorSession

from .account_auth import admin, identity, token_digest
from .accounts import create_account, password_hash, password_matches, revoke_sessions

router = APIRouter(prefix="/auth", tags=["operator-accounts"])
DUMMY_HASH = password_hash(secrets.token_urlsafe(32))
SESSION_SECONDS = 8 * 60 * 60


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    no_store(response)
    now = time.time()
    username = body.username.lower()
    client = request.client.host if request.client else "unknown"
    with Session(request.app.state.engine) as db:
        # DB에 보관하므로 Worker 수·서버 재시작과 무관하다. X-Forwarded-For는 신뢰하지 않는다.
        limits = []
        for key, maximum in [("client:" + client, 30), ("user:" + username, 5)]:
            digest = token_digest(key)
            if db.get(LoginLimit, digest) is None:
                try:
                    with db.begin_nested():
                        db.add(LoginLimit(key=digest, window_start=now))
                        db.flush()
                except IntegrityError:
                    pass  # 다른 요청이 만든 제한 행을 사용하되 앞서 잡은 행 잠금은 유지한다.
            row = db.exec(select(LoginLimit).where(LoginLimit.key == digest).with_for_update()).one()
            if row.locked_until > now:
                raise HTTPException(429, "로그인 시도가 많습니다. 10분 뒤 다시 시도하세요", headers={"Retry-After": "600"})
            if now - row.window_start > 600:
                row.failures, row.window_start, row.locked_until = 0, now, 0
            limits.append((row, maximum))
        account = db.exec(select(OperatorAccount).where(OperatorAccount.username == username).with_for_update()).first()
        valid = password_matches(body.password.get_secret_value(), account.password_hash if account else DUMMY_HASH)
        if not valid or not account or not account.enabled:
            for row, maximum in limits:
                row.failures += 1
                if row.failures >= maximum:
                    row.locked_until = now + 600
                db.add(row)
            db.commit()
            raise HTTPException(401, "ID 또는 비밀번호를 확인하세요")
        # 성공한 계정의 실패 횟수만 초기화한다. 클라이언트의 실패 제한은 유지한다.
        limits[1][0].failures = 0
        db.add(limits[1][0])
        db.exec(delete(OperatorSession).where(OperatorSession.expires_at <= now))
        token = secrets.token_urlsafe(32)
        db.add(OperatorSession(digest=token_digest(token), username=username, expires_at=now + SESSION_SECONDS))
        db.commit()
        return {"access_token": token, "token_type": "bearer", "expires_in": SESSION_SECONDS,
                "username": username, "role": account.role}


@router.get("/me")
def me(request: Request, response: Response):
    no_store(response)
    return identity(request)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    no_store(response)
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    with Session(request.app.state.engine) as db:
        db.exec(delete(OperatorSession).where(OperatorSession.digest == token_digest(token)))
        db.commit()


@router.post("/password", status_code=204)
def change_password(body: PasswordChange, request: Request, response: Response):
    user = identity(request)
    if user["legacy"]:
        raise HTTPException(403, "계정으로 로그인하세요")
    with Session(request.app.state.engine) as db:
        row = db.exec(select(OperatorAccount).where(OperatorAccount.username == user["username"]).with_for_update()).one()
        if not password_matches(body.current_password.get_secret_value(), row.password_hash):
            raise HTTPException(401, "현재 비밀번호를 확인하세요")
        row.password_hash = password_hash(body.new_password.get_secret_value())
        revoke_sessions(db, row.username)
        db.add(row)
        db.commit()
    no_store(response)


@router.get("/accounts")
def accounts(request: Request, response: Response):
    admin(request)
    no_store(response)
    with Session(request.app.state.engine) as db:
        return [{"username": x.username, "role": x.role, "enabled": x.enabled}
                for x in db.exec(select(OperatorAccount).order_by(OperatorAccount.username))]


@router.post("/accounts", status_code=201)
def add_account(body: AccountCreate, request: Request, response: Response):
    admin(request)
    no_store(response)
    with Session(request.app.state.engine) as db:
        try:
            row = create_account(db, body)
        except (ValueError, IntegrityError):
            db.rollback()
            raise HTTPException(409, "이미 등록된 ID입니다") from None
        return {"username": row.username, "role": row.role, "enabled": row.enabled}


@router.put("/accounts/{username}")
def update_account(username: str, body: AccountUpdate, request: Request, response: Response):
    actor = admin(request)
    with Session(request.app.state.engine) as db:
        row = db.exec(select(OperatorAccount).where(OperatorAccount.username == username.lower()).with_for_update()).first()
        if not row:
            raise HTTPException(404, "계정이 없습니다")
        if row.username == actor["username"] and body.enabled is False:
            raise HTTPException(409, "자기 계정은 비활성화할 수 없습니다")
        if row.role == "admin" and body.enabled is False:
            # 관리자 계정 잠금은 직렬화해 마지막 관리자가 사라지지 않게 한다.
            admins = db.exec(select(OperatorAccount).where(
                OperatorAccount.role == "admin", OperatorAccount.enabled == True).with_for_update()).all()
            if len(admins) <= 1:
                raise HTTPException(409, "마지막 관리자는 비활성화할 수 없습니다")
        if body.password is not None:
            row.password_hash = password_hash(body.password.get_secret_value())
        if body.enabled is not None:
            row.enabled = body.enabled
        revoke_sessions(db, row.username)
        db.add(row)
        db.commit()
        no_store(response)
        return {"username": row.username, "role": row.role, "enabled": row.enabled}
