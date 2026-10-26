import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from central.config import Settings
from central.main import create_app
from db import jobs


def test_dedicated_nodes_dependency_slots_and_disabled_legacy(tmp_path):
    registry = {name: {"token": name * 20, "role": "generation", "catalog_version": "v1", "job_kinds": kinds}
                for name, kinds in {"bg": ["background_generate"], "a1": ["assets_generate"],
                                    "a2": ["assets_generate"]}.items()}
    registry["legacy"] = {"token": "legacy" * 20, "role": "generation", "catalog_version": "v1", "enabled": False}
    settings = Settings(data_dir=tmp_path, operator_token="operator", agent_keys_json=json.dumps(registry))
    with TestClient(create_app(settings), headers={"X-Operator-Token": "operator"}) as client:
        def node(name, path, body=None):
            return client.post("/infra/agent/" + path, json=body, headers={"X-Agent-Token": registry[name]["token"]})

        assert node("legacy", "heartbeat").status_code == 401
        for name in ("bg", "a1", "a2"):
            assert node(name, "heartbeat").status_code == 200
        spec = {"artwork_id": "A", "version": "v1", "prompt": "test"}
        bg = client.post("/infra/jobs", json={"request_id": "bg", "kind": "background_generate",
                                              "generation": spec}).json()
        ids = []
        for index in range(3):
            response = client.post("/infra/jobs", json={"request_id": f"a-{index}", "kind": "assets_generate",
                                                        "generation": spec, "depends_on": [bg["id"]]})
            assert response.status_code == 202
            ids.append(response.json()["id"])
        assert node("a1", "claim", {"job_kinds": ["background_generate"]}).json() is None
        lease = node("bg", "claim").json()
        assert lease["id"] == bg["id"]
        assert node("bg", "claim").status_code == 409
        with Session(client.app.state.engine) as session:
            jobs.finish(session, bg["id"], "bg", lease["token"], {})
        assert node("bg", "claim", {"job_kinds": ["assets_generate"]}).json() is None
        a1, a2 = node("a1", "claim").json(), node("a2", "claim").json()
        assert a1["id"] != a2["id"] and {a1["id"], a2["id"]}.issubset(ids)
        assert node("a1", "claim").status_code == 409
        with Session(client.app.state.engine) as session:
            jobs.finish(session, a1["id"], "a1", a1["token"], {})
        assert node("a1", "claim").json()["id"] in set(ids) - {a1["id"], a2["id"]}


@pytest.mark.parametrize("extra", [{"enabled": "false"}, {"job_kinds": ["ue_assemble"]},
                                  {"job_kinds": ["assets_generate", "assets_generate"]},
                                  {"job_kinds": [{}]}, {"job_kinds": None}])
def test_invalid_generation_policy_rejected(extra):
    entry = {"token": "safe-test-token-123456", "role": "generation", "catalog_version": "v1", **extra}
    with pytest.raises(ValueError):
        Settings(agent_keys_json=json.dumps({"node": entry}))


def test_issuer_updates_kind_preserves_token_and_rejects_cross_role(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = tmp_path / "server.env"
    env.write_text("AGENT_KEYS_JSON={}\n", encoding="utf-8")

    def issue(*args):
        return subprocess.run([sys.executable, str(root / "infra/issue_client.py"), "--env", str(env),
                               "--node", "generation-background-1", *args],
                              check=False, capture_output=True)

    assert issue("--job-kind", "background_generate").returncode == 0
    before = json.loads(env.read_text().strip().split("=", 1)[1])["generation-background-1"]
    assert issue("--job-kind", "assets_generate").returncode == 0
    after = json.loads(env.read_text().strip().split("=", 1)[1])["generation-background-1"]
    assert before["token"] == after["token"] and after["job_kinds"] == ["assets_generate"]
    assert issue("--job-kind", "ue_assemble").returncode != 0
    assert json.loads(env.read_text().strip().split("=", 1)[1])["generation-background-1"] == after
