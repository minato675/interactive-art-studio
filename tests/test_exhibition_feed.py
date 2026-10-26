"""전시 자동 순환(infra/HEAD_EXHIBITION_FEED.md): 범위 계산 · 디렉터 작업 · Dispatcher 자리 세기 · 쉬기 · API.
LLM과 노드는 가짜다. 실제 GMS 응답 품질이나 현장 노드 동작의 근거가 아니다."""
import copy
import itertools
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from test_head_dispatch import FakeHead, register_ue

from central.config import Settings
from central.main import create_app
from contracts.spec import Spec
from db import jobs
from db.engine import create_tables, make_engine
from db.exhibition_feed import ExhibitionFeed
from db.head import HeadWorkflow
from db.ue import UECapability
from studio.head import exhibition_feed as feed
from studio.head import ue_support
from studio.head.llm import GMSLLM, LLMUnavailable

ENTRIES = {"gen": ("generation", ["background_generate"]), "a1": ("generation", ["assets_generate"]),
           "ue": ("production", ["ue_assemble"]), "ue-review-1": ("production", ["ue_verify"]),
           "ue-show-1": ("show", ["ue_publish"])}
REGISTRY = {n: {"token": n * 20, "role": r, "job_kinds": k, "catalog_version": "v1"} for n, (r, k) in ENTRIES.items()}
ANSWER = {"prompt": "새벽 안개 속 빛 먼지가 지나가는 사람을 따라 흐른다", "family": "scene_family", "reaction": "follow",
          "medium": "particle", "reveal": "field", "input": "move", "reaction_mode": "while", "idle_motion": "ignore", "mood": "고요",
          "palette": [{"role": "배경", "hex": "#102030"}, {"role": "빛", "hex": "#F0E0A0"}],
          "difference": "최근의 물 장면 대신 안개와 빛 먼지로 바꿈"}
# Input Agent가 원문과 어긋난 값을 냈다고 가정한 명세. 디렉터 값이 이겨야 한다.
MATCHED = {
    "template_family": "scene_family", "raw_prompt": ANSWER["prompt"],
    "content": {"theme": "안개 속 빛 먼지", "mood": "어두움", "background": "새벽 숲", "dimension": "2.5d",
                "medium": "fluid", "main_character": "반딧불", "reveal": "direct"},
    "idle": {"motion": None},
    "interactions": [{"input": "appear", "reaction": "stir", "reaction_mode": "while", "return": "fade"},
                     {"input": "wave", "reaction": "press", "reaction_mode": "once", "return": "stay"}],
    "provenance": [{"field_path": "content.medium", "value": "fluid", "origin": "extracted", "source_text": "안개"},
                   {"field_path": "content.theme", "value": "안개 속 빛 먼지", "origin": "extracted"},
                   {"field_path": "interactions[1].reaction", "value": "press", "origin": "extracted"}],
}
FLOW_ANSWER = {**ANSWER, "prompt": "빛줄기가 사람이 지나간 자리를 따라 그려진다", "family": "flow_family",
               "reaction": "draw", "medium": "stroke", "reveal": None}
# 흐름 계열 명세: 드러내는 방식 · 주인공 · 화면 깊이는 막힌 칸이고 평상시 움직임은 필수다
MATCHED_FLOW = {
    "template_family": "flow_family", "raw_prompt": FLOW_ANSWER["prompt"],
    "content": {"theme": "빛줄기", "mood": "밝음", "medium": "light"},
    "idle": {"motion": "glow"},
    "interactions": [{"input": "appear", "reaction": "glow", "reaction_mode": "while", "return": "fade"}],
    "provenance": [{"field_path": "content.medium", "value": "light", "origin": "extracted", "source_text": "빛줄기"}],
}


class FakeLLM:
    """AI 디렉터 답을 차례로 돌려준다. 예외를 넣으면 그 예외를 낸다."""
    name, model = "gms", "test"

    def __init__(self, *answers):
        self.answers, self.users = list(answers), []

    def complete_json(self, system, user, schema):
        self.users.append(user)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


