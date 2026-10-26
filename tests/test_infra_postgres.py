"""행 잠금은 SQLite 테스트로 검증했다고 간주하지 않는다."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel

from db import jobs


@pytest.fixture
def pg_engine():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL이 없어 실제 PostgreSQL 동시성 검증 생략")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("테스트 DB 이름은 _test로 끝나야 합니다")
    schema = "infra_test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def set_schema(connection, record):
        cursor = connection.cursor()
        cursor.execute(f'SET search_path TO "{schema}"')
        connection.commit()
        cursor.close()

    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.parametrize("same_node", [False, True])
@pytest.mark.parametrize("role", ["production", "generation"])
def test_concurrent_claims_have_no_duplicate_execution(pg_engine, same_node, role):
    with Session(pg_engine) as session:
        for name in ("p1", "p2"):
            session.add(jobs.Node(id=name, role=role, catalog_version="v1"))
        session.commit()
        for index in range(2):
            jobs.enqueue(session, {"request_id": str(index),
                                   "kind": "background_generate" if role == "generation" else "bundle_verify"})
    barrier = Barrier(2)

    def claim(name):
        with Session(pg_engine) as session:
            barrier.wait(timeout=10)
            try:
                row = jobs.claim_node(session, name, 30)
                return row.id if row else None
            except jobs.Conflict:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["p1", "p1" if same_node else "p2"]))
    claimed = [r for r in results if r]
    assert len(claimed) == (1 if same_node else 2)
    assert len(set(claimed)) == len(claimed)


def test_feedback_active_json_query_duplicate_save_and_revision_conflict(pg_engine, tmp_path, monkeypatch):
    from tests.test_artwork_feedback import request_body, seed_show
    from tests.test_infra import infra_client

    from studio.head import feedback

    monkeypatch.setattr("central.main.make_engine", lambda url: pg_engine)
    fixture = infra_client.__wrapped__(tmp_path)
    client = next(fixture)
    try:
        with Session(pg_engine) as session:
            target = seed_show(session)
        current = client.get("/infra/feedback/current")
        assert current.status_code == 200 and current.json()["shows"][0]["artwork"]["target"] == target
        with ThreadPoolExecutor(max_workers=2) as pool:
            saved = list(pool.map(lambda _: client.post("/infra/feedback", json=request_body(target)), range(2)))
        assert [r.status_code for r in saved] == [200, 200]
        assert saved[0].json()["id"] == saved[1].json()["id"]
        path = "/infra/feedback/" + saved[0].json()["id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            changed = list(pool.map(lambda _: client.patch(path, json={"enabled": False, "expected_revision": 1}), range(2)))
        assert sorted(r.status_code for r in changed) == [200, 409]
        assert len(client.get("/infra/feedback").json()) == 1
        with Session(pg_engine) as session:
            assert feedback.capture(session)["items"] == []
    finally:
        fixture.close()


def test_generation_dependency_and_stale_session_ownership(pg_engine):
    import time

    with Session(pg_engine) as session:
        session.add(jobs.Node(id="g", role="generation", catalog_version="v1"))
        session.commit()
        spec = {"artwork_id": "A", "version": "v1", "catalog_version": "v1"}
        bg = jobs.enqueue(session, {"request_id": "bg", "kind": "background_generate", "generation": spec})
        bg_id = bg.id
        assets = jobs.enqueue(session, {"request_id": "asset", "kind": "assets_generate", "generation": spec,
                                        "depends_on": [bg_id]})
        assets_id = assets.id
        lease = jobs.claim_node(session, "g", 30)
        token = lease.token
        assert lease.id == bg_id
    with Session(pg_engine) as stale:
        old_view = stale.get(jobs.Job, bg_id)
        with Session(pg_engine) as fresh:
            row = fresh.get(jobs.Job, bg_id)
            row.lease_until = time.time() - 10
            fresh.add(row)
            fresh.commit()
            renewed = jobs.claim_node(fresh, "g", 30)
            new_token = renewed.token
        assert old_view.token == token
        with pytest.raises(jobs.Conflict):
            jobs.owned(stale, bg_id, "g", token)
        stale.rollback()
        jobs.complete_generation(stale, bg_id, "g", new_token, {"files": []}, "")
        assert jobs.claim_node(stale, "g", 30).id == assets_id


def test_dedicated_generation_two_asset_claims(pg_engine):
    with Session(pg_engine) as session:
        for name in ("bg", "a1", "a2"):
            session.add(jobs.Node(id=name, role="generation", catalog_version="v1"))
        session.commit()
        spec = {"artwork_id": "dedicated", "version": "v1", "catalog_version": "v1"}
        bg = jobs.enqueue(session, {"request_id": "dedicated-bg", "kind": "background_generate", "generation": spec})
        bg_id = bg.id
        for index in range(3):
            jobs.enqueue(session, {"request_id": f"dedicated-a{index}", "kind": "assets_generate",
                                   "generation": spec, "depends_on": [bg_id]})
        assert jobs.claim_node(session, "a1", 30, ["assets_generate"]) is None
        lease = jobs.claim_node(session, "bg", 30, ["background_generate"])
        jobs.finish(session, bg_id, "bg", lease.token, {})
        assert jobs.claim_node(session, "bg", 30, ["background_generate"]) is None
    barrier = Barrier(2)

    def claim(name):
        with Session(pg_engine) as session:
            barrier.wait(timeout=10)
            row = jobs.claim_node(session, name, 30, ["assets_generate"])
            return row.id, row.kind

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["a1", "a2"]))
    assert len({row[0] for row in results}) == 2
    assert all(row[1] == "assets_generate" for row in results)
