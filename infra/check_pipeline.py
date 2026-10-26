"""CI의 임시 PostgreSQL/RabbitMQ에 세 프로세스를 붙여 실제 작업 완료를 확인한다."""
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import ExitStack
from pathlib import Path

from sqlalchemy.engine import make_url


def verify_worker_restart(environment, processes, command, output):
    """Worker가 내려간 동안 접수한 작업이 재시작 후 완료되는지 실제 브로커로 확인한다."""
    processes[1].terminate()
    processes[1].wait(timeout=20)
    base = "http://127.0.0.1:18780"

    def call(path, data=None):
        request = urllib.request.Request(
            base + path, data=json.dumps(data).encode() if data else None,
            headers={"X-Operator-Token": environment["OPERATOR_TOKEN"], "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    job = call("/infra/jobs", {"kind": "diagnostic", "request_id": "restart-" + uuid.uuid4().hex})
    time.sleep(2)
    assert call("/infra/jobs/" + job["id"])["status"] == "queued"
    processes.append(subprocess.Popen([sys.executable, *command], env=environment,
                                      stdout=output, stderr=subprocess.STDOUT))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = call("/infra/jobs/" + job["id"])
        if state["status"] == "succeeded":
            print(json.dumps({"check": "worker_restart_pending_job", "status": "passed"}), flush=True)
            return
        time.sleep(1)
    raise RuntimeError("Worker restart did not complete pending job")


def main():
    if sys.platform == "win32":
        raise SystemExit("Celery 통합 테스트는 Linux 컨테이너에서 실행합니다")
    database_url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_POSTGRES_URL")
    if not database_url or not os.environ.get("BROKER_URL"):
        raise SystemExit("DATABASE_URL(또는 TEST_POSTGRES_URL)과 BROKER_URL이 필요합니다")
    if not (make_url(database_url).database or "").endswith("_test"):
        raise SystemExit("별도 _test 데이터베이스에서만 실행합니다")
    environment = {**os.environ, "OPERATOR_TOKEN": secrets.token_hex(32), "STUDIO_DATA": "runtime/ci",
                   "CENTRAL_PORT": "18780", "LLM_PROVIDER": "none", "PLANNING_QUEUE": "ci-" + uuid.uuid4().hex,
                   "DATABASE_URL": database_url}
    environment["CI_GENERATION_TOKEN"] = secrets.token_hex(32)
    environment["CI_GENERATION_NODE"] = "ci-generation-" + uuid.uuid4().hex[:10]
    environment["AGENT_KEYS_JSON"] = json.dumps({environment["CI_GENERATION_NODE"]: {
        "token": environment["CI_GENERATION_TOKEN"], "role": "generation",
        "catalog_version": environment["CI_GENERATION_NODE"]}})
    folder = Path("runtime/ci")
    folder.mkdir(parents=True, exist_ok=True)
    processes = []
    commands = [
        ["-m", "uvicorn", "central.main:app", "--host", "127.0.0.1", "--port", "18780"],
        ["-m", "celery", "-A", "central.worker:celery", "worker", "-Q", environment["PLANNING_QUEUE"],
         "--concurrency=1"],
        ["-m", "central.worker"],
    ]
    with ExitStack() as stack:
        try:
            for index, command in enumerate(commands):
                output = stack.enter_context((folder / f"process-{index}.log").open("w"))
                process = subprocess.Popen([sys.executable, *command], env=environment,
                                           stdout=output, stderr=subprocess.STDOUT)
                processes.append(process)
                if index == 0:
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError("API process exited")
                        try:
                            with urllib.request.urlopen("http://127.0.0.1:18780/ready", timeout=2):
                                break
                        except (OSError, urllib.error.URLError):
                            time.sleep(1)
                    else:
                        raise RuntimeError("API readiness timed out")
            subprocess.run([sys.executable, "infra/central/smoke.py", "--env", "runtime/ci/no-env-file",
                            "--url", "http://127.0.0.1:18780"], env=environment, check=True, timeout=200)
            restarted_log = stack.enter_context((folder / "worker-restarted.log").open("w"))
            verify_worker_restart(environment, processes, commands[1], restarted_log)
            subprocess.run([sys.executable, "infra/smoke_generation.py"], env=environment, check=True, timeout=120)
        finally:
            for process in reversed(processes):
                process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
