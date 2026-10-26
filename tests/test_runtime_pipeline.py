import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from test_server_exchange import assembly_request, claim, finish, headers, put, queue

from central.config import Settings
from central.main import create_app
from central.runtime_flow import bundle_identity
from db import jobs


@pytest.fixture
def runtime_client(tmp_path):
    roles = {"gen": ("generation", ["background_generate"]), "ue": ("production", ["ue_assemble"]),
             "ue-review-1": ("production", ["ue_verify"]), "show": ("show", ["ue_publish"]),
             "wrong": ("show", ["ue_publish"])}
    keys = {node: {"token": node * 20, "role": role, "catalog_version": "v1", "job_kinds": kinds}
            for node, (role, kinds) in roles.items()}
    with TestClient(create_app(Settings(data_dir=tmp_path, operator_token="operator",
                                       agent_keys_json=json.dumps(keys))),
                    headers={"X-Operator-Token": "operator"}) as client:
        yield client


def assembled(client):
    bg = queue(client)
    client.post("/infra/jobs", json=assembly_request(bg)).raise_for_status()
    gen = claim(client, "gen").json()
    finish(client, gen, "gen", [put(client, gen, "gen", "background.png", b"png")]).raise_for_status()
    ue = claim(client, "ue").json()
    refs = [put(client, ue, "ue", "scene.json", b'{}'),
            put(client, ue, "ue", "assembly-report.json", b'{"errors":[]}')]
    finish(client, ue, "ue", refs).raise_for_status()
    finish(client, ue, "ue", refs).raise_for_status()  # 완료 재전송으로 검증 작업을 중복 등록하지 않는다.
    listing = client.get("/infra/jobs").json()
    assert len([j for j in listing if j["kind"] == "ue_verify"]) == 1
    verification = claim(client, "ue-review-1").json()
    with Session(client.app.state.engine) as session:
        sha = bundle_identity(session.get(jobs.Job, ue["id"]))
    return ue, verification, sha


def verified(client, verdict):
    ue, job, sha = assembled(client)
    capture = put(client, job, "ue-review-1", "captures/rest.png", b"capture")
    report = {"version": "ue-" + ue["id"], "source_job_id": ue["id"], "source_bundle_sha256": sha,
              "verdict": verdict, "passed": verdict != "fail", "needs_review": verdict == "review",
              "checks": [{"passed": verdict != "fail"}], "bundle": {"sha256": sha},
              "screenshots": {"rest": capture["name"]}}
    ref = put(client, job, "ue-review-1", "runtime-report.json", json.dumps(report).encode())
    result = finish(client, job, "ue-review-1", [capture, ref])
    assert result.status_code == 200, result.text
    assert finish(client, job, "ue-review-1", [capture, ref]).status_code == 200
    return ue, job


@pytest.mark.parametrize("verdict", ["pass", "review", "fail"])
def test_approval_and_role_target_isolation(runtime_client, verdict):
    client = runtime_client
    ue, check = verified(client, verdict)
    assert claim(client, "show").json() is None
    approval = {"request_id": "publish-1", "target_node": "show", "reviewer": "operator", "reason": "화면 검수"}
    response = client.post(f"/infra/jobs/{check['id']}/approve-publication", json=approval)
    if verdict == "fail":
        assert response.status_code == 409
        return
    assert response.status_code == 202, response.text
    assert client.post(f"/infra/jobs/{check['id']}/approve-publication", json=approval).json()["id"] == response.json()["id"]
    assert claim(client, "wrong").json() is None
    job = claim(client, "show").json()
    source_ref = next(p for p in job["inputs"] if p["id"] == ue["id"])["result"]["files"][0]
    response = client.get(f"/infra/agent/jobs/{job['id']}/files/{source_ref['sha256']}", headers=headers("show", job))
    assert response.status_code == 200
    status = {"phase": "Applied", "desired_version": "ue-" + ue["id"], "active_version": "ue-" + ue["id"],
              "runtime_session": "session", "runtime_pid": 123}
    report = {"version": "ue-" + ue["id"], "source_job_id": ue["id"],
              "source_bundle_sha256": job["inputs"][1]["result"].get("source_bundle_sha256"), "status": status}
    # Input ordering is defined by depends_on: assembly then verification.
    ref = put(client, job, "show", "applied-report.json", json.dumps(report).encode())
    assert finish(client, job, "show", [ref]).status_code == 200
    saved = client.get(f"/infra/jobs/{job['id']}").json()
    assert saved["result"]["phase"] == "Applied"
    assert saved["result"]["publish_approved"] is True
    assert saved["approval"]["reviewer"] == "operator"


def test_reject_direct_publication_and_forged_report(runtime_client):
    client = runtime_client
    ue, job, sha = assembled(client)
    payload = {"request_id": "bypass", "kind": "ue_publish", "file_transport": "http", "depends_on": [ue["id"]],
               "runtime": {"artwork_id": "art", "version": "v1", "source_job_id": ue["id"], "target_node": "show"}}
    assert client.post("/infra/jobs", json=payload).status_code == 403
    report = {"version": "ue-" + ue["id"], "source_job_id": ue["id"], "source_bundle_sha256": sha,
              "verdict": "pass", "passed": False}
    ref = put(client, job, "ue-review-1", "runtime-report.json", json.dumps(report).encode())
    assert finish(client, job, "ue-review-1", [ref]).status_code == 422
    assert hashlib.sha256(b'{}').hexdigest() in [f["sha256"] for f in job["inputs"][0]["result"]["files"]]
