import copy
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from head_director_fixtures import add_director
from sqlmodel import Session, select
from test_head_dispatch import PNG, SPEC, register_ue
from test_server_exchange import claim, finish, put

from central.config import Settings
from central.main import create_app
from central.runtime_flow import bundle_identity
from contracts.head import HeadRequest
from db import jobs
from db.head import HeadWorkflow
from studio.head import director, dispatch


class AutoHead:
    provider, model = "openai", "gpt-6-luna"

    def __init__(self):
        self.contexts = []
        self.reviews = []
        self.count = 3
        self.clone_first = False

    def complete(self, system, text, schema, image=None):
        context = json.loads(text)
        self.contexts.append((context, image))
        if "runtime_report" in context:
            assert isinstance(image, list) and len(image) >= 2
            return {"decision": self.reviews.pop(0) if self.reviews else "approve",
                    "reason": "배경과 화면의 조화 확인", "instructions": "화면을 가리는 객체를 줄여 제작"}
        if context["phase"] == "background":
            return {"reason": "배경 우선", "prompt": "수정 숲" if context.get("rework") else "처음 숲"}
        assert image.startswith("data:image/png;base64,")
        return add_director({"reason": "배경 구도에 맞춤", "assets": [{"name": f"나무 {i}", "render_mode": "2.5d",
                "prompt": "투명 나무", "placement": {"u": (i+1)/(self.count+1), "v": .5, "size": .12}}
                for i in range(self.count)]}, context, self.clone_first)


@pytest.fixture
def automatic(tmp_path, monkeypatch):
    model = AutoHead()
    monkeypatch.setattr(Settings, "head_connection", lambda self, profile: model)
    entries = {"gen": ("generation", ["background_generate"]), "a1": ("generation", ["assets_generate"]),
               "a2": ("generation", ["assets_generate"]), "ue": ("production", ["ue_assemble"]),
               "ue-review-1": ("production", ["ue_verify"]), "ue-show-1": ("show", ["ue_publish"]),
               "other-ue": ("production", ["ue_assemble"])}
    registry = {n: {"token": n*20, "role": r, "job_kinds": k, "catalog_version": "v1"}
                for n, (r, k) in entries.items()}
    settings = Settings(data_dir=tmp_path, operator_token="operator", agent_keys_json=json.dumps(registry))
    with TestClient(create_app(settings), headers={"X-Operator-Token": "operator"}) as api:
        with Session(api.app.state.engine) as session:
            register_ue(session)
        yield api, settings, model


def tick(api, settings):
    with Session(api.app.state.engine) as session:
        dispatch.advance(session, settings)


def state(api, workflow):
    return api.get('/infra/head/workflows/' + workflow).json()


def decision(api, settings, workflow):
    job_id = state(api, workflow)["decision_job"]
    with Session(api.app.state.engine) as session:
        job = jobs.start(session, session.get(jobs.Job, job_id), "worker", 120)
        token = job.token
    result = dispatch.decide(api.app.state.engine, settings, job_id, "worker", token)
    # 저장 직후 재전송은 두 번째 AI 호출이나 자식 작업을 만들지 않는다.
    dispatch.decide(api.app.state.engine, settings, job_id, "worker", token)
    with Session(api.app.state.engine) as session:
        jobs.finish(session, job_id, "worker", token, result)
    return result


def start(api, **changes):
    response = api.post('/infra/head/workflows', json={"request_id": "automatic", "artwork_id": "art",
                        "version": "v1", "prompt": "숲", "profile": "test", "spec": copy.deepcopy(SPEC), **changes})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def generate(api, node):
    job = claim(api, node).json()
    name = job["payload"]["generation"]["conditions"]["output_name"]
    ref = put(api, job, node, name, PNG)
    response = finish(api, job, node, [ref])
    assert response.status_code == 200, response.text
    return job


