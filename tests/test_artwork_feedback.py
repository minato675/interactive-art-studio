"""운영자 의견 저장→다음 Head 명령 입력. 실제 AI·UE 없이 경계와 지속성을 검증한다."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import Session, select
from tests.test_flow import FakeLLM, make_client, step
from tests.test_head_dispatch import REGISTRY, run_decision
from tests.test_head_dispatch import setup as head_setup  # noqa: F401 — pytest fixture 공유
from tests.test_infra import infra_client, post_agent  # noqa: F401 — pytest fixture 공유

from central.feedback_api import save
from contracts.feedback import FeedbackCreate
from db import jobs
from db.feedback import ArtworkFeedback
from db.head import HeadWorkflow
from studio.head import dispatch, feedback
from studio.head.exhibition_director import ExhibitionDirector
from studio.head.llm import LLMUnavailable
from studio.head.planning import LLMPlanner, RulePlanner


def seed_show(session, suffix="a", node_id="show", preview=False):
    """실제 게시 완료 형태. 세션·활성 버전과 원본 작품 버전을 다르게 둔다."""
    source_id, publication_id = f"source-{suffix}", f"publication-{suffix}"
    timestamp = jobs.now(session)
    spec = {"artwork_id": f"art-{suffix}", "version": "v1", "catalog_version": "v1"}
    assembly = {**spec, "spec": {"raw_prompt": "손에 반응하는 숲", "content": {"theme": "숲의 호흡"},
                                "interactions": [{"input": "move", "reaction": "push", "return": "fade"}]},
                "assets": {"objects": [{}, {}], "layers": [{}]}}
    source = jobs.Job(id=source_id, request_id=f"assemble-{suffix}", fingerprint="fixture", kind="ue_assemble",
                      queue="assembly", status="succeeded", payload={"assembly": assembly},
                      result={"files": []}, created_at=timestamp)
    report = {"phase": "Applied", "desired_version": "ue-" + source_id, "active_version": "ue-" + source_id,
              "runtime_session": "runtime-session", "runtime_pid": 12345, "error": ""}
    publication = jobs.Job(id=publication_id, request_id=f"publish-{suffix}", fingerprint="fixture", kind="ue_publish",
                           queue="exhibition", status="succeeded", node_id=node_id, created_at=timestamp,
                           payload={"runtime": {**spec, "source_job_id": source_id, "target_node": node_id},
                                    "_approval": {"scope": "display_preview_only"} if preview else {}},
                           result={"phase": "Applied", "source_job_id": source_id, "runtime_status": report})
    node = session.get(jobs.Node, node_id) or jobs.Node(id=node_id, role="show", catalog_version="v1")
    node.heartbeat_at, node.show_report = timestamp, {**report, "_received_at": timestamp}
    session.add_all([source, publication, node])
    session.commit()
    return {"publication_job_id": publication_id, "node_id": node_id, "artwork_id": spec["artwork_id"],
            "version": "v1", "runtime_version": "ue-" + source_id}


def request_body(target, request_id="feedback-1", comment="객체 밀도를 줄여 손 반응을 잘 보이게 해주세요"):
    return {"request_id": request_id, "target": target, "intention": "improve", "category": "interaction",
            "comment": comment, "enabled": True}


@pytest.fixture
def published(infra_client):  # noqa: F811
    with Session(infra_client.app.state.engine) as session:
        target = seed_show(session)
    return infra_client, target


def test_current_matches_active_version_instead_of_latest_generated_work(published):
    client, target = published
    with Session(client.app.state.engine) as session:
        seed_show(session, "unrelated", node_id="other-show")
    current = client.get("/infra/feedback/current").json()["shows"]
    assert len(current) == 1 and current[0]["can_review"]
    artwork = current[0]["artwork"]
    assert artwork["target"] == target
    assert artwork["object_count"] == 2 and artwork["title"] == "숲의 호흡"
    assert "move · push · fade" in artwork["interactions"]


def test_heartbeat_without_report_does_not_refresh_old_applied_evidence(published):
    client, _ = published
    with Session(client.app.state.engine) as session:
        node = session.get(jobs.Node, "show")
        node.show_report = {**node.show_report, "_received_at": jobs.now(session) - 100}
        session.add(node)
        session.commit()
        report = {k: v for k, v in node.show_report.items() if not k.startswith("_")}
    assert post_agent(client, "show", "heartbeat").status_code == 200
    state = client.get("/infra/feedback/current").json()["shows"][0]
    assert state["online"] and not state["can_review"]
    assert post_agent(client, "show", "heartbeat", report).status_code == 200
    assert client.get("/infra/feedback/current").json()["shows"][0]["can_review"]
    report.update(phase="Ready", desired_version="next-runtime")
    post_agent(client, "show", "heartbeat", report)
    assert client.get("/infra/feedback/current").json()["shows"][0]["can_review"]  # 다음 준비 중 현재 작품
    report["phase"] = "Unknown"
    post_agent(client, "show", "heartbeat", report)
    assert not client.get("/infra/feedback/current").json()["shows"][0]["can_review"]


def test_feedback_is_persistent_idempotent_and_bound_to_observed_version(published):
    client, target = published
    body = request_body(target)
    first = client.post("/infra/feedback", json=body)
    assert first.status_code == 200, first.text
    record = first.json()
    assert record["target"] == target and "fingerprint" not in record
    assert client.post("/infra/feedback", json=body).json()["id"] == record["id"]
    assert client.post("/infra/feedback", json={**body, "comment": "다른 내용"}).status_code == 409
    with Session(client.app.state.engine) as session:
        seed_show(session, "next")
    past = client.post("/infra/feedback", json=request_body(target, "past-work"))
    assert past.status_code == 200 and past.json()["target"]["artwork_id"] == "art-a"
    assert client.get("/infra/feedback/current").json()["shows"][0]["artwork"]["target"]["artwork_id"] == "art-next"
    with Session(client.app.state.engine) as session:
        assert len(session.exec(select(ArtworkFeedback)).all()) == 2


def test_bad_target_and_unauthorized_agent_cannot_write(published):
    client, target = published
    wrong = {**target, "version": "v2"}
    assert client.post("/infra/feedback", json=request_body(wrong)).status_code == 409
    assert client.post("/infra/feedback", json=request_body(target, comment=" " * 10)).status_code == 422
    assert client.post("/infra/feedback", json=request_body(target, comment="x" * 1001)).status_code == 422
    headers = {"X-Operator-Token": "", "X-Agent-Token": "show" * 10}
    assert client.get("/infra/feedback/current", headers=headers).status_code == 401
    assert client.get("/infra/feedback", headers=headers).status_code == 401
    assert client.post("/infra/feedback", json=request_body(target), headers=headers).status_code == 401


def test_concurrent_retransmission_stores_only_one_feedback(published):
    client, target = published
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.post("/infra/feedback", json=request_body(target)), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert len(client.get("/infra/feedback").json()) == 1


def test_disable_is_revision_checked_and_future_snapshots_exclude_it(published):
    client, target = published
    record = client.post("/infra/feedback", json=request_body(target)).json()
    with Session(client.app.state.engine) as session:
        snapshot = feedback.capture(session)
    path = "/infra/feedback/" + record["id"]
    change = {"enabled": False, "expected_revision": 1}
    response = client.patch(path, json=change)
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert client.patch(path, json=change).status_code == 409
    assert len(client.get("/infra/feedback", params={"enabled": False}).json()) == 1
    with Session(client.app.state.engine) as session:
        assert feedback.capture(session)["items"] == []
    assert snapshot["items"][0]["id"] == record["id"]


def test_only_latest_eight_active_records_are_context_but_history_is_not_deleted(published):
    client, target = published
    for index in range(10):
        assert client.post("/infra/feedback", json=request_body(target, f"note-{index}", f"의견{index}")).status_code == 200
    with Session(client.app.state.engine) as session:
        captured = feedback.capture(session)
    assert len(captured["items"]) == 8
    assert captured["items"][0]["comment"] == "의견9"
    assert len(client.get("/infra/feedback").json()) == 10
    assert len(client.get("/infra/feedback", params={"limit": 3, "offset": 8}).json()) == 2


def test_head_freezes_feedback_for_next_commands_and_idempotent_submission(head_setup):  # noqa: F811
    engine, settings, connection, body = head_setup
    with Session(engine) as session:
        target = seed_show(session)
        first = save(session, FeedbackCreate(**request_body(target)))
        first_id = first.id
        row = dispatch.submit(session, body, connection, REGISTRY)
        workflow_id, decision_id = row.id, row.decision_job
        save(session, FeedbackCreate(**request_body(target, "new-note", "그 이후 의견")))
        first.enabled = False
        session.add(first)
        session.commit()
        repeated = dispatch.submit(session, body, connection, REGISTRY)
        assert repeated.id == workflow_id
        assert [f["id"] for f in repeated.payload["_operator_feedback"]["items"]] == [first_id]
    run_decision(engine, settings, decision_id)
    assert [f["id"] for f in connection.contexts[-1]["operator_feedback"]["items"]] == [first_id]
    with Session(engine) as session:
        row = session.get(HeadWorkflow, workflow_id)
        assert row.decisions[-1]["operator_feedback_ids"] == [first_id]
        assert session.get(jobs.Job, row.background_job).payload["generation"]["prompt"]  # 다음 생성 명령 등록


def test_sync_draft_and_confirmation_use_same_feedback_without_mutating_shared_planner(tmp_path):
    class RecordingLLM(FakeLLM):
        def __init__(self):
            super().__init__({"title": "여유로운 흐름", "mood": ["calm"], "palette": ["teal"]},
                             {"reactions": [{"family": "fluid", "reaction": "stir"}]})
            self.prompts = []

        def complete_json(self, system, user, schema):
            self.prompts.append((system, user))
            return super().complete_json(system, user, schema)

    llm = RecordingLLM()
    original = LLMPlanner(llm)
    with make_client(tmp_path, planner=original) as client:
        with Session(client.app.state.engine) as session:
            target = seed_show(session)
            saved = save(session, FeedbackCreate(**request_body(target, comment="첫 관찰 메모")))
            saved_id = saved.id
        result = client.post("/api/drafts", json={"theme": "바다", "families": ["fluid"], "reveal": "none"}).json()
        assert result["status"] == "awaiting"
        assert step(result, "운영 피드백")["delivery"] == "llm_context"
        with Session(client.app.state.engine) as session:
            saved = session.get(ArtworkFeedback, saved_id)
            saved.enabled = False
            session.add(saved)
            session.commit()
            save(session, FeedbackCreate(**request_body(target, "after-draft", "나중에 들어온 메모")))
        confirmed = client.post(f"/api/artworks/{result['id']}/confirm", json={}).json()
        assert confirmed["status"] == "prepared"
        assert all("첫 관찰 메모" in user and "나중에 들어온 메모" not in user for _, user in llm.prompts)
        assert all("정식 명세" in system for system, _ in llm.prompts)
        assert original.llm is llm and original.sources == {}


def test_preview_feedback_is_labelled_and_not_publication_approval(published):
    client, target = published
    with Session(client.app.state.engine) as session:
        publication = session.get(jobs.Job, target["publication_job_id"])
        publication.payload = {**publication.payload, "_approval": {"scope": "display_preview_only"}}
        session.add(publication)
        session.commit()
    record = client.post("/infra/feedback", json=request_body(target)).json()
    assert record["preview_only"]
    body = copy.deepcopy(request_body(target, "another"))
    body["target"]["runtime_version"] = "not-active"
    assert client.post("/infra/feedback", json=body).status_code == 409


@pytest.mark.parametrize("llm_planning", [False, True])
def test_exhibition_director_and_planner_share_frozen_feedback_during_retry(tmp_path, llm_planning):
    class RecordingLLM(FakeLLM):
        def __init__(self):
            super().__init__({"title": "여유로운 밤", "mood": ["calm"], "palette": ["night"]},
                             {"reactions": [{"family": "particles", "reaction": "scatter"}]})
            self.prompts = []

        def complete_json(self, system, user, schema):
            self.prompts.append((system, user))
            return super().complete_json(system, user, schema)

    class DirectorLLM:
        name, model = "fake-director", "director-model"

        def __init__(self):
            self.prompts = []

        def complete_json(self, system, user, schema):
            self.prompts.append((system, user))
            if len(self.prompts) == 1:
                with Session(client.app.state.engine) as session:
                    row = session.get(ArtworkFeedback, saved_id)
                    row.enabled = False
                    session.add(row)
                    save(session, FeedbackCreate(**request_body(target, "during-director", "도중에 새로 온 의견")))
                return {"prompt": ""}  # 최초 제안 거부 후 다시 쓰기도 같은 문맥이어야 함
            return {"prompt": "밤하늘의 빛 입자가 손끝을 따라 흩어진다", "families": ["particles"],
                    "mood": "calm", "palette": "night", "subject": "", "reveal": "none"}

    llm, director_llm = RecordingLLM(), DirectorLLM()
    planner = LLMPlanner(llm) if llm_planning else RulePlanner()
    director = ExhibitionDirector(director_llm)
    with make_client(tmp_path, "ue", planner=planner, director=director) as client:
        with Session(client.app.state.engine) as session:
            target = seed_show(session)
            saved_id = save(session, FeedbackCreate(**request_body(target, comment="첫 관찰 의견"))).id
        response = client.post("/api/artworks", json={"mode": "exhibition"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "prepared", result["steps"]
        detail = step(result, "디렉터")
        assert detail["source"] == "llm-repaired" and detail["model"] == director.model_ref == "fake-director:director-model"
        assert len(detail["attempts"]) == 2 and detail["fallback"] is None
        memory = step(result, "운영 피드백")
        assert memory["delivery"] == "llm_context" and [r["id"] for r in memory["snapshot"]["items"]] == [saved_id]
        assert len(director_llm.prompts) == 2
        assert bool(llm.prompts) == llm_planning
        for system, user in [*director_llm.prompts, *llm.prompts]:
            assert "첫 관찰 의견" in user and "도중에 새로 온 의견" not in user
            assert "정식 명세" in system
        assert director.llm is director_llm
        if llm_planning:
            assert planner.llm is llm and planner.sources == {}


def test_feedback_keeps_exhibition_director_connection_failure_fallback(tmp_path):
    llm = FakeLLM(LLMUnavailable("offline"))
    director = ExhibitionDirector(llm)
    with make_client(tmp_path, "ue", planner=RulePlanner(), director=director) as client:
        with Session(client.app.state.engine) as session:
            target = seed_show(session)
            save(session, FeedbackCreate(**request_body(target)))
        result = client.post("/api/artworks", json={"mode": "exhibition"}).json()
        assert result["status"] == "prepared", result["steps"]
        assert step(result, "디렉터")["fallback"] == "연결 실패"
        assert step(result, "디렉터")["model"] == "fake"
        assert len(step(result, "디렉터")["attempts"]) == 1
        assert step(result, "자동 요청 생성")["preset"]
        assert director.llm is llm
