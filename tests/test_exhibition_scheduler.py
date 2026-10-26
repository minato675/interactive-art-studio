"""생산 API와 분리된 정책 검토: 빈 시작, 실제 시작/완료 구분, 오프라인·복구 가정."""
import json
from dataclasses import replace

import pytest

from studio.exhibition.scheduler import Artwork, Policy, Scheduler
from studio.exhibition.simulation import scenarios, simulate


def finish_next(scheduler, now, play_id=None):
    selection = scheduler.select(now)
    play_id = play_id or f"play-{len(scheduler.plays)}"
    scheduler.start(selection, play_id, now)
    scheduler.applied(play_id, now, "runtime-session")
    scheduler.complete(play_id, now + 1, confirmation="simulated_runtime_finished")
    return selection


def test_empty_start_adds_only_ready_new_work_without_seed_or_capacity_limit():
    scheduler = Scheduler()
    assert scheduler.select(0) is None
    scheduler.register(Artwork("new", 0, 1200, cached=False))
    assert scheduler.select(1200) is None
    scheduler.artworks["new"].cached = True
    assert scheduler.select(1199) is None
    assert scheduler.select(1200).artwork_id == "new"
    for number in range(30):
        scheduler.register(Artwork(f"later-{number}", 1200, 1300))
    assert len(scheduler.artworks) == 31
    assert scheduler.register(Artwork("new", 0, 1200)) is scheduler.artworks["new"]
    with pytest.raises(ValueError, match="immutable"):
        scheduler.register(Artwork("new", 0, 1201))


def test_first_presentation_fifo_and_three_distinct_counters():
    scheduler = Scheduler()
    scheduler.register(Artwork("later", 0, 10))
    scheduler.register(Artwork("earlier", 0, 5))
    selection = scheduler.select(20)
    assert selection.artwork_id == "earlier" and selection.reason == "first_play"
    scheduler.start(selection, "one", 20)
    a = scheduler.artworks["earlier"]
    assert (a.attempt_count, a.started_count, a.play_count) == (1, 0, 0)
    with pytest.raises(ValueError, match="unconfirmed"):
        scheduler.complete("one", 140, confirmation="timer_elapsed")
    scheduler.applied("one", 22, "session")
    assert (a.attempt_count, a.started_count, a.play_count) == (1, 1, 0)
    assert a.first_played_at == 22
    with pytest.raises(ValueError, match="unconfirmed"):
        scheduler.complete("one", 142, confirmation="timer_elapsed")
    scheduler.complete("one", 142, confirmation="runtime_finished")
    assert a.play_count == 1 and scheduler.select(142).artwork_id == "later"


def test_duplicate_events_are_idempotent_and_conflicts_rejected():
    scheduler = Scheduler()
    scheduler.register(Artwork("one", 0, 0))
    choice = scheduler.select(0)
    assert scheduler.start(choice, "p", 0)
    assert scheduler.start(choice, "p", 0) is False
    assert scheduler.applied("p", 1, "session")
    assert scheduler.applied("p", 1, "session") is False
    with pytest.raises(ValueError, match="conflicting start"):
        scheduler.applied("p", 1, "other-session")
    assert scheduler.complete("p", 121, confirmation="runtime_finished")
    assert scheduler.complete("p", 121, confirmation="runtime_finished") is False
    assert scheduler.artworks["one"].play_count == 1
    with pytest.raises(ValueError, match="conflicting completion"):
        scheduler.complete("p", 122, confirmation="runtime_finished")
    with pytest.raises(ValueError, match="conflicting play"):
        scheduler.start(replace(choice, artwork_id="other"), "p", 0)


def test_weighted_completed_slots_are_30_70_excluding_forced_first_plays():
    scheduler = Scheduler(Policy(recent_seconds=1000))
    for index in range(3):
        scheduler.register(Artwork(f"r{index}", -1, 0))
        scheduler.register(Artwork(f"e{index}", -3000, -2000))
    selections = [finish_next(scheduler, now * 2) for now in range(106)]
    assert sum(s.reason == "first_play" for s in selections) == 6
    assert scheduler.mixed_counts == {"recent": 30, "existing": 70}
    assert all(a != b for a, b in zip(scheduler.history, scheduler.history[1:]))
    for prefix in ("r", "e"):
        counts = [a.play_count for key, a in scheduler.artworks.items() if key.startswith(prefix)]
        assert max(counts) - min(counts) <= 1


