"""백업 변조·경로 이탈은 복원 자원을 만들기 전에 거부한다."""
import hashlib
import json

import pytest
from infra.verify_backup import verify_checksums


def write_backup(path):
    files = {"postgres.dump": b"dump", "central-data.tar.gz": b"data", "gateway-data.tar.gz": b"ca",
             "metadata.json": b'{"rows":{"infra_job":2}}'}
    for name, data in files.items():
        (path / name).write_bytes(data)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    (path / "sha256.json").write_text(json.dumps(hashes))
    return hashes


def test_valid_backup_preserves_original_counts(tmp_path):
    write_backup(tmp_path)
    assert verify_checksums(tmp_path)["rows"] == {"infra_job": 2}


def test_corrupt_database_dump_is_rejected(tmp_path):
    write_backup(tmp_path)
    (tmp_path / "postgres.dump").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="checksum"):
        verify_checksums(tmp_path)


def test_manifest_cannot_read_outside_backup_directory(tmp_path):
    hashes = write_backup(tmp_path)
    hashes["../outside"] = "0" * 64
    (tmp_path / "sha256.json").write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="Invalid"):
        verify_checksums(tmp_path)
