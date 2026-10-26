"""사람 검수(선택): 소재 검수(asset_review) · 최종 검수(final_review) → POST /api/artworks/{id}/review."""
import hashlib
from pathlib import Path

import pytest
from tests.test_flow import create, make_client, step


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as test_client:
        yield test_client


@pytest.fixture
def both(tmp_path):
    with make_client(tmp_path, "both") as test_client:
        yield test_client


def decide(client, artwork_id, approve=True, expect=200, **body):
    response = client.post(f"/api/artworks/{artwork_id}/review", json={"approve": approve, **body})
    assert response.status_code == expect, response.text
    return response.json()


def statuses(client):
    return {Path(a["path"]).name: a["status"] for a in client.get("/api/pool").json()}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_gates_off_keeps_pool_approved(client):
    artwork = create(client, theme="바다")
    assert artwork["status"] == "prepared" and artwork["gate"] is None
    assert set(statuses(client).values()) == {"approved"}


def test_asset_review_stops_and_shows_new_assets(client, tmp_path):
    artwork = create(client, theme="바다", review_assets=True)
    assert artwork["status"] == "asset_review" and artwork["gate"] == "assets", artwork["steps"]
    assert artwork["scene_path"] is None and not (tmp_path / "delivery_td" / "release.json").exists()
    assert set(statuses(client).values()) == {"pending"}   # 배경·실루엣 모두 대기
    png = next(name for name in artwork["gate_files"] if name.endswith(".png"))
    assert client.get(f"/api/artworks/{artwork['id']}/review/files/{png}").status_code == 200
    assert client.get(f"/api/artworks/{artwork['id']}/review/files/studio.db").status_code == 404
    assert client.get(f"/api/artworks/{artwork['id']}/review/files/..%2Fstudio.db").status_code == 404


def test_pending_assets_are_not_reused_from_pool(client):
    create(client, theme="바다", reveal="none", families=["fluid"], review_assets=True)
    other = create(client, theme="바다", reveal="none", families=["fluid"], assets="pool")
    assert "새로 생성" in step(other, "소재")[0]


def test_pending_silhouette_is_not_reused(client):
    create(client, theme="바다", subject="고래", review_assets=True)
    second = create(client, theme="우주", subject="고래", reveal="formation", families=["particles"])
    assert not any("실루엣: 풀에서 사용" in line for line in step(second, "소재"))


def test_asset_approve_continues_to_publish(client):
    artwork = create(client, theme="바다", review_assets=True)
    done = decide(client, artwork["id"], note="색 좋음")
    assert done["status"] == "prepared" and done["gate"] is None, done["steps"]
    assert step(done, "검수 결과") == {"gate": "assets", "approve": True, "note": "색 좋음"}
    assert set(statuses(client).values()) == {"approved"}
    assert "subject" not in step(done, "의도")


def test_asset_reject_removes_new_assets(client, tmp_path):
    artwork = create(client, theme="바다", review_assets=True)
    done = decide(client, artwork["id"], approve=False)
    assert done["status"] == "held" and "소재 검수 반려" in step(done, "상영")
    assert client.get("/api/pool").json() == []
    assert not list((tmp_path / "generated").glob("*.png")) and not list((tmp_path / "subjects").glob("*.json"))


def test_asset_review_skips_when_nothing_new(client):
    create(client, mode="exhibition", theme="바다", reveal="none", families=["fluid"])   # 풀에 배경 하나
    artwork = create(client, theme="바다", reveal="none", families=["fluid"], assets="pool", review_assets=True)
    assert artwork["status"] == "prepared" and step(artwork, "소재 검수") == "새로 만든 소재 없음 → 건너뜀"


