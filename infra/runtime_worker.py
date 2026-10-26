"""PC1 실행 검증 / PC2 승인 게시 실행기. 로컬 runtime.local.json만 명령·경로를 결정한다."""
import argparse
import json
import shutil
import tempfile
import threading
import time
from contextlib import ExitStack
from pathlib import Path

from client import Client, atomic_json
from runtime_delivery import observe, prepare, publish, singleton
from runtime_process import RuntimeConfig, RuntimeSupervisor


def verify(runner, job, downloaded, delivery, output, lost, renew=lambda: None):
    version, sha = prepare(job, downloaded, delivery)
    def commit():
        renew()
        publish(delivery, version, lost)

    report = runner.verify(version, publish=commit).summary()
    report.update(source_job_id=job["payload"]["runtime"]["source_job_id"], source_bundle_sha256=sha)
    captures = {}
    for index, (label, name) in enumerate(report.get("screenshots", {}).items()):
        source = Path(name).resolve(strict=True)
        if not source.is_relative_to(runner.probe.resolve()):
            raise ValueError("캡처가 전용 probe 폴더 밖에 있습니다")
        relative = f"captures/{index:03d}.png"
        (output / "captures").mkdir(exist_ok=True)
        shutil.copyfile(source, output / relative)
        captures[label] = relative
    report["screenshots"] = captures
    atomic_json(output / "runtime-report.json", report)
    return report


def display(client, config, job, downloaded, delivery, output, lost, supervisor=None):
    version, sha = prepare(job, downloaded, delivery)
    staged = {"phase": "Staged", "desired_version": version}
    client.call("/infra/agent/heartbeat", staged, post=True)
    client.renew(60)
    if supervisor and config.get("auto_launch") is True:
        source = next(p for p in job["inputs"] if p["id"] == job["payload"]["runtime"]["source_job_id"])
        record = {"version": version, "files": source["result"]["files"], "source_bundle_sha256": sha,
                  "source_job_id": source["id"]}
        ticket = supervisor.activate(version, record, lost)
        try:
            while not ticket["event"].wait(0.5):
                if lost.is_set():
                    raise RuntimeError("lease_lost")
                client.call("/infra/agent/heartbeat", supervisor.snapshot(), post=True)
            if ticket["error"]:
                raise RuntimeError(ticket["error"])
            if lost.is_set():
                raise RuntimeError("lease_lost")
            report = {"version": version, "source_job_id": source["id"], "source_bundle_sha256": sha,
                      "status": ticket["status"]}
            atomic_json(output / "applied-report.json", report)
            atomic_json(client.folder / "last-good.json", report)
            return
        finally:
            supervisor.abort(ticket)
    if supervisor:
        if not supervisor.running_requested:
            raise RuntimeError("runtime_explicitly_stopped")
        supervisor.activate(lambda: publish(delivery, version, lost), config.get("apply_timeout_seconds", 120))
    else:
        publish(delivery, version, lost)
    deadline = time.monotonic() + config.get("apply_timeout_seconds", 120)
    while time.monotonic() < deadline:
        if lost.is_set():
            raise RuntimeError("lease_lost")
        if supervisor and not supervisor.running_requested:
            raise RuntimeError("runtime_explicitly_stopped")
        pid = supervisor.pid if supervisor else config["runtime_pid"]
        status = observe(delivery, pid, version)
        client.call("/infra/agent/heartbeat", status, post=True)
        if status["phase"] == "Applied":
            report = {"version": version, "source_job_id": job["payload"]["runtime"]["source_job_id"],
                      "source_bundle_sha256": sha, "status": status}
            atomic_json(output / "applied-report.json", report)
            atomic_json(client.folder / "last-good.json", report)
            return
        if status["phase"] == "Failed":
            raise RuntimeError("runtime_failed")
        time.sleep(1)
    raise TimeoutError("runtime_apply_unconfirmed")


