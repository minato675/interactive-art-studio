"""UE MR 체크아웃에서 실행하는 키트용 조립 실행기. 중앙·DB·Celery를 import하지 않는다."""
import argparse
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

from client import Client, atomic_json


def validate_head_template(job, installed):
    expected = job["payload"].get("_head", {}).get("template")
    if expected and not any(t["template"] == expected["template"] and t["fingerprint"] == expected["fingerprint"]
                            for t in installed["templates"]):
        raise ValueError("head_template_fingerprint_mismatch")
    target = job["payload"]["assembly"].get("target_node")
    if target and target != job["node_id"]:
        raise ValueError("assembly_target_mismatch")


def run_one(client, cache):
    from ue_capabilities import report

    installed = report()
    client.call("/infra/agent/ue-capabilities", installed, post=True)
    assignment = client.claim()
    if assignment.get("event") == "no_ready_job":
        return assignment
    job = client.current()
    if job["kind"] != "ue_assemble":
        raise ValueError("UE 실행기는 ue_assemble 작업만 처리합니다")
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
            except Exception:  # noqa: BLE001 — 소유권이 불확실하면 완료 확정 금지
                lost.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    cache.mkdir(parents=True, exist_ok=True)
    try:
        validate_head_template(job, installed)
        from contracts.assets import ArtworkAssets
        from contracts.spec import Spec
        from studio.assembly.ue_assemble import assemble, select_template

        with tempfile.TemporaryDirectory(dir=cache, prefix=".ue-job-") as temporary:
            root = Path(temporary)
            downloaded, assembled, output = root / "download", root / "input", root / "output"
            assembled.mkdir()
            output.mkdir()
            client.inputs(downloaded)
            request = job["payload"]["assembly"]
            for item in request["files"]:
                source = (downloaded / item["source_job_id"] / item["source_name"]).resolve()
                target = (assembled / item["name"]).resolve()
                if not source.is_relative_to(downloaded.resolve()) or not target.is_relative_to(assembled.resolve()):
                    raise ValueError("조립 입력 경로 이탈")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            spec, assets = Spec.model_validate(request["spec"]), ArtworkAssets.model_validate(request["assets"])
            choice = select_template(spec)
            expected = job["payload"].get("_head", {}).get("template")
            if expected and choice.template != expected["template"]:
                raise ValueError("head_template_selection_mismatch")
            result = assemble(spec, assets, assembled, choice)
            if result.errors or result.scene is None:
                atomic_json(client.folder / "last-assembly-error.json", {"job_id": job["id"], "errors": result.errors})
                raise ValueError("ue_assembly_rejected")
            atomic_json(output / "scene.json", result.scene)
            atomic_json(output / "assembly-report.json", {"template": result.template, "warnings": result.warnings,
                "substitutions": result.substitutions, "errors": [], "runtime_verified": False, "publish_approved": False})
            for name, source in result.files.items():
                target = (output / name).resolve()
                if not target.is_relative_to(output.resolve()) or name.split("/")[0].casefold() in {
                        "scene.json", "assembly-report.json"}:
                    raise ValueError("조립 출력 경로 충돌")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            if lost.is_set():
                raise RuntimeError("lease_lost")
            return client.complete(output)
    except Exception as exc:  # noqa: BLE001 — 사용자 프롬프트·키·경로를 로그에 출력하지 않는다.
        if not lost.is_set():
            try:
                client.complete(error="ue_assembly_" + type(exc).__name__)
            except Exception:  # noqa: BLE001 — 만료/통신 단절은 기존 임대 회수에 맡긴다.
                print(json.dumps({"event": "ue_completion_unconfirmed", "job_id": job["id"]}), flush=True)
        return {"event": "ue_assembly_failed", "error": type(exc).__name__, "job_id": job["id"]}
    finally:
        stop.set()
        thread.join(timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--cache", type=Path, default=Path("runtime/ue-exchange"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--report-capabilities", action="store_true", help="설치 리소스 보고 후 종료; 작업은 임대하지 않음")
    args = parser.parse_args()
    client = Client(args.folder)
    if client.node["role"] != "production":
        raise SystemExit("production 역할의 UE 전용 키트가 필요합니다")
    from studio.infra.agent import singleton

    with singleton(args.folder / "ue-worker.lock"):
        print(json.dumps(client.check(), ensure_ascii=False), flush=True)
        from ue_capabilities import report

        print(json.dumps({"event": "ue_capabilities_reported",
                          **client.call("/infra/agent/ue-capabilities", report(), post=True)}), flush=True)
        if args.report_capabilities:
            return
        while True:
            try:
                result = run_one(client, args.cache)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except (OSError, ValueError, KeyError) as exc:
                print(json.dumps({"event": "ue_worker_retry", "error": type(exc).__name__}), flush=True)
                if args.once:
                    raise
            if args.once:
                break
            time.sleep(3)


if __name__ == "__main__":
    main()
