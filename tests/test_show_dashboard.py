"""실제 중앙 API의 시연 구성·검수 후보·게시 연결을 확인한다. UE 대신 기존 시험 Agent를 사용한다."""
import copy

import pytest
from sqlmodel import Session, select
from tests.test_runtime_pipeline import runtime_client, verified  # noqa: F401 — fixture 공유

from db import jobs


def plan_for(verification_id):
    return {"autoplay": False, "duration_seconds": 90, "items": [{"verification_job_id": verification_id, "repeat": 2,
                       "enabled": True}], "loop": False}


def test_empty_dashboard_has_real_registry_and_no_seeded_artworks(runtime_client):  # noqa: F811
    response = runtime_client.get("/infra/show-dashboard")
    assert response.status_code == 200
    value = response.json()
    assert value["library"] == []
    assert {n["node_id"] for n in value["nodes"]} == {"show", "wrong"}
    assert all(not n["can_review"] for n in value["nodes"])
    assert value["configuration"]["plan"]["items"] == []
    assert value["configuration"]["plan"]["duration_seconds"] == 120
    assert value["configuration"]["automatic_playback_connected"] is False
    assert runtime_client.get("/infra/show-dashboard?node_id=missing").status_code == 404


@pytest.mark.parametrize("verdict,visible", [("pass", True), ("review", False), ("fail", False)])
def test_only_verified_candidates_are_listed(runtime_client, verdict, visible):  # noqa: F811
    _, check = verified(runtime_client, verdict)
    rows = runtime_client.get("/infra/show-dashboard").json()["library"]
    assert bool(rows) is visible
    if visible:
        assert rows[0]["verification_job_id"] == check["id"]


def test_saved_configuration_survives_sessions_and_rejects_old_revision(runtime_client):  # noqa: F811
    _, check = verified(runtime_client, "pass")
    plan = plan_for(check["id"])
    response = runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 0, "plan": plan})
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    assert runtime_client.get("/infra/show-dashboard?node_id=show").json()["configuration"]["plan"] == plan
    assert runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 0, "plan": plan}).status_code == 409
    assert runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 1, "plan": plan}).json()["revision"] == 2
    assert runtime_client.get("/infra/show-dashboard?node_id=wrong").json()["configuration"]["revision"] == 0
    with Session(runtime_client.app.state.engine) as session:
        assert session.exec(select(jobs.Job).where(jobs.Job.kind == "ue_publish")).all() == []


@pytest.mark.parametrize("field,value", [("duration_seconds", 0), ("duration_seconds", 4),
                                         ("duration_seconds", 3601), ("duration_seconds", True),
                                         ("duration_seconds", "120"), ("duration_seconds", 120.5),
                                         ("repeat", 0), ("repeat", True), ("enabled", "yes")])
def test_invalid_settings_are_rejected(runtime_client, field, value):  # noqa: F811
    plan = plan_for("not-yet-created")
    target = plan if field == "duration_seconds" else plan["items"][0]
    target[field] = value
    assert runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 0, "plan": plan}).status_code == 422


def test_common_duration_survives_empty_queue_and_new_artwork(runtime_client):  # noqa: F811
    response = runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 0, "plan": {}})
    assert response.status_code == 200
    assert response.json()["plan"] == {"autoplay": False, "duration_seconds": 120, "items": [], "loop": True}
    plan = {"autoplay": False, "duration_seconds": 180, "items": [], "loop": True}
    response = runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 1, "plan": plan})
    assert response.status_code == 200
    saved = runtime_client.get("/infra/show-dashboard?node_id=show").json()["configuration"]
    assert saved["plan"] == plan
    _, check = verified(runtime_client, "pass")
    plan["items"].append({"verification_job_id": check["id"], "repeat": 1, "enabled": True})
    response = runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": saved["revision"], "plan": plan})
    assert response.status_code == 200
    assert runtime_client.get("/infra/show-dashboard?node_id=show").json()["configuration"]["plan"] == plan
    plan["items"][0]["duration_seconds"] = 90  # 작품마다 다른 시간 예외는 허용하지 않음
    assert runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 3, "plan": plan}).status_code == 422
    assert runtime_client.get("/infra/show-dashboard?node_id=show").json()["configuration"]["plan"]["duration_seconds"] == 180


@pytest.mark.parametrize("duration", [5, 3600])
def test_common_duration_boundaries_can_be_saved(runtime_client, duration):  # noqa: F811
    response = runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 0,
                                  "plan": {"duration_seconds": duration}})
    assert response.status_code == 200
    assert response.json()["plan"]["duration_seconds"] == duration


def test_auth_missing_candidates_and_duplicate_items(runtime_client):  # noqa: F811
    for method, path, body in [("GET", "/infra/show-dashboard", None),
                               ("PUT", "/infra/show-dashboard/show", {"expected_revision": 0, "plan": {}})]:
        assert runtime_client.request(method, path, json=body, headers={"X-Operator-Token": ""}).status_code == 401
        assert runtime_client.request(method, path, json=body,
                                      headers={"X-Operator-Token": "", "X-Agent-Token": "show" * 20}).status_code == 401
    plan = plan_for("missing")
    assert runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 0, "plan": plan}).status_code == 409
    plan["items"].append(copy.deepcopy(plan["items"][0]))
    assert runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 0, "plan": plan}).status_code == 422


def test_rejected_review_and_incompatible_source_cannot_be_saved(runtime_client):  # noqa: F811
    _, check = verified(runtime_client, "pass")
    with Session(runtime_client.app.state.engine) as session:
        row = session.get(jobs.Job, check["id"])
        row.result = {**row.result, "human_review": {"request": {"decision": "reject"}}}
        session.add(row)
        session.commit()
    assert runtime_client.get("/infra/show-dashboard").json()["library"] == []
    assert runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 0,
                              "plan": plan_for(check["id"])}).status_code == 409
    with Session(runtime_client.app.state.engine) as session:
        row = session.get(jobs.Job, check["id"])
        row.result = {k: v for k, v in row.result.items() if k != "human_review"}
        row.payload = {**row.payload, "runtime": {**row.payload["runtime"], "catalog_version": "different"}}
        session.add(row)
        session.commit()
    assert runtime_client.get("/infra/show-dashboard").json()["library"] == []
    assert runtime_client.put("/infra/show-dashboard/show", json={"expected_revision": 0,
                              "plan": plan_for(check["id"])}).status_code == 409


def test_approved_review_becomes_candidate_but_preview_exception_is_excluded(runtime_client):  # noqa: F811
    _, check = verified(runtime_client, "review")
    response = runtime_client.post(f"/infra/runtime/jobs/{check['id']}/review", json={
        "decision": "approve", "reviewer": "developer", "reason": "검수 캡처 확인", "expected_attempt": 1})
    assert response.status_code == 200, response.text
    assert len(runtime_client.get("/infra/show-dashboard").json()["library"]) == 1
    with Session(runtime_client.app.state.engine) as session:
        row = session.get(jobs.Job, check["id"])
        row.payload = {**row.payload, "_approval": {"scope": "display_preview_only"}}
        session.add(row)
        session.commit()
    assert runtime_client.get("/infra/show-dashboard").json()["library"] == []
