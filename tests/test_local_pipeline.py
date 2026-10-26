"""배포 게이트/실패 복구. 실제 운영 컨테이너를 고의로 실패시키지 않는다."""
from pathlib import Path

import pytest
from infra import local_pipeline


def test_source_version_records_untracked_changes():
    def runner(command):
        return {"HEAD": "a" * 40, "--show-current": "feature", "--untracked-files=all": "?? new.py"}[command[-1]]

    assert local_pipeline.source_version(runner) == {"revision": "a" * 40, "branch": "feature", "dirty": True}


def test_runtime_build_stamps_application_revision():
    command = local_pipeline.build_command("candidate", {"revision": "a" * 40}, "2026-10-21T00:00:00Z")
    assert "VCS_REF=" + "a" * 40 in command
    assert "BUILD_DATE=2026-10-21T00:00:00Z" in command


def test_active_job_blocks_deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(local_pipeline, "active_jobs", lambda: 1)
    called = []
    with pytest.raises(RuntimeError, match="진행 중"):
        local_pipeline.deploy("candidate", tmp_path, "test", lambda *a, **k: called.append(a))
    assert called == []


def test_failed_deployment_restores_previous_image_and_preserves_env(tmp_path, monkeypatch):
    monkeypatch.setattr(local_pipeline, "active_jobs", lambda: 0)
    monkeypatch.setattr(local_pipeline, "ROOT", tmp_path)
    env = tmp_path / ".env.infra"
    env.write_text("OPERATOR_TOKEN=test-only\nAPP_IMAGE=previous\n")
    before = env.read_bytes()
    used_images = []

    def runner(command, **kwargs):
        if command[-3:] == ["ps", "-q", "central"]:
            return "container"
        if command[1:2] == ["inspect"]:
            return "sha256:previous"
        if kwargs.get("env"):
            used_images.append(kwargs["env"]["APP_IMAGE"])
        if command[-1:] == ["infra/central/smoke.py"] and len(used_images) == 1:
            raise RuntimeError("candidate smoke failed")
        return ""

    with pytest.raises(RuntimeError, match="candidate smoke"):
        local_pipeline.deploy("candidate", tmp_path, "test", runner)
    assert used_images == ["candidate", "ranel-central:rollback-test"]
    assert env.read_bytes() == before


def test_successful_deployment_persists_image_without_replacing_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(local_pipeline, "active_jobs", lambda: 0)
    monkeypatch.setattr(local_pipeline, "ROOT", tmp_path)
    env = tmp_path / ".env.infra"
    env.write_text("OPERATOR_TOKEN=test-only\nAPP_IMAGE=previous\n")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return "container-or-image"

    result = local_pipeline.deploy("candidate", tmp_path, "test", runner)
    assert result["image"] == "candidate"
    assert env.read_text() == "OPERATOR_TOKEN=test-only\nAPP_IMAGE=candidate\n"
    assert any(Path(command[-1]).name == "check_https.py" for command in calls)
