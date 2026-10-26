import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from central.config import Settings
from central.main import create_app
from db import jobs
from studio.infra.http_store import HTTPStore


@pytest.fixture
def exchange(tmp_path):
    keys = {node: {"token": node * 20, "role": role, "catalog_version": "v1"}
            for node, role in [("gen", "generation"), ("other", "generation"), ("ue", "production"),
                               ("old", "production")]}
    keys["ue"]["job_kinds"] = ["ue_assemble", "bundle_verify"]
    settings = Settings(data_dir=tmp_path, operator_token="operator", agent_keys_json=json.dumps(keys),
                        artifact_max_bytes=1024)
    with TestClient(create_app(settings), headers={"X-Operator-Token": "operator"}) as client:
        yield client


def headers(node, job=None):
    return {"X-Agent-Token": node * 20, **({"X-Lease-Token": job["token"]} if job else {})}


def claim(client, node):
    assert client.post("/infra/agent/heartbeat", headers=headers(node)).status_code == 200
    return client.post("/infra/agent/claim", headers=headers(node))


def queue(client, request_id="bg", artwork="art"):
    response = client.post("/infra/jobs", json={"request_id": request_id, "kind": "background_generate",
        "file_transport": "http", "generation": {"artwork_id": artwork, "version": "v1", "prompt": "test"}})
    assert response.status_code == 202, response.text
    return response.json()


def put(client, job, node, name, raw):
    ref = {"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    response = client.put(f"/infra/agent/jobs/{job['id']}/files", params=ref, content=raw, headers=headers(node, job))
    assert response.status_code == 200, response.text
    return ref


def finish(client, job, node, files):
    return client.post(f"/infra/agent/jobs/{job['id']}/complete", headers=headers(node), json={
        "token": job["token"], "result": {"artwork_id": "art", "version": "v1", "files": files}})


def assembly_request(parent):
    return {"request_id": "ue-job", "kind": "ue_assemble", "file_transport": "http", "depends_on": [parent["id"]],
            "assembly": {"artwork_id": "art", "version": "v1", "catalog_version": "v1", "spec": {}, "assets": {},
                         "files": [{"source_job_id": parent["id"], "source_name": "background.png", "name": "far.png"}]}}


def test_real_bytes_generation_to_production_and_output(exchange):
    client = exchange
    bg = queue(client)
    submitted = client.post("/infra/jobs", json=assembly_request(bg))
    assert submitted.status_code == 202, submitted.text
    assert claim(client, "ue").json() is None  # 부모 완료 전 배정 금지
    generation = claim(client, "gen").json()
    raw = b"actual transport bytes\0" * 10
    ref = put(client, generation, "gen", "background.png", raw)
    assert finish(client, generation, "gen", [ref]).status_code == 200
    assert finish(client, generation, "gen", [ref]).status_code == 200
    assert claim(client, "old").json() is None  # 준비되지 않은 기존 제작 서버 제외
    production = claim(client, "ue").json()
    download = client.get(f"/infra/agent/jobs/{production['id']}/files/{ref['sha256']}", headers=headers("ue", production))
    assert download.content == raw
    assert client.get(f"/infra/agent/jobs/{production['id']}/files/{ref['sha256']}",
                      headers=headers("other", production)).status_code == 409
    assert client.get(f"/infra/jobs/{bg['id']}/files/{ref['sha256']}").content == raw
    scene = put(client, production, "ue", "scene.json", b'{"SchemaVersion":1}')
    report = put(client, production, "ue", "assembly-report.json", b'{"errors":[]}')
    assert finish(client, production, "ue", [scene, report]).json()["verification"] == "assembly_report_only"
    assert finish(client, production, "ue", [scene, report]).status_code == 200
    assert client.get(f"/infra/jobs/{production['id']}").json()["result"]["publish_approved"] is False
    assert client.get(f"/infra/jobs/{production['id']}/files/{scene['sha256']}").content == b'{"SchemaVersion":1}'


def test_transfer_rejects_missing_wrong_oversized_or_unrelated_files(exchange):
    client = exchange
    queue(client)
    job = claim(client, "gen").json()
    ref = {"name": "a.png", "sha256": hashlib.sha256(b"a").hexdigest(), "size": 1}
    assert finish(client, job, "gen", [ref]).status_code == 409
    url = f"/infra/agent/jobs/{job['id']}/files"
    assert client.put(url, params=ref, content=b"b", headers=headers("gen", job)).status_code == 422
    assert client.put(url, params={**ref, "name": "../a.png"}, content=b"a",
                      headers=headers("gen", job)).status_code == 422
    assert client.put(url, params={**ref, "size": 1025}, content=b"a",
                      headers=headers("gen", job)).status_code == 413
    put(client, job, "gen", "a.png", b"a")
    assert client.put(url, params={**ref, "sha256": hashlib.sha256(b"b").hexdigest()}, content=b"b",
                      headers=headers("gen", job)).status_code == 409
    queue(client, "unrelated", "other-art")
    other = claim(client, "other").json()
    assert client.get(f"/infra/agent/jobs/{other['id']}/files/{ref['sha256']}",
                      headers=headers("other", other)).status_code == 404
    with Session(client.app.state.engine) as session:
        row = session.get(jobs.Job, job["id"])
        row.lease_until = 0
        session.add(row)
        session.commit()
    assert client.put(url, params=ref, content=b"a", headers=headers("gen", job)).status_code == 409


def test_assembly_rejects_cross_artwork_and_unsafe_mapping(exchange):
    bg = queue(exchange, artwork="other-art")
    assert exchange.post("/infra/jobs", json=assembly_request(bg)).status_code == 409
    request = assembly_request(bg)
    request["assembly"]["files"][0]["name"] = "scene.json"
    assert exchange.post("/infra/jobs", json=request).status_code == 422
    request["assembly"]["files"][0]["name"] = "../escape.png"
    assert exchange.post("/infra/jobs", json=request).status_code == 422


def test_http_store_streams_and_rejects_corrupted_cache(exchange, tmp_path):
    queue(exchange)
    generation = claim(exchange, "gen").json()
    exchange.headers.update(headers("gen"))
    source = tmp_path / "background.png"
    source.write_bytes(b"binary transport" * 32)
    store = HTTPStore(SimpleNamespace(http=exchange), generation, tmp_path / "cache")
    ref = store.ingest(source, source.name)
    assert store.path(ref).read_bytes() == source.read_bytes()
    store.path(ref).write_bytes(b"corrupted")
    with pytest.raises(ValueError):
        store.path(ref)


def test_head_condition_output_name_is_required(exchange):
    from studio.head import ue_support

    selected = {"template": "gpu_particles", "fingerprint": "0" * 64, "background_usage": "display",
                "allowed_render_modes": ["2d"], "behaviors": ["float"]}
    response = exchange.post("/infra/jobs", json={"request_id": "fixed-name", "kind": "background_generate",
        "file_transport": "http", "generation": {"artwork_id": "art", "version": "v1", "prompt": "test",
            "conditions": ue_support.conditions(selected, "background").model_dump(mode="json")}})
    assert response.status_code == 202
    job = claim(exchange, "gen").json()
    wrong = put(exchange, job, "gen", "other.png", b"png fixture")
    assert finish(exchange, job, "gen", [wrong]).status_code == 422
    correct = put(exchange, job, "gen", "background.png", b"png fixture")
    assert finish(exchange, job, "gen", [correct]).status_code == 200
