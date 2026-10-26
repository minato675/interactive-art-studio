"""Check Linux native modules, a temporary SQLModel DB, and the running local server."""
import argparse
import hashlib
import importlib
import json
import os
import platform
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import sqlalchemy
import sqlmodel
from sqlmodel import Field, Session, SQLModel, create_engine, select


class DockerProbe(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    value: str


def check_database() -> None:
    if sys.platform != "linux":
        raise RuntimeError("Run this check inside the Linux container.")
    root = Path(sqlalchemy.__file__).parent
    extensions = sorted(root.rglob("*.so"))
    if not extensions or list(root.rglob("*.pyd")):
        raise RuntimeError("Expected Linux SQLAlchemy extensions, without Windows .pyd files.")
    for extension in extensions:
        relative = extension.relative_to(root)
        module = ".".join(("sqlalchemy", *relative.parts[:-1], relative.name.split(".")[0]))
        importlib.import_module(module)
    with tempfile.TemporaryDirectory(prefix="a504-docker-db-") as directory:
        engine = create_engine(f"sqlite:///{Path(directory) / 'probe.db'}")
        try:
            DockerProbe.__table__.create(engine)
            with Session(engine) as session:
                session.add(DockerProbe(value="linux-db-ok"))
                session.commit()
            with Session(engine) as session:
                assert session.exec(select(DockerProbe)).one().value == "linux-db-ok"
        finally:
            engine.dispose()
    print(json.dumps({
        "platform": platform.platform(),
        "python": platform.python_version(),
        "sqlalchemy": sqlalchemy.__version__,
        "sqlmodel": sqlmodel.__version__,
        "sqlite": sqlite3.sqlite_version,
        "linux_extensions": [path.name for path in extensions],
        "sqlmodel_write_read": "passed",
    }, ensure_ascii=False))


def request(path: str, body: dict | None = None, *, authenticated: bool = True):
    headers = {}
    if authenticated and os.environ.get("OPERATOR_TOKEN"):
        headers["X-Operator-Token"] = os.environ["OPERATOR_TOKEN"]
    if body is not None:
        headers["Content-Type"] = "application/json"
    message = Request(
        "http://127.0.0.1:8000" + path,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
    )
    with urlopen(message, timeout=120) as response:
        payload = response.read()
        if "application/json" in response.headers.get("Content-Type", ""):
            return json.loads(payload)
        return payload


def check_http() -> None:
    assert request("/health") == {"ok": True}
    assert b"<!DOCTYPE html" in request("/").lstrip() or b"<!doctype html" in request("/").lstrip()
    assert request("/docs")
    assert len(request("/api/families")) > 0
    assert isinstance(request("/api/artworks"), list)
    # 제작 기능 복원과 별개로 MR !23에서 추가한 인증 회귀 검사는 유지한다.
    for path, body in (("/api/families", None), ("/api/artworks", None),
                       ("/api/artworks", {"theme": "우주"})):
        try:
            request(path, body, authenticated=False)
        except HTTPError as error:
            if error.code != 401:
                raise RuntimeError(f"{path}: expected unauthenticated 401, received {error.code}") from error
        else:
            raise RuntimeError(f"{path}: unauthenticated request succeeded")
    print("HTTP health, web, API docs, families and DB-backed artwork list: passed")


def create_smoke_artwork(state_file: Path) -> None:
    body = {"reveal": "none", "families": ["particles"], "theme": "Docker smoke check"}
    try:
        request("/api/artworks", body, authenticated=False)
    except HTTPError as error:
        if error.code != 401:
            raise
    else:
        raise RuntimeError("Unauthenticated artwork creation was not rejected.")
    artwork = request("/api/artworks", body)
    if artwork["status"] != "prepared":
        raise RuntimeError(f"Smoke artwork was not fully prepared: {artwork}")
    preparation = artwork.get("preparation") or {}
    if not preparation.get("complete") or any(t["status"] != "prepared" for t in preparation["targets"].values()):
        raise RuntimeError("Required outputs were not all prepared.")
    if artwork.get("runtime_phase") != "Unknown":
        raise RuntimeError("File preparation must not claim runtime Ready or Applied.")
    scene = Path(artwork["scene_path"])
    if not scene.is_file():
        raise RuntimeError("Prepared scene file is missing.")
    state = {"id": artwork["id"], "scene": str(scene), "sha256": hashlib.sha256(scene.read_bytes()).hexdigest()}
    state_file.write_text(json.dumps(state), encoding="utf-8")
    print(f"Authorized artwork creation and generated scene: passed (artwork {artwork['id']})")
    print("Unauthenticated creation: 401 as expected")


def check_persistence(state_file: Path) -> None:
    state = json.loads(state_file.read_text(encoding="utf-8"))
    artwork = request(f"/api/artworks/{state['id']}")
    assert artwork["status"] == "prepared"
    assert artwork["scene_path"] == state["scene"]
    assert hashlib.sha256(Path(state["scene"]).read_bytes()).hexdigest() == state["sha256"]
    print(f"Persisted DB row and scene content: passed (artwork {state['id']})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--db-only", action="store_true")
    action.add_argument("--create-smoke-artwork", action="store_true", help="Create one record in this local server.")
    action.add_argument("--check-persistence", action="store_true", help="Check the record after container recreation.")
    args = parser.parse_args()
    check_database()
    if args.db_only:
        return
    check_http()
    state_file = Path(os.environ.get("STUDIO_DATA", "/data")) / ".docker-smoke.json"
    if args.create_smoke_artwork:
        create_smoke_artwork(state_file)
    if args.check_persistence:
        check_persistence(state_file)


if __name__ == "__main__":
    main()
