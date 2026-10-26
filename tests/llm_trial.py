"""실제 LLM 시험: 원문 → 명세 매칭(LLMSpecMatcher)과 계획(LLMPlanner)을 실제 LLM으로 불러 결과를 남긴다.

pytest에 들어가지 않는다(이름이 test_로 시작하지 않음). 설명은 tests/README.md 「실제 LLM 시험」.
  $env:LLM_PROVIDER="ollama"; uv run python tests/llm_trial.py [--check] [--only spec|plan] [--cases a,b] [--repeat N]
결과: runtime/llm_trial/<시각>-<공급자>/results.jsonl · summary.md
"""
import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from central.config import Settings
from contracts.schemas import CreateRequest
from studio.head.llm import make_llm
from studio.head.planning import LLMPlanner
from studio.head.spec_matching import PROMPT_VERSION, LLMSpecMatcher

CASES = Path(__file__).with_name("llm_trial_cases.json")


class Recorder:
    """LLM 호출마다 걸린 시간 · 답 원본 · 오류 · 서버 검사에서 틀린 이유를 남긴다."""

    def __init__(self, llm):
        self.llm, self.calls = llm, []
        self.name, self.model = llm.name, llm.model

    def complete_json(self, system, user, schema):
        call = {"user": user, "seconds": None, "answer": None, "error": None, "invalid": None}
        self.calls.append(call)
        started = time.perf_counter()
        try:
            call["answer"] = self.llm.complete_json(system, user, schema)
            return call["answer"]
        except Exception as error:
            call["error"] = str(error)
            raise
        finally:
            call["seconds"] = round(time.perf_counter() - started, 1)

    def take(self) -> list[dict]:
        calls, self.calls = self.calls, []
        return calls


def recording(recorder, validate):
    """서버 검사(validate)에서 틀린 이유를 마지막 호출 기록에 붙인다."""
    def wrapped(data):
        try:
            return validate(data)
        except Exception as error:
            if recorder.calls:
                recorder.calls[-1]["invalid"] = f"{type(error).__name__}: {error}"
            raise
    return wrapped


class TrialSpecMatcher(LLMSpecMatcher):
    def _ask(self, system, user, schema, validate):
        self.last_system = system
        return super()._ask(system, user, schema, recording(self.llm, validate))


class TrialPlanner(LLMPlanner):
    def _ask(self, stage, user, schema, validate):
        return super()._ask(stage, user, schema, recording(self.llm, validate))


def run_spec(recorder, name, case) -> dict:
    matcher = TrialSpecMatcher(recorder)
    result = matcher.match(case["raw"], case["family"])
    spec = result.spec
    return {"kind": "spec", "case": name, "family": case["family"], "raw": case["raw"], "sources": result.sources,
            "holds": [h.model_dump() for h in result.holds],
            "content": spec.content.model_dump(mode="json", exclude_none=True),
            "idle": spec.idle.model_dump(mode="json", exclude_none=True),
            "interactions": [i.model_dump(mode="json", exclude_none=True) for i in spec.interactions],
            "unmatched": [u.model_dump() for u in spec.unmatched_phrases],
            "system": matcher.last_system, "calls": recorder.take()}


def run_plan(recorder, name, fields) -> dict:
    planner = TrialPlanner(recorder)
    request = CreateRequest(**fields)
    theme = "custom" if request.theme.strip() else "ocean"   # central._prepare와 같은 규칙
    intent = planner.intent(request, theme)
    elements = planner.composition(intent, request.families)
    mappings = planner.mappings(elements, request)
    return {"kind": "plan", "case": name, "request": fields, "sources": planner.sources,
            "intent": intent.model_dump(mode="json", exclude_none=True),
            "families": [e.family.value for e in elements],
            "mappings": [m.model_dump(mode="json") for m in mappings], "calls": recorder.take()}