def through_assembly(api, settings, workflow, has_background=False):
    decision(api, settings, workflow)
    if not has_background:
        generate(api, "gen")
        tick(api, settings)
        assert state(api, workflow)["status"] == "planning_assets"
        decision(api, settings, workflow)
    row = state(api, workflow)
    assert len(row["asset_jobs"]) == 3
    for i in range(3):
        job = generate(api, "a1" if i % 2 == 0 else "a2")
        assert job["inputs"][0]["kind"] == "background_generate"
    tick(api, settings)
    tick(api, settings)
    assert state(api, workflow)["status"] == "waiting_assembly"
    assert claim(api, "other-ue").json() is None
    assembly = claim(api, "ue").json()
    assets = assembly["payload"]["assembly"]["assets"]
    assert assets["layers"][0]["image"] == "background.png"
    assert [o["image"] for o in assets["objects"][:3]] == [f"asset-{i:03d}.png" for i in range(1, 4)]
    refs = [put(api, assembly, "ue", "scene.json", b'{}'),
            put(api, assembly, "ue", "assembly-report.json", b'{"errors":[]}')]
    assert finish(api, assembly, "ue", refs).status_code == 200
    tick(api, settings)
    verification = claim(api, "ue-review-1").json()
    return assembly, verification


def verify_result(api, settings, assembly, verification, verdict="pass", suspect=False, screenshots=None):
    with Session(api.app.state.engine) as session:
        sha = bundle_identity(session.get(jobs.Job, assembly["id"]))
    screenshots = screenshots if screenshots is not None else {"rest": "captures/rest.png"}
    captures = [put(api, verification, "ue-review-1", name, PNG) for name in dict.fromkeys(screenshots.values())]
    report = {"version": "ue-" + assembly["id"], "source_job_id": assembly["id"], "source_bundle_sha256": sha,
              "verdict": verdict, "passed": verdict != "fail", "needs_review": verdict == "review",
              "checks": [{"passed": verdict != "fail"}], "bundle": {"sha256": sha},
              "screenshots": screenshots, "environment_suspect": suspect}
    ref = put(api, verification, "ue-review-1", "runtime-report.json", json.dumps(report).encode())
    assert finish(api, verification, "ue-review-1", [*captures, ref]).status_code == 200
    tick(api, settings)


def test_automatic_optional_count_to_ai_approval_and_applied(automatic):
    api, settings, model = automatic
    workflow = start(api)
    assembly, verification = through_assembly(api, settings, workflow)
    assert len(model.contexts) == 2  # duplicate decision delivery did not call twice
    verify_result(api, settings, assembly, verification)
    premature = api.post(f"/infra/jobs/{verification['id']}/approve-publication", json={
        "request_id": "premature", "target_node": "ue-show-1", "reviewer": "operator", "reason": "검수 중"})
    assert premature.status_code == 409
    decision(api, settings, workflow)
    row = state(api, workflow)
    assert row["status"] == "waiting_publication"
    assert row["payload"]["_flow"]["asset_count"] == 3
    assert row["decisions"][-1]["evidence"][0]["sha256"] == hashlib.sha256(PNG).hexdigest()
    show = claim(api, "ue-show-1").json()
    assert show["approval"]["reviewer"].startswith("head:")
    with Session(api.app.state.engine) as session:
        owned = session.get(jobs.Job, show["id"])
        jobs.finish(session, owned.id, "ue-show-1", owned.token, {"phase": "Applied"})
    tick(api, settings)
    assert state(api, workflow)["status"] == "completed"


