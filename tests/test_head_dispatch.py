import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from head_director_fixtures import add_director
from sqlmodel import Session, select

from central.config import Settings
from central.main import create_app
from contracts.head import BackgroundInput, HeadRequest
from db import jobs
from db.engine import create_tables, make_engine
from db.head import HeadWorkflow
from db.ue import UECapability
from studio.head import director, dispatch, ue_support

SPEC = {
    "template_family": "flow_family",
    "raw_prompt": "숲",
    "content": {"theme": "숲", "mood": "고요", "medium": "particle"},
    "idle": {"motion": "ignore"},
    "interactions": [{"input": "move", "reaction": "push", "reaction_mode": "while", "return": "fade"}],
}
REGISTRY = {
    "ue": {"token": "ue-report-token-long", "role": "production", "catalog_version": "v1", "job_kinds": ["ue_assemble"]}
}


def register_ue(session):
    session.add(jobs.Node(id="ue", role="production", catalog_version="v1", heartbeat_at=jobs.now(session)))
    session.commit()
    session.add(
        UECapability(
            node_id="ue",
            reported_at=jobs.now(session),
            report={
                "version": 1,
                "templates": [
                    {k: r[k] for k in ("template", "fingerprint")} for r in ue_support.catalog()["templates"]
                ],
            },
        )
    )
    session.commit()


PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c49444154789c633070080000016400c12ba8247f0000000049454e44ae426082")


class FakeHead:
    provider, model = "openai", "gpt-6-luna"

    def __init__(self):
        self.calls = []
        self.contexts = []
        self.count = 3

    def complete(self, system, text, schema, image=None):
        self.calls.append(image)
        self.contexts.append(json.loads(text))
        if image is None:
            return {"reason": "배경 우선", "prompt": "빛나는 숲 배경"}
        return add_director({
            "reason": "배경과 조명 일치",
            "assets": [
                {"name": f"나무 {i}", "render_mode": "2.5d", "prompt": "투명 알파와 깊이 레이어"}
                for i in range(self.count)
            ],
        }, self.contexts[-1])


@pytest.fixture
def setup(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'head.db'}")
    create_tables(engine)
    connection = FakeHead()
    settings = SimpleNamespace(
        data_dir=tmp_path, head_connection=lambda profile: connection, agent_keys_json=json.dumps(REGISTRY)
    )
    with Session(engine) as session:
        register_ue(session)
    request = HeadRequest(
        request_id="head-test", artwork_id="art", version="v1", prompt="숲", asset_count=3, profile="test", automation=False, spec=SPEC
    )
    yield engine, settings, connection, request
    engine.dispose()


def run_decision(engine, settings, job_id):
    with Session(engine) as session:
        job = jobs.start(session, session.get(jobs.Job, job_id), "worker", 120)
        token = job.token
    result = dispatch.decide(engine, settings, job_id, "worker", token)
    return token, result


def complete_background(session, row):
    job = jobs.start(session, session.get(jobs.Job, row.background_job), "synthetic", 120)
    assert job.payload["file_transport"] == "http"
    jobs.finish(
        session,
        job.id,
        "synthetic",
        job.token,
        {"files": [{"name": "background.png", "sha256": hashlib.sha256(PNG).hexdigest(), "size": len(PNG)}]},
    )


def upload():
    return BackgroundInput(name="background.png", data_url="data:image/png;base64," + base64.b64encode(PNG).decode())


def test_head_n_assets_image_dependency_and_replay(setup):
    engine, settings, connection, request = setup
    with Session(engine) as session:
        row = dispatch.submit(session, request, connection, REGISTRY)
        workflow_id, decision_id = row.id, row.decision_job
        assert dispatch.submit(session, request, connection, REGISTRY).id == workflow_id
        with pytest.raises(jobs.Conflict):
            dispatch.submit(session, request.model_copy(update={"asset_count": 2}), connection, REGISTRY)
        with pytest.raises(jobs.Conflict):
            dispatch.attach_background(session, workflow_id, upload(), settings.data_dir)
    token, _ = run_decision(engine, settings, decision_id)
    # Worker가 판단 저장 직후 죽어도 자식 중복과 두 번째 API 비용을 방지한다.
    dispatch.decide(engine, settings, decision_id, "worker", token)
    assert connection.calls == [None]
    with Session(engine) as session:
        jobs.finish(session, decision_id, "worker", token, {})
        row = session.get(HeadWorkflow, workflow_id)
        complete_background(session, row)
        dispatch.advance(session)
        session.refresh(row)
        assert row.status == "awaiting_background_input"
        with pytest.raises(jobs.Conflict):
            dispatch.attach_background(
                session, workflow_id, upload().model_copy(update={"name": "wrong.png"}), settings.data_dir
            )
        row = dispatch.attach_background(session, workflow_id, upload(), settings.data_dir)
        asset_decision = row.decision_job
        assert (
            dispatch.attach_background(session, workflow_id, upload(), settings.data_dir).decision_job == asset_decision
        )
    run_decision(engine, settings, asset_decision)
    assert connection.calls[1].startswith("data:image/png;base64,")
    with Session(engine) as session:
        row = session.get(HeadWorkflow, workflow_id)
        assert len(row.asset_jobs) == 3
        for asset in row.asset_jobs:
            job = session.get(jobs.Job, asset["id"])
            assert job.payload["depends_on"] == [row.background_job]
            assert job.payload["file_transport"] == "http"
            job = jobs.start(session, job, "synthetic", 120)
            jobs.finish(session, job.id, "synthetic", job.token, {})
        dispatch.advance(session)
        session.refresh(row)
        assert row.status == "generation_complete"
        assert dispatch.public(session, row)["publish_approved"] is False


