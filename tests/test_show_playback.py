"""실제 중앙 HTTP·파일과 시험 자식 Runtime. GPU·LED 또는 생성 AI는 호출하지 않는다."""
import copy
import json
import sys
from pathlib import Path

import pytest
from sqlmodel import Session
from tests.test_runtime_kit import kit
from tests.test_runtime_pipeline import runtime_client, verified  # noqa: F401
from tests.test_server_exchange import assembly_request, claim, finish, headers, put, queue
from tests.test_show_supervisor import manager, start  # noqa: F401

from db import jobs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
from show_playback import PlaylistPlayer, validated


def second_verified(client, scene=b'{}'):
    bg = queue(client, "bg-second")
    request = assembly_request(bg)
    request["request_id"] = "ue-second"
    client.post("/infra/jobs", json=request).raise_for_status()
    gen = claim(client, "gen").json()
    finish(client, gen, "gen", [put(client, gen, "gen", "background.png", b"png")]).raise_for_status()
    source = claim(client, "ue").json()
    finish(client, source, "ue", [put(client, source, "ue", "scene.json", scene),
                                 put(client, source, "ue", "assembly-report.json", b'{"errors":[]}')]).raise_for_status()
    check = claim(client, "ue-review-1").json()
    from central.runtime_flow import bundle_identity
    with Session(client.app.state.engine) as session:
        sha = bundle_identity(session.get(jobs.Job, source["id"]))
    capture = put(client, check, "ue-review-1", "captures/rest.png", b"capture")
    report = {"version": "ue-" + source["id"], "source_job_id": source["id"], "source_bundle_sha256": sha,
              "verdict": "pass", "passed": True, "needs_review": False, "checks": [{"passed": True}],
              "bundle": {"sha256": sha}, "screenshots": {"rest": capture["name"]}}
    ref = put(client, check, "ue-review-1", "runtime-report.json", json.dumps(report).encode())
    finish(client, check, "ue-review-1", [capture, ref]).raise_for_status()
    return source, check


def save_plan(client, ids, revision=0, duration=120, **options):
    plan = {"autoplay": True, "duration_seconds": duration, "loop": True,
            "items": [{"verification_job_id": key, "repeat": 1, "enabled": True} for key in ids], **options}
    response = client.put("/infra/show-dashboard/show", json={"expected_revision": revision, "plan": plan})
    assert response.status_code == 200, response.text
    return response.json()


def test_agent_only_downloads_own_approved_playlist(runtime_client):  # noqa: F811
    _, check = verified(runtime_client, "pass")
    save_plan(runtime_client, [check["id"]])
    snapshot = runtime_client.get("/infra/agent/show-playlist", headers=headers("show"))
    assert snapshot.status_code == 200
    value = snapshot.json()
    assert value["revision"] == 1 and value["plan"]["duration_seconds"] == 120
    item = value["entries"][0]
    url = f"/infra/agent/show-playlist/{check['id']}/files/{item['files'][0]['sha256']}"
    assert runtime_client.get(url, headers=headers("show")).status_code == 200
    assert runtime_client.get(url, headers=headers("wrong")).status_code == 404
    assert runtime_client.get(url, headers=headers("gen")).status_code == 403
    assert runtime_client.get(url).status_code == 401
    assert runtime_client.get(url[:-64] + "0" * 64, headers=headers("show")).status_code == 404
    save_plan(runtime_client, [], revision=1)
    assert runtime_client.get(url, headers=headers("show")).status_code == 404


def test_rejected_entry_does_not_block_stop_configuration_sync(runtime_client):  # noqa: F811
    _, check = verified(runtime_client, "pass")
    save_plan(runtime_client, [check["id"]])
    with Session(runtime_client.app.state.engine) as session:
        row = session.get(jobs.Job, check["id"])
        row.result = {**row.result, "human_review": {"request": {"decision": "reject"}}}
        session.add(row)
        session.commit()
    value = runtime_client.get("/infra/agent/show-playlist", headers=headers("show")).json()
    assert value["entries"][0]["error"] == "artwork_not_approved"
    save_plan(runtime_client, [check["id"]], revision=1, autoplay=False)
    assert runtime_client.get("/infra/agent/show-playlist", headers=headers("show")).json()["plan"]["autoplay"] is False