def heartbeat(session, *node_ids, age=0):
    for node_id in node_ids:
        node = session.get(jobs.Node, node_id) or jobs.Node(id=node_id, role=REGISTRY[node_id]["role"],
                                                             catalog_version="v1")
        node.heartbeat_at = jobs.now(session) - age
        session.add(node)
    session.commit()


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'feed.db'}")
    create_tables(engine)
    settings = SimpleNamespace(data_dir=tmp_path, agent_keys_json=json.dumps(REGISTRY),
                               head_connection=lambda profile: FakeHead(), exhibition_feed_max_in_flight=10,
                               exhibition_feed_stale_seconds=1800, exhibition_feed_retry_seconds=60)
    with Session(engine) as session:
        register_ue(session)
        heartbeat(session, "gen", "a1", "ue-review-1", "ue-show-1")
        session.add(ExhibitionFeed(enabled=True))
        session.commit()
    matched, families = [], []

    def match(prompt, family):
        matched.append(prompt)
        families.append(str(family))
        base = MATCHED_FLOW if family == "flow_family" else MATCHED
        return SimpleNamespace(spec=Spec.model_validate({**base, "raw_prompt": prompt}), holds=[],
                               sources={"명세": "llm"})

    monkeypatch.setattr(feed, "LLMSpecMatcher", lambda *args, **kwargs: SimpleNamespace(match=match))
    yield SimpleNamespace(engine=engine, settings=settings, matched=matched, families=families)
    engine.dispose()


def new_job(env, result=None, status=None, error=""):
    """디렉터 작업 하나를 넣고 Worker처럼 임대한다. result가 있으면 그 결과로 끝낸 기록을 만든다."""
    with Session(env.engine) as session:
        row = jobs.enqueue(session, {"request_id": f"{feed.KIND}-{uuid.uuid4().hex}", "kind": feed.KIND})
        job = jobs.start(session, row, "worker", 120)
        if result is not None:
            jobs.finish(session, job.id, "worker", job.token, result, error)
        return job.id, job.token


def run(env, llm):
    job_id, token = new_job(env)
    return job_id, feed.run(env.engine, env.settings, job_id, "worker", token, llm)


def feed_state(env):
    with Session(env.engine) as session:
        return session.get(ExhibitionFeed, 1)


# ---------- 범위 계산 ----------
def test_range_matches_preflight_and_excludes_unsupported_values():
    table = ue_support.catalog()
    rng = feed.full_range(table)
    common = {"input": ["appear", "move"], "reaction_mode": ["while"], "idle_motion": ["ignore"]}
    assert rng.fields == {"scene_family": {"reveal": ["direct", "field"], **common}, "flow_family": common}
    assert rng.families() == ["flow_family", "scene_family"]
    for family in rng.families():
        reactions = {r for f, r, _ in rng.combos if f == family}
        assert {"scatter", "swirl", "press"}.isdisjoint(reactions) and {"follow", "stir", "draw"} <= reactions
    # 칸별 허용값을 어떻게 섞어도 모든 조합이 사전 확인을 통과하고 같은 템플릿이 고른다(칸 분리가 맞음)
    for (family, reaction, medium), template in rng.combos.items():
        fields = rng.fields[family]
        for values in itertools.product(*fields.values()):
            chosen = dict(zip(fields, values, strict=True))
            assert feed.probe({"family": family, "reaction": reaction, "medium": medium, **chosen}, table) == template


def test_flow_family_probe_needs_idle_motion():
    table = ue_support.catalog()
    values = {"family": "flow_family", "reaction": "draw", "medium": "stroke", "input": "move",
              "reaction_mode": "while", "idle_motion": "ignore"}
    assert feed.probe(values, table)
    assert feed.probe({**values, "idle_motion": None}, table) is None   # 흐름 계열은 평상시 움직임이 필수


def test_fields_outside_range_do_not_change_preflight_result():
    table = ue_support.catalog()
    rng = feed.full_range(table)
    for (family, reaction, medium), template in rng.combos.items():
        base = {name: values[0] for name, values in rng.fields[family].items()}
        for back, depth in itertools.product(["instant", "fade", "stay"], ["2d", "2.5d"]):
            values = {"family": family, "reaction": reaction, "medium": medium, **base, "return": back,
                      "dimension": depth}
            assert feed.probe(values, table) == template


