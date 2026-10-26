"""노트북 중앙 시작·상태 확인·백업. 비밀 환경 값은 출력하지 않는다."""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from studio.infra.agent import singleton

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", ".env.infra", "--env-file", ".env.https",
           "-f", "infra/central/compose.yaml", "-f", "infra/central/compose.https.yaml",
           "-f", "infra/central/compose.laptop.yaml"]


def state_dir():
    if os.name == "nt":
        return Path(os.environ["USERPROFILE"]) / "RanelMaster"
    return Path.home() / ".local/state/ranel-master"


def run(command, timeout=180):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace",
                            timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{command[0]} exit={result.returncode}")
    return result.stdout.strip()


def save_report(name, report):
    folder = state_dir() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def status():
    text = run([*COMPOSE, "ps", "--format", "json"])
    rows = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line]
    services = [{k: row.get(k) for k in ("Service", "State", "Health", "Image")} for row in rows]
    config = dict(line.split("=", 1) for line in (ROOT / ".env.infra").read_text("utf-8-sig").splitlines()
                  if line and not line.startswith("#") and "=" in line)
    url = "http://127.0.0.1:" + config.get("CENTRAL_PORT", "18080") + "/ready"
    with urllib.request.urlopen(url, timeout=5) as response:
        ready = json.load(response)
    container = run([*COMPOSE, "ps", "-q", "central"])
    revision = run(["docker", "inspect", container, "--format",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}'])
    required = {"central", "worker", "dispatcher", "postgres", "rabbitmq", "gateway"}
    running = {row["Service"] for row in services
               if row["State"] == "running" and row["Health"] in (None, "", "healthy")}
    ok = ready.get("database") == "postgresql" and required <= running
    return {"ok": ok, "ready": ready, "services": services, "revision": revision,
            "scope": "local containers and database; worker/remote PC checks are separate"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "status", "backup"])
    args = parser.parse_args()
    report = {"action": args.action, "at": datetime.now(UTC).isoformat(), "ok": False}
    try:
        if args.action == "start":
            with singleton(ROOT / "runtime/local-pipeline.lock"):
                deadline = time.monotonic() + 180
                while True:
                    try:
                        run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10)
                        break
                    except (RuntimeError, subprocess.TimeoutExpired):
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Docker Desktop startup timeout") from None
                        time.sleep(5)
                run([*COMPOSE, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120"])
                report.update(status())
        elif args.action == "backup":
            with singleton(ROOT / "runtime/local-pipeline.lock"):
                target = state_dir() / "backups" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                result = run([sys.executable, "-X", "utf8", "infra/backup_central.py", "--output", str(target)], timeout=900)
                report.update(json.loads(result))
                report["ok"] = True
        else:
            report.update(status())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report["error"] = type(exc).__name__
        report["operator_note"] = "Check Docker Desktop, local configuration, active jobs and operation lock"
    save_report(args.action + ".json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
