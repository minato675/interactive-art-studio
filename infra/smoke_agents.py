"""깨끗한 로컬 개발 환경에서 실제 HTTP와 두 Windows Agent의 파일 작업을 확인한다."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from smoke import load_env

from contracts.infra import Bundle
from node_agent.__main__ import run_one
from studio.infra.agent import AgentClient
from studio.infra.storage import FileStore


def main():
    config = load_env(Path(".env.infra"))
    base = f"http://127.0.0.1:{config['CENTRAL_PORT']}"
    keys = json.loads(config["AGENT_KEYS_JSON"])
    folder = Path("runtime/agent-smoke") / uuid.uuid4().hex
    folder.mkdir(parents=True)
    source = folder / "scene.json"
    source.write_text('{"diagnostic_only":true}', encoding="utf-8")
    store = FileStore(folder / "share")
    ref = store.ingest(source, "scene.json")
    job_ids = []
    with httpx.Client(base_url=base, headers={"X-Operator-Token": config["OPERATOR_TOKEN"]}, timeout=15) as operator:
        for name in ("A", "B"):
            bundle = Bundle(artwork_id="infra-" + name, version=uuid.uuid4().hex, catalog_version="v1", files=[ref])
            response = operator.post("/infra/jobs", json={"request_id": "agent-" + uuid.uuid4().hex,
                                                          "kind": "bundle_verify", "bundle": bundle.model_dump()})
            response.raise_for_status()
            job_ids.append(response.json()["id"])

        def run(name):
            client = AgentClient(base, keys[name]["token"])
            try:
                return run_one(client, store, folder / name)
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert all(pool.map(run, ["production-1", "production-2"]))
        results = [operator.get("/infra/jobs/" + key).json() for key in job_ids]
        assert all(r["status"] == "succeeded" for r in results), results
        assert len({r["node_id"] for r in results}) == 2, results
        assert all(r["result"]["publish_approved"] is False for r in results)
        print(json.dumps({"check": "two_agents_http_and_cache", "status": "passed", "jobs": job_ids}))


if __name__ == "__main__":
    main()
