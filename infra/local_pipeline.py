"""검사 후 노트북 중앙 이미지 배포. EC2/공유 GitLab Runner 없이 실행한다."""
import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from studio.infra.agent import singleton

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", ".env.infra", "--env-file", ".env.https",
           "-f", "infra/central/compose.yaml", "-f", "infra/central/compose.https.yaml",
           "-f", "infra/central/compose.laptop.yaml"]


def run(args, *, env=None, output=None, timeout=900):
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, check=False,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if output:
        output.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed (exit {result.returncode}); see local validation log")
    return result.stdout.strip()


def active_jobs():
    container = run([*COMPOSE, "ps", "-q", "postgres"])
    return int(run(["docker", "exec", container, "psql", "-U", "ranel", "-d", "ranel", "-Atc",
                    "SELECT count(*) FROM infra_job WHERE status='running';"]))


def save_image_env(path, image):
    lines = path.read_text("utf-8-sig").splitlines()
    lines = [line for line in lines if not line.startswith("APP_IMAGE=")]
    temporary = path.with_name(path.name + ".deploy-new")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join([*lines, f"APP_IMAGE={image}", ""]))
    temporary.replace(path)


def source_version(runner=run):
    revision = runner(["git", "rev-parse", "HEAD"])
    branch = runner(["git", "branch", "--show-current"])
    dirty = bool(runner(["git", "status", "--porcelain", "--untracked-files=all"]))
    return {"revision": revision, "branch": branch, "dirty": dirty}


def build_command(candidate, version, created):
    return ["docker", "build", "--target", "runtime", "-t", candidate,
            "--build-arg", "VCS_REF=" + version["revision"], "--build-arg", "BUILD_DATE=" + created,
            "-f", "infra/central/Dockerfile", "."]


def deploy(candidate, folder, label, runner=run):
    if active_jobs():
        raise RuntimeError("진행 중 작업이 있어 배포하지 않습니다. 완료 후 다시 실행하세요")
    container = runner([*COMPOSE, "ps", "-q", "central"])
    if not container:
        raise RuntimeError("기존 중앙 서비스가 실행 중이어야 합니다")
    previous_id = runner(["docker", "inspect", container, "--format", "{{.Image}}"])
    previous_tag = "ranel-central:rollback-" + label
    runner(["docker", "image", "tag", previous_id, previous_tag])
    state = Path(os.environ["USERPROFILE"]) / "RanelMaster" if os.name == "nt" else ROOT.parent / "ranel-private"
    backup = (state / "backups" / label).resolve()
    runner([sys.executable, "infra/backup_central.py", "--output", str(backup), "--leave-stopped"],
           output=folder / "backup.log")
    environment = {**os.environ, "APP_IMAGE": candidate}
    try:
        runner([*COMPOSE, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120"],
               env=environment, output=folder / "deploy.log")
        runner([sys.executable, "infra/central/smoke.py"], output=folder / "smoke.log")
        # 공개 HTTPS와 인증 경로는 앱 이미지 배포와 별도로 유지되는 프록시를 통과해야 한다.
        runner([sys.executable, "infra/check_https.py"], output=folder / "https.log")
        runner([sys.executable, "infra/central_cli.py", "status"], output=folder / "status.json")
        save_image_env(ROOT / ".env.infra", candidate)
    except Exception:
        runner([*COMPOSE, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120"],
               env={**os.environ, "APP_IMAGE": previous_tag}, output=folder / "rollback.log")
        runner([sys.executable, "infra/central/smoke.py"], output=folder / "rollback-smoke.log")
        raise
    return {"image": candidate, "previous_image": previous_id, "backup": str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", action="store_true")
    args = parser.parse_args()
    version = source_version()
    if args.deploy and version["dirty"]:
        parser.error("변경 사항을 커밋한 뒤 배포하세요. 실행 이미지의 Git 버전을 확정해야 합니다")
    label = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    folder = ROOT / "runtime/validation/laptop-ci" / label
    folder.mkdir(parents=True)
    candidate = "ranel-central:laptop-" + version["revision"][:12] + "-" + label.lower()
    stages = [
        ("lint", ["uv", "run", "ruff", "check", "."]),
        ("lock", ["uv", "lock", "--check"]),
        ("compose", [*COMPOSE, "config", "--quiet"]),
        ("dependencies", [*COMPOSE, "up", "-d", "--wait", "postgres", "rabbitmq"]),
        ("linux-tests", [*COMPOSE, "--profile", "test", "run", "--build", "--rm", "-v", f"{folder}:/reports", "test",
                         "uv", "run", "--no-sync", "pytest", "-q", "--junitxml=/reports/junit.xml"]),
        ("async-integration", [*COMPOSE, "--profile", "test", "run", "--rm", "test", "uv", "run", "--no-sync",
                               "python", "infra/check_pipeline.py"]),
        ("runtime-image", build_command(candidate, version, datetime.now(UTC).isoformat())),
    ]
    report = {"validation_dir": str(folder), "source": version, "stages": [], "deployed": False}
    try:
        with singleton(ROOT / "runtime/local-pipeline.lock"):
            for name, command in stages:
                print(json.dumps({"stage": name, "status": "running"}), flush=True)
                run(command, output=folder / (name + ".log"))
                report["stages"].append(name)
                print(json.dumps({"stage": name, "status": "passed"}), flush=True)
            if args.deploy:
                if source_version() != version:
                    raise RuntimeError("검사 중 소스가 변경되어 배포하지 않습니다. 다시 검사하세요")
                print(json.dumps({"stage": "deploy", "status": "running"}), flush=True)
                report.update(deploy(candidate, folder, label))
                report["deployed"] = True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
        raise SystemExit(f"파이프라인 중단: {exc}") from None
    finally:
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
