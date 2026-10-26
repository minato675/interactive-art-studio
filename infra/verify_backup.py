"""서버 백업을 새 격리 컨테이너·볼륨에 복원해 행 수와 파일 바이트를 확인한다."""
import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path

from infra.backup_central import HELPER, row_counts
from infra.master_node import run, save_report


def verify_checksums(backup):
    hashes = json.loads((backup / "sha256.json").read_text("utf-8"))
    required = {"postgres.dump", "central-data.tar.gz", "gateway-data.tar.gz", "metadata.json"}
    if not required <= hashes.keys():
        raise ValueError("Backup manifest is incomplete")
    for name, expected in hashes.items():
        if Path(name).name != name or name in (".", "..") or not (backup / name).is_file():
            raise ValueError("Invalid backup member")
        if (backup / name).is_symlink():
            raise ValueError("Backup member must not be a symlink")
        with (backup / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("Backup checksum mismatch")
    return json.loads((backup / "metadata.json").read_text("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    backup = args.backup.resolve()
    metadata = verify_checksums(backup)  # Docker 자원을 만들기 전에 검사한다.
    prefix = "ranel-restore-check-" + uuid.uuid4().hex[:12]
    volumes, container_created = [], False
    report = {"backup": str(backup), "scope": "isolated restore; no production volumes", "ok": False}
    try:
        run(["docker", "run", "-d", "--name", prefix, "--network", "none",
             "--label", "ranel.restore-check=" + prefix, "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
             "postgres:16-bookworm"])
        container_created = True
        deadline = time.monotonic() + 90
        while True:
            try:
                run(["docker", "exec", prefix, "pg_isready", "-U", "postgres"], timeout=5)
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Restore database startup timeout") from None
                time.sleep(1)
        run(["docker", "exec", prefix, "psql", "-U", "postgres", "-c", "CREATE USER ranel SUPERUSER;"])
        run(["docker", "exec", prefix, "createdb", "-U", "postgres", "-O", "ranel", "ranel"])
        run(["docker", "cp", str(backup / "postgres.dump"), prefix + ":/tmp/restore.dump"])
        run(["docker", "exec", prefix, "pg_restore", "--exit-on-error", "--no-owner", "--no-acl",
             "-U", "ranel", "-d", "ranel", "/tmp/restore.dump"])
        restored_rows = row_counts(prefix)
        if restored_rows != metadata["rows"]:
            raise RuntimeError("Restored table counts differ")
        restored_files = {}
        for index, filename in enumerate(("central-data.tar.gz", "gateway-data.tar.gz")):
            volume = prefix + "-files-" + str(index)
            run(["docker", "volume", "create", "--label", "ranel.restore-check=" + prefix, volume])
            volumes.append(volume)
            run(["docker", "run", "--rm", "--network", "none", "-v", volume + ":/target",
                 "-v", str(backup) + ":/backup:ro", HELPER,
                 "sh", "-ec", "test -z \"$(ls -A /target)\"; tar xzf /backup/" + filename + " -C /target"])
            output = run(["docker", "run", "--rm", "--network", "none", "-v", volume + ":/target:ro", HELPER,
                          "sh", "-ec", "cd /target; find . -type f -exec sha256sum '{}' +"])
            actual = {}
            for line in output.splitlines():
                digest, name = line.split("  ", 1)
                actual[name.removeprefix("./")] = digest
            if actual != metadata["volume_files"][filename]:
                raise RuntimeError("Restored file hashes differ")
            restored_files[filename] = len(actual)
        report.update(ok=True, rows=restored_rows, files=restored_files)
    finally:
        # 생성한 고유 이름만 정리하며 운영 컨테이너·볼륨을 열거/삭제하지 않는다.
        if container_created:
            run(["docker", "rm", "-f", "-v", prefix])
        for volume in volumes:
            run(["docker", "volume", "rm", volume])
        save_report("restore-check.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
