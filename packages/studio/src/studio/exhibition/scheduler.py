"""검토용 순수 재생 정책. 생산 API·UE·DB에 연결하지 않는 결정론적 프로토타입."""
import math
from copy import deepcopy
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Policy:
    recent_seconds: float = 7200
    recent_share: float = .3
    avoid_recent: int = 1
    failure_limit: int = 3
    cooldown_seconds: float = 60

    def __post_init__(self):
        if (not math.isfinite(self.recent_seconds) or self.recent_seconds <= 0
                or not math.isfinite(self.recent_share) or not 0 <= self.recent_share <= 1
                or type(self.avoid_recent) is not int or not 0 <= self.avoid_recent <= 100
                or type(self.failure_limit) is not int or self.failure_limit < 1
                or not math.isfinite(self.cooldown_seconds) or self.cooldown_seconds < 0):
            raise ValueError("invalid scheduling policy")


@dataclass
class Artwork:
    artwork_id: str
    created_at: float
    validated_at: float
    duration_seconds: float = 120
    title: str = ""
    enabled: bool = True
    cached: bool = True
    play_count: int = 0
    attempt_count: int = 0
    started_count: int = 0
    first_played_at: float | None = None
    last_played_at: float | None = None
    last_play_result: str = ""
    failure_count: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0


@dataclass(frozen=True)
class Selection:
    artwork_id: str
    group: str
    reason: str
    exclusion_relaxed: bool


