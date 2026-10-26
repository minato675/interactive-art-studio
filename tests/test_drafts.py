"""편집 모드: AI 초안(POST /api/drafts) → 사용자가 바꾼 칸만 확정(POST /api/artworks/{id}/confirm)."""
import pytest
from tests.test_flow import FakeLLM, make_client, step

from studio.head.planning import LLMPlanner


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as test_client:
        yield test_client


def make_draft(client, **body):
    response = client.post("/api/drafts", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, artwork_id, expect=200, **changes):
    response = client.post(f"/api/artworks/{artwork_id}/confirm", json=changes)
    assert response.status_code == expect, response.text
    return response.json()


def test_draft_stops_before_assets_with_ai_values(client):
    draft = make_draft(client, theme="바다")
    assert draft["status"] == "awaiting" and draft["scene_path"] is None
    assert draft["draft"]["families"] == ["fluid"]
    assert draft["draft"]["subject"] == "" and draft["draft"]["reveal"] == "none"
    assert {"families", "subject", "reveal", "mood", "palette"} <= set(draft["review"])
    assert not any(s["name"] == "소재" for s in draft["steps"])   # 소재를 만들기 전에 멈춤
    assert client.get("/api/artworks").json()[0]["status"] == "awaiting"


def test_user_given_values_are_not_asked_again(client):
    draft = make_draft(client, theme="바다", families=["particles"], chips={"mood": "joyful"})
    assert "families" not in draft["review"] and "mood" not in draft["review"]
    assert draft["draft"]["mood"] == "joyful"


def test_confirm_without_changes_keeps_draft(client):
    draft = make_draft(client, theme="바다")
    done = confirm(client, draft["id"])
    assert done["status"] == "prepared", done["steps"]
    intent = step(done, "의도")
    assert "subject" not in intent and intent["reveal"] == "none"
    assert step(done, "확정")["changed"] == []
    assert step(done, "초안")["values"]["families"] == ["fluid"]   # 초안 기록이 남는다
    assert done["draft"] is None and done["review"] == []


def test_only_changed_fields_become_user_explicit(client):
    draft = make_draft(client, theme="바다")
    done = confirm(client, draft["id"], mood="mysterious", palette="warm", subject=draft["draft"]["subject"])
    final = step(done, "확정")
    assert sorted(final["changed"]) == ["mood", "palette"]   # 같은 값을 보낸 subject는 바뀐 것이 아님
    assert final["origins"]["mood"] == "user_explicit" and final["origins"]["subject"] == "unextracted"
    intent = step(done, "의도")
    assert intent["mood"] == ["mysterious"] and intent["palette"] == ["amber", "coral"]


def test_changing_family_repicks_ai_reveal(client):
    draft = make_draft(client, theme="바다", subject="고래")   # 고래 · 형상 · 군집
    done = confirm(client, draft["id"], families=["paint"])
    assert step(done, "의도")["reveal"] == "trace"
    final = step(done, "확정")
    assert final["origins"]["reveal"] == "head_selected" and final["notes"]


def test_conflicting_user_choices_are_422_and_draft_waits(client):
    draft = make_draft(client, theme="바다")
    confirm(client, draft["id"], expect=422, reveal="formation", families=["paint"])
    confirm(client, draft["id"], expect=422, families=["paint", "fluid"])   # 캔버스 방식은 하나만
    confirm(client, draft["id"], expect=422, mood="angry")
    confirm(client, draft["id"], expect=422, colour="red")
    confirm(client, draft["id"], expect=422, title=None)
    confirm(client, draft["id"], expect=422, title="   ")
    assert client.get(f"/api/artworks/{draft['id']}").json()["status"] == "awaiting"
    assert confirm(client, draft["id"])["status"] == "prepared"


def test_second_confirm_is_409(client):
    draft = make_draft(client, theme="숲")
    confirm(client, draft["id"])
    confirm(client, draft["id"], expect=409)
    confirm(client, 9999, expect=404)


def test_unconnected_family_is_held_at_confirm(client):
    draft = make_draft(client, theme="바다")
    done = confirm(client, draft["id"], families=["media"])
    assert done["status"] == "failed" and "아직 연결되지 않은" in step(done, "상영")
    assert done["title"] == draft["title"]   # 보류의 rollback이 확정 값을 되돌리지 않음


def test_exhibition_draft_is_422(client):
    assert client.post("/api/drafts", json={"mode": "exhibition"}).status_code == 422


