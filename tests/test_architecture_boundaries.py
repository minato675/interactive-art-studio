"""후속 개발의 렌더러 선택·검수와 실제 센서 계약을 확인한다."""
import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from interactive_controller.orchestration.compiler import DEFAULT_SOURCES
from td.fake_hands import packet

from central.config import Settings
from central.main import create_app
from contracts.schemas import Element, Family, InputSource, Intent, Mapping
from studio.assembly import ue_scene

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("via_draft", [False, True])
def test_default_both_keeps_creation_confirmation_and_two_renderer_review(tmp_path, monkeypatch, via_draft):
    monkeypatch.delenv("RENDERER", raising=False)
    # MR !23에서 만든 옛 환경 파일이 남아 있어도 후속 개발 API를 막지 않는다.
    monkeypatch.setenv("ENABLE_LOCAL_PREVIEW", "false")
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
                        operator_token="test-operator")
    assert settings.renderer == "both"
    headers = {"X-Operator-Token": "test-operator"}
    with TestClient(create_app(settings)) as client:
        body = {"theme": "바다", "families": ["fluid"], "reveal": "none",
                "review_assets": True, "review_final": True}
        for endpoint in ("/api/artworks", "/api/drafts"):
            assert client.post(endpoint, json=body).status_code == 401
        if via_draft:
            draft = client.post("/api/drafts", json=body, headers=headers)
            assert draft.status_code == 200 and draft.json()["status"] == "awaiting"
            artwork_id = draft.json()["id"]
            response = client.post(f"/api/artworks/{artwork_id}/confirm", json={}, headers=headers)
        else:
            response = client.post("/api/artworks", json=body, headers=headers)
        assert response.status_code == 200, response.text
        artwork = response.json()
        assert artwork["status"] == "asset_review", artwork["steps"]
        path = f"/api/artworks/{artwork['id']}/review"
        assert client.post(path, json={"approve": True}).status_code == 401
        final = client.post(path, json={"approve": True}, headers=headers)
        assert final.status_code == 200, final.text
        assert final.json()["status"] == "final_review", final.json()["steps"]
        targets = final.json()["gate_targets"]
        assert set(targets) == {"td", "ue"}
        done = client.post(path, json={"approve": True}, headers=headers)
        assert done.status_code == 200 and done.json()["status"] == "prepared", done.text
        for renderer, folder, filename in (("td", "delivery_td", "artwork.json"),
                                           ("ue", "delivery", "scene.json")):
            version = targets[renderer]
            assert (tmp_path / folder / "versions" / version / filename).is_file()
            assert not (tmp_path / folder / "release.json").exists()
        assert client.get("/infra/jobs", headers=headers).status_code == 200
        queued = client.post("/infra/jobs", json={"request_id": "restored-draft", "kind": "draft",
                                                 "draft": {"theme": "우주"}}, headers=headers)
        assert queued.status_code == 202, queued.text


@pytest.mark.parametrize("renderer", ["td", "ue", "both"])
def test_renderer_selection_keeps_the_requested_output(tmp_path, renderer):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
                        renderer=renderer, operator_token="test-operator")
    with TestClient(create_app(settings), headers={"X-Operator-Token": "test-operator"}) as client:
        response = client.post("/api/artworks", json={"theme": "우주", "reveal": "none", "families": ["particles"]})
        assert response.status_code == 200 and response.json()["status"] == "prepared", response.text
        assert (tmp_path / "delivery_td" / "versions").is_dir() == (renderer in ("td", "both"))
        assert (tmp_path / "delivery" / "versions").is_dir() == (renderer in ("ue", "both"))


def test_active_packages_do_not_import_archive_snapshots():
    blocked = ("archive",)
    violations = []
    for area in (ROOT / "apps", ROOT / "packages"):
        for path in area.rglob("*.py"):
            if "src" not in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text("utf-8"), filename=str(path))):
                names = []
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom):
                    # 상대 import도 패키지의 절대 이름으로 바꿔 검사한다.
                    prefix = node.module or ""
                    if node.level:
                        package = path.relative_to(area).parts
                        package = package[package.index("src") + 1:-1]
                        prefix = ".".join((*package[:len(package) - node.level + 1], prefix)).strip(".")
                    names = [prefix, *(f"{prefix}.{item.name}" for item in node.names)]
                if any(name == item or name.startswith(item + ".") for name in names for item in blocked):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert violations == [], "활성 코드가 archive 사본을 참조합니다: " + ", ".join(violations)


def test_simulated_hand_matches_compiled_ue_condition_and_td_source():
    intent = Intent(title="입력 계약 시험", theme="ocean")
    elements = [Element(family=Family.fluid, module="Fluid")]
    mappings = [Mapping(source=InputSource.hand, family=Family.fluid, reaction="stir")]
    brief = ue_scene.to_brief(intent, elements, mappings, "ocean", "입력 계약 시험")
    scene, _ = ue_scene.compile_scene(brief, {})
    assert ue_scene.check(brief, scene) == []
    frame = json.loads(packet(1, "test-session", 0.25, 0.75))["frames"][0]
    sample = frame["samples"][0]
    assert frame["source_id"] == DEFAULT_SOURCES[0]
    conditions = [row for row in scene["Conditions"] if frame["source_id"] in row.get("SourceIds", [])]
    assert conditions, "모의 손 출처를 구독하는 UE 조건이 없습니다"
    assert all(row["SampleId"] == sample["sample_id"] and row["CoordinateSpace"] == sample["space"]
               for row in conditions)
    assert sample["kind"] == "position2d" and sample["value"] == [0.25, 0.75]
    config = json.loads((ROOT / "td/project/Json/sensor_input_config_ai.json").read_text("utf-8"))
    sources = [row for row in config["sources"] if row["source_id"] == frame["source_id"]]
    assert len(sources) == 1 and sources[0]["enabled"] and sources[0]["adapter"] == "hand_dat"
    assert sources[0]["subject_id"] == sample["subject_id"]


def test_simulated_hand_can_target_an_explicit_scene_source():
    frame = json.loads(packet(7, "another-session", 0.1, 0.2, "td_main/custom_hand"))["frames"][0]
    assert frame["source_id"] == "td_main/custom_hand"
    assert frame["source_session_id"] == "another-session" and frame["sequence"] == 7
