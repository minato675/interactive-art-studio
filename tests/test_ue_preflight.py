import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from infra import ue_capabilities
from sqlmodel import Session, select
from test_head_dispatch import REGISTRY, SPEC, FakeHead, complete_background, register_ue, run_decision, upload

from central.config import Settings
from central.main import create_app
from contracts.head import HeadRequest
from db import jobs
from db.engine import create_tables, make_engine
from db.head import HeadWorkflow
from db.ue import UECapability
from studio.head import dispatch, ue_support


def test_pinned_catalog_matches_source_and_file_evidence():
    table = ue_support.catalog()
    assert table["source"] == {"merge_request": 57, "revision": "8d2b03b740172307c4cf556f8e1116b8f08beac2"}
    installed = ue_capabilities.installed_catalog()
    assert table["mapping"] == installed["mapping"]
    assert table["templates"] == installed["templates"]
    assert {t["template"] for t in table["templates"]} == {
        "gpu_particles", "image_stir", "fluid", "paint", "deformation"}
    for entry in table["templates"]:
        evidence = entry["files"]
        assert all(name in evidence for name in ("ue_assemble.py", "assets.py", "spec.py", "slots.json", "scene.json"))
        assert hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == entry[
            "fingerprint"]
        slots = json.dumps(entry["slots"], sort_keys=True, separators=(",", ":"))
        assert hashlib.sha256(slots.encode()).hexdigest() == evidence["slots.json"]


@pytest.mark.parametrize("extra, expected", [([], 33), (["--source-merge-request", "57"], 57)])
def test_catalog_export_records_requested_source(tmp_path, monkeypatch, extra, expected):
    output = tmp_path / "catalog.json"
    monkeypatch.setattr("sys.argv", ["ue_capabilities", "--export-catalog", str(output),
                                     "--source-revision", "548676de", *extra])
    ue_capabilities.main()
    assert json.loads(output.read_text("utf-8"))["source"] == {"merge_request": expected, "revision": "548676de"}


@pytest.fixture
def context(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'preflight.db'}")
    create_tables(engine)
    settings = SimpleNamespace(data_dir=tmp_path, agent_keys_json=json.dumps(REGISTRY),
                               head_connection=lambda profile: model)
    model = FakeHead()
    request = HeadRequest(request_id="preflight", artwork_id="art", version="v1", prompt="숲", asset_count=3,
                          profile="test", automation=False, spec=copy.deepcopy(SPEC))
    yield engine, settings, model, request
    engine.dispose()


@pytest.mark.parametrize("reaction", ["bloom", "press", "swirl", "scatter", "ignore"])
def test_unsupported_reaction_stops_before_ai_and_generation(context, reaction):
    engine, _, model, request = context
    spec = request.spec.model_dump(mode="json")
    spec["interactions"][0]["reaction"] = reaction
    request = HeadRequest.model_validate({**request.model_dump(), "spec": spec})
    with Session(engine) as session:
        register_ue(session)
        with pytest.raises(jobs.Conflict):
            dispatch.submit(session, request, model, REGISTRY)
        assert session.exec(select(HeadWorkflow)).all() == []
        assert session.exec(select(jobs.Job)).all() == []
    assert model.calls == []


@pytest.mark.parametrize("condition", ["missing", "offline", "mismatch", "removed", "wrong_catalog"])
def test_unavailable_ue_node_stops_before_ai(context, condition):
    engine, _, model, request = context
    with Session(engine) as session:
        register_ue(session)
        registry = copy.deepcopy(REGISTRY)
        evidence, node = session.get(UECapability, "ue"), session.get(jobs.Node, "ue")
        if condition == "missing":
            session.delete(evidence)
        elif condition == "offline":
            node.heartbeat_at = jobs.now(session) - 91
            session.add(node)
        elif condition == "mismatch":
            evidence.report = {"version": 1, "templates": [{"template": "gpu_particles", "fingerprint": "0" * 64}]}
            session.add(evidence)
        elif condition == "removed":
            registry = {}
        elif condition == "wrong_catalog":
            registry["ue"]["catalog_version"] = "other"
        session.commit()
        with pytest.raises(jobs.Conflict):
            dispatch.submit(session, request, model, registry)
        assert session.exec(select(jobs.Job)).all() == []
    assert model.calls == []


def test_selected_conditions_are_pinned_to_ai_and_generation(context):
    engine, settings, model, request = context
    with Session(engine) as session:
        register_ue(session)
        row = dispatch.submit(session, request, model, REGISTRY)
        workflow_id, decision = row.id, row.decision_job
        assert row.payload["_ue"]["compatible_nodes"] == ["ue"]
    run_decision(engine, settings, decision)
    with Session(engine) as session:
        row = session.get(HeadWorkflow, workflow_id)
        background = session.get(jobs.Job, row.background_job)
        fixed = background.payload["generation"]["conditions"]
        assert fixed["template"] == "gpu_particles" and fixed["output_name"] == "background.png"
        assert fixed["recommended_size"] == [1920, 1080] and fixed["layer_depth"] == "far"
        assert fixed == model.contexts[0]["generation_conditions"]
        assert fixed["fingerprint"] in background.payload["generation"]["prompt"]
        complete_background(session, row)
        row = dispatch.attach_background(session, workflow_id, upload(), settings.data_dir)
        asset_decision = row.decision_job
    run_decision(engine, settings, asset_decision)
    with Session(engine) as session:
        row = session.get(HeadWorkflow, workflow_id)
        for index, item in enumerate(row.asset_jobs):
            fixed = session.get(jobs.Job, item["id"]).payload["generation"]["conditions"]
            assert fixed["output_name"] == f"asset-{index + 1:03d}.png"
            assert fixed["alpha_required"] is True and fixed["behaviors"] == ["float"]
            assert fixed == model.contexts[1]["generation_conditions"][index]


