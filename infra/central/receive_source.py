"""CI 소스 압축을 검증해 새 커밋 폴더에만 푼다. 기존 릴리스·설정은 덮어쓰지 않는다."""
import argparse
import hashlib
import json
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

REQUIRED = {"pyproject.toml", "uv.lock", "infra/central/Dockerfile", "infra/central/compose.yaml",
            "infra/central/deploy.sh", "infra/central/smoke.py", "infra/central/verify_gateway.py",
            "infra/check_packaged_scene.py"}


def validate_members(archive):
    members = archive.getmembers()
    if len(members) > 20000 or sum(m.size for m in members) > 512 * 1024 * 1024:
        raise ValueError("Source archive exceeds limits")
    names = set()
    for member in members:
        path = PurePosixPath(member.name)
        if (path.is_absolute() or ".." in path.parts or "\\" in member.name
                or not (member.isfile() or member.isdir())):
            raise ValueError("Unsafe source member")
        if any(p == ".git" or p.lower().startswith(".env") for p in path.parts):
            raise ValueError("Secret settings or Git metadata in source archive")
        if path.suffix.lower() in (".pem", ".key") or str(path) == ".source-receipt.json":
            raise ValueError("Private key or reserved member in source archive")
        if member.isfile():
            if str(path) in names:
                raise ValueError("Duplicate source member")
            names.add(str(path))
    if not REQUIRED <= names:
        raise ValueError("Required source files missing")
    return members


def receive(revision, archive_path, expected_hash, root):
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("Invalid revision or checksum")
    archive_path, root = archive_path.resolve(), root.resolve()
    if (root / "RETIRED").exists():
        raise ValueError("Deployment environment is retired")
    with archive_path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_hash:
            raise ValueError("Source checksum mismatch")
    releases = root / "releases"
    releases.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = releases / revision
    receipt = {"revision": revision, "sha256": expected_hash}
    if destination.exists():
        if destination.is_symlink() or json.loads((destination / ".source-receipt.json").read_text()) != receipt:
            raise ValueError("Existing release differs; refusing overwrite")
        return destination
    with tarfile.open(archive_path, "r:gz") as archive:
        members = validate_members(archive)
        with tempfile.TemporaryDirectory(prefix=".receive-", dir=releases) as temporary:
            staging = Path(temporary)
            archive.extractall(staging, members=members, filter="data")
            (staging / ".source-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            staging.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision")
    parser.add_argument("archive", type=Path)
    parser.add_argument("sha256")
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    destination = receive(args.revision, args.archive, args.sha256, args.root)
    print(json.dumps({"received": args.revision, "path": str(destination)}))


if __name__ == "__main__":
    main()
