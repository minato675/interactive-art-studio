"""가상 시간으로 재생 정책을 비교한다. 실제 AI·UE·생산 DB/API를 호출하지 않는다."""
import json
import math
from dataclasses import asdict

from .scheduler import Artwork, Scheduler


def simulate(*, initial_artworks=0, minutes=480, generation_minutes=20, duration_seconds=120,
             load_seconds=0, failed_generations=(), fail_every=0, offline=(), restart_after=None, policy=None):
    if (not all(math.isfinite(x) for x in (minutes, generation_minutes, duration_seconds, load_seconds))
            or type(initial_artworks) is not int or initial_artworks < 0 or minutes <= 0 or generation_minutes <= 0
            or duration_seconds <= 0 or load_seconds < 0 or fail_every < 0
            or any(start < 0 or end <= start for start, end in offline)):
        raise ValueError("invalid simulation scenario")
    scheduler = Scheduler(policy)
    central = {}
    for index in range(initial_artworks):
        artwork = Artwork(f"initial-{index + 1:02d}", -86400 - index, -7201 - index,
                          duration_seconds=duration_seconds, title=f"사전 작품 {index + 1}")
        central[artwork.artwork_id] = artwork
        scheduler.register(Artwork(**asdict(artwork)))
    horizon, interval = minutes * 60, generation_minutes * 60
    arrivals = [(number, number * interval) for number in range(1, int(horizon // interval) + 1)]
    cursor, now, waiting, holding = 0, 0.0, 0.0, 0.0
    restarted, restored_count = False, 0
    last_good = None

    def online(at):
        return not any(start <= at < end for start, end in offline)

    def ingest(at):
        nonlocal cursor
        while cursor < len(arrivals) and arrivals[cursor][1] <= at:
            number, arrival = arrivals[cursor]
            cursor += 1
            if number not in failed_generations:
                artwork = Artwork(f"new-{number:02d}", arrival - interval, arrival,
                                  duration_seconds=duration_seconds, title=f"신규 작품 {number}")
                central[artwork.artwork_id] = artwork
        if online(at):
            for artwork in central.values():
                scheduler.register(Artwork(**asdict(artwork)))

    while now < horizon:
        ingest(now)
        if restart_after is not None and len(scheduler.plays) >= restart_after and not restarted:
            scheduler = Scheduler.restore(json.loads(json.dumps(scheduler.snapshot())))
            restarted, restored_count = True, sum(a.play_count for a in scheduler.artworks.values())
        selection = scheduler.select(now)
        if selection is None:
            future = [arrival for _, arrival in arrivals[cursor:] if arrival > now]
            future += [end for _, end in offline if end > now]
            future += [a.cooldown_until for a in scheduler.artworks.values() if a.enabled and a.cooldown_until > now]
            until = min([horizon, *future])
            if last_good:
                holding += until - now
            else:
                waiting += until - now
            now = until
            continue
        play_id = f"sim-play-{len(scheduler.plays) + 1:04d}"
        scheduler.start(selection, play_id, now)
        now += load_seconds
        if now >= horizon:
            break
        if fail_every and len(scheduler.plays) % fail_every == 0:
            scheduler.fail(play_id, now, "simulated_load_failure")
            continue
        scheduler.applied(play_id, now, "simulated-runtime-session")
        ending = now + scheduler.artworks[selection.artwork_id].duration_seconds
        if ending > horizon:
            now = horizon  # 상영 중 종료는 정상 완료 횟수에 넣지 않는다.
            break
        scheduler.complete(play_id, ending, confirmation="simulated_runtime_finished")
        last_good, now = selection.artwork_id, ending
    ingest(horizon)  # 480분 도착 작품도 라이브러리에는 보존하되 8시간 상영으로 세지 않는다.
    finished = [p for p in scheduler.plays.values() if p["phase"] == "FINISHED"]
    failures = [p for p in scheduler.plays.values() if p["phase"] == "ERROR"]
    counts = {key: scheduler.artworks[key].play_count if key in scheduler.artworks else 0 for key in central}
    started = {key for key, artwork in scheduler.artworks.items() if artwork.first_played_at is not None}
    groups = {group: sum(p["selection"]["group"] == group for p in finished) for group in ("recent", "existing")}
    mixed = scheduler.mixed_counts
    return {"simulation_only": True, "initial_artworks": initial_artworks, "new_artworks": len(central) - initial_artworks,
            "total_artworks": len(central), "cached_artworks": len(scheduler.artworks),
            "total_play_attempts": len(scheduler.plays), "successful_plays": len(finished), "failed_attempts": len(failures),
            "never_started_artworks": sorted(set(central) - started),
            "never_completed_artworks": sorted(key for key, count in counts.items() if not count),
            "average_plays": sum(counts.values()) / len(counts) if counts else 0,
            "min_play_count": min(counts.values(), default=0), "max_play_count": max(counts.values(), default=0),
            "consecutive_plays": sum(a == b for a, b in zip(scheduler.history, scheduler.history[1:])),
            "recent_ratio": groups["recent"] / len(finished) if finished else None,
            "weighted_recent_ratio": mixed["recent"] / sum(mixed.values()) if sum(mixed.values()) else None,
            "waiting_seconds": waiting, "holding_last_good_seconds": holding,
            "restart_restored_completed": restored_count, "controller_restarted": restarted,
            "disabled_artworks": sorted(a.artwork_id for a in scheduler.artworks.values() if not a.enabled),
            "play_counts": counts, "policy": asdict(scheduler.policy), "plays": scheduler.plays}


def scenarios():
    return {
        "empty_start": simulate(),
        "initial_20": simulate(initial_artworks=20),
        "initial_20_loading_3s": simulate(initial_artworks=20, load_seconds=3),
        "creation_delays": simulate(initial_artworks=20, failed_generations=(2, 5, 9, 17)),
        "offline_60_to_180m": simulate(initial_artworks=20, offline=((3600, 10800),)),
        "runtime_failures": simulate(initial_artworks=20, fail_every=11, load_seconds=1),
        "controller_restart": simulate(initial_artworks=20, restart_after=100),
        "no_new_artworks": simulate(initial_artworks=20, failed_generations=tuple(range(1, 25))),
    }
