"""전시 자동 순환: AI 디렉터가 쓴 요청을 분산 Head에 계속 접수한다. 설계는 infra/HEAD_EXHIBITION_FEED.md.

- tick(): Dispatcher가 2초마다 부른다. DB만 보고 자리가 있으면 디렉터 작업(KIND) 하나를 넣는다.
- run(): Worker가 디렉터 작업 하나를 처리한다. AI 디렉터 → Input Agent → 명세 덮어쓰기 → 사전 확인 → 접수.
LLM 호출 중에는 DB 잠금을 잡지 않고, 접수 직전에 작업 소유권을 다시 확인한다.
"""
import hashlib
import itertools
import json
import uuid
from collections import Counter
from dataclasses import dataclass
from types import SimpleNamespace

from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from contracts.head import HeadRequest
from contracts.spec import (
    Input,
    Medium,
    Origin,
    PaletteColor,
    Provenance,
    Reaction,
    ReactionMode,
    Spec,
    SpecReveal,
    TemplateFamily,
)
from db import jobs
from db.exhibition_feed import ExhibitionFeed
from db.head import HeadWorkflow

from . import dispatch, ue_support
from .exhibition_director import MAX_ATTEMPTS
from .llm import LLMError, LLMUnavailable
from .spec_matching import LLMSpecMatcher

KIND = "exhibition_feed"
PREFIX = "exhibit-"               # 자동 순환이 넣은 Head 흐름의 request_id 접두어
EXHAUSTED = "director_exhausted"  # 다시 쓰기 소진으로 끝난 디렉터 작업의 error
PROMPT_VERSION = "exhibition-feed-1"
RECENT = 10                       # 지시문에 넣는 최근 디렉터 기록 수
OVERLAP = 3                       # 겹침 표시를 볼 최근 기록 수
DONE = ("completed", "needs_attention")
REVIEW_NODE = "ue-review-1"

# select_template가 템플릿과 상관없이 통과 여부만 가르는 칸(계열별). 계열 · 반응 · 매질은 템플릿을 정하므로 조합으로 본다.
# 비운 값(None)은 디렉터에게 주지 않는다. 복귀 방식 · 화면 깊이는 통과 여부와 무관해 Input Agent 값을 쓴다.
# 흐름 계열은 드러내는 방식이 막힌 칸이라 고르지 않는다(디렉터는 null로 답한다).
_COMMON = {"input": list(Input), "reaction_mode": list(ReactionMode), "idle_motion": list(Reaction)}
FIELDS = {TemplateFamily.scene_family: {"reveal": list(SpecReveal), **_COMMON},
          TemplateFamily.flow_family: _COMMON}
NAMES = list(dict.fromkeys(name for fields in FIELDS.values() for name in fields))   # 모든 계열의 칸

SYSTEM = ("너는 AI 미디어아트 디렉터다. 전시장 화면에 올릴 인터랙티브 작품 한 건의 요청 문장(원문)과 템플릿 선택 값을 정한다. "
          "관람객이 화면 앞에 나타나거나 움직이면 장면이 반응한다. 주어진 선택지 밖의 값은 쓰지 않고, 주어진 JSON 형식으로만 답한다.")


