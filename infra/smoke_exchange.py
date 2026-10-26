"""중앙 HTTPS에서 테스트 PNG 생성 역할 → MR !33 조립 → 결과 회수를 확인한다. Shipping은 실행하지 않는다."""
import argparse
import copy
import hashlib
import json
import os
import struct
import subprocess
import urllib.request
import uuid
import zlib
from pathlib import Path

from central_cli import Operator
from client import Client, atomic_json, digest

CATALOG = "exchange-smoke-v1"
ROOT = Path(__file__).resolve().parents[1]


def png(path, sprite=False):
    def chunk(kind, raw):
        return struct.pack(">I", len(raw)) + kind + raw + struct.pack(">I", zlib.crc32(kind + raw))

    rows = []
    for y in range(36):
        row = bytearray(b"\0")
        for x in range(64):
            alpha = 220 if 20 < x < 44 and 8 < y < 28 else 0
            row.extend((40, 100, 200, alpha if sprite else 255))
        rows.append(row)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 36, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-kit", required=True, type=Path)
    parser.add_argument("--ue-kit", required=True, type=Path)
    parser.add_argument("--ue-checkout", required=True, type=Path)
    parser.add_argument("--ue-env", required=True, type=Path)
    args = parser.parse_args()
    operator = Operator(ROOT / ".env.infra")
    generation, production = Client(args.generation_kit.resolve()), Client(args.ue_kit.resolve())
    generation.check()
    production.check()
    for client in (generation, production):
        identity = client.call("/infra/agent/identity")
        if identity["catalog_version"] != CATALOG:
            raise ValueError("전용 exchange-smoke-v1 카탈로그의 테스트 키트만 사용합니다")
        if client.current() is not None:
            raise ValueError("이전 테스트의 진행 작업을 먼저 확인하세요")
    label = uuid.uuid4().hex[:12]
    folder = ROOT / "runtime/validation/server-exchange" / label
    folder.mkdir(parents=True)
    meta = {"artwork_id": "exchange-" + label, "version": "v1", "catalog_version": CATALOG}

    def submit(kind, parents=()):
        return operator.call("/infra/jobs", {"request_id": label + "-" + kind + "-" + str(len(parents)),
            "kind": kind, "file_transport": "http", "depends_on": list(parents),
            "generation": {**meta, "prompt": "통신 검증용 PNG. 이미지 생성 모델을 호출하지 않습니다."}}, post=True)

    background = submit("background_generate")
    # 조립 요청을 부모 완료 전에 등록해 선행 관계 대기도 확인한다.
    asset = submit("assets_generate", [background["id"]])
    request = json.loads((ROOT / "infra/examples/ue-assembly-http.json").read_text("utf-8"))
    request["request_id"] = label + "-assembly"
    request["depends_on"] = [background["id"], asset["id"]]
    request["assembly"].update(meta)
    for item, parent in zip(request["assembly"]["files"], (background, asset), strict=True):
        item["source_job_id"] = parent["id"]
    assembly = operator.call("/infra/jobs", request, post=True)
    assert production.claim()["event"] == "no_ready_job"
    assert generation.claim()["job_id"] == background["id"]
    source = folder / "background"
    source.mkdir()
    png(source / "background.png")
    generation.complete(source)
    assert generation.claim()["job_id"] == asset["id"]
    generation.inputs(folder / "asset-inputs")
    assert digest(folder / "asset-inputs" / background["id"] / "background.png") == digest(source / "background.png")
    source = folder / "asset"
    source.mkdir()
    png(source / "sprite.png", sprite=True)
    generation.complete(source)
    command = ["uv", "run", "python", str(args.ue_kit.resolve() / "ue_worker.py"),
               "--folder", str(args.ue_kit.resolve()), "--cache", str(folder / "ue-cache"), "--once"]
    result = subprocess.run(command, cwd=args.ue_checkout.resolve(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180, check=False,
        env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(args.ue_env.resolve()), "UV_LINK_MODE": "copy",
             "PYTHONUTF8": "1"})
    (folder / "ue-worker.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, "UE 실행기 로그 확인 필요"
    rows = [operator.call("/infra/jobs/" + job["id"]) for job in (background, asset, assembly)]
    assert all(row["status"] == "succeeded" for row in rows), "중앙 작업 상태와 UE 실행기 로그 확인 필요"
    assembled = rows[-1]
    assert assembled["result"]["verification"] == "assembly_report_only"
    assert assembled["result"]["publish_approved"] is False
    recovered = folder / "recovered"
    recovered.mkdir()
    for ref in assembled["result"]["files"]:
        request = urllib.request.Request(operator.url + f"/infra/jobs/{assembly['id']}/files/{ref['sha256']}",
            headers={"X-Operator-Token": operator.token})
        with operator.opener.open(request, timeout=20) as response:
            raw = response.read()
        assert (hashlib.sha256(raw).hexdigest(), len(raw)) == (ref["sha256"], ref["size"])
        target = recovered / ref["name"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    report = json.loads((recovered / "assembly-report.json").read_text("utf-8"))
    assert report["errors"] == [] and report["runtime_verified"] is False
    summary = {"check": "generation_to_ue_http_exchange", "status": "passed", "fixture_only": True,
               "actual_remote_pcs": False, "shipping_executed": False, "template": report["template"],
               "jobs": [{k: row[k] for k in ("id", "kind", "status")} for row in rows],
               "recovered_files": len(assembled["result"]["files"]), "validation_dir": str(folder)}
    atomic_json(folder / "report.json", copy.deepcopy(summary))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
