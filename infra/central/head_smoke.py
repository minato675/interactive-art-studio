"""실제 RabbitMQ Worker에서 Luna 판단 2회. 생성 PC 결과만 합성한다."""
import argparse
import base64
import hashlib
import json
import struct
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import zlib
from pathlib import Path

from smoke import load_env


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    pixels = b"".join(b"\0" + bytes((25, 90 + y, 150)) * 64 for y in range(64))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-version", required=True, help="온라인 UE 테스트 노드가 보고한 head-smoke-... 카탈로그")
    args = parser.parse_args()
    if not args.catalog_version.startswith("head-smoke-"):
        parser.error("운영 작업과 분리된 head-smoke-... 카탈로그가 필요합니다")
    config = load_env(Path(".env.infra"))
    base = f"http://127.0.0.1:{config.get('CENTRAL_PORT', '18080')}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Operator-Token": config["OPERATOR_TOKEN"],
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Head smoke HTTP {exc.code}") from None

    def wait(workflow_id, wanted):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            row = call("/infra/head/workflows/" + workflow_id)
            if row["status"] == wanted:
                return row
            failures = [{"id": j["id"], "error": j["error"]} for j in row["jobs"] if j["status"] == "failed"]
            if failures:
                raise RuntimeError("Head smoke failed: " + json.dumps(failures))
            time.sleep(2)
        raise RuntimeError("Head smoke timed out")

    def synthetic_complete(workflow_id, job_id, files):
        code = """
import json,sys
from sqlmodel import Session
from central.config import Settings
from db.engine import make_engine
from db.head import HeadWorkflow
from db import jobs
with Session(make_engine(Settings().db_url)) as session:
    flow=session.get(HeadWorkflow, sys.argv[1])
    assert flow.payload['profile']=='test' and flow.payload['catalog_version'].startswith('head-smoke-')
    assert sys.argv[2] in [flow.background_job]+[a['id'] for a in flow.asset_jobs]
    row=jobs.start(session, session.get(jobs.Job, sys.argv[2]), 'head-smoke-synthetic', 120)
    jobs.complete_generation(session,row.id,row.node_id,row.token,
        {'artwork_id':flow.payload['artwork_id'],'version':flow.payload['version'],'files':json.loads(sys.argv[3])},'')
"""
        result = subprocess.run(["docker", "exec", "ranel-worker-1", "python", "-c", code,
                                 workflow_id, job_id, json.dumps(files)], check=False,
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("Synthetic generation completion failed")

    suffix = uuid.uuid4().hex[:12]
    row = call("/infra/head/workflows", {"request_id": "head-smoke-" + suffix, "artwork_id": "head-smoke-" + suffix,
               "version": "v1", "catalog_version": args.catalog_version,
               "prompt": "푸른 수중 배경에 어울리는 미디어아트. 산호, 물고기, 빛 입자를 고려한다.",
               "asset_count": 3, "profile": "test", "spec": json.loads(
                   Path("infra/examples/ue-assembly-http.json").read_text("utf-8"))["assembly"]["spec"]})
    workflow_id = row["id"]
    row = wait(workflow_id, "waiting_background")
    raw = png()
    files = [{"name": "background.png", "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}]
    synthetic_complete(workflow_id, row["background_job"], files)
    wait(workflow_id, "awaiting_background_input")
    call(f"/infra/head/workflows/{workflow_id}/background-input", {
        "name": "background.png", "data_url": "data:image/png;base64," + base64.b64encode(raw).decode()})
    row = wait(workflow_id, "waiting_assets")
    assert len(row["asset_jobs"]) == 3
    for index, child in enumerate(row["asset_jobs"]):
        synthetic_complete(workflow_id, child["id"], [{**files[0], "name": f"asset-{index + 1:03d}.png"}])
    row = wait(workflow_id, "generation_complete")
    assert len(row["decisions"]) == 2
    assert all(d["provider"] == "openai" and d["model"] == "gpt-6-luna" for d in row["decisions"])
    assert row["publish_approved"] is False
    report = {"workflow_id": workflow_id, "status": row["status"], "model": row["model"],
              "live_decisions": len(row["decisions"]), "assets": len(row["asset_jobs"]),
              "image_sha256": row["image"]["sha256"], "generation": "synthetic", "publish_approved": False}
    target = Path("runtime/validation/head-smoke.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