def test_wrong_asset_count_and_stale_owner_cannot_register(setup):
    engine, settings, connection, request = setup
    with Session(engine) as session:
        row = dispatch.submit(session, request, connection, REGISTRY)
        workflow_id, first = row.id, row.decision_job
    token, _ = run_decision(engine, settings, first)
    with Session(engine) as session:
        jobs.finish(session, first, "worker", token, {})
        row = session.get(HeadWorkflow, workflow_id)
        complete_background(session, row)
        row = dispatch.attach_background(session, workflow_id, upload(), settings.data_dir)
        decision_id = row.decision_job
    connection.count = 2
    with pytest.raises(ValueError, match="에셋 수"):
        run_decision(engine, settings, decision_id)
    with Session(engine) as session:
        assert session.get(HeadWorkflow, workflow_id).asset_jobs == []
        assert len(session.exec(select(jobs.Job).where(jobs.Job.kind == "assets_generate")).all()) == 0
    with pytest.raises(jobs.Conflict):
        dispatch.decide(engine, settings, decision_id, "other", "stale")


def test_head_api_auth_profiles_and_hidden_internal_kind(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        operator_token="operator",
        gms_key="",
        openai_api_key="test-key",
        agent_keys_json=json.dumps(REGISTRY),
    )
    with TestClient(create_app(settings)) as client:
        body = {
            "request_id": "api-test",
            "artwork_id": "art",
            "version": "v1",
            "prompt": "숲",
            "asset_count": 4,
            "automation": False,
            "spec": SPEC,
        }
        headers = {"X-Operator-Token": "operator"}
        assert client.post("/infra/head/workflows", json=body).status_code == 401
        assert client.post("/infra/head/workflows", json=body, headers=headers).status_code == 503
        assert (
            client.post(
                "/infra/agent/ue-capabilities",
                headers={"X-Agent-Token": REGISTRY["ue"]["token"]},
                json={
                    "version": 1,
                    "templates": [
                        {k: r[k] for k in ("template", "fingerprint")} for r in ue_support.catalog()["templates"]
                    ],
                },
            ).status_code
            == 200
        )
        response = client.post("/infra/head/workflows", json={**body, "profile": "test"}, headers=headers)
        assert response.status_code == 202
        assert response.json()["model"] == "gpt-6-luna"
        assert "test-key" not in response.text
        assert (
            client.post("/infra/jobs", json={"request_id": "bad", "kind": "head_decide"}, headers=headers).status_code
            == 422
        )


def test_legacy_inflight_decisions_keep_original_schema_without_skill_bundle(setup, tmp_path, monkeypatch):
    engine, settings, connection, request = setup
    with Session(engine) as session:
        row = dispatch.submit(session, request, connection, REGISTRY)
        workflow_id, decision_id = row.id, row.decision_job
        # 배포 이전에 저장된 흐름에는 director 마커가 없었다.
        row.payload = {key: value for key, value in row.payload.items() if key != "_director"}
        session.add(row)
        session.commit()
    monkeypatch.setattr(director, "PROMPT_PATH", tmp_path / "bundle-not-installed.md")
    schemas = []

    def legacy_complete(system, text, schema, image=None):
        context = json.loads(text)
        assert "director_skill" not in context and "director_capabilities" not in context
        assert system == dispatch.SYSTEM
        schemas.append(schema)
        if image is None:
            return {"reason": "기존 배경 판단", "prompt": "빛나는 숲"}
        return {"reason": "기존 에셋 판단", "assets": [
            {"name": f"잎 {index}", "render_mode": "2.5d", "prompt": "투명 잎"} for index in range(3)]}

    monkeypatch.setattr(connection, "complete", legacy_complete)
    token, _ = run_decision(engine, settings, decision_id)
    with Session(engine) as session:
        jobs.finish(session, decision_id, "worker", token, {})
        row = session.get(HeadWorkflow, workflow_id)
        complete_background(session, row)
        row = dispatch.attach_background(session, workflow_id, upload(), settings.data_dir)
        decision_id = row.decision_job
    run_decision(engine, settings, decision_id)
    schema = schemas[1]
    assert set(schema["properties"]) == {"reason", "assets"}
    assert set(schema["$defs"]) == {"AssetPlan", "AssetPlacement"}
    assert schema["title"] == "AssetsPlan" and schema["additionalProperties"] is False
    assert schema["required"] == ["reason", "assets"]
    assert schema["properties"]["reason"] == {"maxLength": 2000, "minLength": 1, "title": "Reason", "type": "string"}
    assert schema["properties"]["assets"]["minItems"] == 1 and schema["properties"]["assets"]["maxItems"] == 12
    with Session(engine) as session:
        row = session.get(HeadWorkflow, workflow_id)
        assert "_director" not in row.payload
        assert len(row.asset_jobs) == 3 and row.status == "waiting_assets"
        assert set(row.decisions[-1]["plan"]) == {"reason", "assets"}


def test_missing_skill_preserves_specific_hold_reason_before_ai_call(setup, tmp_path, monkeypatch):
    engine, settings, connection, request = setup
    with Session(engine) as session:
        row = dispatch.submit(session, request, connection, REGISTRY)
        workflow_id, decision_id = row.id, row.decision_job
    monkeypatch.setattr(director, "PROMPT_PATH", tmp_path / "private-bundle-location.md")
    with pytest.raises(jobs.Conflict, match="서버 패키지") as error:
        run_decision(engine, settings, decision_id)
    assert "private-bundle-location" not in str(error.value)
    assert connection.calls == []
    with Session(engine) as session:
        row = session.get(HeadWorkflow, workflow_id)
        assert row.status == "needs_attention"
        assert row.payload["_ue_hold"] == str(error.value)
        assert "Spec" not in row.payload["_ue_hold"]
        assert row.background_job == ""