class Scheduler:
    def __init__(self, policy=None):
        self.policy = policy or Policy()
        self.artworks: dict[str, Artwork] = {}
        self.plays: dict[str, dict] = {}
        self.history: list[str] = []
        self.active: str | None = None
        self.mixed_counts = {"recent": 0, "existing": 0}

    def register(self, artwork):
        """검증·승인·캐시 이벤트를 모사한다. 같은 ID로 메타데이터를 바꿀 수 없다."""
        if (not artwork.artwork_id or not all(math.isfinite(x) for x in
                (artwork.created_at, artwork.validated_at, artwork.duration_seconds))
                or artwork.created_at > artwork.validated_at or artwork.duration_seconds <= 0):
            raise ValueError("invalid artwork metadata")
        existing = self.artworks.get(artwork.artwork_id)
        if existing:
            immutable = ("created_at", "validated_at", "duration_seconds", "title")
            if any(getattr(existing, key) != getattr(artwork, key) for key in immutable):
                raise ValueError("immutable artwork registration changed")
            return existing
        self.artworks[artwork.artwork_id] = artwork
        return artwork

    def group(self, artwork, now):
        return "recent" if 0 <= now - artwork.validated_at < self.policy.recent_seconds else "existing"

    def select(self, now):
        if not math.isfinite(now):
            raise ValueError("invalid selection time")
        if self.active:
            raise ValueError("a play is already in progress")
        eligible = [a for a in self.artworks.values() if a.enabled and a.cached and
                    a.validated_at <= now and a.cooldown_until <= now]
        if not eligible:
            return None
        # Always try avoiding the previous artwork, even when the configurable wider window is zero.
        depth = min(max(1, self.policy.avoid_recent), len(self.history))
        pool = []
        for count in range(depth, -1, -1):
            excluded = set(self.history[-count:]) if count else set()
            pool = [a for a in eligible if a.artwork_id not in excluded]
            if pool:
                break
        relaxed = count < depth
        unseen = [a for a in pool if a.first_played_at is None]
        if unseen:
            chosen = min(unseen, key=lambda a: (a.validated_at, a.created_at, a.artwork_id))
            return Selection(chosen.artwork_id, self.group(chosen, now), "first_play", relaxed)
        total = sum(self.mixed_counts.values())
        wanted = "recent" if self.mixed_counts["recent"] < (total + 1) * self.policy.recent_share else "existing"
        candidates = [a for a in pool if self.group(a, now) == wanted]
        reason = "weighted" if candidates else "group_fallback"
        candidates = candidates or pool
        chosen = min(candidates, key=lambda a: (a.play_count,
                     a.last_played_at if a.last_played_at is not None else -math.inf, a.created_at, a.artwork_id))
        return Selection(chosen.artwork_id, self.group(chosen, now), reason, relaxed)

    def start(self, selection, play_id, now):
        if not math.isfinite(now):
            raise ValueError("invalid selection time")
        if play_id in self.plays:
            previous = self.plays[play_id]
            if previous["selection"] != asdict(selection) or previous["selected_at"] != now:
                raise ValueError("conflicting play id")
            return False
        if self.active or not play_id or self.select(now) != selection:
            raise ValueError("stale or invalid selection")
        self.artworks[selection.artwork_id].attempt_count += 1
        self.plays[play_id] = {"selection": asdict(selection), "selected_at": now, "phase": "LOADING"}
        self.active = play_id
        return True

    def applied(self, play_id, now, runtime_session):
        if not math.isfinite(now):
            raise ValueError("invalid start time")
        play = self.plays[play_id]
        if "started_at" in play:
            if play["started_at"] != now or play["runtime_session"] != runtime_session:
                raise ValueError("conflicting start event")
            return False
        if self.active != play_id or play["phase"] != "LOADING" or not runtime_session or now < play["selected_at"]:
            raise ValueError("unconfirmed runtime start")
        artwork = self.artworks[play["selection"]["artwork_id"]]
        play.update(phase="PLAYING", started_at=now, runtime_session=runtime_session)
        artwork.started_count += 1
        artwork.first_played_at = now if artwork.first_played_at is None else artwork.first_played_at
        artwork.last_played_at = now
        self.history.append(artwork.artwork_id)
        return True

    def complete(self, play_id, now, *, confirmation):
        """경과 시간만으로 성공 처리하지 않는다. 호출자가 별도로 확인한 완료 근거를 요구한다."""
        if not math.isfinite(now):
            raise ValueError("invalid completion time")
        play = self.plays[play_id]
        if play["phase"] == "FINISHED":
            if (now, confirmation) != (play["ended_at"], play["confirmation"]):
                raise ValueError("conflicting completion event")
            return False
        if (self.active != play_id or play["phase"] != "PLAYING" or now < play["started_at"]
                or confirmation not in ("runtime_finished", "simulated_runtime_finished")):
            raise ValueError("unconfirmed completion")
        artwork = self.artworks[play["selection"]["artwork_id"]]
        artwork.play_count += 1
        artwork.consecutive_failures, artwork.last_play_result = 0, "finished"
        if play["selection"]["reason"] != "first_play":
            self.mixed_counts[play["selection"]["group"]] += 1
        play.update(phase="FINISHED", ended_at=now, confirmation=confirmation)
        self.active = None
        return True

    def fail(self, play_id, now, reason):
        if not math.isfinite(now):
            raise ValueError("invalid failure time")
        play = self.plays[play_id]
        if play["phase"] == "ERROR":
            if (play["ended_at"], play["error"]) != (now, reason):
                raise ValueError("conflicting failure event")
            return False
        if self.active != play_id or now < play.get("started_at", play["selected_at"]) or not reason:
            raise ValueError("invalid failure event")
        artwork = self.artworks[play["selection"]["artwork_id"]]
        artwork.failure_count += 1
        artwork.consecutive_failures += 1
        artwork.cooldown_until = now + self.policy.cooldown_seconds
        artwork.last_play_result = "failed"
        if artwork.consecutive_failures >= self.policy.failure_limit:
            artwork.enabled = False
        play.update(phase="ERROR", ended_at=now, error=reason)
        self.active = None
        return True

    def snapshot(self):
        return deepcopy({"schema": 1, "policy": asdict(self.policy), "artworks": [asdict(a) for a in self.artworks.values()],
                         "plays": self.plays, "history": self.history, "active": self.active,
                         "mixed_counts": self.mixed_counts})

    @classmethod
    def restore(cls, value):
        value = deepcopy(value)
        if value.get("schema") != 1:
            raise ValueError("unsupported scheduler snapshot")
        result = cls(Policy(**value["policy"]))
        for entry in value["artworks"]:
            result.register(Artwork(**entry))
        result.plays, result.history = value["plays"], value["history"]
        result.active, result.mixed_counts = value["active"], value["mixed_counts"]
        pending = [key for key, play in result.plays.items() if play["phase"] in ("LOADING", "PLAYING")]
        if pending != ([result.active] if result.active else []):
            raise ValueError("invalid active play")
        if any(key not in result.artworks for key in result.history):
            raise ValueError("unknown artwork in playback history")
        mixed = {"recent": 0, "existing": 0}
        for artwork_id, artwork in result.artworks.items():
            ledger = [p for p in result.plays.values() if p["selection"]["artwork_id"] == artwork_id]
            counts = (len(ledger), sum("started_at" in p for p in ledger),
                      sum(p["phase"] == "FINISHED" for p in ledger), sum(p["phase"] == "ERROR" for p in ledger))
            if counts != (artwork.attempt_count, artwork.started_count, artwork.play_count, artwork.failure_count):
                raise ValueError("playback counters do not match event ledger")
        for play in result.plays.values():
            selection = Selection(**play["selection"])
            if (selection.artwork_id not in result.artworks or selection.group not in mixed
                    or play["phase"] not in ("LOADING", "PLAYING", "FINISHED", "ERROR")):
                raise ValueError("invalid play ledger")
            if play["phase"] == "FINISHED" and selection.reason != "first_play":
                mixed[selection.group] += 1
        if mixed != result.mixed_counts:
            raise ValueError("allocation counters do not match event ledger")
        return result
