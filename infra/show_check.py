"""키트 상태 조회와 운영자가 명시적으로 실행하는 UE 강제 종료·복구 검사."""
import argparse
import json
import time
from pathlib import Path

import psutil
from client import atomic_json
from runtime_delivery import observe
from show_supervisor import Supervisor


def inspect(folder):
    config = json.loads((folder / "runtime.local.json").read_text("utf-8-sig"))
    if config.get("auto_launch") is not True or config["mode"] != "show":
        raise ValueError("automatic_show_configuration_required")
    manager = Supervisor(folder, config)
    owner = folder / "runtime-process.json"
    if owner.exists():
        manager.owner = json.loads(owner.read_text("utf-8"))
    process = manager.owned_process()
    pointer = json.loads((manager.delivery / "release.json").read_text("utf-8"))["Version"]
    status = observe(manager.delivery, process.pid, pointer) if process else {"phase": "Unknown"}
    foreign = manager.foreign_processes()
    state = json.loads((folder / "supervisor-state.json").read_text("utf-8"))
    try:
        supervisor = psutil.Process(state["supervisor_pid"])
        supervising = (supervisor.is_running()
                       and abs(supervisor.create_time() - state["supervisor_create_time"]) <= 0.01
                       and 0 <= time.time() - state["updated_at"] <= 5)
    except (psutil.Error, KeyError):
        supervising = False
    passed = (bool(process) and not foreign and status["phase"] == "Applied"
              and status["active_version"] == manager.good["version"] and state["phase"] == "Running" and supervising)
    return manager, {"event": "show_status", "passed": passed, "ue_process_count": int(bool(process)) + len(foreign),
                     "owner_verified": bool(process), "runtime_pid": process.pid if process else None,
                     "runtime_session": status.get("runtime_session"), "phase": status["phase"],
                     "active_version": status.get("active_version"), "last_good_version": manager.good["version"],
                     "supervisor_phase": state["phase"], "supervisor_error": state.get("error", "")}


def crash_test(folder, *, timeout=None):
    manager, before = inspect(folder)
    if not before["passed"]:
        raise RuntimeError("wait_for_single_applied_runtime_before_test")
    process = manager.owned_process()  # 종료 직전 경로·생성 시각·실행 식별자를 재검증한다.
    process.kill()
    process.wait(timeout=10)
    limit = timeout if timeout is not None else manager.timeout + manager.maximum + 15
    started = time.monotonic()
    after, last_error = None, ""
    while time.monotonic() - started < limit:
        try:
            _, after = inspect(folder)
            if (after["passed"] and after["runtime_pid"] != before["runtime_pid"]
                    and after["runtime_session"] != before["runtime_session"]
                    and after["active_version"] == before["last_good_version"]):
                result = {"event": "recovery_test", "passed": True, "before": before, "after": after,
                          "elapsed_seconds": round(time.monotonic() - started, 2)}
                atomic_json(folder / "recovery-test-result.json", result)
                return result
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            last_error = type(exc).__name__
        time.sleep(0.5)
    result = {"event": "recovery_test", "passed": False, "before": before, "after": after,
              "error": last_error or "recovery_unconfirmed", "elapsed_seconds": round(time.monotonic() - started, 2)}
    atomic_json(folder / "recovery-test-result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--crash-test", action="store_true")
    args = parser.parse_args()
    folder = args.folder.resolve()
    if args.crash_test:
        print("This will kill ONLY the owned UE process to test recovery. Keep Start-Worker running.")
        if input("Type RECOVER to continue: ").strip() != "RECOVER":
            raise SystemExit("recovery_test_cancelled")
        result = crash_test(folder)
    else:
        _, result = inspect(folder)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    raise SystemExit(0 if result["passed"] else 2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — 설정·인증 정보 전체는 출력하지 않는다.
        raise SystemExit("show_check_failed: " + type(exc).__name__) from None