def test_recent_window_is_disjoint_and_two_hours():
    scheduler = Scheduler()
    artwork = Artwork("one", 0, 10)
    assert scheduler.group(artwork, 7209) == "recent"
    assert scheduler.group(artwork, 7210) == "existing"


def test_insufficient_groups_and_history_relaxation_keep_available_work_playing():
    scheduler = Scheduler(Policy(avoid_recent=10))
    scheduler.register(Artwork("one", 0, 0))
    first = finish_next(scheduler, 0)
    next_choice = finish_next(scheduler, 2)
    assert first.reason == "first_play"
    assert next_choice.exclusion_relaxed
    scheduler.register(Artwork("two", 0, 0))
    assert finish_next(scheduler, 4).artwork_id == "two"
    assert finish_next(scheduler, 6).artwork_id == "one"
    assert scheduler.mixed_counts["existing"] == 0


def test_failed_load_stays_unseen_cools_down_then_disables_without_counting_success():
    scheduler = Scheduler(Policy(cooldown_seconds=10, failure_limit=2))
    scheduler.register(Artwork("bad", 0, 0))
    scheduler.start(scheduler.select(0), "failure1", 0)
    assert scheduler.fail("failure1", 0, "load_error")
    assert scheduler.fail("failure1", 0, "load_error") is False
    assert scheduler.select(9) is None
    selection = scheduler.select(10)
    assert selection.reason == "first_play"
    scheduler.start(selection, "failure2", 10)
    scheduler.fail("failure2", 10, "load_error")
    artwork = scheduler.artworks["bad"]
    assert not artwork.enabled and artwork.failure_count == 2
    assert artwork.first_played_at is None and artwork.play_count == 0
    assert scheduler.select(100) is None


def test_snapshot_round_trip_does_not_finish_inflight_and_does_not_alias():
    scheduler = Scheduler()
    scheduler.register(Artwork("one", 0, 0))
    finish_next(scheduler, 0)
    scheduler.start(scheduler.select(2), "inflight", 2)
    scheduler.applied("inflight", 3, "session")
    snapshot = json.loads(json.dumps(scheduler.snapshot()))
    restored = Scheduler.restore(snapshot)
    assert restored.active == "inflight" and restored.artworks["one"].play_count == 1
    with pytest.raises(ValueError, match="in progress"):
        restored.select(999)
    restored.complete("inflight", 123, confirmation="runtime_finished")
    assert snapshot["plays"]["inflight"]["phase"] == "PLAYING"
    assert scheduler.plays["inflight"]["phase"] == "PLAYING"
    restored_snapshot = restored.snapshot()
    restored_snapshot["history"].clear()
    assert restored.history
    snapshot["artworks"][0]["play_count"] = 900
    with pytest.raises(ValueError, match="counters"):
        Scheduler.restore(snapshot)


@pytest.mark.parametrize("now", [float("nan"), float("inf")])
def test_nonfinite_clock_is_rejected(now):
    with pytest.raises(ValueError, match="time"):
        Scheduler().select(now)


def test_empty_start_simulation_waits_then_accumulates_new_works():
    result = simulate()
    assert result["initial_artworks"] == 0
    assert result["waiting_seconds"] == 1200
    assert result["successful_plays"] == 230
    assert result["total_artworks"] == 24
    assert result["never_started_artworks"] == ["new-24"]
    assert result["plays"]["sim-play-0001"]["selected_at"] == 1200


def test_comparison_and_failure_scenarios_are_explicitly_simulated():
    results = scenarios()
    baseline = results["initial_20"]
    assert baseline["total_artworks"] == 44 and baseline["successful_plays"] == 240
    assert baseline["never_started_artworks"] == ["new-24"]
    assert results["initial_20_loading_3s"]["successful_plays"] < 240
    offline = results["offline_60_to_180m"]
    assert offline["successful_plays"] == 240 and offline["waiting_seconds"] == 0
    assert all(p["started_at"] >= 10800 for p in offline["plays"].values()
               if p["selection"]["artwork_id"] == "new-03")
    assert results["runtime_failures"]["failed_attempts"] > 0
    restart = results["controller_restart"]
    assert restart["controller_restarted"] and restart["restart_restored_completed"] == 100
    assert restart["play_counts"] == baseline["play_counts"]
    assert results["no_new_artworks"]["total_artworks"] == 20
    assert results["no_new_artworks"]["successful_plays"] == 240
    assert all(value["simulation_only"] for value in results.values())