def summary(rows: list[dict], model_ref: str, args) -> str:
    out = [f"# LLM 시험 {datetime.now():%Y-%m-%d %H:%M}", "",
           f"- 모델 `{model_ref}` · 명세 프롬프트 `{PROMPT_VERSION}` · repeat {args.repeat} · timeout {args.timeout}초", ""]
    for kind, title in (("spec", "원문 → 명세"), ("plan", "계획")):
        part = [r for r in rows if r["kind"] == kind]
        if not part:
            continue
        out += [f"## {title}", "", "| 원문 | 회차 | 출처 | 호출(초) | 보류 | 틀린 이유 |", "|---|---|---|---|---|---|"]
        for r in part:
            seconds = " · ".join(str(c["seconds"]) for c in r["calls"])
            reasons = " / ".join((c["invalid"] or c["error"])[:120] for c in r["calls"] if c["invalid"] or c["error"])
            holds = "; ".join(f'{h["reason"]}: {h["text"]}' for h in r.get("holds", []))
            out.append(f'| {r["case"]} | {r["round"]} | {json.dumps(r["sources"], ensure_ascii=False)} | {seconds} '
                       f'| {holds.replace("|", "/")} | {reasons.replace("|", "/")} |')
        out.append("")
    calls = [c for r in rows for c in r["calls"]]
    if calls:
        out.append(f"평균 호출 시간: {sum(c['seconds'] for c in calls) / len(calls):.1f}초 ({len(calls)}번)")
    return "\n".join(out) + "\n"


def check(settings: Settings, provider: str, model: str, url: str) -> None:
    """연결 · 모델 확인. Ollama는 받아 둔 모델 목록을 보고, GMS는 키가 있는지만 본다(크레딧을 쓰지 않게)."""
    if provider == "ollama":
        tags = json.loads(urllib.request.urlopen(url.replace("/api/chat", "/api/tags"), timeout=10).read())
        names = [m["name"] for m in tags.get("models", [])]
        print(f"Ollama 모델: {names}")
        if model not in names:
            sys.exit(f"{model} 없음. ollama pull {model}")
    elif not settings.gms_key:
        sys.exit("GMS_KEY 환경 변수가 없음")


def main():
    parser = argparse.ArgumentParser(description="실제 LLM으로 명세 매칭 · 계획 시험")
    parser.add_argument("--only", choices=["spec", "plan"])
    parser.add_argument("--cases", default="", help="쉼표로 구분한 원문 이름 (llm_trial_cases.json의 키)")
    parser.add_argument("--repeat", type=int, default=1, help="원문마다 반복 횟수")
    parser.add_argument("--timeout", type=float, default=180, help="호출 한 번의 시간 제한(초)")
    parser.add_argument("--check", action="store_true", help="연결 · 모델만 확인")
    args = parser.parse_args()

    settings = Settings()
    provider = settings.llm_provider
    if provider not in ("ollama", "gms"):
        sys.exit("LLM_PROVIDER를 ollama 또는 gms로 지정할 것")
    model, url, key = settings.llm_args(provider)
    check(settings, provider, model, url)
    if args.check:
        return

    llm = make_llm(provider, model, url, key)
    llm.timeout = args.timeout
    recorder = Recorder(llm)
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    wanted = {c for c in args.cases.split(",") if c}
    jobs = [(kind, name, case) for kind in ("spec", "plan") if args.only in (None, kind)
            for name, case in cases[kind].items() if not wanted or name in wanted]

    folder = settings.data_dir / "llm_trial" / f"{datetime.now():%Y%m%d-%H%M%S}-{provider}"
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    with open(folder / "results.jsonl", "w", encoding="utf-8") as results:
        for round_ in range(1, args.repeat + 1):
            for kind, name, case in jobs:
                row = run_spec(recorder, name, case) if kind == "spec" else run_plan(recorder, name, case)
                row["round"] = round_
                rows.append(row)
                results.write(json.dumps(row, ensure_ascii=False) + "\n")
                results.flush()
                seconds = sum(c["seconds"] for c in row["calls"])
                print(f"[{round_}] {kind} {name}: {row['sources']} ({seconds:.0f}초)", flush=True)
    (folder / "summary.md").write_text(summary(rows, f"{provider}:{model}", args), encoding="utf-8")
    print(f"결과: {folder}")


if __name__ == "__main__":
    main()
