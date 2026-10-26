"""계약 JSON 기본 읽기와 기존 명시 경로 인터페이스를 보존한다."""
import json
from pathlib import Path

import pytest
from infra.check_packaged_scene import check_scenes
from interactive_controller.orchestration.compiler import load_presets
from interactive_controller.validation.catalog import load_catalog
from interactive_controller.validation.scene import load_contract


def test_default_resources_compile_and_validate_outside_repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert check_scenes() == 4


@pytest.mark.parametrize("path_type", [str, Path])
def test_explicit_resource_paths_take_precedence(tmp_path, path_type):
    presets = tmp_path / "presets.json"
    presets.write_text(json.dumps({"families": {"custom": {"title": "사용자 프리셋"}}}), encoding="utf-8")
    assert load_presets(path_type(presets)) == {"custom": {"title": "사용자 프리셋"}}

    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"schemaVersion": 1, "catalogId": "custom", "provenance": "provisional",
                                   "patterns": [], "objects": {"Custom": {"role": "test"}}}), encoding="utf-8")
    assert load_catalog(path_type(catalog)).catalog_id == "custom"

    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"custom": "사용자 계약"}), encoding="utf-8")
    assert load_contract(path_type(contract)) == {"custom": "사용자 계약"}


@pytest.mark.parametrize("loader", [load_presets, load_catalog, load_contract])
def test_missing_explicit_resource_does_not_fall_back_to_default(tmp_path, loader):
    with pytest.raises(FileNotFoundError):
        loader(tmp_path / "missing.json")