def test_range_follows_template_table_and_reporting_nodes(env):
    table = copy.deepcopy(ue_support.catalog())
    table["mapping"]["dedicated"] = [r for r in table["mapping"]["dedicated"] if r["template"] != "paint"]
    assert not any(r == "draw" for _, r, _ in feed.full_range(table).combos)
    with Session(env.engine) as session:
        evidence = session.get(UECapability, "ue")
        gpu = next(r for r in ue_support.catalog()["templates"] if r["template"] == "gpu_particles")
        evidence.report = {"version": 1, "templates": [{"template": "gpu_particles", "fingerprint": gpu["fingerprint"]}]}
        session.add(evidence)
        session.commit()
        rng = feed.feed_range(session, REGISTRY)
    assert rng.combos and set(rng.combos.values()) == {"gpu_particles"}


# ---------- 디렉터 작업 ----------
def test_run_submits_exhibit_workflow_with_director_values(env):
    llm = FakeLLM(ANSWER)
    job_id, result = run(env, llm)
    assert result["outcome"] == "submitted" and result["request_id"] == "exhibit-" + job_id
    assert result["submitted"]["prompt"] == ANSWER["prompt"] and result["submitted"]["overlaps_recent"] is False
    assert result["submitted"]["choice"]["reaction"] == "follow" and result["submitted"]["template"]
    assert result["input_agent"]["sources"] == {"명세": "llm"} and env.matched == [ANSWER["prompt"]]
    with Session(env.engine) as session:
        flow = session.exec(select(HeadWorkflow).where(HeadWorkflow.request_id == "exhibit-" + job_id)).one()
    payload = flow.payload
    assert payload["automation"] is True and payload["auto_publish"] is True and payload["prompt"] == ANSWER["prompt"]
    spec = payload["spec"]
    assert spec["content"]["medium"] == "particle" and spec["content"]["reveal"] == "field"
    assert spec["content"]["main_character"] is None and spec["content"]["mood"] == "고요"
    assert spec["content"]["theme"] == "안개 속 빛 먼지"   # 자유 칸은 Input Agent 값
    assert spec["interactions"] == [{"input": "move", "reaction": "follow", "reaction_mode": "while",
                                     "return": "fade", "duration_sec": None}]
    assert spec["idle"]["motion"] == "ignore"
    origins = {p["field_path"]: p for p in spec["provenance"]}
    assert origins["content.medium"]["origin"] == "head_selected" and origins["content.medium"]["model_ref"] == "gms:test"
    assert origins["content.theme"]["origin"] == "extracted" and "interactions[1].reaction" not in origins
    assert env.families == ["scene_family"]
    Spec.model_validate(spec)


def test_run_submits_flow_family_with_blocked_fields_empty(env):
    llm = FakeLLM(FLOW_ANSWER)
    job_id, result = run(env, llm)
    assert result["outcome"] == "submitted" and result["submitted"]["choice"]["family"] == "flow_family"
    assert result["submitted"]["choice"]["reveal"] is None and env.families == ["flow_family"]
    assert "flow_family" in llm.users[0]
    with Session(env.engine) as session:
        flow = session.exec(select(HeadWorkflow).where(HeadWorkflow.request_id == "exhibit-" + job_id)).one()
    spec = flow.payload["spec"]
    assert spec["template_family"] == "flow_family" and spec["content"]["medium"] == "stroke"
    assert spec["content"]["reveal"] is None and spec["content"]["main_character"] is None
    assert spec["content"]["dimension"] is None and spec["idle"]["motion"] == "ignore"
    assert spec["interactions"][0]["reaction"] == "draw"
    assert "content.reveal" not in {p["field_path"] for p in spec["provenance"]}
    Spec.model_validate(spec)


def test_reveal_in_flow_family_is_retried(env):
    llm = FakeLLM({**FLOW_ANSWER, "reveal": "field"}, FLOW_ANSWER)
    _, result = run(env, llm)
    assert result["outcome"] == "submitted" and len(result["attempts"]) == 2
    assert "막힌 칸" in result["attempts"][0]["problems"][0] and "막힌 칸" in llm.users[1]


def test_rerun_returns_existing_workflow_without_director(env):
    job_id, token = new_job(env)
    first = feed.run(env.engine, env.settings, job_id, "worker", token, FakeLLM(ANSWER))
    other = FakeLLM({**ANSWER, "prompt": "전혀 다른 원문"})
    again = feed.run(env.engine, env.settings, job_id, "worker", token, other)
    assert again["workflow_id"] == first["workflow_id"] and other.users == []
    assert again["submitted"]["prompt"] == ANSWER["prompt"] and again["submitted"]["choice"]["medium"] == "particle"