@pytest.mark.parametrize("legacy", [True, False])
def test_missing_snapshot_or_resource_change_blocks_worker(context, monkeypatch, legacy):
    engine, settings, model, request = context
    with Session(engine) as session:
        register_ue(session)
        row = dispatch.submit(session, request, model, REGISTRY)
        workflow_id, decision = row.id, row.decision_job
        if legacy:
            row.payload = {k: v for k, v in row.payload.items() if k not in ("spec", "_ue")}
            session.add(row)
            session.commit()
        else:
            selected = ue_support.select_template(request)
            monkeypatch.setattr(ue_support, "select_template", lambda request: {**selected, "policy_version": 2})
    with pytest.raises(jobs.Conflict):
        run_decision(engine, settings, decision)
    with Session(engine) as session:
        row = session.get(HeadWorkflow, workflow_id)
        assert row.status == "needs_attention" and row.payload["_ue_hold"]
        assert not row.background_job
    assert model.calls == []


@pytest.mark.parametrize("medium,reaction,template,usage", [
    ("particle", "push", "gpu_particles", "display"),
    ("surface", "stir", "image_stir", "canvas"),
    ("fluid", "stir", "fluid", "style_reference"),
    ("stroke", "draw", "paint", "style_reference"),
    ("surface", "ripple", "deformation", "style_reference"),
    ("particle", "follow", "gpu_particles", "display"),
])
def test_template_specific_roles(context, medium, reaction, template, usage):
    *_, request = context
    raw = request.model_dump(mode="json")
    raw["spec"]["content"]["medium"] = medium
    raw["spec"]["interactions"][0]["reaction"] = reaction
    selected = ue_support.select_template(HeadRequest.model_validate(raw))
    assert (selected["template"], selected["background_usage"]) == (template, usage)
    if reaction == "follow":
        assert ue_support.conditions(selected, "object").behaviors == ["follow"]


def test_capability_api_role_and_spec_required(tmp_path):
    registry = {**REGISTRY, "gen": {"role": "generation", "catalog_version": "v1", "token": "generation-token-long"}}
    settings = Settings(data_dir=tmp_path, operator_token="operator", agent_keys_json=json.dumps(registry),
                        openai_api_key="unused-test-key")
    report = {"version": 1, "templates": [{k: r[k] for k in ("template", "fingerprint")}
                                         for r in ue_support.catalog()["templates"]]}
    with TestClient(create_app(settings)) as client:
        assert client.post("/infra/agent/ue-capabilities", json=report).status_code == 401
        assert client.post("/infra/agent/ue-capabilities", json=report,
                           headers={"X-Agent-Token": "generation-token-long"}).status_code == 403
        assert client.post("/infra/agent/ue-capabilities", json=report,
                           headers={"X-Agent-Token": REGISTRY["ue"]["token"]}).json()["runtime_verified"] is False
        headers = {"X-Operator-Token": "operator"}
        assert client.get("/infra/head/templates").status_code == 401
        assert client.get("/infra/head/templates", headers=headers).json()["nodes"][0]["online"] is True
        body = {"request_id": "missing-spec", "artwork_id": "art", "version": "v1", "prompt": "숲",
                "asset_count": 1, "profile": "test", "automation": False}
        assert client.post("/infra/head/workflows", json=body, headers=headers).status_code == 422
        preview = client.post("/infra/head/preflight", json={**body, "spec": SPEC}, headers=headers)
        assert preview.status_code == 200 and preview.json()["background"]["output_name"] == "background.png"
        assert len(preview.json()["assets"]) == 1 and preview.json()["runtime_verified"] is False
        with Session(client.app.state.engine) as session:
            assert session.exec(select(jobs.Job)).all() == []
        bad = copy.deepcopy(SPEC)
        bad["interactions"][0]["reaction"] = "bloom"
        assert client.post("/infra/head/preflight", json={**body, "spec": bad}, headers=headers).status_code == 409
        assert client.post("/infra/head/workflows", json={**body, "spec": SPEC}, headers=headers).status_code == 202


@pytest.mark.parametrize("medium", ["fluid", "surface", "stroke"])
def test_follow_on_opaque_canvas_is_rejected(context, medium):
    *_, request = context
    raw = request.model_dump(mode="json")
    raw["spec"]["content"]["medium"] = medium
    raw["spec"]["interactions"][0]["reaction"] = "follow"
    with pytest.raises(jobs.Conflict):
        ue_support.select_template(HeadRequest.model_validate(raw))


def test_ai_cannot_override_requested_flat_assets(context):
    engine, settings, model, request = context
    raw = request.model_dump(mode="json")
    raw["spec"]["template_family"] = "scene_family"
    raw["spec"]["content"].update(dimension="2d", background="숲 배경")
    request = HeadRequest.model_validate(raw)
    with Session(engine) as session:
        register_ue(session)
        row = dispatch.submit(session, request, model, REGISTRY)
        workflow_id, decision = row.id, row.decision_job
    run_decision(engine, settings, decision)
    with Session(engine) as session:
        row = session.get(HeadWorkflow, workflow_id)
        complete_background(session, row)
        row = dispatch.attach_background(session, workflow_id, upload(), settings.data_dir)
        decision = row.decision_job
    with pytest.raises(ValueError, match="표현 조건"):
        run_decision(engine, settings, decision)
    with Session(engine) as session:
        assert session.get(HeadWorkflow, workflow_id).asset_jobs == []
        assert session.exec(select(jobs.Job).where(jobs.Job.kind == "assets_generate")).all() == []