def run_one(client, config, runner=None, supervisor=None, activation_lock=None):
    client.claim()
    job = client.current()
    if job is None:
        return {"event": "no_ready_job"}
    expected = "ue_verify" if config["mode"] == "verify" else "ue_publish"
    if job["kind"] != expected or job["payload"]["runtime"]["target_node"] != client.node["node_id"]:
        raise ValueError("전용 실행기와 작업 종류·대상이 다릅니다")
    # 완료 응답 유실 후 재시작하면 실행 검증이나 게시를 되풀이하지 않는다.
    pending = client.folder / "completion.secret.json"
    if pending.exists():
        body = json.loads(pending.read_text("utf-8"))
        if body.get("job_id") == job["id"] and body.get("attempt_id") == job["attempt_id"]:
            return client.complete()
    stop, lost = threading.Event(), threading.Event()

    def renew():
        while not stop.wait(job["heartbeat_seconds"]):
            try:
                client.renew(30)
            except Exception:  # noqa: BLE001 — 소유권이 불확실하면 게시·완료 금지
                lost.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    cache = Path(config["cache"])
    delivery = Path(config["delivery"])
    cache.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=cache, prefix=".runtime-") as temporary:
            root = Path(temporary)
            downloaded, output = root / "download", root / "output"
            output.mkdir()
            client.inputs(downloaded, source_ids={job["payload"]["runtime"]["source_job_id"]})
            if expected == "ue_verify":
                verify(runner, job, downloaded, delivery, output, lost, lambda: client.renew(60))
            else:
                with activation_lock or threading.RLock():
                    display(client, config, job, downloaded, delivery, output, lost, supervisor)
            if lost.is_set():
                raise RuntimeError("lease_lost")
            return client.complete(output)
    except Exception as exc:  # noqa: BLE001 — 예외 원문·토큰·프롬프트는 출력하지 않는다.
        if not lost.is_set():
            try:
                return client.complete(error="ue_runtime_" + type(exc).__name__)
            except Exception:  # noqa: BLE001 — 완료 불확실 시 영속 pending으로 재전송
                print(json.dumps({"event": "runtime_report_retry", "job_id": job["id"]}), flush=True)
        return {"event": "runtime_completion_unconfirmed", "job_id": job["id"], "error": type(exc).__name__}
    finally:
        stop.set()
        thread.join(timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--runtime-action", choices=("start", "stop", "status"),
                        help="관리 모드의 로컬 실행 의도·상태. 중앙 명령이나 적용 확인을 대신하지 않음")
    args = parser.parse_args()
    if args.check and args.runtime_action:
        parser.error("--check와 --runtime-action은 함께 사용하지 않습니다")
    config = json.loads((args.folder / "runtime.local.json").read_text("utf-8-sig"))
    managed = config.get("managed_runtime")
    auto_launch = config.get("auto_launch") is True
    if auto_launch and managed is not None:
        raise ValueError("choose_one_runtime_manager")
    if auto_launch and config["mode"] != "show":
        raise ValueError("UE 자동 실행 모드는 시연 키트 전용입니다")
    process_config = RuntimeConfig.parse(managed) if managed is not None else None
    if process_config and config["mode"] != "show":
        raise ValueError("UE 프로세스 관리 모드는 시연 키트 전용입니다")
    if args.runtime_action:
        if process_config is None:
            parser.error("managed_runtime 설정이 필요합니다")
        supervisor = RuntimeSupervisor(process_config, Path(config["delivery"]) / "release.json")
        result = (supervisor.snapshot() if args.runtime_action == "status"
                  else supervisor.request(args.runtime_action == "start"))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return
    client = Client(args.folder)
    expected_role = "production" if config["mode"] == "verify" else "show"
    expected_kind = "ue_verify" if config["mode"] == "verify" else "ue_publish"
    if config["mode"] not in ("verify", "show") or client.node["role"] != expected_role:
        raise ValueError("키트 역할과 실행 모드가 다릅니다")
    delivery, cache = Path(config["delivery"]).resolve(), Path(config["cache"]).resolve()
    if not Path(config["delivery"]).is_absolute() or not Path(config["cache"]).is_absolute():
        raise ValueError("로컬 delivery·cache는 절대 경로로 지정합니다")
    if delivery.is_relative_to(cache) or cache.is_relative_to(delivery):
        raise ValueError("delivery와 cache는 별도 경로여야 합니다")
    if set(client.node.get("job_kinds", [])) != {expected_kind}:
        raise ValueError("작업 종류 하나의 전용 키트가 필요합니다")
    runner = None
    supervisor = None
    if config["mode"] == "verify":
        from studio.verify.ue_runner import Runner

        runner = Runner(Path(config["executable"]), delivery / "release.json", Path(config["probe"]),
                        working_directory=Path(config["working_directory"]))
        if not runner.executable.is_file():
            raise ValueError("Shipping 실행본 경로를 설정하세요")
        probe = runner.probe.resolve()
        if probe.is_relative_to(delivery) or delivery.is_relative_to(probe):
            raise ValueError("probe와 delivery는 별도 경로여야 합니다")
    elif auto_launch:
        from show_supervisor import Supervisor

        supervisor = Supervisor(args.folder, config)
        supervisor.validate_windows()
    elif process_config is None and (not isinstance(config.get("runtime_pid"), int) or config["runtime_pid"] <= 0):
        raise ValueError("PC2에서 실행 중인 UE의 실제 PID를 설정하세요")
    if args.check:
        print(json.dumps(client.check(), ensure_ascii=False), flush=True)
        print(json.dumps({"event": "local_configuration_valid", "mode": config["mode"]}), flush=True)
        return
    with ExitStack() as stack:
        if supervisor:
            from show_supervisor import machine_lock

            stack.enter_context(machine_lock())
        stack.enter_context(singleton(args.folder / "runtime-worker.lock"))
        stack.enter_context(singleton(delivery / ".runtime-worker.lock"))
        stack.enter_context(singleton(delivery / ".runtime-writer.lock"))
        if process_config:
            supervisor = stack.enter_context(RuntimeSupervisor(process_config, delivery / "release.json").monitor())
        elif supervisor:
            supervisor.start()
            stack.callback(supervisor.close)
        activation_lock = threading.RLock()
        playback = None
        if config["mode"] == "show" and not args.once:
            from show_playback import PlaylistPlayer

            playback = PlaylistPlayer(client, config, supervisor, activation_lock).start()
            stack.callback(playback.close)
        if process_config is None:
            try:
                print(json.dumps(client.check(), ensure_ascii=False), flush=True)
            except (OSError, ValueError, KeyError) as exc:
                if not supervisor and (not playback or playback.value["revision"] == 0):
                    raise
                print(json.dumps({"event": "offline_local_recovery", "error": type(exc).__name__}), flush=True)
        try:
            while True:
                try:
                    if config["mode"] == "show":
                        pointer = delivery / "release.json"
                        desired = json.loads(pointer.read_text("utf-8"))["Version"] if pointer.exists() else None
                        if auto_launch:
                            status = supervisor.snapshot()
                        else:
                            pid = supervisor.pid if supervisor else config["runtime_pid"]
                            status = observe(delivery, pid, desired)
                        if playback:
                            status["playback"] = playback.report()
                        client.call("/infra/agent/heartbeat", status, post=True)
                        if auto_launch and (not supervisor.ready or not supervisor.thread.is_alive()):
                            time.sleep(1)
                            continue
                    if process_config and not supervisor.running_requested:
                        result = {"event": "runtime_stopped_no_claim"}
                    else:
                        result = run_one(client, config, runner, supervisor, activation_lock)
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                except (OSError, ValueError, KeyError) as exc:
                    print(json.dumps({"event": "runtime_retry", "error": type(exc).__name__}), flush=True)
                    if args.once:
                        raise
                if args.once:
                    break
                time.sleep(3)
        finally:
            if runner:
                runner.stop()  # 이 실행기가 시작한 PC1 검증 프로세스만 종료한다.


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001 — 접속 오류에 비밀 값이 섞이지 않게 한다.
        code = str(exc)
        if code not in {"another_show_supervisor_is_running", "runtime_ownership_mismatch",
                        "use_direct_windows_shipping_executable", "last_good_requires_applied_evidence",
                        "bootstrap_requires_historical_applied", "recovery_integrity_failed",
                        "choose_one_runtime_manager"}:
            code = type(exc).__name__
        raise SystemExit("runtime_worker_failed: " + code) from None