def test_out_of_range_answer_is_retried_with_reason(env):
    llm = FakeLLM({**ANSWER, "reaction": "scatter"}, ANSWER)
    _, result = run(env, llm)
    assert result["outcome"] == "submitted" and len(result["attempts"]) == 2
    assert "scatter" in result["attempts"][0]["problems"][0] and "scatter" in llm.users[1]
    assert "submitted" in result and result["submitted"]["choice"]["reaction"] == "follow"


def test_three_rejections_exhaust_without_submit(env):
    llm = FakeLLM({**ANSWER, "reveal": "trace"})
    _, result = run(env, llm)
    assert result["outcome"] == "exhausted" and len(result["attempts"]) == 3 and "submitted" not in result
    with Session(env.engine) as session:
        assert session.exec(select(HeadWorkflow)).all() == []


def test_spec_hold_is_returned_to_director(env, monkeypatch):
    calls = []

    def match(prompt, family):
        calls.append(prompt)
        holds = [SimpleNamespace(reason="family_mismatch", text="호랑이",
                                 model_dump=lambda **_: {"reason": "family_mismatch", "text": "호랑이"})]
        return SimpleNamespace(spec=Spec.model_validate(MATCHED), holds=holds if len(calls) == 1 else [],
                               sources={"명세": "llm"})

    monkeypatch.setattr(feed, "LLMSpecMatcher", lambda *args, **kwargs: SimpleNamespace(match=match))
    llm = FakeLLM(ANSWER)
    _, result = run(env, llm)
    assert result["outcome"] == "submitted" and "호랑이" in result["attempts"][0]["problems"][0]
    assert "호랑이" in llm.users[1]


def test_recent_records_and_overlap(env):
    previous = {"prompt": "지난 빛 먼지", "choice": {**{k: ANSWER[k] for k in ("reaction", "medium", "reveal", "input",
                                                                       "reaction_mode", "idle_motion", "mood")},
                                                   "palette": ANSWER["palette"]},
                "difference": "첫 작품", "template": "gpu_particles", "overlaps_recent": False}
    new_job(env, {"outcome": "submitted", "submitted": previous})
    # 3번 모두 거부된 회차: 마지막으로 쓴 답 하나만 그 회차의 기록이 된다
    rejected = [{"answer": {**ANSWER, "prompt": "첫 번째 거부된 답", "reaction": "scatter"}, "problems": ["x"]},
                {"answer": None, "problems": ["LLM 답을 읽지 못함(LLMError)"]},
                {"answer": {**ANSWER, "prompt": "마지막 거부된 답"}, "problems": ["x"]}]
    new_job(env, {"outcome": "exhausted", "attempts": rejected}, error=feed.EXHAUSTED)
    new_job(env, {"outcome": "paused", "reason": "nodes_offline"})   # 디렉터를 부르지 않은 회차는 빠진다
    with Session(env.engine) as session:
        records = feed.recent_records(session)
    assert [r["prompt"] for r in records] == ["마지막 거부된 답", "지난 빛 먼지"]
    assert records[0]["choice"]["family"] == "scene_family" and records[0]["choice"]["reaction"] == "follow"
    llm = FakeLLM(ANSWER)
    _, result = run(env, llm)
    assert "지난 빛 먼지" in llm.users[0] and "마지막 거부된 답" in llm.users[0]
    assert "첫 번째 거부된 답" not in llm.users[0]
    assert '"count": 2' in llm.users[0]   # 거부된 회차도 조합 사용 횟수에 들어간다
    assert result["submitted"]["overlaps_recent"] is True   # 겹쳐도 거부하지 않고 표시만 한다


def test_overlap_counts_rejected_rounds(env):
    rejected = [{"answer": {**ANSWER, "prompt": "거부된 답"}, "problems": ["x"]}]
    new_job(env, {"outcome": "exhausted", "attempts": rejected}, error=feed.EXHAUSTED)
    _, result = run(env, FakeLLM(ANSWER))
    assert result["submitted"]["overlaps_recent"] is True