class FeedChoice(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    family: TemplateFamily
    reaction: Reaction
    medium: Medium
    reveal: SpecReveal | None = None   # 흐름 계열은 막힌 칸이라 None
    input: Input
    reaction_mode: ReactionMode
    idle_motion: Reaction
    mood: str = Field(min_length=1, max_length=200)
    palette: list[PaletteColor] = Field(min_length=1, max_length=6)
    difference: str = Field(min_length=1, max_length=500)   # 최근 작품과 다르게 한 점. 코드 검사에는 쓰지 않는다

    def values(self) -> dict:
        """기록 · 겹침 비교에 쓰는 템플릿 선택 칸 값과 분위기 · 색."""
        return {"family": self.family.value, "reaction": self.reaction.value, "medium": self.medium.value,
                "reveal": self.reveal.value if self.reveal else None,
                "input": self.input.value, "reaction_mode": self.reaction_mode.value,
                "idle_motion": self.idle_motion.value, "mood": self.mood,
                "palette": [p.model_dump() for p in self.palette]}


@dataclass(frozen=True)
class FeedRange:
    combos: dict[tuple[str, str, str], str]   # (계열, 반응, 매질) → 템플릿
    fields: dict[str, dict[str, list[str]]]   # 계열 → FIELDS 칸별 허용값

    def as_dict(self) -> dict:
        return {"combos": [{"family": f, "reaction": r, "medium": m, "template": t}
                           for (f, r, m), t in sorted(self.combos.items())],
                "fields": self.fields}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()

    def families(self) -> list[str]:
        return sorted({f for f, _, _ in self.combos})

    def media(self) -> dict[str, dict[str, list[str]]]:
        """계열 · 반응마다 쓸 수 있는 매질. 디렉터 지시문의 조합 규칙 요약."""
        out: dict[str, dict[str, list[str]]] = {}
        for family, reaction, medium in sorted(self.combos):
            out.setdefault(family, {}).setdefault(reaction, []).append(medium)
        return out


# ---------- 범위 계산 (설계 5.3) ----------
def probe(values: dict, table: dict) -> str | None:
    """시험용 명세로 select_template를 불러 고른 템플릿을 돌려준다. 통과하지 못하면 None.
    흐름 계열은 막힌 칸(드러내는 방식 · 화면 깊이)과 선택 칸인 배경을 비운다."""
    family = TemplateFamily(values.get("family", TemplateFamily.scene_family))
    content = {"theme": "범위 확인", "mood": "범위 확인", "medium": values["medium"]}
    if family is TemplateFamily.scene_family:
        content.update(background="범위 확인", dimension=values.get("dimension", "2.5d"), reveal=values["reveal"])
    try:
        spec = Spec.model_validate({
            "template_family": family, "raw_prompt": "범위 확인", "content": content,
            "idle": {"motion": values["idle_motion"]},
            "interactions": [{"input": values["input"], "reaction": values["reaction"],
                              "reaction_mode": values["reaction_mode"], "return": values.get("return", "fade")}]})
        return ue_support.select_template(SimpleNamespace(spec=spec), table)["template"]
    except (jobs.Conflict, ValueError):
        return None


def full_range(table: dict) -> FeedRange:
    """템플릿 표와 사전 확인 규칙만으로 정해지는 범위. 계열마다 통과하는 값 하나를 찾아 기준으로 삼고 칸마다 바꿔 본다."""
    combos, fields = {}, {}
    for family, options in FIELDS.items():
        names = ["reaction", "medium", *options]
        candidates = ({"family": family.value, **dict(zip(names, (v.value for v in values), strict=True))}
                      for values in itertools.product(list(Reaction), list(Medium), *options.values()))
        seed = next((values for values in candidates if probe(values, table)), None)
        if seed is None:
            fields[family.value] = {name: [] for name in options}
            continue
        fields[family.value] = {name: [v.value for v in allowed if probe({**seed, name: v.value}, table)]
                                for name, allowed in options.items()}
        for reaction, medium in itertools.product(Reaction, Medium):
            template = probe({**seed, "reaction": reaction.value, "medium": medium.value}, table)
            if template:
                combos[(family.value, reaction.value, medium.value)] = template
    return FeedRange(combos, fields)


def feed_range(session, registry: dict, table: dict | None = None) -> FeedRange:
    """지금 만들 수 있는 범위: 템플릿을 설치했다고 보고한 켜진 제작 노드가 있는 조합만 남긴다."""
    table = table or ue_support.catalog()
    whole = full_range(table)
    ready = set()
    for entry in table["templates"]:
        try:
            ue_support.ready_nodes(session, SimpleNamespace(catalog_version="v1"),
                                   {"template": entry["template"], "fingerprint": entry["fingerprint"]}, registry)
            ready.add(entry["template"])
        except jobs.Conflict:
            continue
    return FeedRange({k: t for k, t in whole.combos.items() if t in ready}, whole.fields)


def missing_nodes(session, registry: dict, show_node: str) -> list[str]:
    """디렉터를 부르기 전에 확인하는 노드(설계 5.6 쉬기 3). 생존 신호가 최근이 아니면 꺼진 것으로 본다.
    제작 노드는 feed_range가 템플릿 보고와 함께 확인한다."""
    timestamp = jobs.now(session)

    def online(node_id):
        node = session.get(jobs.Node, node_id)
        return bool(node and 0 <= timestamp - node.heartbeat_at <= ue_support.MAX_HEARTBEAT_AGE)

    missing = []
    if not any(identity.get("role") == "generation" and identity.get("enabled", True) and online(node_id)
               for node_id, identity in registry.items()):
        missing.append("generation")
    missing += [node_id for node_id in (REVIEW_NODE, show_node) if not online(node_id)]
    return missing


# ---------- AI 디렉터 (설계 5.4) ----------
def schema(rng: FeedRange) -> dict:
    families = rng.families()
    reactions = sorted({r for _, r, _ in rng.combos})
    media = sorted({m for _, _, m in rng.combos})
    choices = {}
    for name in NAMES:   # 어떤 계열에서 막힌 칸이면 null도 받는다
        values = sorted({v for f in families for v in rng.fields[f].get(name, [])})
        nullable = any(name not in rng.fields[f] for f in families)
        choices[name] = ({"type": ["string", "null"], "enum": [*values, None]} if nullable
                         else {"type": "string", "enum": values})
    properties = {
        "prompt": {"type": "string", "minLength": 1, "maxLength": 8000},
        "family": {"type": "string", "enum": families},
        "reaction": {"type": "string", "enum": reactions},
        "medium": {"type": "string", "enum": media},
        **choices,
        "mood": {"type": "string", "minLength": 1, "maxLength": 200},
        "palette": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
            "type": "object", "properties": {"role": {"type": "string"}, "hex": {"type": "string"}},
            "required": ["role", "hex"]}},
        "difference": {"type": "string", "minLength": 1, "maxLength": 500},
    }
    return {"type": "object", "properties": properties, "required": list(properties)}


