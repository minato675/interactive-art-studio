"""이관용 중앙 스냅샷. 작업이 없는 동안 잠시 정지하고 DB·데이터·CA·코드를 보관한다."""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = "caddy:2.11.7-alpine@sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f"


def run(*args):
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=180).stdout.strip()


def protect_directory(path):
    """Windows의 chmod는 ACL을 제한하지 않으므로 생성 직후 명시적으로 보호한다."""
    if os.name == "nt":
        # SID는 로캘과 무관한 CSV의 두 번째 필드다.
        sid = next(csv.reader([run("whoami", "/user", "/fo", "csv", "/nh")]))[1]
        run("icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "/Q")
    else:
        path.chmod(0o700)


def row_counts(container):
    tables = run("docker", "exec", container, "psql", "-U", "ranel", "-d", "ranel", "-Atc",
                 "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;").splitlines()
    counts = {}
    for table in tables:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
            raise RuntimeError("Unexpected table identifier")
        counts[table] = int(run("docker", "exec", container, "psql", "-U", "ranel", "-d", "ranel", "-Atc",
                                f'SELECT count(*) FROM "{table}";'))
    return counts


def archive_hashes(path):
    hashes = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            if member.isfile():
                with archive.extractfile(member) as stream:
                    hashes[member.name.removeprefix("./")] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--leave-stopped", action="store_true", help="실제 이전 시 기존 중앙을 재개하지 않음")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("새 백업 폴더를 지정합니다")
    if output.is_relative_to(ROOT):
        parser.error("비밀 설정이 포함되므로 저장소 밖의 개인 폴더를 지정합니다")
    compose = ["docker", "compose", "--env-file", ".env.infra", "--env-file", ".env.https",
               "-f", "infra/central/compose.yaml", "-f", "infra/central/compose.https.yaml",
               "-f", "infra/central/compose.laptop.yaml"]
    containers = {name: run(*compose, "ps", "-a", "-q", name)
                  for name in ["postgres", "central", "gateway"]}
    if not all(containers.values()):
        parser.error("중앙·PostgreSQL·HTTPS 컨테이너를 먼저 생성해야 합니다")
    running = set(run(*compose, "ps", "--services", "--status", "running").splitlines())
    active = int(run("docker", "exec", containers["postgres"], "psql", "-U", "ranel", "-d", "ranel", "-Atc",
                     "SELECT count(*) FROM infra_job WHERE status='running';"))
    if active:
        parser.error("실행 중인 작업이 있습니다. 작업 완료/임대 회수를 확인하고 다시 백업합니다")
    output.mkdir(parents=True, mode=0o700)
    protect_directory(output)
    stopped = [name for name in ["gateway", "central", "dispatcher", "worker"] if name in running]
    success = False
    try:
        if stopped:
            run(*compose, "stop", "--timeout", "30", *stopped)
        # 정지 전후에 시작된 작업이 있으면 미완료 상태를 이관하지 않는다.
        active = int(run("docker", "exec", containers["postgres"], "psql", "-U", "ranel", "-d", "ranel", "-Atc",
                         "SELECT count(*) FROM infra_job WHERE status='running';"))
        if active:
            raise RuntimeError("정지 중 시작된 작업이 있습니다. 완료/회수 후 다시 백업합니다")
        metadata = {"created_at": datetime.now(UTC).isoformat(),
                    "revision": run("git", "rev-parse", "HEAD"),
                    "dirty": bool(run("git", "status", "--porcelain")),
                    "image_id": run("docker", "inspect", containers["central"], "--format", "{{.Image}}"),
                    "rows": row_counts(containers["postgres"])}
        run("docker", "exec", containers["postgres"], "pg_dump", "-U", "ranel", "-d", "ranel",
            "-Fc", "-f", "/tmp/ranel-transfer.dump")
        run("docker", "cp", containers["postgres"] + ":/tmp/ranel-transfer.dump", str(output / "postgres.dump"))
        for service, filename in [("central", "central-data.tar.gz"), ("gateway", "gateway-data.tar.gz")]:
            mounts = json.loads(run("docker", "inspect", containers[service], "--format", "{{json .Mounts}}"))
            volume = next(m["Name"] for m in mounts if m["Destination"] == "/data" and m["Type"] == "volume")
            run("docker", "run", "--rm", "-v", volume + ":/source:ro", "-v", str(output) + ":/backup",
                HELPER, "tar", "czf", "/backup/" + filename, "-C", "/source", ".")
        for filename in [".env.infra", ".env.https", *([".env.head"] if (ROOT / ".env.head").exists() else [])]:
            shutil.copyfile(ROOT / filename, output / filename)
            (output / filename).chmod(0o600)
        files = sorted(set(run("git", "ls-files", "-z", "--cached").split("\0")) - {""})
        with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
            for filename in files:
                path = ROOT / filename
                if path.is_file() and not path.is_symlink() and not any(p.startswith(".env") for p in path.parts):
                    archive.add(path, arcname=filename, recursive=False)
        shutil.copyfile(ROOT / "infra/RESTORE.txt", output / "RESTORE.txt")
        metadata["volume_files"] = {name: archive_hashes(output / name)
                                    for name in ("central-data.tar.gz", "gateway-data.tar.gz")}
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        hashes = {}
        for path in output.iterdir():
            if path.is_file():
                with path.open("rb") as stream:
                    hashes[path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
        (output / "sha256.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
        success = True
        print(json.dumps({"backup": str(output), "leave_stopped": args.leave_stopped,
                          "files": sorted(hashes), "secrets": "server-only"}, ensure_ascii=False))
    finally:
        if stopped and (not args.leave_stopped or not success):
            run(*compose, "up", "-d", "--no-build", "--wait", "--wait-timeout", "90", *stopped)


if __name__ == "__main__":
    main()