def test_history_search_pages_past_two_hundred_ineligible_jobs(runtime_client):  # noqa: F811
    source, check = verified(runtime_client, "pass")
    with Session(runtime_client.app.state.engine) as session:
        original = session.get(jobs.Job, source["id"]).model_dump()
        verification = session.get(jobs.Job, check["id"]).model_dump()
        for index in range(56):
            key = f"historic-{index:03d}"
            src = {**original, "id": key, "request_id": key, "created_at": index,
                   "result": {**original["result"], "title": f"과거 작품 {index:03d}"}}
            session.add(jobs.Job(**src))
            row = {**verification, "id": "check-" + key, "request_id": "check-" + key, "created_at": index,
                   "payload": {**verification["payload"], "runtime": {**verification["payload"]["runtime"], "source_job_id": key}}}
            session.add(jobs.Job(**row))
        for index in range(210):
            key = f"rejected-{index}"
            session.add(jobs.Job(**{**verification, "id": key, "request_id": key, "created_at": 1000 + index,
                                   "result": {**verification["result"], "verdict": "fail"}}))
        session.commit()
    page = runtime_client.get("/infra/show-dashboard").json()
    assert len(page["library"]) == 50 and page["library_has_more"]
    later = runtime_client.get("/infra/show-dashboard?library_offset=50").json()
    assert len(later["library"]) == 7 and not later["library_has_more"]
    found = runtime_client.get("/infra/show-dashboard", params={"search": "과거 작품 000"}).json()
    assert len(found["library"]) == 1 and found["library"][0]["title"] == "과거 작품 000"


def player_with_two(runtime_client, tmp_path, manager=None, scene=b'{}'):  # noqa: F811
    first_source, first = verified(runtime_client, "pass")
    second_source, second = second_verified(runtime_client, scene)
    save_plan(runtime_client, [first["id"], second["id"]])
    client = kit(runtime_client, "show", tmp_path / "player-kit")
    config = manager.config if manager else {"mode": "show", "delivery": str(tmp_path / "delivery"),
                                            "cache": str(tmp_path / "cache"), "runtime_pid": 123}
    clock = [0.0]
    player = PlaylistPlayer(client, config, manager, clock=lambda: clock[0])
    player.sync()
    return player, clock, first_source, second_source, first, second


def test_120_second_deadline_waits_for_files_and_setting_is_latched(runtime_client, tmp_path):  # noqa: F811
    player, clock, first_source, _, first, second = player_with_two(runtime_client, tmp_path)
    status = {"phase": "Applied", "runtime_pid": 123, "runtime_session": "one",
              "active_version": "ue-" + first_source["id"]}
    player.observed = lambda: status
    switches = []

    def switch(entry, record):
        switches.append(entry["verification_job_id"])
        status["active_version"] = record["version"]
        return status

    player.switch = switch
    player.prepare_entry(player.value["entries"][0])
    player.tick()
    clock[0] = 119
    player.tick()
    assert switches == []
    save_plan(runtime_client, [first["id"], second["id"]], revision=1, duration=180)
    player.sync()
    clock[0] = 120
    player.tick()
    assert switches == [] and player.state == "WaitingForNext" and player.duration == 120
    player.prepare_entry(player.value["entries"][1])
    player.tick()
    assert switches == [second["id"]] and player.duration == 180 and player.started == 120
    clock[0] = 299
    player.tick()
    assert len(switches) == 1
    clock[0] = 300
    player.tick()
    assert switches == [second["id"], first["id"]]
    save_plan(runtime_client, [], revision=2, autoplay=False)
    player.sync()
    player.tick()
    assert player.state == "Disabled" and len(switches) == 2


