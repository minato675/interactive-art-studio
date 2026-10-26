"""원문 접수의 중복 방지·UE 대기 복구·실제 Head 연결을 확인한다."""

import json
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select
from tests.test_flow import make_client
from tests.test_head_dispatch import REGISTRY, SPEC, FakeHead, register_ue

from contracts.head import HeadRequest, OperatorHeadIntake
from contracts.spec import Spec
from db import jobs
from db.engine import create_tables, make_engine
from db.head import HeadWorkflow
from studio.head import intake
from studio.head.llm import LLMUnavailable
from studio.head.spec_matching import LLMSpecMatcher


def test_intake_persists_raw_request_and_is_idempotent(tmp_path):
    with make_client(tmp_path) as client:
        body = {"request_id": "operator-test", "request": {"theme": "검은 공간의 빛 먼지"}}
        first = client.post("/infra/head/intakes", json=body)
        assert first.status_code == 202, first.text
        assert first.json()["kind"] == "head_intake"
        assert client.post("/infra/head/intakes", json=body).json()["id"] == first.json()["id"]
        body["request"]["theme"] = "다른 작품"
        assert client.post("/infra/head/intakes", json=body).status_code == 409
        assert client.get("/api/artworks").json() == []
        assert len(client.get("/infra/head/intakes").json()) == 1
        with Session(client.app.state.engine) as session:
            assert session.get(jobs.Job, first.json()["id"]).payload["request"]["theme"] == "검은 공간의 빛 먼지"
        assert client.get("/infra/head/intakes", headers={"X-Operator-Token": "wrong"}).status_code == 401


@pytest.mark.parametrize(
    "body", [{"theme": ""}, {"theme": "빛", "review_assets": True}, {"theme": "빛", "families": ["paint", "fluid"]}]
)
def test_unsupported_controls_are_not_silently_ignored(tmp_path, body):
    with make_client(tmp_path) as client:
        assert client.post("/infra/head/intakes", json={"request_id": "invalid", "request": body}).status_code == 422


def test_waiting_request_resumes_once_compatible_ue_returns(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'intake.db'}")
    create_tables(engine)
    connection = FakeHead()
    settings = SimpleNamespace(agent_keys_json=json.dumps(REGISTRY), head_connection=lambda profile: connection)
    prepared = HeadRequest(
        request_id="pending-75",
        artwork_id="operator-75",
        version="v1",
        prompt="숲",
        spec=SPEC,
        automation=False,
        profile="test",
    ).model_dump(mode="json")
    with Session(engine) as session:
        row = jobs.enqueue(
            session,
            {
                "request_id": "pending-75",
                "kind": "head_intake",
                "source_artwork_id": 75,
                "request": {"theme": "숲"},
                "prepared": prepared,
            },
        )
        job_id = row.id
        token = jobs.start(session, row, "worker", 120).token
    assert intake.process(engine, settings, job_id, "worker", token) == {"_deferred": True}
    with Session(engine) as session:
        row = session.get(jobs.Job, job_id)
        assert row.status == "waiting_ue" and row.payload["source_artwork_id"] == 75
        assert not session.exec(select(HeadWorkflow)).all()
        register_ue(session)
        row.dispatch_after = 0
        session.add(row)
        session.commit()
        intake.wake(session, settings)
        session.refresh(row)
        assert row.status == "queued"
        token = jobs.start(session, row, "worker", 120).token
    result = intake.process(engine, settings, job_id, "worker", token)
    assert result["workflow_id"]
    # Worker 응답을 잃어 같은 작업을 다시 처리해도 Head·배경 계획 작업을 중복 생성하지 않는다.
    assert intake.process(engine, settings, job_id, "worker", token)["workflow_id"] == result["workflow_id"]
    with Session(engine) as session:
        assert len(session.exec(select(HeadWorkflow)).all()) == 1
        assert len(session.exec(select(jobs.Job).where(jobs.Job.kind == "head_decide")).all()) == 1
    assert connection.calls == []
    engine.dispose()


def test_production_matcher_does_not_hide_connection_failure():
    def unavailable(*args):
        raise LLMUnavailable("API unavailable")

    llm = SimpleNamespace(name="gms", model="test", complete_json=unavailable)
    with pytest.raises(LLMUnavailable):
        LLMSpecMatcher(llm, fail_on_unavailable=True).match("숲", "scene_family")
    assert LLMSpecMatcher(llm).match("숲", "scene_family").sources


@pytest.mark.parametrize("expire_lease", [False, True])
def test_raw_intake_preserves_prompt_review_choice_and_rechecks_lease(tmp_path, monkeypatch, expire_lease):
    engine = make_engine(f"sqlite:///{tmp_path / 'raw.db'}")
    create_tables(engine)
    registry = {**REGISTRY, "ue-review-1": {"role": "production", "catalog_version": "v1", "job_kinds": ["ue_verify"]}}
    settings = SimpleNamespace(agent_keys_json=json.dumps(registry), head_connection=lambda profile: FakeHead())
    raw = "검은 공간에 금빛 먼지가 흐르고 사람이 지나가면 양옆으로 밀린다."
    with Session(engine) as session:
        register_ue(session)
        row = intake.submit(session, OperatorHeadIntake(request_id="raw-prompt", request={"theme": raw, "review_final": True}))
        job_id = row.id
        token = jobs.start(session, row, "worker", 120).token

    def match(prompt, family):
        assert prompt == raw
        if expire_lease:
            with Session(engine) as session:
                row = session.get(jobs.Job, job_id)
                row.lease_until = 0
                session.add(row)
                session.commit()
        return SimpleNamespace(holds=[], spec=Spec.model_validate(SPEC), sources={"read": "llm"})

    monkeypatch.setattr(intake, "LLMSpecMatcher", lambda *args, **kwargs: SimpleNamespace(match=match))
    if expire_lease:
        with pytest.raises(jobs.Conflict):
            intake.process(engine, settings, job_id, "worker", token)
    else:
        intake.process(engine, settings, job_id, "worker", token)
    with Session(engine) as session:
        flows = session.exec(select(HeadWorkflow)).all()
        assert len(flows) == (0 if expire_lease else 1)
        if flows:
            assert flows[0].payload["auto_publish"] is False
            assert flows[0].payload["prompt"] == flows[0].payload["spec"]["raw_prompt"] == raw
    engine.dispose()
