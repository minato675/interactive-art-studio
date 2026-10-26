"""실제 Worker 완료를 확인한다. 메모리 브로커/eager 모드는 성공으로 인정하지 않는다."""
import argparse
import json
import os
import time
import urllib.request
import uuid
from pathlib import Path


def load_env(path: Path) -> dict:
    return dict(line.strip().split("=", 1) for line in path.read_text("utf-8").splitlines()
                if line.strip() and not line.startswith("#"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=Path(".env.infra"))
    parser.add_argument("--url", default=None)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    config = {**os.environ, **(load_env(args.env) if args.env.exists() else {})}
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    base = args.url or f"http://127.0.0.1:{config.get('CENTRAL_PORT', '18080')}"
    token = config["OPERATOR_TOKEN"]

    def call(path, body=None):
        request = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                         headers={"X-Operator-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    for kind in ("diagnostic", "draft"):
        body = {"request_id": "smoke-" + uuid.uuid4().hex, "kind": kind}
        if kind == "draft":
            body["draft"] = {"mode": "manual", "theme": "바다"}
        job = call("/infra/jobs", body)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            status = call("/infra/jobs/" + job["id"])
            if status["status"] in ("succeeded", "failed"):
                break
            time.sleep(1)
        else:
            raise SystemExit(f"{kind}: Worker did not finish within {args.timeout} seconds")
        assert status["status"] == "succeeded", status
        if kind == "diagnostic":
            assert status["result"]["database"] == "postgresql", status
        else:
            assert status["result"]["status"] == "awaiting", status
        print(json.dumps({"check": kind, "job_id": job["id"], "status": status["status"]}))


if __name__ == "__main__":
    main()
