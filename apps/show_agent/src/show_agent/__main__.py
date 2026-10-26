import argparse
import json
import os
import time
from contextlib import ExitStack
from pathlib import Path

from contracts.infra import Bundle
from studio.infra.agent import AgentClient, log, singleton
from studio.infra.runtime_process import RuntimeConfig, RuntimeSupervisor
from studio.infra.storage import FileStore, atomic_json, release_version

from .runtime import activate, observe, prepare


def main():
    parser = argparse.ArgumentParser(description="실행 중인 UE에 파일 준비와 실제 적용 상태 연결")
    parser.add_argument("--bundle", type=Path, help="이번에 준비할 번들 manifest. 없으면 상태 관찰만")
    parser.add_argument("--activate", action="store_true", help="검토한 번들을 UE release watcher에 전달")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--runtime-config", type=Path, help="선택적인 UE 프로세스 관리 JSON 설정")
    parser.add_argument("--runtime-action", choices=("start", "stop", "status"))
    args = parser.parse_args()
    if args.activate and not args.bundle:
        parser.error("--activate에는 --bundle이 필요합니다")
    cache = Path(os.environ.get("SHOW_CACHE", "runtime/show-cache"))
    process_config = (RuntimeConfig.parse(json.loads(args.runtime_config.read_text("utf-8-sig")))
                      if args.runtime_config else None)
    if args.runtime_action:
        if process_config is None:
            parser.error("--runtime-config가 필요합니다")
        supervisor = RuntimeSupervisor(process_config, cache.resolve() / "release.json")
        result = (supervisor.snapshot() if args.runtime_action == "status"
                  else supervisor.request(args.runtime_action == "start"))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return
    pid = 0 if process_config else int(os.environ["UE_RUNTIME_PID"])
    client = AgentClient(os.environ.get("CENTRAL_URL", "http://127.0.0.1:8000"), os.environ["AGENT_TOKEN"])
    try:
        with ExitStack() as stack:
            stack.enter_context(singleton(cache / "agent.lock"))
            stack.enter_context(singleton(cache / ".runtime-writer.lock"))
            supervisor = (stack.enter_context(RuntimeSupervisor(process_config, cache.resolve() / "release.json").monitor())
                          if process_config else None)
            desired = None
            preparation_error = ""
            if args.bundle:
                bundle = Bundle.model_validate_json(args.bundle.read_text("utf-8"))
                desired = release_version(bundle)
                try:
                    prepare(FileStore(Path(os.environ["ARTIFACT_ROOT"])), cache, bundle,
                            os.environ.get("CATALOG_VERSION", "v1"))
                    if args.activate:
                        if supervisor and not supervisor.running_requested:
                            raise ValueError("runtime_explicitly_stopped")
                        if supervisor:
                            supervisor.activate(lambda: activate(cache, bundle))
                        else:
                            activate(cache, bundle)
                except Exception as exc:  # noqa: BLE001 — 준비 실패 뒤에도 현재 전시 관찰을 계속한다
                    # 기존 release.json과 활성 작품은 수정하지 않는다.
                    preparation_error = type(exc).__name__
                    log("show_prepare_failed", error=preparation_error, desired_version=desired)
            while True:
                report = observe(cache / "release.json.status.json", supervisor.pid if supervisor else pid, desired)
                if preparation_error:
                    report = report.model_copy(update={"phase": "Failed", "error": preparation_error})
                elif args.bundle and not args.activate:
                    report = report.model_copy(update={"phase": "Staged", "error": ""})
                if report.phase == "Applied":
                    atomic_json(cache / "last-good.json", report.model_dump())
                try:
                    client.post("heartbeat", report.model_dump())
                except Exception as exc:
                    log("show_report_retry", error=type(exc).__name__)
                    if args.once:
                        raise
                if args.once:
                    if preparation_error:
                        raise SystemExit(1)
                    break
                time.sleep(5)
    finally:
        client.close()


if __name__ == "__main__":
    main()