def test_drafts_need_operator_token(client):
    assert client.post("/api/drafts", json={}, headers={"X-Operator-Token": "wrong"}).status_code == 401
    assert client.post("/api/artworks/1/confirm", json={}, headers={"X-Operator-Token": "wrong"}).status_code == 401


def test_llm_draft_values_survive_confirm(tmp_path):
    """LLM이 고른 색 조합(칩으로 못 돌아감)·제목이 확정 때 다시 정해지지 않는다."""
    llm = FakeLLM({"title": "푸른 숨", "mood": ["dreamy"], "palette": ["amber", "teal"]})
    with make_client(tmp_path, planner=LLMPlanner(llm)) as test_client:
        draft = make_draft(test_client, theme="바다", families=["flock"], subject="고래")
        assert draft["draft"]["palette"] is None and draft["draft"]["palette_names"] == ["amber", "teal"]
        assert "palette" in draft["review"]
        llm.answers = [{"title": "다른 제목", "mood": ["joyful"], "palette": ["coral"]}]   # 다시 물으면 바뀔 답
        done = confirm(test_client, draft["id"])
        intent = step(done, "의도")
        assert intent["title"] == "푸른 숨" and intent["palette"] == ["amber", "teal"] and intent["mood"] == ["dreamy"]
        assert step(done, "출처")["의도"] == "llm"


def test_null_values_are_422_not_ignored(client):
    draft = make_draft(client, theme="바다")
    for name in ("palette", "mood", "intensity", "subject", "families"):
        confirm(client, draft["id"], expect=422, **{name: None})
    assert client.get(f"/api/artworks/{draft['id']}").json()["status"] == "awaiting"


def test_clearing_subject_repicks_reveal_none(client):
    draft = make_draft(client, theme="바다", subject="고래")   # 고래 · 형상
    done = confirm(client, draft["id"], subject="")
    assert step(done, "의도")["reveal"] == "none" and "subject" not in step(done, "의도")


def test_adding_subject_to_field_draft_picks_reveal(client):
    draft = make_draft(client, theme="바다", reveal="none")
    assert draft["draft"]["reveal"] == "none" and "reveal" not in draft["review"]
    done = confirm(client, draft["id"], subject="사슴", families=["paint"])   # 사용자가 정한 none은 그대로
    assert done["status"] == "failed" and "드러낼 수 없음" in step(done, "상영")
    draft = make_draft(client, theme="숲", families=["paint"])
    done = confirm(client, draft["id"], subject="여우")
    assert step(done, "의도")["reveal"] == "trace"


def test_user_reveal_from_request_blocks_family_change(client):
    draft = make_draft(client, theme="바다", subject="고래", reveal="formation")
    confirm(client, draft["id"], expect=422, families=["paint"])


def test_failed_confirm_keeps_confirmed_values(tmp_path, monkeypatch):
    import central.pipeline

    def broken_publish(*args):
        raise OSError("디스크 가득 참")

    monkeypatch.setattr(central.pipeline, "prepare_version", broken_publish)
    with make_client(tmp_path) as test_client:
        draft = make_draft(test_client, theme="바다")
        done = confirm(test_client, draft["id"], title="고친 제목")
        assert done["status"] == "failed" and done["title"] == "고친 제목"
        assert step(done, "확정")["changed"] == ["title"]


def test_claim_lets_only_one_confirm_run(tmp_path):
    """두 확정이 동시에 awaiting을 읽어도 상태 전환은 한 번만 성공한다."""
    from sqlmodel import Session

    from central.pipeline import DraftConflict
    from central.pipeline import confirm as confirm_draft
    from contracts.schemas import DraftEdit
    from studio.head.planning import RulePlanner

    with make_client(tmp_path) as test_client:
        draft = make_draft(test_client, theme="숲")
        state = test_client.app.state
        with Session(state.engine) as first, Session(state.engine) as second:
            stale = second.get(central_artwork(), draft["id"])   # 두 번째 세션도 awaiting을 읽어 둔다
            assert stale.status == "awaiting"
            confirm_draft(first, draft["id"], DraftEdit(), state.settings, RulePlanner(), state.images, state.outlines)
            with pytest.raises(DraftConflict):
                confirm_draft(second, draft["id"], DraftEdit(), state.settings, RulePlanner(), state.images,
                              state.outlines)


def central_artwork():
    from db.models import Artwork

    return Artwork