@pytest.mark.parametrize("screenshots", [
    {label: f"captures/{label}.png" for label in ("rest", "hands", "after", "idle", "afterimage", "settled")},
    {label: f"captures/{label}.png" for label in ("rest", "hands", "afterimage")},
    {"rest": "captures/rest.png", "hands": "captures/hands.png", "idle": "captures/rest.png",
     "afterimage": "captures/afterimage.png", "settled": "captures/afterimage.png"},
])
def test_head_review_receives_afterimage_evidence_without_duplicate_files(automatic, screenshots):
    api, settings, model = automatic
    workflow = start(api)
    assembly, verification = through_assembly(api, settings, workflow)
    verify_result(api, settings, assembly, verification, screenshots=screenshots)
    decision(api, settings, workflow)
    context, images = model.contexts[-1]
    expected = list(dict.fromkeys(screenshots.values()))
    assert [item["name"] for item in context["evidence"]][1:] == expected
    assert len(images) == len(expected) + 1   # 배경 + 중복을 뺀 검증 화면 전부
    assert state(api, workflow)["status"] == "waiting_publication"


@pytest.mark.parametrize('verdict,suspect', [('fail', False), ('review', False), ('pass', True)])
def test_ai_approval_cannot_override_technical_or_environment_hold(automatic, verdict, suspect):
    api, settings, _ = automatic
    workflow = start(api)
    assembly, verification = through_assembly(api, settings, workflow)
    verify_result(api, settings, assembly, verification, verdict, suspect)
    decision(api, settings, workflow)
    assert state(api, workflow)["status"] == "needs_attention"
    assert claim(api, "ue-show-1").json() is None


@pytest.mark.parametrize('mode', ['rework_assets', 'rework_background'])
def test_rework_preserves_history_and_stops_at_limit(automatic, mode):
    api, settings, model = automatic
    model.reviews = [mode, mode]
    workflow = start(api, max_reworks=1)
    assembly, verification = through_assembly(api, settings, workflow)
    original_background = state(api, workflow)["background_job"]
    verify_result(api, settings, assembly, verification)
    decision(api, settings, workflow)
    row = state(api, workflow)
    assert row['payload']['_flow']['round'] == 1
    assert row['payload']['_flow']['history'][0]['assembly_job'] == assembly['id']
    assert (row['background_job'] == original_background) == (mode == 'rework_assets')
    again, check = through_assembly(api, settings, workflow, has_background=mode == 'rework_assets')
    assert again['id'] != assembly['id']
    latest_assets = [c for c, _ in model.contexts if c.get('phase') == 'assets'][-1]
    assert latest_assets['background_plan']['prompt'] == ('수정 숲' if mode == 'rework_background' else '처음 숲')
    verify_result(api, settings, again, check)
    decision(api, settings, workflow)
    row = state(api, workflow)
    assert row['status'] == 'needs_attention'
    assert row['payload']['_flow']['hold'] == 'rework_limit_reached'


def test_explicit_count_enforced(automatic):
    api, settings, _model = automatic
    workflow = start(api, asset_count=2)
    decision(api, settings, workflow)
    generate(api, 'gen')
    tick(api, settings)
    with pytest.raises(ValueError, match='에셋 수'):
        decision(api, settings, workflow)
    assert state(api, workflow)['asset_jobs'] == []
    with pytest.raises(ValueError):
        HeadRequest(request_id='x', artwork_id='a', version='v', prompt='x', spec=SPEC, asset_count=4, max_asset_count=2)


def test_auto_publish_off_waits_for_operator(automatic):
    api, settings, _ = automatic
    workflow = start(api, auto_publish=False)
    assembly, verification = through_assembly(api, settings, workflow)
    verify_result(api, settings, assembly, verification)
    decision(api, settings, workflow)
    assert state(api, workflow)['status'] == 'awaiting_publication_approval'
    response = api.post(f"/infra/jobs/{verification['id']}/approve-publication", json={
        'request_id': 'human-approval', 'target_node': 'ue-show-1', 'reviewer': 'operator', 'reason': '검수 완료'})
    assert response.status_code == 202
    assert state(api, workflow)['status'] == 'waiting_publication'