def combo(values: dict) -> tuple[str, str, str]:
    """계열이 없는 예전 기록은 장면 계열로 본다."""
    return values.get("family", TemplateFamily.scene_family.value), values["reaction"], values["medium"]


def usage(rng: FeedRange, recent: list[dict]) -> dict:
    """최근 기록의 조합별 사용 횟수와 안 쓴 조합. LLM이 세지 않게 코드가 센다."""
    counts = Counter(combo(r["choice"]) for r in recent)
    used = [{"family": f, "reaction": r, "medium": m, "count": n} for (f, r, m), n in counts.most_common()]
    unused = [{"family": f, "reaction": r, "medium": m} for f, r, m in sorted(rng.combos) if (f, r, m) not in counts]
    return {"used": used, "unused": unused}


def problems(choice: FeedChoice, rng: FeedRange) -> list[str]:
    """코드 검사(LLM 없이). 빈 목록이면 통과. 이유에는 틀린 값과 가능한 값을 넣어 고칠 수 있게 한다."""
    found = []
    family = choice.family.value
    if not choice.prompt.strip():
        found.append("prompt가 비어 있음")
    if family not in rng.families():
        found.append(f"계열 {family}는 지금 만들 수 없음. 가능한 계열: {rng.families()}")
        return found
    if combo(choice.values()) not in rng.combos:
        media = rng.media()[family]
        allowed = media.get(choice.reaction.value)
        found.append(f"{family}에서 반응 {choice.reaction.value}와 매질 {choice.medium.value} 조합은 지금 만들 수 없음. "
                     + (f"이 반응에 가능한 매질: {allowed}" if allowed else f"이 계열에 가능한 반응: {sorted(media)}"))
    fields = rng.fields[family]
    for name in NAMES:
        value = getattr(choice, name)
        value = value.value if value is not None else None
        if name not in fields:
            if value is not None:
                found.append(f"{name}는 {family}에서 막힌 칸이라 null이어야 함")
        elif value not in fields[name]:
            found.append(f"{name} {value}는 {family}에서 지원하지 않음. 가능: {fields[name]}")
    return found


