"""기존 완료 패키지를 보존하고 선택 재생 Agent의 새 고정 버전을 만든다."""

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def build(source, output, version):
    if output.exists():
        raise ValueError("output_already_exists")
    original = json.loads((source / "release.json").read_text(encoding="utf-8"))
    for package in original["packages"]:
        if digest(source / (package["name"] + ".zip")) != package["sha256"]:
            raise ValueError("source_archive_changed")
    output.mkdir(parents=True)
    original_support = source / "earth-support.zip"
    support = output / "earth-support.zip"
    with zipfile.ZipFile(original_support) as old:
        info = json.loads(old.read("release-info.json"))
        info.update(version=version, operator_selection=True, physical_pc2_test=False)
        replacement = {
            "agent/agent.py": Path(__file__).with_name("agent.py").read_bytes(),
            "release-info.json": encode(info),
        }
        manifest = json.loads(old.read("file-manifest.json"))
        for entry in manifest:
            if entry["path"] in replacement:
                raw = replacement[entry["path"]]
                entry.update(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
        replacement["file-manifest.json"] = encode(manifest)
        with zipfile.ZipFile(support, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as new:
            for entry in old.infolist():
                if entry.filename in replacement:
                    new.writestr(entry.filename, replacement[entry.filename])
                else:
                    with old.open(entry) as src, new.open(entry.filename, "w") as dest:
                        shutil.copyfileobj(src, dest, 1024 * 1024)
    with zipfile.ZipFile(support) as archive:
        unpacked = sum(x.file_size for x in archive.infolist())
    runtime = next(p for p in original["packages"] if p["name"] == "earth-runtime")
    release = {
        "version": version,
        "packages": [
            runtime,
            {
                "name": "earth-support",
                "sha256": digest(support),
                "size": support.stat().st_size,
                "unpacked_size": unpacked,
            },
        ],
    }
    (output / "release.json").write_bytes(encode(release))
    kit = output / "PC2-OneClick"
    shutil.copytree(source / "PC2-OneClick", kit)
    (kit / "install-manifest.json").write_bytes(encode(release))
    with (kit / "README.txt").open("a", encoding="utf-8") as stream:
        stream.write(
            "\n이번 버전은 PySide 아카이브 작품 탭의 선택 재생/정지를 지원합니다.\n"
            "기존 설치 ZIP 대신 이번 ZIP을 사용하세요. 최초 설치 후 PC2는 약 3초마다 요청을 확인합니다.\n"
            "현재 버전은 실행 패키지가 고정되어 있으며 향후 실행기 자동 업데이트는 별도 기능입니다.\n"
        )
    installer = output / "EARTH-PC2-OneClick-v2.zip"
    with zipfile.ZipFile(installer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        for path in sorted(kit.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(kit).as_posix())
    record = {
        "release": release,
        "installer": str(installer),
        "installer_sha256": digest(installer),
        "runtime_archive_unchanged": True,
        "source": str(source),
        "actual_pc2_test": False,
    }
    (output / "delivery.json").write_bytes(encode(record))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default="earth-native-20261025-v2")
    args = parser.parse_args()
    if not args.version or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in args.version):
        parser.error("invalid_version")
    result = build(args.source.resolve(), args.output.resolve(), args.version)
    print(
        json.dumps(
            {
                "version": result["release"]["version"],
                "installer": result["installer"],
                "runtime_archive_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