def test_corrupted_background_stops_before_asset_ai_call(automatic):
    from contracts.infra import FileRef
    from studio.infra.storage import FileStore

    api, settings, model = automatic
    workflow = start(api)
    decision(api, settings, workflow)
    job = generate(api, "gen")
    with Session(api.app.state.engine) as session:
        result = session.get(jobs.Job, job["id"]).result
        ref = FileRef.model_validate(result["files"][0])
    FileStore(settings.data_dir / "artifacts").path(ref).write_bytes(b"corrupt")
    tick(api, settings)
    assert state(api, workflow)["status"] == "needs_attention"
    assert len(model.contexts) == 1


def test_review_responses_multi_image_and_strict_nullable_schema(monkeypatch):
    import io
    import urllib.request
    from types import SimpleNamespace

    from contracts.head import AssetsPlan
    from studio.head.responses import ResponsesHead

    captured = []
    def send(request, timeout):
        captured.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"status":"completed","output":[{"type":"message","content":[
            {"type":"output_text","text":"{}"}]}]}).encode())
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: SimpleNamespace(open=send))
    schema = AssetsPlan.model_json_schema()
    before = copy.deepcopy(schema)
    response = ResponsesHead("openai", "gpt-6-luna", "https://api.openai.com/v1/responses", "test-key")
    assert response.complete("judge", "context", schema, ["data:image/png;base64,a", "data:image/png;base64,b"]) == {}
    payload = captured[0]
    assert [v["type"] for v in payload["input"][0]["content"]] == ["input_text","input_image","input_image"]
    assert schema == before
    normalized = payload["text"]["format"]["schema"]
    assert normalized["$defs"]["AssetPlan"]["additionalProperties"] is False
    assert "placement" in normalized["$defs"]["AssetPlan"]["required"]