class FeedDirector:
    def __init__(self, llm):
        self.llm = llm

    @property
    def model_ref(self) -> str:
        model = getattr(self.llm, "model", None)
        return f"{self.llm.name}:{model}" if model else self.llm.name

    def propose(self, rng: FeedRange, recent: list[dict], rejected: list[str]) -> FeedChoice:
        """작품 한 건의 원문과 템플릿 선택 값을 제안한다. LLMError는 그대로 올리고 형식이 틀린 답은 ValueError."""
        history = [{"family": combo(r["choice"])[0], "reaction": r["choice"]["reaction"],
                    "medium": r["choice"]["medium"], "reveal": r["choice"].get("reveal"), "mood": r["choice"]["mood"],
                    "prompt": r["prompt"]} for r in recent]
        fields = {family: rng.fields[family] for family in rng.families()}
        user = ("전시장에 올릴 새 작품 하나를 정하라.\n"
                "템플릿 계열(family): scene_family는 배경 이미지를 만들어 장면을 조립하고, "
                "flow_family는 이미지 없이 입자 · 궤적 · 빛의 움직임만으로 화면을 채운다.\n"
                f"계열 · 반응(reaction)마다 쓸 수 있는 매질(medium): {json.dumps(rng.media(), ensure_ascii=False)}\n"
                f"계열별 나머지 칸의 선택지: {json.dumps(fields, ensure_ascii=False)}\n"
                "고른 계열의 선택지에 없는 칸은 null로 답한다.\n"
                "scene_family에서 reveal이 direct면 원문에 주인공(생물·사물)이 직접 나오고, "
                "field면 주인공 없이 장면 전체가 반응한다. flow_family는 주인공 없이 움직임만 쓴다.\n"
                "prompt는 장면 · 분위기 · 사람이 왔을 때의 반응을 한국어 한두 문장으로 쓰고, 고른 값이 드러나게 쓴다.\n"
                "mood는 분위기 한 낱말, palette는 역할과 #RRGGBB 색 코드 목록이다.\n"
                f"최근 작품(최신순): {json.dumps(history, ensure_ascii=False)}\n"
                f"최근 작품의 조합 사용 횟수와 안 쓴 조합: {json.dumps(usage(rng, recent), ensure_ascii=False)}\n"
                "최근 작품과 다른 결로 정하라. 장면 소재(물, 빛, 숲, 바람, 도시, 천체, 종이, 금속 등)는 최근 3개와 다르게 고르고, "
                "많이 쓴 조합은 피하고 안 쓴 조합을 우선한다. difference에는 최근 작품과 무엇을 다르게 했는지 한 문장으로 쓴다.")
        if rejected:
            user += "\n이전 제안이 거부된 이유:\n" + "\n".join(f"- {reason}" for reason in rejected) + "\n고쳐서 다시 답하라."
        data = self.llm.complete_json(SYSTEM, user, schema(rng))
        return FeedChoice.model_validate(data)   # ValidationError는 ValueError


def apply_choice(spec: Spec, choice: FeedChoice, model_ref: str) -> Spec:
    """디렉터가 고른 칸으로 명세를 덮어쓰고 처음부터 다시 검증한다(설계 5.4 작품 명세 2~3)."""
    data = spec.model_dump(mode="json")
    rule = {**data["interactions"][0], "input": choice.input.value, "reaction": choice.reaction.value,
            "reaction_mode": choice.reaction_mode.value}
    data["interactions"] = [rule]   # 사전 확인은 규칙 하나만 받는다
    data["idle"]["motion"] = choice.idle_motion.value
    data["content"].update(medium=choice.medium.value, mood=choice.mood, palette=[p.model_dump() for p in choice.palette])
    chosen = {"content.medium": choice.medium.value, "content.mood": choice.mood, "content.palette": data["content"]["palette"],
              "idle.motion": choice.idle_motion.value, "interactions[0].input": choice.input.value,
              "interactions[0].reaction": choice.reaction.value,
              "interactions[0].reaction_mode": choice.reaction_mode.value}
    if choice.reveal is not None:   # 흐름 계열은 막힌 칸이라 Input Agent가 비운 그대로 둔다
        data["content"]["reveal"] = chosen["content.reveal"] = choice.reveal.value
    if choice.reveal is SpecReveal.field:   # 주인공 없이 장면 전체 → 명세 규칙상 주인공 칸도 비운다
        data["content"]["main_character"] = None
        chosen["content.main_character"] = None
    data["provenance"] = [p for p in data["provenance"] if p["field_path"] not in chosen
                          and not (p["field_path"].startswith("interactions[")
                                   and not p["field_path"].startswith("interactions[0]"))]
    data["provenance"] += [Provenance(field_path=path, value=value, origin=Origin.head_selected, model_ref=model_ref,
                                      prompt_version=PROMPT_VERSION).model_dump(mode="json")
                           for path, value in chosen.items()]
    data["unmatched_phrases"] = [p for p in data["unmatched_phrases"]
                                 if not (p["field_path"].startswith("interactions[")
                                         and not p["field_path"].startswith("interactions[0]"))]
    return Spec.model_validate(data)


