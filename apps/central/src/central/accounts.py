"""서버 운영자의 최초 계정 생성: python -m central.accounts create ID --admin."""
import argparse
import getpass
import hashlib
import hmac
import secrets

from pydantic import ValidationError
from sqlalchemy import delete
from sqlmodel import Session

from contracts.auth import AccountCreate
from db.accounts import OperatorAccount, OperatorSession
from db.engine import create_tables, make_engine

ITERATIONS = 600_000


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def password_matches(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt, expected = stored.split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def revoke_sessions(db: Session, username: str) -> None:
    db.exec(delete(OperatorSession).where(OperatorSession.username == username))


def create_account(db: Session, body: AccountCreate) -> OperatorAccount:
    username = body.username.lower()
    if db.get(OperatorAccount, username):
        raise ValueError("이미 등록된 ID입니다")
    row = OperatorAccount(username=username, password_hash=password_hash(body.password.get_secret_value()), role=body.role)
    db.add(row)
    db.commit()
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "reset-password"])
    parser.add_argument("username")
    parser.add_argument("--admin", action="store_true")
    args = parser.parse_args()
    first = getpass.getpass("Password (12-128 characters): ")
    if first != getpass.getpass("Confirm password: "):
        parser.error("비밀번호가 다릅니다")
    try:
        body = AccountCreate(username=args.username, password=first, role="admin" if args.admin else "operator")
    except ValidationError:
        parser.error("ID는 영문·숫자·_·.·- 3~64자, 비밀번호는 12~128자여야 합니다")
    from .config import Settings
    engine = make_engine(Settings().db_url)
    create_tables(engine)
    with Session(engine) as db:
        if args.action == "create":
            create_account(db, body)
        else:
            row = db.get(OperatorAccount, body.username.lower())
            if not row:
                parser.error("등록되지 않은 ID입니다")
            row.password_hash = password_hash(first)
            revoke_sessions(db, row.username)
            db.add(row)
            db.commit()
    print("계정 설정 완료")


if __name__ == "__main__":
    main()
