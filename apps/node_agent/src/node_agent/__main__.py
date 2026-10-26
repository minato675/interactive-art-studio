import argparse
import os
import threading
import time
from pathlib import Path

from contracts.infra import Bundle
from studio.infra.agent import AgentClient, log, singleton
from studio.infra.http_store import HTTPStore
from studio.infra.storage import FileStore, stage_bundle
from studio.infra.ue_task import assemble_job


def run_one(client: AgentClient, store: FileStore, cache: Path) -> bool:
    client.post("heartbeat")
    job = client.post("claim")
    if job is None:
        return False
    stop, lost = threading.Event(), threading.Event()
    path = f"jobs/{job['id']}"

    def renew():
        while not stop.wait(job["heartbeat_seconds"]):
            try:
                client.post(path + "/heartbeat", {"token": job["token"], "progress": 10})
                client.post("heartbeat")
            except Exception:  # noqa: BLE001 — 갱신 실패 시 결과 확정을 금지
                lost.set()  # 연결이 끊긴 작업은 결과 확정을 시도하지 않는다.
                return

    def check():
        if lost.is_set():
            raise RuntimeError("lease_lost")

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    error, result = "", None
    try:
        task_store = HTTPStore(client, job, cache / "http", check) if job["payload"].get("file_transport") == "http" else store
        if job["kind"] == "ue_assemble":
            result = assemble_job(job, task_store, cache, check)
        elif job["kind"] == "bundle_verify":
            bundle = Bundle.model_validate(job["payload"]["bundle"])
            stage_bundle(task_store, cache, bundle, check)
        else:
            raise ValueError("unsupported_job")
        check()
    except Exception as exc:  # noqa: BLE001 — 작업 경계에서 실패 결과를 중앙에 기록
        error = type(exc).__name__
    finally:
        stop.set()
        thread.join(timeout=20)
    if not lost.is_set():
        client.post(path + "/complete", {"token": job["token"], "error": error, "result": result if not error else None})
    log("node_job_finished", job_id=job["id"], attempt=job["attempt"], attempt_id=job["attempt_id"],
        artwork_id=job["artwork_id"], artwork_version=job["artwork_version"],
        node_id=job["node_id"], outcome="lease_lost" if lost.is_set() else error or "verified")
    return True


def main():
    parser = argparse.ArgumentParser(description="공통 대기열에서 파일 무결성 작업을 하나씩 실행")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    cache = Path(os.environ.get("NODE_CACHE", "runtime/node-cache"))
    store = FileStore(Path(os.environ.get("ARTIFACT_ROOT", str(cache / "shared"))))
    client = AgentClient(os.environ.get("CENTRAL_URL", "http://127.0.0.1:8000"), os.environ["AGENT_TOKEN"],
                         os.environ.get("CENTRAL_CA"))
    try:
        with singleton(cache / "agent.lock"):
            delay = 1
            while True:
                try:
                    worked = run_one(client, store, cache)
                    delay = 1 if worked else 3
                except Exception as exc:
                    log("node_retry", error=type(exc).__name__, delay_seconds=delay)
                    if args.once:
                        raise
                    delay = min(delay * 2, 30)
                if args.once:
                    break
                time.sleep(delay)
    finally:
        client.close()


if __name__ == "__main__":
    main()