def test_director_clones_and_static_subset_reach_assembly(automatic):
    api, settings, model = automatic
    model.clone_first = True
    workflow = start(api)
    assembly, _ = through_assembly(api, settings, workflow)
    objects = assembly["payload"]["assembly"]["assets"]["objects"]
    assert len(objects) == 4
    assert objects[0]["image"] == objects[3]["image"] == "asset-001.png"
    assert objects[0]["position"] != objects[3]["position"]
    assert objects[0]["size"] != objects[3]["size"]
    assert objects[0]["behaviors"] == ["float"]
    assert all(o["behaviors"] == [] for o in objects[1:])
    row = state(api, workflow)
    assert len(row["asset_jobs"]) == 3
    assert row["payload"]["_flow"]["source_asset_count"] == 3
    assert row["payload"]["_flow"]["asset_count"] == 4
    assert row["payload"]["_director"]["version"] == "ranel-director-v2"
    assert row["decisions"][-1]["background_evidence"]["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert assembly["payload"]["_head"]["scenario"]["instances"][3]["id"] == "source_one_copy"


def test_new_workflow_missing_skill_returns_safe_conflict_without_jobs(automatic, tmp_path, monkeypatch):
    api, _settings, model = automatic
    monkeypatch.setattr(director, "PROMPT_PATH", tmp_path / "private-bundle-location.md")
    response = api.post('/infra/head/workflows', json={
        "request_id": "missing-skill", "artwork_id": "art", "version": "v1", "prompt": "숲",
        "profile": "test", "spec": copy.deepcopy(SPEC)})
    assert response.status_code == 409
    assert "서버 패키지" in response.json()["detail"]
    assert "private-bundle-location" not in response.text
    with Session(api.app.state.engine) as session:
        assert session.exec(select(HeadWorkflow)).all() == []
        assert session.exec(select(jobs.Job)).all() == []
    assert claim(api, "gen").json() is None and model.contexts == []


@pytest.mark.parametrize("change", ["changed", "missing"])
def test_review_rechecks_pinned_skill_before_recording_or_publishing(automatic, tmp_path, monkeypatch, change):
    api, settings, model = automatic
    prompt = tmp_path / "review-prompt.md"
    prompt.write_bytes(director.PROMPT_PATH.read_bytes())
    monkeypatch.setattr(director, "PROMPT_PATH", prompt)
    workflow = start(api)
    assembly, verification = through_assembly(api, settings, workflow)
    verify_result(api, settings, assembly, verification)
    before = state(api, workflow)
    original_complete = model.complete

    def complete_after_bundle_change(system, text, schema, image=None):
        result = original_complete(system, text, schema, image)
        assert "runtime_report" in json.loads(text)
        if change == "changed":
            prompt.write_text("검수 대기 중 교체된 지침", encoding="utf-8")
        else:
            prompt.unlink()
        return result

    monkeypatch.setattr(model, "complete", complete_after_bundle_change)
    message = "지침이 변경" if change == "changed" else "서버 패키지"
    with pytest.raises(jobs.Conflict, match=message):
        decision(api, settings, workflow)
    after = state(api, workflow)
    assert after["decisions"] == before["decisions"]
    assert "publication_job" not in after["payload"]["_flow"]
    assert claim(api, "ue-show-1").json() is None


@pytest.mark.parametrize("specified_count", [None, 0])
def test_background_only_reaches_review_and_applied_without_asset_jobs(automatic, specified_count):
    api, settings, model = automatic
    model.count = 0
    spec = copy.deepcopy(SPEC)
    spec["content"]["medium"] = "surface"
    spec["interactions"][0]["reaction"] = "stir"
    workflow = start(api, asset_count=specified_count, spec=spec)
    decision(api, settings, workflow)
    background = generate(api, "gen")
    tick(api, settings)
    decision(api, settings, workflow)
    assert len(model.contexts[-1][0]["generation_conditions"]) == (1 if specified_count is None else 0)
    row = state(api, workflow)
    assert row["asset_jobs"] == []
    assert row["decisions"][-1]["plan"]["assets"] == []
    assert row["decisions"][-1]["plan"]["director"]["instances"] == []
    assert claim(api, "a1").json() is None and claim(api, "a2").json() is None
    tick(api, settings)
    tick(api, settings)
    assembly = claim(api, "ue").json()
    body = assembly["payload"]["assembly"]
    assert body["assets"]["objects"] == [] and len(body["files"]) == 1
    assert assembly["payload"]["depends_on"] == [background["id"]]
    refs = [put(api, assembly, "ue", "scene.json", b'{}'),
            put(api, assembly, "ue", "assembly-report.json", b'{"errors":[]}')]
    assert finish(api, assembly, "ue", refs).status_code == 200
    tick(api, settings)
    verification = claim(api, "ue-review-1").json()
    verify_result(api, settings, assembly, verification)
    decision(api, settings, workflow)
    show = claim(api, "ue-show-1").json()
    with Session(api.app.state.engine) as session:
        owned = session.get(jobs.Job, show["id"])
        jobs.finish(session, owned.id, "ue-show-1", owned.token, {"phase": "Applied"})
    tick(api, settings)
    assert state(api, workflow)["status"] == "completed"


def test_explicit_positive_asset_count_cannot_be_satisfied_by_empty_plan(automatic):
    api, settings, model = automatic
    model.count = 0
    spec = copy.deepcopy(SPEC)
    spec["content"]["medium"] = "surface"
    spec["interactions"][0]["reaction"] = "stir"
    workflow = start(api, asset_count=1, spec=spec)
    decision(api, settings, workflow)
    generate(api, "gen")
    tick(api, settings)
    with pytest.raises(ValueError, match="요청한 에셋 수"):
        decision(api, settings, workflow)
    assert state(api, workflow)["asset_jobs"] == []


def test_zero_asset_count_rejected_before_generation_for_object_reaction(automatic):
    api, _, _ = automatic
    spec = copy.deepcopy(SPEC)
    spec["interactions"][0]["reaction"] = "follow"
    response = api.post("/infra/head/preflight", json={"request_id": "zero-follow", "artwork_id": "art",
        "version": "v1", "prompt": "따라오기", "asset_count": 0, "spec": spec})
    assert response.status_code == 409 and "객체" in response.text
