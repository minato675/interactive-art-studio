"""생성 PC의 로컬 이미지 프로그램을 작업 임대·입력 다운로드·결과 업로드에 연결한다."""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from client import Client, atomic_json
from runtime_delivery import singleton


def run_one(client, config):
    client.claim()
    job = client.current()
    if job is None:
        return {"event": "no_ready_job"}
    if job["kind"] not in ("background_generate", "assets_generate"):
        raise ValueError("generation_kind_mismatch")
    pending = client.folder / "completion.secret.json"
    if pending.exists():
        body = json.loads(pending.read_text("utf-8"))
        if body.get("job_id") == job["id"] and body.get("attempt_id") == job["attempt_id"]:
            return client.complete()
    stop, lost = threading.Event(), threading.Event()

    def renew():
        while not stop.wait(job["heartbeat_seconds"]):
            try:
                client.renew(20)
            except Exception:  # noqa: BLE001 — 소유권이 불확실하면 완료하지 않는다.
                lost.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    cache = Path(config["cache"])
    cache.mkdir(parents=True, exist_ok=True)
    process = None
    try:
        with tempfile.TemporaryDirectory(dir=cache, prefix=".generation-") as temporary:
            root = Path(temporary)
            inputs, output = root / "inputs", root / "output"
            inputs.mkdir()
            output.mkdir()
            client.inputs(inputs)
            # 비밀 임대 토큰은 외부 생성 프로그램 입력에 넣지 않는다.
            public = {"job_id": job["id"], "kind": job["kind"], "generation": job["payload"]["generation"],
                      "inputs": job.get("inputs", []), "input_root": str(inputs), "output_dir": str(output)}
            task = root / "task.json"
            atomic_json(task, public)
            arguments = [*config["handler"], "--request", str(task), "--output", str(output)]
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            with (client.folder / "last-handler.log").open("wb") as log:
                process = subprocess.Popen(arguments, cwd=config["working_directory"], stdout=log,
                                           stderr=subprocess.STDOUT, shell=False, **options)
                deadline = time.monotonic() + config.get("timeout_seconds", 1800)
                while process.poll() is None:
                    if lost.is_set():
                        process.kill()
                        process.wait(timeout=10)
                        raise RuntimeError("lease_lost")
                    if time.monotonic() > deadline:
                        process.kill()
                        process.wait(timeout=10)
                        raise TimeoutError("generation_handler_timeout")
                    time.sleep(.2)
                if process.returncode:
                    raise RuntimeError("generation_handler_failed")
            expected = job["payload"]["generation"].get("conditions", {}).get("output_name")
            if expected and not (output / expected).is_file():
                raise ValueError("required_generation_file_missing")
            if lost.is_set():
                raise RuntimeError("lease_lost")
            return client.complete(output)
    except Exception as exc:  # noqa: BLE001 — 외부 프로그램 원문과 API 키를 출력하지 않는다.
        if not lost.is_set():
            try:
                return client.complete(error="generation_" + type(exc).__name__)
            except Exception:  # noqa: BLE001 — 전송 불확실 시 저장된 완료를 재전송한다.
                print(json.dumps({"event": "generation_report_retry", "job_id": job["id"]}), flush=True)
        return {"event": "generation_unconfirmed", "job_id": job["id"], "error": type(exc).__name__}
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        stop.set()
        thread.join(timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    client = Client(args.folder)
    config = json.loads((args.folder / "generation.local.json").read_text("utf-8-sig"))
    if client.node["role"] != "generation" or len(client.node.get("job_kinds", [])) != 1:
        raise ValueError("generation_dedicated_kit_required")
    command = config.get("handler")
    if not isinstance(command, list) or not command or not all(isinstance(v, str) and v for v in command):
        raise ValueError("handler_argv_required")
    if not Path(command[0]).is_file() and shutil.which(command[0]) is None:
        raise ValueError("handler_executable_missing")
    for name in ("cache", "working_directory"):
        if not Path(config[name]).is_absolute():
            raise ValueError("absolute_local_path_required")
    if not Path(config["working_directory"]).is_dir() or not 1 <= config.get("timeout_seconds", 1800) <= 7200:
        raise ValueError("handler_configuration_invalid")
    print(json.dumps(client.check(), ensure_ascii=False), flush=True)
    if args.check:
        return
    with singleton(args.folder / "generation-worker.lock"):
        while True:
            try:
                print(json.dumps(run_one(client, config), ensure_ascii=False), flush=True)
            except (OSError, ValueError, KeyError) as exc:
                print(json.dumps({"event": "generation_retry", "error": type(exc).__name__}), flush=True)
                if args.once:
                    raise
            if args.once:
                return
            time.sleep(3)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001 — 비밀 값이 섞일 수 있는 예외 원문은 출력하지 않는다.
        raise SystemExit("generation_worker_failed: " + type(exc).__name__) from None