# ---------- 상태 · 기록 ----------
def feed_row(session, *, lock: bool = False) -> ExhibitionFeed | None:
    query = select(ExhibitionFeed).where(ExhibitionFeed.id == 1)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return session.exec(query).first()


def pause(session, settings, reason: str) -> None:
    row = feed_row(session, lock=True) or ExhibitionFeed()
    timestamp = jobs.now(session)
    row.paused_until, row.pause_reason, row.updated_at = timestamp + settings.exhibition_feed_retry_seconds, reason, timestamp
    session.add(row)
    session.commit()


def generated(result: dict) -> dict | None:
    """디렉터 작업 한 회차의 대표 원문(설계 5.4). 모든 검사를 통과한 원문이 있으면 그것, 없으면 마지막으로 쓴 답.
    디렉터가 답을 하나도 내지 못한 회차(디렉터를 부르기 전에 쉼 등)는 None."""
    if result.get("submitted"):
        return result["submitted"]
    answer = next((a["answer"] for a in reversed(result.get("attempts", []))
                   if (a.get("answer") or {}).get("prompt")), None)
    if answer is None:
        return None
    choice = {name: answer.get(name) for name in ("reaction", "medium", "reveal", "input", "reaction_mode",
                                                  "idle_motion", "mood", "palette")}
    choice["family"] = answer.get("family") or TemplateFamily.scene_family.value
    return {"prompt": answer["prompt"], "choice": choice, "difference": answer.get("difference"), "template": None,
            "overlaps_recent": None}


def recent_records(session, limit: int = RECENT) -> list[dict]:
    """AI 디렉터가 생성한 원문 전체에서 최근 limit회차(설계 5.4). 검사 통과 여부와 상관없이 한 회차는 한 건이다."""
    rows = session.exec(select(jobs.Job).where(jobs.Job.kind == KIND).order_by(jobs.Job.created_at.desc())
                        .limit(limit * 20)).all()
    return [record for record in (generated(row.result) for row in rows) if record][:limit]


def ended_at(session, job: jobs.Job) -> float:
    attempt = session.exec(select(jobs.Attempt).where(jobs.Attempt.job_id == job.id,
                                                      jobs.Attempt.number == job.attempt)).first()
    return (attempt.ended_at or 0) if attempt else 0


def head_unavailable(session, since: float) -> bool:
    """자동 작품의 분산 Head 판단 중 가장 최근에 끝난 2개가 모두 GMS 연결 실패인가(설계 5.6 쉬기 2)."""
    flows = set(session.exec(select(HeadWorkflow.id).where(HeadWorkflow.request_id.startswith(PREFIX))).all())
    if not flows:
        return False
    rows = session.exec(select(jobs.Job).where(jobs.Job.kind == "head_decide",
                                               jobs.Job.status.in_(["succeeded", "failed"]))
                        .order_by(jobs.Job.created_at.desc()).limit(50)).all()
    finished = sorted(((ended_at(session, row), row) for row in rows if row.payload.get("workflow_id") in flows),
                      key=lambda pair: pair[0], reverse=True)
    latest = [row for when, row in finished if when > since][:2]
    return len(latest) == 2 and all(row.status == "failed" and row.error == "LLMUnavailable" for row in latest)


