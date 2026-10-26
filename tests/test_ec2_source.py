"""원격 소스 수신이 설정·기존 릴리스·경로 경계를 보존하는지 검사한다."""
import hashlib
import io
import tarfile

import pytest
from infra.central.receive_source import REQUIRED, receive


def archive_at(path, extra=None):
    with tarfile.open(path, "w:gz") as archive:
        for name in sorted(REQUIRED):
            body = name.encode()
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
        if extra:
            archive.addfile(extra, io.BytesIO(b"x") if extra.isfile() else None)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_receive_is_idempotent_and_keeps_server_secrets(tmp_path):
    root = tmp_path / "server"
    root.mkdir()
    (root / ".env.infra").write_text("server-only")
    archive = tmp_path / "source.tar.gz"
    digest = archive_at(archive)
    release = receive("a" * 40, archive, digest, root)
    assert receive("a" * 40, archive, digest, root) == release
    assert (root / ".env.infra").read_text() == "server-only"
    assert all((release / name).is_file() for name in REQUIRED)
    other = tmp_path / "other.tar.gz"
    info = tarfile.TarInfo("extra.py")
    info.size = 1
    other_digest = archive_at(other, info)
    with pytest.raises(ValueError, match="refusing overwrite"):
        receive("a" * 40, other, other_digest, root)


@pytest.mark.parametrize("name", ["../../outside", "/absolute", ".env.infra", "id.pem", "nested/private.key", ".git/config"])
def test_rejects_unsafe_or_secret_paths(tmp_path, name):
    path = tmp_path / "source.tar.gz"
    extra = tarfile.TarInfo(name)
    extra.size = 1
    digest = archive_at(path, extra)
    with pytest.raises(ValueError):
        receive("b" * 40, path, digest, tmp_path / "server")
    assert not (tmp_path / "server/releases" / ("b" * 40)).exists()


def test_rejects_links_and_bad_checksum(tmp_path):
    archive = tmp_path / "source.tar.gz"
    link = tarfile.TarInfo("outside-link")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc"
    digest = archive_at(archive, link)
    with pytest.raises(ValueError, match="checksum"):
        receive("c" * 40, archive, "0" * 64, tmp_path / "server")
    with pytest.raises(ValueError, match="Unsafe"):
        receive("c" * 40, archive, digest, tmp_path / "server")
    assert not (tmp_path / "server/releases" / ("c" * 40)).exists()


def test_rejects_missing_build_files(tmp_path):
    path = tmp_path / "source.tar.gz"
    with tarfile.open(path, "w:gz"):
        pass
    with pytest.raises(ValueError, match="missing"):
        receive("d" * 40, path, hashlib.sha256(path.read_bytes()).hexdigest(), tmp_path / "server")


def test_retired_environment_cannot_be_recreated_by_old_pipeline(tmp_path):
    root = tmp_path / "retired"
    root.mkdir()
    (root / "RETIRED").write_text("User requested shutdown")
    path = tmp_path / "source.tar.gz"
    digest = archive_at(path)
    with pytest.raises(ValueError, match="retired"):
        receive("e" * 40, path, digest, root)
    assert not (root / "releases").exists()