def test_first_run_without_history(env):
    with Session(env.engine) as session:
        assert feed.recent_records(session) == []
    _, result = run(env, FakeLLM(ANSWER))
    assert result["outcome"] == "submitted"


# ---------- 쉬기 ----------
def test_director_unavailable_pauses_feed(env):
    _, result = run(env, FakeLLM(LLMUnavailable("down")))
    assert result["outcome"] == "paused" and result["reason"] == "gms_unavailable"
    state = feed_state(env)
    assert state.pause_reason == "gms_unavailable" and state.paused_until > 0
    with Session(env.engine) as session:
        assert feed.tick(session, env.settings) is None   # 쉬는 동안 넣지 않는다
        row = session.get(ExhibitionFeed, 1)
        row.paused_until = jobs.now(session) - 1
        session.add(row)
        session.commit()
        assert feed.tick(session, env.settings) is not None   # 쉬는 시간이 지나면 저절로 다시 넣는다


@pytest.mark.parametrize("nodes", [("gen", "a1"), ("ue-review-1",), ("ue-show-1",), ("ue",)])
def test_offline_node_pauses_before_director(env, nodes):
    with Session(env.engine) as session:   # 생성 노드는 한 대라도 켜져 있으면 된다
        heartbeat(session, *nodes, age=ue_support.MAX_HEARTBEAT_AGE + 5)
    llm = FakeLLM(ANSWER)
    _, result = run(env, llm)
    assert result["outcome"] == "paused" and result["reason"] == "nodes_offline" and llm.users == []
    assert feed_state(env).pause_reason == "nodes_offline"


def test_node_rejection_at_submit_pauses_and_keeps_record(env):
    # 시연 노드는 켜져 있지만 ue_publish 등록이 없어 접수가 거부된다
    env.settings.agent_keys_json = json.dumps({**REGISTRY, "ue-show-1": {**REGISTRY["ue-show-1"], "job_kinds": []}})
    _, result = run(env, FakeLLM(ANSWER))
    assert result["outcome"] == "paused" and result["reason"] == "submit_rejected"
    assert result["submitted"]["prompt"] == ANSWER["prompt"]   # 모든 검사를 통과해 보낸 원문은 기록한다


def add_flow(session, request_id, status="planning_background", age=0):
    session.add(HeadWorkflow(request_id=request_id, fingerprint="f", payload={}, provider="gms", model="test",
                             status=status, created_at=jobs.now(session) - age))
    session.commit()
    return session.exec(select(HeadWorkflow).where(HeadWorkflow.request_id == request_id)).one()


def add_decision(session, flow, error):
    row = jobs.enqueue(session, {"request_id": f"head-{flow.id}-{uuid.uuid4().hex}", "kind": "head_decide",
                                 "workflow_id": flow.id, "phase": "background"})
    job = jobs.start(session, row, "worker", 120)
    jobs.finish(session, job.id, "worker", job.token, {}, error)


@pytest.mark.parametrize(("errors", "paused"), [(["LLMUnavailable", "LLMUnavailable"], True),
                                                (["LLMUnavailable"], False),
                                                (["LLMUnavailable", "ValueError"], False)])
def test_head_unavailable_pauses_after_two_failures(env, errors, paused):
    with Session(env.engine) as session:
        flow = add_flow(session, "exhibit-flow")
        for error in errors:
            add_decision(session, flow, error)
        job = feed.tick(session, env.settings)
    assert (job is None) is paused
    assert (feed_state(env).pause_reason == "head_unavailable") is paused


def test_unexpected_feed_failure_pauses_once(env):
    job_id, _ = new_job(env, {}, error="ValueError")
    with Session(env.engine) as session:
        attempt = session.exec(select(jobs.Attempt).where(jobs.Attempt.job_id == job_id)).one()
        attempt.ended_at -= 120   # 2분 전에 실패한 작업
        session.add(attempt)
        row = session.get(ExhibitionFeed, 1)
        row.last_job = job_id
        session.add(row)
        session.commit()
        assert feed.tick(session, env.settings) is None
        assert session.get(ExhibitionFeed, 1).pause_reason == "feed_error"
        row = session.get(ExhibitionFeed, 1)
        row.paused_until = jobs.now(session) - 1
        session.add(row)
        session.commit()
        assert feed.tick(session, env.settings) is not None   # 같은 실패로 다시 쉬지 않는다