def test_offline_cached_playlist_changes_real_child_runtime_once_per_segment(runtime_client, tmp_path, manager):  # noqa: F811
    pid = start(manager)
    session = manager.snapshot()["runtime_session"]
    player, clock, first_source, second_source, _, _ = player_with_two(runtime_client, tmp_path, manager)
    for entry in player.value["entries"]:
        player.prepare_entry(entry)
    player.tick()
    player.client.call = lambda *a, **kw: (_ for _ in ()).throw(OSError("offline"))
    with pytest.raises(OSError):
        player.sync()
    clock[0] = 120
    player.tick()
    assert manager.snapshot()["active_version"] == "ue-" + first_source["id"]
    clock[0] = 240
    player.tick()
    assert manager.snapshot()["active_version"] == "ue-" + second_source["id"]
    assert player.state == "Playing" and manager.owner["pid"] == pid
    assert manager.snapshot()["runtime_session"] == session
    # Agent 재시작은 구성 파일과 이미 검증한 로컬 버전을 사용하며 남은 시간을 추측하지 않는다.
    restored = PlaylistPlayer(player.client, player.config, manager, clock=lambda: clock[0])
    for entry in restored.value["entries"]:
        restored.prepare_entry(entry)
    restored.tick()
    assert restored.current == player.current and restored.started == 240
    playback = restored.report()
    assert set(playback["cached_verification_job_ids"]) == set(restored.prepared)
    assert playback["current_repeat"] >= 1 and playback["cache_errors"] == {}
    response = runtime_client.post("/infra/agent/heartbeat", headers=headers("show"),
                                   json={**manager.snapshot(), "playback": playback})
    assert response.status_code == 200, response.text
    value = runtime_client.get("/infra/show-dashboard").json()
    assert next(node for node in value["nodes"] if node["node_id"] == "show")["playback"]["revision"] == 1


def test_preparation_corruption_and_runtime_failure_preserve_last_good(runtime_client, tmp_path, manager):  # noqa: F811
    start(manager)
    player, clock, _, _, _, _ = player_with_two(runtime_client, tmp_path, manager, b'{"hang":true}')
    for entry in player.value["entries"]:
        player.prepare_entry(entry)
    player.tick()
    clock[0] = 120
    player.tick()
    good = manager.snapshot()["active_version"]
    # 다음 작품이 실제 적용되지 않으면 성공·새 시간 구간으로 처리하지 않는다.
    clock[0] = 240
    player.tick()
    from tests.test_show_supervisor import wait_for
    wait_for(lambda: manager.ready)
    assert manager.snapshot()["active_version"] == good and player.state == "WaitingForNext"
    assert player.started == 120
    record = player.value["entries"][1]
    (player.delivery / "versions" / record["runtime_version"] / "scene.json").write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        player.prepare_entry(record)
    assert manager.snapshot()["active_version"] == good


def test_snapshot_rejects_path_escape_and_other_node(runtime_client, tmp_path):  # noqa: F811
    player, _, _, _, _, _ = player_with_two(runtime_client, tmp_path)
    for mutate in (lambda v: v.update(node_id="wrong"),
                   lambda v: v["entries"][0]["files"][0].update(name="../escape"),
                   lambda v: v["plan"].update(duration_seconds=True)):
        value = copy.deepcopy(player.value)
        mutate(value)
        with pytest.raises(ValueError):
            validated(value, "show")


def test_repeat_finite_queue_removal_and_empty_keep_current(runtime_client, tmp_path):  # noqa: F811
    player, clock, first_source, _, first, second = player_with_two(runtime_client, tmp_path)
    status = {"phase": "Applied", "runtime_pid": 123, "runtime_session": "one",
              "active_version": "ue-" + first_source["id"]}
    player.observed = lambda: status
    switches = []

    def switch(entry, record):
        switches.append(entry["verification_job_id"])
        status["active_version"] = record["version"]
        return status

    player.switch = switch
    value = copy.deepcopy(player.value)
    value["revision"] = 2
    value["plan"]["loop"] = False
    value["plan"]["items"][0]["repeat"] = 2
    player.accept(value)
    for entry in value["entries"]:
        player.prepare_entry(entry)
    player.tick()
    clock[0] = 120
    player.tick()
    assert switches == [] and player.repetition == 2  # 같은 작품의 다음 구간은 UE 재게시 없음
    clock[0] = 240
    player.tick()
    assert switches == [second["id"]]
    clock[0] = 360
    player.tick()
    assert player.state == "Completed" and switches == [second["id"]]
    value["revision"] = 3
    value["plan"]["items"] = [value["plan"]["items"][0]]
    value["entries"] = [value["entries"][0]]
    player.accept(value)
    player.tick()
    assert switches == [second["id"], first["id"]]  # 제거된 현재 작품의 구간은 만료된 뒤 대체
    value["revision"] = 4
    value["plan"]["items"], value["entries"] = [], []
    player.accept(value)
    player.tick()
    assert player.state == "Empty" and len(switches) == 2
