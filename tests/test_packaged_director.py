"""설치된 wheel 기록을 기준으로 감독 지침의 누락과 소스 체크아웃 혼입을 검출한다."""
import base64
import hashlib
from importlib.metadata import FileHash, PackagePath
from pathlib import Path
from types import SimpleNamespace

import pytest
from infra import check_packaged_scene as checks


@pytest.fixture
def installed_director(tmp_path, monkeypatch):
    installed = tmp_path / "site-packages"
    source = Path(checks.director.__file__).parents[1]
    records = []
    for name in checks.DIRECTOR_PACKAGE_FILES:
        raw = (source / name.removeprefix("studio/")).read_bytes()
        path = installed / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        record = PackagePath(name)
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
        record.hash = FileHash("sha256=" + digest)
        records.append(record)
    package = SimpleNamespace(files=records, locate_file=lambda name: installed / name)
    monkeypatch.setattr(checks, "distribution", lambda name: package)
    monkeypatch.setattr(checks.director, "__file__", str(installed / checks.DIRECTOR_PACKAGE_FILES[0]))
    monkeypatch.setattr(checks.director, "PROMPT_PATH", installed / checks.DIRECTOR_PACKAGE_FILES[-1])
    return installed, package


def test_installed_director_reads_recorded_prompt_for_every_phase(installed_director, tmp_path, monkeypatch):
    installed, _ = installed_director
    original = checks.director.instructions
    phases = []

    def instructions(phase):
        phases.append(phase)
        return original(phase)

    monkeypatch.setattr(checks.director, "instructions", instructions)
    monkeypatch.chdir(tmp_path)
    assert checks.check_director(installed) == checks.director.skill_metadata()
    assert phases == ["background", "assets", "review"]


def test_director_missing_from_wheel_record_cannot_use_existing_file(installed_director):
    installed, package = installed_director
    package.files = package.files[:-1]
    assert checks.director.PROMPT_PATH.is_file()
    with pytest.raises(RuntimeError, match="Missing director package data"):
        checks.check_director(installed)


def test_missing_installed_prompt_stops_package_check(installed_director):
    installed, _ = installed_director
    checks.director.PROMPT_PATH.unlink()
    with pytest.raises(RuntimeError, match="Expected installed director package file"):
        checks.check_director(installed)


def test_modified_prompt_cannot_self_certify_with_recomputed_metadata(installed_director):
    installed, _ = installed_director
    prompt = checks.director.PROMPT_PATH
    before = checks.director.skill_metadata()
    prompt.write_bytes(prompt.read_bytes() + b"\nmodified outside wheel\n")
    assert checks.director.skill_metadata() != before
    with pytest.raises(RuntimeError, match="differs from wheel RECORD"):
        checks.check_director(installed)


def test_source_checkout_module_cannot_mask_installed_package(installed_director, tmp_path, monkeypatch):
    installed, _ = installed_director
    monkeypatch.setattr(checks.director, "__file__", str(tmp_path / "checkout" / "director.py"))
    with pytest.raises(RuntimeError, match="non-editable installed module"):
        checks.check_director(installed)


def test_external_prompt_is_rejected_even_when_content_matches(installed_director, tmp_path, monkeypatch):
    installed, _ = installed_director
    external = tmp_path / "checkout-prompt.md"
    external.write_bytes(checks.director.PROMPT_PATH.read_bytes())
    monkeypatch.setattr(checks.director, "PROMPT_PATH", external)
    with pytest.raises(RuntimeError, match="must come from the installed wheel"):
        checks.check_director(installed)


def test_phase_must_load_installed_prompt(installed_director, monkeypatch):
    installed, _ = installed_director
    monkeypatch.setattr(checks.director, "instructions", lambda phase: "unrelated instructions")
    with pytest.raises(RuntimeError, match="not loaded for background"):
        checks.check_director(installed)