# ---------- Dispatcher 자리 세기 ----------
def test_tick_fills_one_at_a_time_up_to_limit(env):
    with Session(env.engine) as session:
        first = feed.tick(session, env.settings)
        assert first.kind == feed.KIND and first.queue == "planning"
        assert feed.tick(session, env.settings) is None   # 디렉터 작업은 한 번에 하나
        job = jobs.start(session, session.get(jobs.Job, first.id), "worker", 120)
        jobs.finish(session, job.id, "worker", job.token, {"outcome": "submitted"})
        flows = [add_flow(session, f"exhibit-{i}") for i in range(10)]
        add_flow(session, "operator-other")   # 사람 요청은 세지 않는다
        assert feed.tick(session, env.settings) is None   # 상한 10
        flows[0].status = "needs_attention"
        session.add(flows[0])
        session.commit()
        assert feed.tick(session, env.settings) is not None   # 멈춘 작품 자리를 바로 채운다


def test_stale_flows_are_not_counted(env):
    with Session(env.engine) as session:
        for i in range(10):
            add_flow(session, f"exhibit-old-{i}", age=env.settings.exhibition_feed_stale_seconds + 10)
        assert feed.tick(session, env.settings) is not None


def test_tick_does_nothing_when_off(env):
    with Session(env.engine) as session:
        feed.toggle(session, env.settings, False)
        assert feed.tick(session, env.settings) is None
        assert session.exec(select(jobs.Job).where(jobs.Job.kind == feed.KIND)).all() == []


# ---------- Worker · API ----------
def test_feed_llm_uses_gms_without_fallback():
    from central import worker

    settings = Settings(llm_provider="gms", llm_fallback="ollama", gms_key="k" * 20, director_model="director-x")
    llm = worker.feed_llm(settings)
    assert isinstance(llm, GMSLLM) and llm.model == "director-x"


def test_worker_and_dispatcher_run_feed_jobs(tmp_path, monkeypatch):
    from central import worker

    settings = Settings(data_dir=tmp_path, operator_token="operator", agent_keys_json=json.dumps(REGISTRY))
    with TestClient(create_app(settings), headers={"X-Operator-Token": "operator"}) as api:
        monkeypatch.setattr(worker, "engine", api.app.state.engine)
        monkeypatch.setattr(worker, "settings", settings)
        sent = []
        monkeypatch.setattr(worker.celery, "send_task", lambda *a, **k: sent.append(k["args"][0]))
        assert api.put("/infra/exhibition/feed", json={"enabled": True}).status_code == 200
        worker.dispatch_once()
        assert len(sent) == 1
        monkeypatch.setattr(worker, "feed_llm", lambda settings: None)
        monkeypatch.setattr(worker.exhibition_feed, "run", lambda *args: {"outcome": "exhausted", "attempts": []})
        worker.execute.run(sent[0])
        with Session(api.app.state.engine) as session:
            job = session.get(jobs.Job, sent[0])
        assert job.status == "failed" and job.error == feed.EXHAUSTED and job.result["outcome"] == "exhausted"
        worker.dispatch_once()   # 다시 쓰기 소진으로는 쉬지 않고 다음 작업을 넣는다
        assert len(sent) == 2


def test_feed_api_requires_operator_and_toggles(tmp_path):
    settings = Settings(data_dir=tmp_path, operator_token="operator")
    with TestClient(create_app(settings)) as api:
        assert api.get("/infra/exhibition/feed").status_code == 401
        assert api.put("/infra/exhibition/feed", json={"enabled": True}).status_code == 401
        headers = {"X-Operator-Token": "operator"}
        state = api.get("/infra/exhibition/feed", headers=headers).json()
        assert state["enabled"] is False and state["max_in_flight"] == 10 and state["recent"] == []
        state = api.put("/infra/exhibition/feed", json={"enabled": True}, headers=headers).json()
        assert state["enabled"] is True and state["paused"] is False and state["in_flight"] == 0
        assert api.put("/infra/exhibition/feed", json={"enabled": "yes", "x": 1}, headers=headers).status_code == 422
        assert api.put("/infra/exhibition/feed", json={"enabled": False}, headers=headers).json()["enabled"] is False