def test_final_review_publishes_reviewed_files(both, tmp_path):
    artwork = create(both, theme="바다", reveal="none", families=["fluid"], review_final=True)
    assert artwork["status"] == "final_review", artwork["steps"]
    version = artwork["gate_targets"]["ue"]
    assert artwork["gate_targets"] == {"td": version, "ue": version}
    assert not (tmp_path / "delivery" / "release.json").exists()
    reviewed = tmp_path / "review" / "delivery" / "versions" / version
    assert not (tmp_path / "review" / "delivery" / "release.json").exists() and (reviewed / "scene.json").exists()
    assert set(statuses(both).values()) == {"pending"}

    done = decide(both, artwork["id"])
    assert done["status"] == "prepared", done["steps"]
    shown = tmp_path / "delivery" / "versions" / version
    assert Path(done["scene_path"]).parent.parent.parent in (tmp_path / "delivery_td", tmp_path / "delivery")
    for name in ("scene.json", "background.png"):
        assert digest(shown / name) == digest(reviewed / name)
    assert set(statuses(both).values()) == {"approved"}


def test_final_review_of_td_only_artwork(both):
    artwork = create(both, theme="바다", subject="고래", review_final=True)   # 주인공 작품은 UE를 건너뛴다
    assert artwork["status"] == "failed" and not artwork["gate_targets"]


def test_final_reject_keeps_exhibition_untouched(both, tmp_path):
    artwork = create(both, theme="바다", reveal="none", families=["fluid"], review_final=True)
    done = decide(both, artwork["id"], approve=False)
    assert done["status"] == "held" and "최종 검수 반려" in step(done, "상영")
    assert not (tmp_path / "delivery").exists() and not (tmp_path / "delivery_td").exists()
    assert both.get("/api/pool").json() == []


def test_both_gates_in_order(client):
    artwork = create(client, theme="숲", review_assets=True, review_final=True)
    assert artwork["status"] == "asset_review"
    middle = decide(client, artwork["id"])
    assert middle["status"] == "final_review" and middle["gate"] == "final"
    assert set(statuses(client).values()) == {"approved"}   # 사람이 본 소재는 남는다
    done = decide(client, artwork["id"], approve=False)
    assert done["status"] == "held" and set(statuses(client).values()) == {"approved"}


def test_exhibition_ignores_gates(client):
    artwork = create(client, mode="exhibition", theme="바다", review_assets=True, review_final=True)
    assert artwork["status"] == "prepared"


def test_edit_mode_carries_gates(client):
    draft = client.post("/api/drafts", json={"theme": "바다", "review_assets": True}).json()
    confirmed = client.post(f"/api/artworks/{draft['id']}/confirm", json={}).json()
    assert confirmed["status"] == "asset_review"


def test_review_conflicts(client):
    artwork = create(client, theme="바다", review_assets=True)
    decide(client, artwork["id"])
    decide(client, artwork["id"], expect=409)
    decide(client, 999, expect=404)
    decide(client, artwork["id"], expect=422, colour="red")
    assert client.post(f"/api/artworks/{artwork['id']}/review", json={"approve": True},
                       headers={"X-Operator-Token": "wrong"}).status_code == 401


def test_check_failure_after_asset_approve_keeps_approved_assets(client, tmp_path, monkeypatch):
    import central.pipeline

    artwork = create(client, theme="바다", review_assets=True)
    monkeypatch.setattr(central.pipeline.td_bridge, "check", lambda spec, files: ["일부러 실패"])
    done = decide(client, artwork["id"])
    assert done["status"] == "failed" and "검사 실패" in step(done, "상영")
    assert set(statuses(client).values()) == {"approved"}   # 사람이 승인한 소재는 남는다
    assert list((tmp_path / "generated").glob("*.png"))


def test_failure_after_final_approve_cleans_pending(tmp_path, monkeypatch):
    import central.pipeline

    with make_client(tmp_path) as test_client:
        artwork = create(test_client, theme="바다", review_final=True)
        real = central.pipeline.prepare_version

        def broken_publish(folder, *args):
            if "review" not in Path(folder).parts:
                raise OSError("디스크 오류")
            return real(folder, *args)

        monkeypatch.setattr(central.pipeline, "prepare_version", broken_publish)
        done = decide(test_client, artwork["id"])
        assert done["status"] == "failed" and "디스크 오류" in step(done, "상영")
        assert test_client.get("/api/pool").json() == []
    assert not list((tmp_path / "generated").glob("*.png")) and not list((tmp_path / "subjects").glob("*.json"))
