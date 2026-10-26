"""생성 PC별 토큰 등록과 전달용 접속 키트. 토큰을 출력하지 않는다."""
import argparse
import hashlib
import json
import re
import secrets
import shutil
import ssl
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text("utf-8-sig").splitlines()
                if line.strip() and not line.lstrip().startswith("#"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--role", choices=["generation", "production", "show"], default="generation")
    parser.add_argument("--catalog-version", default="v1")
    parser.add_argument("--job-kind", action="append",
                        choices=["background_generate", "assets_generate", "bundle_verify", "ue_assemble", "ue_verify", "ue_publish"])
    parser.add_argument("--env", type=Path, default=ROOT / ".env.infra")
    parser.add_argument("--url")
    parser.add_argument("--ca", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output:
        args.output = args.output.resolve()
        if args.output.is_relative_to(ROOT):
            parser.error("토큰 키트는 저장소 밖 개인 폴더에 보관합니다")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", args.node):
        parser.error("노드 ID는 소문자·숫자·하이픈으로 지정합니다")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", args.catalog_version):
        parser.error("카탈로그 버전 형식이 잘못되었습니다")
    if any((args.url, args.ca, args.output)) and not all((args.url, args.ca, args.output)):
        parser.error("키트 발급에는 --url, --ca, --output을 함께 지정합니다")
    if args.output and args.output.exists():
        parser.error("새 출력 폴더를 지정합니다. 기존 토큰 파일을 덮어쓰지 않습니다")
    values = load_env(args.env)
    registry = json.loads(values["AGENT_KEYS_JSON"])
    allowed = {"generation": {"background_generate", "assets_generate"},
               "production": {"bundle_verify", "ue_assemble", "ue_verify"}, "show": {"ue_publish"}}[args.role]
    if args.job_kind and (not set(args.job_kind).issubset(allowed) or len(set(args.job_kind)) != len(args.job_kind)):
        parser.error("노드 역할에 맞는 중복 없는 --job-kind를 지정합니다")
    if args.node in registry and (registry[args.node]["role"] != args.role
                                 or registry[args.node]["catalog_version"] != args.catalog_version):
        parser.error("기존 다른 역할·카탈로그의 ID를 재사용할 수 없습니다")
    changed = args.node not in registry
    if changed:
        registry[args.node] = {"token": secrets.token_hex(32), "role": args.role, "catalog_version": args.catalog_version}
        if args.role == "production":
            registry[args.node]["job_kinds"] = ["ue_assemble"]
    if args.job_kind is not None:
        registry[args.node]["job_kinds"] = args.job_kind
        changed = True
    if changed:
        values["AGENT_KEYS_JSON"] = json.dumps(registry, separators=(",", ":"))
        temp = args.env.with_name(args.env.name + ".new")
        with temp.open("x", encoding="utf-8", newline="\n") as output:
            output.write("".join(f"{key}={value}\n" for key, value in values.items()))
        temp.chmod(0o600)
        temp.replace(args.env)
    if not args.output:
        print(f"Registered {args.node}: {args.role}. Recreate central services to apply; token omitted.")
        return
    args.output.mkdir(parents=True)
    shutil.copyfile(args.ca, args.output / "central-ca.crt")
    shutil.copyfile(Path(__file__).with_name("client.py"), args.output / "client.py")
    shutil.copyfile(Path(__file__).with_name("WORKFLOW.md"), args.output / "WORKFLOW.md")
    shutil.copyfile(Path(__file__).with_name("SERVER_EXCHANGE.md"), args.output / "SERVER_EXCHANGE.md")
    if args.role == "generation":
        for name in ("generation_worker.py", "runtime_delivery.py", "GENERATION_HANDLER.md", "HEAD_AUTOMATION.md"):
            shutil.copyfile(Path(__file__).with_name(name), args.output / name)
        local = {"cache": f"C:/Ranel/generation/{args.node}/cache", "working_directory": "C:/Ranel/GenerationRepo",
                 "handler": ["C:/Path/To/python.exe", "C:/Ranel/GenerationRepo/handler.py"], "timeout_seconds": 1800}
        (args.output / "generation.local.example.json").write_text(json.dumps(local, indent=2), encoding="utf-8")
    if args.role == "production":
        shutil.copyfile(Path(__file__).with_name("ue_worker.py"), args.output / "ue_worker.py")
        shutil.copyfile(Path(__file__).with_name("ue_capabilities.py"), args.output / "ue_capabilities.py")
    (args.output / "server.json").write_text(json.dumps({"url": args.url, "ca_file": "central-ca.crt"}, indent=2),
                                            encoding="utf-8")
    (args.output / "node.secret.json").write_text(
        json.dumps({"node_id": args.node, "role": args.role, "token": registry[args.node]["token"],
                    **({"job_kinds": registry[args.node]["job_kinds"]} if "job_kinds" in registry[args.node] else {})},
                   indent=2),
        encoding="utf-8")
    runtime_mode = ("verify" if registry[args.node].get("job_kinds") == ["ue_verify"] else
                    "show" if registry[args.node].get("job_kinds") == ["ue_publish"] else None)
    if runtime_mode:
        for name in ("runtime_worker.py", "runtime_delivery.py", "UE_RUNTIME_PIPELINE.md", "UE_RUNTIME_SETUP.md",
                     "Start-Runtime.ps1"):
            shutil.copyfile(Path(__file__).with_name(name), args.output / name)
        shutil.copyfile(ROOT / "packages/studio/src/studio/infra/runtime_process.py", args.output / "runtime_process.py")
        shutil.copyfile(ROOT / "apps/show_agent/LIFECYCLE.md", args.output / "LIFECYCLE.md")
        if runtime_mode == "show":
            for name in ("show_supervisor.py", "show_check.py", "show_playback.py", "UE_SHOW_RECOVERY.md"):
                shutil.copyfile(Path(__file__).with_name(name), args.output / name)
        for name in ("UE_RUNTIME_PIPELINE.md", "UE_RUNTIME_SETUP.md"):
            document = args.output / name
            document.write_text(document.read_text("utf-8").replace("../apps/show_agent/LIFECYCLE.md", "LIFECYCLE.md"),
                                encoding="utf-8")
        guide = args.output / "LIFECYCLE.md"
        guide.write_text(guide.read_text("utf-8").replace("../../infra/OPERATIONS.md",
                         "https://lab.ssafy.com/s15-final/S15P31A504/-/blob/develop/infra/OPERATIONS.md").replace(
                         "../../infra/UE_SHOW_RECOVERY.md",
                         "https://lab.ssafy.com/s15-final/S15P31A504/-/blob/develop/infra/UE_SHOW_RECOVERY.md"),
                         encoding="utf-8")
        local = {"mode": runtime_mode, "delivery": f"C:/Ranel/{runtime_mode}/delivery",
                 "cache": f"C:/Ranel/{runtime_mode}/cache"}
        if runtime_mode == "verify":
            local.update(repository="C:/Ranel/UERepo", executable="C:/Ranel/Shipping/Art.exe",
                         working_directory="C:/Ranel/Shipping", probe="C:/Ranel/verify/probe")
        else:
            local.update(runtime_pid=0, apply_timeout_seconds=120, managed_runtime=None)
        (args.output / "runtime.local.example.json").write_text(json.dumps(local, indent=2), encoding="utf-8")
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(args.ca.read_text("ascii"))).hexdigest()
    (args.output / "README.txt").write_text(
        f"노드: {args.node} ({args.role})\n중앙: {args.url}\n"
        f"허용 작업: {', '.join(registry[args.node].get('job_kinds', ['역할 기본값']))}\n"
        "Python 3.10 이상. 먼저 python client.py --check, 이후 python client.py --watch\n"
        "또는 Check-Connection.cmd / Start-Heartbeat.cmd를 실행합니다. 종료: Ctrl+C\n"
        "작업: python client.py --claim. 별도 창의 --watch를 유지하고 Codex는 별도로 실행합니다.\n"
        "진행률: python client.py --progress 50. 완료/입력 파일 명령은 WORKFLOW.md를 따릅니다.\n"
        "Codex 자동 실행은 하지 않습니다. assignment.secret.json은 임대 토큰이므로 공유하지 않습니다.\n"
        "UE 제작 키트: MR !33 모듈이 있는 저장소에서 uv run python <키트>/ue_worker.py --once\n"
        "EC2 이전 시 server.json의 url을 변경합니다. 같은 CA를 이관하면 인증서 파일은 유지합니다.\n"
        "node.secret.json은 해당 PC에만 보관하고 Git/공개 채팅에 올리지 않습니다.\n"
        f"CA SHA-256 (DER): {fingerprint}\n", encoding="utf-8")
    for filename, mode in [("Check-Connection.cmd", "check"), ("Start-Heartbeat.cmd", "watch")]:
        (args.output / filename).write_text(
            f'@echo off\r\ncd /d "%~dp0"\r\npython client.py --{mode}\r\npause\r\n', encoding="ascii")
    if runtime_mode:
        (args.output / "Start-Heartbeat.cmd").unlink(missing_ok=True)
        (args.output / "README.txt").write_text(
            "전용 실행 키트: " + args.node + "\n먼저 UE_RUNTIME_SETUP.md를 읽고 runtime.local.json을 설정하세요.\n"
            "Start-Runtime.ps1을 실행합니다. client.py --watch는 별도로 실행하지 않습니다.\n"
            "시연 프로세스 자동 실행·정지·복구의 선택적 설정은 LIFECYCLE.md를 따릅니다.\n"
            "토큰 파일은 Git·공개 채팅에 올리지 않습니다.\n", encoding="utf-8")
    shutil.make_archive(str(args.output), "zip", root_dir=args.output)
    print(json.dumps({"node": args.node, "kit": str(args.output.with_suffix(".zip")),
                      "ca_sha256": fingerprint}, ensure_ascii=False))


if __name__ == "__main__":
    main()