def in_flight(session, settings) -> int:
    """진행 중 자동 작품 + 대기·실행 중 디렉터 작업. 접수 후 오래된 흐름은 세지 않는다(설계 5.6)."""
    oldest = jobs.now(session) - settings.exhibition_feed_stale_seconds
    flows = session.exec(select(HeadWorkflow).where(HeadWorkflow.request_id.startswith(PREFIX),
                                                    HeadWorkflow.status.not_in(DONE),
                                                    HeadWorkflow.created_at > oldest)).all()
    feeds = session.exec(select(jobs.Job).where(jobs.Job.kind == KIND,
                                                jobs.Job.status.in_(["queued", "running"]))).all()
    return len(flows) + len(feeds)


def tick(session, settings) -> jobs.Job | None:
    """Dispatcher가 부른다. 켜져 있고 쉬는 중이 아니고 자리가 있으면 디렉터 작업 하나를 넣는다. LLM을 부르지 않는다."""
    row = feed_row(session, lock=True)
    if row is None or not row.enabled:
        session.rollback()
        return None
    timestamp = jobs.now(session)
    if row.paused_until > timestamp:
        session.rollback()
        return None
    last = session.get(jobs.Job, row.last_job) if row.last_job else None
    if last and last.status in ("queued", "running"):   # 디렉터 작업은 한 번에 하나
        session.rollback()
        return None
    if last and last.status == "failed" and last.error != EXHAUSTED and ended_at(session, last) >= row.paused_until:
        pause(session, settings, "feed_error")   # 예상 못 한 실패가 2초마다 반복되지 않게 한 번 쉰다
        return None
    if head_unavailable(session, row.paused_until):
        pause(session, settings, "head_unavailable")
        return None
    if in_flight(session, settings) >= settings.exhibition_feed_max_in_flight:
        session.rollback()
        return None
    job = jobs.enqueue(session, {"request_id": f"{KIND}-{uuid.uuid4().hex}", "kind": KIND}, commit=False)
    row.last_job, row.updated_at = job.id, timestamp
    session.add(row)
    session.commit()
    return job


def status(session, settings) -> dict:
    row = feed_row(session) or ExhibitionFeed()
    timestamp = jobs.now(session)
    recent = session.exec(select(jobs.Job).where(jobs.Job.kind == KIND).order_by(jobs.Job.created_at.desc())
                          .limit(5)).all()
    return {"enabled": row.enabled, "paused": row.paused_until > timestamp,
            "pause_reason": row.pause_reason if row.paused_until > timestamp else "",
            "paused_until": row.paused_until, "in_flight": in_flight(session, settings),
            "max_in_flight": settings.exhibition_feed_max_in_flight,
            "recent": [{"id": job.id, "status": job.status, "created_at": job.created_at,
                        "outcome": job.result.get("outcome"), "reason": job.result.get("reason"),
                        "prompt": (job.result.get("submitted") or {}).get("prompt"),
                        "workflow_id": job.result.get("workflow_id")} for job in recent]}


def toggle(session, settings, enabled: bool) -> dict:
    row = feed_row(session, lock=True) or ExhibitionFeed()
    row.enabled, row.updated_at = enabled, jobs.now(session)
    if enabled:   # 다시 켜면 쉬는 상태도 지우고 바로 채운다
        row.paused_until, row.pause_reason = 0, ""
    session.add(row)
    session.commit()
    return status(session, settings)


# ---------- 디렉터 작업 (설계 5.1 Worker) ----------
def submitted_from(row: HeadWorkflow) -> dict:
    """다시 실행된 작업이 기존 흐름을 찾았을 때, 흐름의 요청에서 기록을 채운다(설계 5.7)."""
    spec = row.payload["spec"]
    rule = spec["interactions"][0]
    choice = {"family": spec["template_family"], "reaction": rule["reaction"], "medium": spec["content"].get("medium"),
              "reveal": spec["content"].get("reveal"), "input": rule["input"], "reaction_mode": rule["reaction_mode"],
              "idle_motion": spec["idle"].get("motion"), "mood": spec["content"].get("mood"),
              "palette": spec["content"].get("palette")}
    return {"prompt": row.payload["prompt"], "choice": choice, "difference": None,
            "template": row.payload.get("_ue", {}).get("selection", {}).get("template")}


def run(engine, settings, job_id: str, owner: str, token: str, llm) -> dict:
    """디렉터 작업 하나. llm은 대체 LLM 없는 GMS 연결이며 AI 디렉터와 Input Agent가 함께 쓴다."""
    registry = json.loads(settings.agent_keys_json)
    request_id = PREFIX + job_id
    show_node = HeadRequest.model_fields["show_node"].default
    with Session(engine) as session:
        jobs.owned(session, job_id, owner, token)
        existing = session.exec(select(HeadWorkflow).where(HeadWorkflow.request_id == request_id)).first()
        if existing:   # 재실행이면 디렉터를 부르지 않는다. 새 원문은 같은 request_id에서 거부되기 때문이다
            return {"outcome": "submitted", "workflow_id": existing.id, "request_id": request_id,
                    "submitted": submitted_from(existing)}
        rng = feed_range(session, registry)
        missing = missing_nodes(session, registry, show_node) + ([] if rng.combos else ["production"])
        if missing:
            pause(session, settings, "nodes_offline")
            return {"outcome": "paused", "reason": "nodes_offline", "missing": missing}
        recent = recent_records(session)
    director = FeedDirector(llm)
    record = {"prompt_version": PROMPT_VERSION, "model": director.model_ref,
              "range": {"digest": rng.digest(), "combos": len(rng.combos)}, "attempts": []}
    reasons, passed = [], None
    for _ in range(MAX_ATTEMPTS):
        answer, found = None, []
        try:
            choice = director.propose(rng, recent, reasons)
            answer = choice.model_dump(mode="json")
            found = problems(choice, rng)
            if not found:
                match = LLMSpecMatcher(llm, fail_on_unavailable=True).match(choice.prompt, choice.family)
                record["input_agent"] = {"sources": match.sources,
                                         "holds": [hold.model_dump(mode="json") for hold in match.holds]}
                found = [f"작품 명세 보류({hold.reason}): 원문 '{hold.text}'" for hold in match.holds]
                if not found:
                    spec = apply_choice(match.spec, choice, director.model_ref)
                    request = HeadRequest(request_id=request_id, artwork_id=request_id, version="v1",
                                          prompt=choice.prompt, spec=spec, automation=True, auto_publish=True,
                                          profile="production")
                    template = ue_support.select_template(request)["template"]
                    passed = choice, request, template
        except LLMUnavailable:
            record["attempts"].append({"answer": answer, "problems": ["LLMUnavailable"], "model": director.model_ref})
            with Session(engine) as session:
                jobs.owned(session, job_id, owner, token)
                pause(session, settings, "gms_unavailable")
            return {**record, "outcome": "paused", "reason": "gms_unavailable"}
        except LLMError as error:   # 공급자 응답 원문이 섞일 수 있어 종류 이름만 남긴다
            found = [f"LLM 답을 읽지 못함({type(error).__name__})"]
        except ValueError as error:   # 형식 · 명세 · 사전 확인(jobs.Conflict) 거부
            found = [str(error)]
        record["attempts"].append({"answer": answer, "problems": found, "model": director.model_ref})
        if passed:
            break
        reasons.extend(found)
    if not passed:
        return {**record, "outcome": "exhausted"}
    choice, request, template = passed
    recent_combos = {combo(r["choice"]) for r in recent[:OVERLAP]}
    record["submitted"] = {"prompt": choice.prompt, "choice": choice.values(), "difference": choice.difference,
                           "template": template, "overlaps_recent": combo(choice.values()) in recent_combos}
    with Session(engine) as session:
        jobs.owned(session, job_id, owner, token)
        connection = settings.head_connection(request.profile)
        try:
            try:
                row = dispatch.submit(session, request, connection, registry)
            except IntegrityError:
                session.rollback()
                row = dispatch.submit(session, request, connection, registry)
        except jobs.Conflict as error:   # 명세는 위에서 통과했으므로 노드 · 등록 문제다(설계 5.5)
            session.rollback()
            pause(session, settings, "submit_rejected")
            return {**record, "outcome": "paused", "reason": "submit_rejected", "detail": str(error)}
        return {**record, "outcome": "submitted", "workflow_id": row.id, "request_id": request_id}
