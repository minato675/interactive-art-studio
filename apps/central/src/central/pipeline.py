"""입력 → 모든 필수 후보 파일 준비. prepared와 실제 런타임 Ready·Applied를 구분한다.

① 의미: (사용자 제작) Input Agent 작품 명세 → 의도(보이지 않는 주인공·드러내는 방식) → 구성 → 입력·반응 → 안무
② 소재: 배경(풀/생성) + 주인공 데이터(실루엣 점·발자취 경로)
③ 준비: TD ← artwork.json / UE ← scene.json. both는 두 출력 모두 필수이고 미지원은 오류다.

자동 모드는 run() 한 번에 끝까지 간다. 편집 모드는 draft()가 ①의 의도·구성까지만 정해 초안으로 멈추고,
confirm()이 사용자가 고친 칸만 덮어쓴 뒤 플래너를 다시 부르지 않고 이어서 만든다(초안에서 본 값이 그대로 결과가 된다).
전시 모드에서 테마가 비고 디렉터가 있으면 디렉터가 먼저 방향을 정하고(실패하면 프리셋 규칙)
기획·검사까지 미리 통과시킨다.
사람 검수(선택, 기본 꺼짐, 전시 모드 제외): 소재 검수는 ② 뒤, 최종 검수는 검수 폴더 게시 뒤 멈추고 review()로 이어 간다.
"""
import json
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, update
from sqlmodel import Session, select

from central.config import Settings
from contracts.families import FAMILIES, REVEAL_FAMILIES
from contracts.schemas import (
    PRESETS,
    CreateRequest,
    Direction,
    DraftEdit,
    Element,
    InputSource,
    Intent,
    Mapping,
    Mode,
    Phase,
    Reveal,
    ReviewDecision,
    Step,
    Timeline,
)
from contracts.spec import TemplateFamily
from db.models import Artwork, Asset
from studio.assembly import td_artwork as td_bridge
from studio.assembly import ue_scene as ue_bridge
from studio.exhibition.delivery import prepare_version
from studio.generation import assets as asset_steps
from studio.generation import subjects
from studio.generation.images import ImageProvider
from studio.generation.subjects import OutlineProvider
from studio.head.capabilities import capabilities
from studio.head.choreography import phases as choreograph
from studio.head.exhibition_director import MAX_ATTEMPTS, PROMPT_VERSION, ExhibitionDirector, problems, to_request
from studio.head.llm import LLMError, LLMUnavailable
from studio.head.planning import PALETTES, Planner, RulePlanner, prompt_subject
from studio.head.spec_matching import STAGE as SPEC_STAGE
from studio.head.spec_matching import LLMSpecMatcher

PLANNED_INPUTS = {InputSource.body, InputSource.sound}   # 렌더러 입력 연결 예정


def _renderers(settings: Settings) -> list[str]:
    return {"td": ["td"], "ue": ["ue"], "both": ["td", "ue"]}[settings.renderer]


def _check_request(request: CreateRequest) -> None:
    """알려진 미지원 요청은 계획·유료 생성 전에 오류로 기록한다."""
    reasons = []
    chips = sorted(set(request.chips).intersection(("composition", "focus", "space")))
    if chips:
        reasons.append(f"지원하지 않는 칩: {chips}")
    if request.reference is not None:
        reasons.append("reference 참고 이미지 연결 미지원")
    inputs = sorted(s.value for s in PLANNED_INPUTS.intersection(request.inputs))
    if inputs:
        reasons.append(f"지원하지 않는 입력: {inputs}")
    if request.timeline is not Timeline.loop:
        reasons.append(f"시간 구조 {request.timeline.value} 미지원 (loop만 지원)")
    todo = [f.value for f in request.families if FAMILIES[f].status == "todo"]
    if todo:
        reasons.append(f"아직 연결되지 않은 표현 방식: {todo}")
    if request.direction is Direction.none and set(request.chips).intersection(("mood", "palette")):
        reasons.append("direction=none에서는 명시한 mood·palette를 반영할 수 없음")
    if reasons:
        raise ValueError("지원하지 않는 요청 — " + "; ".join(reasons))


def _check_plan(request: CreateRequest, intent: Intent, elements: list[Element], renderers: list[str]) -> None:
    reasons = [f"아직 연결되지 않은 표현 방식 {e.family.value}" for e in elements
               if FAMILIES[e.family].status == "todo"]
    missing = set(request.families) - {e.family for e in elements}
    if missing:
        reasons.append(f"요청한 표현 방식 누락: {sorted(f.value for f in missing)}")
    requested_subject = request.subject.strip()
    if requested_subject and intent.subject != requested_subject:
        reasons.append(f"요청한 주인공 {requested_subject}이 계획에 반영되지 않음")
    if intent.subject and intent.reveal is Reveal.none:
        reasons.append(f"주인공 {intent.subject}을 선택한 표현 방식으로 드러낼 수 없음")
    if not intent.subject and intent.reveal is not Reveal.none:
        reasons.append(f"{intent.reveal.value}에는 표현할 주인공이 필요함")
    if "ue" in renderers:
        reasons.extend(ue_bridge.unsupported(intent, elements))
    if reasons:
        raise ValueError("지원하지 않는 요청 — " + "; ".join(reasons))


def _rollback(session: Session) -> None:
    """이번 작품에서 새로 만든 소재 파일을 지우고 DB 변경(소재·사용 횟수)을 되돌린다."""
    for path in session.info.pop("new_files", []):
        Path(path).unlink(missing_ok=True)
    session.rollback()


def _recent(session: Session, count: int) -> list[tuple[str, tuple[str, ...]]]:
    rows = session.exec(select(Artwork).where(Artwork.status.in_(("prepared", "published")))
                        .order_by(Artwork.id.desc()).limit(count)).all()
    return [(row.theme, tuple(row.families)) for row in rows]


def _prepare(session: Session, request: CreateRequest, settings: Settings, planner: Planner, rng: random.Random,
             steps: list[Step]) -> tuple[CreateRequest, str]:
    # 자동 선택은 실행 가능한 범위에서 시작한다. 명시한 주인공·표현 요구는 바꾸지 않고 지원 검사로 넘긴다.
    if request.mode is Mode.exhibition and "ue" in _renderers(settings) and \
            request.reveal is None and not prompt_subject(request):
        request = request.model_copy(update={"reveal": Reveal.none})
        steps.append(Step(name="자동 기획 범위", detail="UE 어댑터의 지원 범위에서 주인공 없는 장 표현을 기획"))
    preset = None
    if request.mode is Mode.exhibition and not request.theme.strip():
        auto = planner.auto_request(_recent(session, settings.recent_count), rng, request.families)
        preset = auto["preset"]
        request = request.model_copy(update={"families": auto["families"]})
        steps.append(Step(name="자동 요청 생성", detail={"preset": preset,
                                                    "families": [f.value for f in auto["families"]]}))
    # 풀·기록의 테마 키는 내부 기본 테마(또는 custom). 자유 문장은 요청과 의도에만 쓴다(실제: LLM이 테마를 정규화).
    theme = preset or ("custom" if request.theme.strip() else rng.choice(PRESETS))
    return request, theme


def _input_agent(input_agent: LLMSpecMatcher | None, request: CreateRequest,
                 steps: list[Step]) -> tuple[CreateRequest, str | None]:
    """사용자 제작: Input Agent가 원문을 작품 명세로 정리해 남기고, 주인공 판단을 기획에 넘긴다(요청, 메인 캐릭터).
    메인 캐릭터가 없으면 '드러내기 없음'을, 있으면 그 문구를 넘겨 기획이 주인공을 그 안에서 고르게 한다.
    기획(planning)의 의도 단계는 원문에 주인공이 없어도 관람객 · 장면 요소를 주인공으로 고르기 쉬워서다.
    사용자 · 규칙이 주인공이나 드러내는 방식을 이미 정했거나, LLM 답이 규칙으로 대체되면 판단에 쓰지 않는다.
    Input Agent는 보조 단계라 예상 못 한 오류가 나도 작품을 실패시키지 않고 기획만으로 진행한다."""
    if input_agent is None or request.mode is not Mode.manual or not request.theme.strip():
        return request, None
    try:
        result = input_agent.match(request.theme, TemplateFamily.scene_family)   # 메인 캐릭터 칸은 장면 계열에만 있다
    except Exception as error:  # noqa: BLE001 — 보조 단계 경계: 기록만 남기고 기획만으로 진행
        steps.append(Step(name="작품 명세", detail={
            "decision": f"Input Agent 오류({type(error).__name__}) → 기획만으로 진행"}))
        return request, None
    character, source = result.spec.content.main_character, result.sources.get(SPEC_STAGE, "rule")
    asked, hint = request, None
    if request.reveal is not None or prompt_subject(request):
        decision = "사용자 · 규칙이 정한 주인공 · 드러내는 방식을 따름"
    elif not source.startswith("llm"):
        decision = "LLM 답이 규칙으로 대체되어 판단에 쓰지 않음"
    elif character:
        decision = f"메인 캐릭터 있음({character}) → 기획이 그 안에서 주인공을 고름"
        hint = character
    else:
        decision = "메인 캐릭터 없음 → 드러내기 없음으로 기획"
        asked = request.model_copy(update={"reveal": Reveal.none})
    steps.append(Step(name="작품 명세", detail={
        "decision": decision, "sources": result.sources, "family": TemplateFamily.scene_family.value,
        "holds": [hold.model_dump() for hold in result.holds],
        "spec": result.spec.model_dump(mode="json", exclude={"provenance"}, exclude_none=True)}))
    return asked, hint


def planning_feedback(session, planner, steps, director=None):
    from studio.head import feedback

    snapshot = feedback.capture(session)
    contextual = feedback.for_planner(planner, snapshot)
    contextual_director = feedback.for_director(director, snapshot)
    if snapshot.get("items"):
        uses_llm = contextual is not planner or contextual_director is not director
        steps.append(Step(name="운영 피드백", detail={"snapshot": snapshot,
                          "delivery": "llm_context" if uses_llm else "rule_only"}))
    return contextual, contextual_director


def _direct(director: ExhibitionDirector, request: CreateRequest, settings: Settings, planner: Planner,
            steps: list[Step]) -> tuple[CreateRequest, tuple[Intent, list[Element]] | None]:
    """전시 모드: 디렉터가 지원 범위 안에서 방향을 정하고, 소재 생성 전 검사(코드 · 요청 · 기획 · 계획)를 통과할 때까지
    거부 이유를 붙여 디렉터를 최대 MAX_ATTEMPTS번(처음 1번 + 다시 쓰기 2번) 부른다.
    통과하면 (새 요청, 계획), 연결 실패 · 예상 못 한 오류 · 소진이면 (원래 요청, None)이라
    호출한 쪽이 프리셋 규칙으로 만든다. 어느 경우든 '디렉터' 단계를 남긴다.
    디렉터는 _build의 실패 기록 밖에서 돌므로, 여기서 오류가 새면 작품 기록 없이 HTTP 500이 된다."""
    renderers = _renderers(settings)
    caps = capabilities(renderers)
    attempts: list[dict] = []
    reasons: list[str] = []
    passed: tuple[CreateRequest, tuple[Intent, list[Element]]] | None = None
    fallback = "다시 쓰기 소진"
    for _ in range(MAX_ATTEMPTS):
        answer, found = None, []
        try:
            choice = director.propose(caps, reasons)
            answer = choice.model_dump(mode="json")
            found = problems(choice, caps)
            if not found:
                asked = to_request(choice, request)
                _check_request(asked)
                intent = planner.intent(asked, "custom")
                elements = planner.composition(intent, asked.families)
                _check_plan(asked, intent, elements, renderers)
                passed = asked, (intent, elements)
        except LLMUnavailable as error:
            attempts.append({"answer": None, "problems": [str(error)], "model": director.model_ref})
            fallback = "연결 실패"
            break
        except (LLMError, ValueError) as error:
            found = [str(error)]
        except Exception as error:  # noqa: BLE001 — 보조 단계 경계: 다시 물어도 반복되기 쉬워 기록만 남기고 프리셋으로
            attempts.append({"answer": answer, "problems": [f"{type(error).__name__}: {error}"],
                             "model": director.model_ref})
            fallback = "디렉터 오류"
            break
        attempts.append({"answer": answer, "problems": found, "model": director.model_ref})
        if passed:
            break
        reasons.extend(found)
    source = ("llm" if len(attempts) == 1 else "llm-repaired") if passed else "preset"
    steps.append(Step(name="디렉터", detail={
        "source": source, "prompt_version": PROMPT_VERSION, "model": attempts[-1]["model"],
        "capabilities": caps.as_dict(), "attempts": attempts, "fallback": None if passed else fallback}))
    return passed if passed else (request, None)


def run(session: Session, request: CreateRequest, settings: Settings, planner: Planner, images: ImageProvider,
        outlines: OutlineProvider, rng: random.Random | None = None,
        input_agent: LLMSpecMatcher | None = None, director: ExhibitionDirector | None = None) -> Artwork:
    """자동 모드: 의미 정하기부터 상영까지 한 번에."""
    rng = rng or random.Random()
    steps: list[Step] = []
    directed = None
    uses_director = director is not None and request.mode is Mode.exhibition and not request.theme.strip()
    planner, contextual_director = planning_feedback(session, planner, steps, director if uses_director else None)
    if uses_director:
        request, directed = _direct(contextual_director, request, settings, planner, steps)
    request, theme = _prepare(session, request, settings, planner, rng, steps)
    artwork = Artwork(mode=request.mode.value, theme=theme, status="held", request=request.model_dump(mode="json"))

    def plan() -> tuple[Intent, list[Element]]:
        if directed:   # 디렉터 시도 중에 이미 기획·검사까지 통과했다
            intent, elements = directed
            artwork.title = intent.title
            steps.append(Step(name="의도", detail=intent.model_dump(mode="json", exclude_none=True)))
            artwork.families = [e.family.value for e in elements]
            steps.append(Step(name="구성", detail=[e.model_dump(mode="json") for e in elements]))
            return intent, elements
        asked, character = _input_agent(input_agent, request, steps)
        intent = planner.intent(asked, theme, character=character)
        artwork.title = intent.title
        steps.append(Step(name="의도", detail=intent.model_dump(mode="json", exclude_none=True)))
        elements = planner.composition(intent, request.families)
        artwork.families = [e.family.value for e in elements]
        steps.append(Step(name="구성", detail=[e.model_dump(mode="json") for e in elements]))
        return intent, elements

    return _build(session, artwork, steps, request, theme, plan, settings, planner, images, outlines, rng)


def _build(session: Session, artwork: Artwork, steps: list[Step], request: CreateRequest, theme: str, plan,
           settings: Settings, planner: Planner, images: ImageProvider, outlines: OutlineProvider,
           rng: random.Random, sources: dict | None = None) -> Artwork:
    """plan()으로 의도·구성을 받아 보류 검사 → 반응 → 안무 → 소재 → (소재 검수) → 조립·게시. 실패하면 지금 작품을 유지한다."""
    renderers = _renderers(settings)
    review_assets, review_final = _gates(request)

    try:
        _check_request(request)
        intent, elements = plan()
        _check_plan(request, intent, elements, renderers)
        mappings = planner.mappings(elements, request)
        steps.append(Step(name="입력·반응 매핑", detail=[m.model_dump(mode="json", exclude_none=True)
                                                    for m in mappings]))
        phases = choreograph(intent.reveal, request.chips.get("intensity", "normal"))
        steps.append(Step(name="안무", detail=[f"{p.name} {p.seconds}초" for p in phases]))
        used = dict(getattr(planner, "sources", {}))
        if sources is not None:   # 확정: 의도·구성은 초안 때의 출처, 반응만 이번 호출의 출처
            used = {**sources, **({"반응": used["반응"]} if "반응" in used else {})}
        steps.append(Step(name="출처", detail=used or "rule"))

        # 검수를 켜면 새 소재는 사람이 승인할 때까지 풀에서 뽑히지 않게 pending으로 둔다.
        status = "pending" if review_assets or review_final else "approved"
        need = asset_steps.needs(elements)
        steps.append(Step(name="필요한 소재", detail=need.model_dump()))
        assets, log = asset_steps.gather(session, need.required, request.assets, request.mode, theme,
                                         intent.palette, images, settings.data_dir, set_id=uuid.uuid4().hex[:8],
                                         seed=uuid.uuid4().int & 0x3FFFFFFF, status=status)   # 생성마다 다른 그림
        passing = next((p.seconds for p in phases if p.name == "지나감"), 20.0)
        subject_files, subject_log = subjects.prepare(session, intent, outlines, settings.data_dir, rng, passing,
                                                      status=status, reuse=request.mode is not Mode.exhibition)
        steps.append(Step(name="소재", detail=log + subject_log))
        new_files = [Path(p).as_posix() for p in session.info.get("new_files", [])]
        session.flush()
        context = {
            "intent": intent.model_dump(mode="json"), "elements": [e.model_dump(mode="json") for e in elements],
            "mappings": [m.model_dump(mode="json") for m in mappings],
            "phases": [p.model_dump(mode="json") for p in phases], "renderers": renderers,
            "required_renderers": renderers, "theme": theme,
            "free_theme": request.theme, "assets": {k: Path(v).as_posix() for k, v in assets.items()},
            "subject_files": {k: Path(v).as_posix() for k, v in subject_files.items()}, "new_files": new_files,
            "new_asset_ids": list(session.exec(select(Asset.id).where(Asset.path.in_(new_files))).all())
            if new_files else []}
    except Exception as error:  # noqa: BLE001 — 작품 경계: 어느 단계든 실패하면 기록만 남기고 지금 작품 유지
        _rollback(session)
        steps.append(Step(name="상영", detail=f"실패 — {type(error).__name__}: {error}, 지금 작품 유지"))
        return _finish(session, artwork, steps, "failed")
    if review_assets:
        if context["new_asset_ids"]:
            return _pause(session, artwork, steps, "assets", context)
        steps.append(Step(name="소재 검수", detail="새로 만든 소재 없음 → 건너뜀"))
    return _continue(session, artwork, steps, context, settings, review_final)


# --- 사람 검수(선택): 소재 검수 · 최종 검수 ----------------------------------------------------------
# 멈출 때 이어 갈 문맥을 '검수' 단계 기록에 남긴다. 재개는 플래너·생성기를 다시 부르지 않는다(본 것이 그대로 결과).

GATE_STATUS = {"assets": "asset_review", "final": "final_review"}
GATE_LABEL = {"assets": "소재 검수", "final": "최종 검수"}


def _gates(request: CreateRequest) -> tuple[bool, bool]:
    """(소재 검수, 최종 검수). 전시 모드는 사람 없이 돌므로 항상 건너뛴다."""
    if request.mode is Mode.exhibition:
        return False, False
    return request.review_assets, request.review_final


def _finish(session: Session, artwork: Artwork, steps: list[Step], status: str,
            scene_path: str | None = None) -> Artwork:
    artwork.status, artwork.scene_path = status, scene_path
    artwork.steps = [s.model_dump(mode="json") for s in steps]
    session.add(artwork)
    session.commit()
    session.info.pop("new_files", None)   # 저장했으니 지울 목록에서 뺀다
    session.refresh(artwork)
    return artwork


def _pause(session: Session, artwork: Artwork, steps: list[Step], gate: str, context: dict) -> Artwork:
    message = ("소재 검수 대기 — 운영 앱에서 소재를 보고 승인해 주세요" if gate == "assets" else
               "최종 검수 대기 — 개발 검수 폴더의 후보를 확인해 주세요 (실제 전시 적용 미확인)")
    context = {**context, "saved": True}   # 커밋된 문맥: 이후 정리는 DB 행도 직접 지운다
    steps.append(Step(name="검수", detail={"gate": gate, "message": message, "context": context}))
    return _finish(session, artwork, steps, GATE_STATUS[gate])


def gate_step(artwork: Artwork) -> dict | None:
    """가장 최근의 검수 대기 기록."""
    return next((s["detail"] for s in reversed(artwork.steps)
                 if s["name"] == "검수" and isinstance(s["detail"], dict)), None)


def _approve_assets(session: Session, context: dict) -> None:
    if context["new_asset_ids"]:
        session.execute(update(Asset).where(Asset.id.in_(context["new_asset_ids"]), Asset.status == "pending")
                        .values(status="approved"))


def _discard(session: Session, context: dict | None) -> None:
    """아직 사람이 승인하지 않은(pending) 이번 새 소재의 행과 이번에 만든 파일을 지운다. 승인된 소재 파일은 남긴다.
    한 번도 멈추지 않은(커밋 전) 문맥의 행은 _rollback이 이미 되돌렸고, 그 id는 다른 요청이 다시 쓸 수 있어 건드리지 않는다."""
    if not context:
        return
    ids = context["new_asset_ids"] if context.get("saved") else []
    kept = set(session.exec(select(Asset.path).where(Asset.id.in_(ids), Asset.status == "approved")).all()) \
        if ids else set()
    if ids:
        session.execute(delete(Asset).where(Asset.id.in_(ids), Asset.status == "pending"))
    for path in context["new_files"]:
        if path not in kept:
            Path(path).unlink(missing_ok=True)


def _end(session: Session, artwork: Artwork, steps: list[Step], context: dict | None, status: str,
         detail: str) -> Artwork:
    _rollback(session)
    _discard(session, context)
    steps.append(Step(name="상영", detail=detail))
    return _finish(session, artwork, steps, status)


def _assemble(context: dict) -> dict:
    intent = Intent.model_validate(context["intent"])
    elements = [Element.model_validate(e) for e in context["elements"]]
    mappings = [Mapping.model_validate(m) for m in context["mappings"]]
    # MR !23의 UE 전용 검수 기록에는 TD 전용 칸이 없다. UE 승인 재개도 유지한다.
    phases = [Phase.model_validate(p) for p in context.get("phases", [])]
    assets = {k: Path(v) for k, v in context["assets"].items()}
    subject_files = {k: Path(v) for k, v in context.get("subject_files", {}).items()}
    outputs = {}
    if "td" in context["renderers"]:
        files = {**({"background.png": assets["background"]} if "background" in assets else {}), **subject_files}
        spec = td_bridge.build_spec(intent, elements, mappings, phases, files)
        outputs["td"] = (td_bridge.check(spec, files), "artwork.json", spec, files)
    if "ue" in context["renderers"]:
        brief = ue_bridge.to_brief(intent, elements, mappings, context["theme"], context["free_theme"])
        scene, files = ue_bridge.compile_scene(brief, assets)
        outputs["ue"] = (ue_bridge.check(brief, scene), "scene.json", scene, files)
    return outputs


def _prepare_all(outputs: dict, version: str, folders: dict[str, Path], steps: list[Step]) -> list:
    """모든 필수 후보를 준비한다. 실패 여부와 무관하게 활성 포인터는 변경하지 않는다."""
    prepared, targets, failures = [], {}, []
    for name, (main_name, main, files) in outputs.items():
        try:
            path = prepare_version(folders[name], version, main_name, main, files)
            prepared.append((name, path))
            targets[name] = {"status": "prepared", "path": path.as_posix(), "error": ""}
        except Exception as error:  # noqa: BLE001 — 출력별 실패 원인을 모두 보존한다
            reason = f"{name.upper()} 준비 실패 — {type(error).__name__}: {error}"
            targets[name] = {"status": "failed", "path": None, "error": reason}
            failures.append(reason)
    steps.append(Step(name="파일 준비", detail={"version": version, "required_targets": list(outputs),
                                              "targets": targets, "complete": bool(outputs) and not failures}))
    if failures or not prepared:
        raise RuntimeError("; ".join(failures) or "필수 출력이 없음")
    return prepared


def _validate_context(artwork: Artwork, context: dict, settings: Settings) -> None:
    request = CreateRequest.model_validate(artwork.request)
    _check_request(request)
    required = context.get("required_renderers", _renderers(settings))
    if not required or set(context["renderers"]) != set(required):
        raise ValueError(f"검수 기록의 필수 출력 누락: 필요 {required}, 기록 {context['renderers']}")
    _check_plan(request, Intent.model_validate(context["intent"]),
                [Element.model_validate(e) for e in context["elements"]], required)


def _prepared(session: Session, artwork: Artwork, steps: list[Step], context: dict, prepared: list) -> Artwork:
    _approve_assets(session, context)
    steps.append(Step(name="상영", detail=f"{' · '.join(n.upper() for n, _ in prepared)} 후보 파일 준비 완료 "
                      "— 현재 작품 유지, 런타임 Ready·Applied 미확인"))
    path = dict(prepared).get("ue", prepared[0][1])
    return _finish(session, artwork, steps, "prepared", path.as_posix())


def _continue(session: Session, artwork: Artwork, steps: list[Step], context: dict, settings: Settings,
              review_final: bool) -> Artwork:
    """조립·검사·전체 후보 준비. 활성화는 시연 경로의 별도 요청이다."""
    try:
        _validate_context(artwork, context, settings)
        outputs = _assemble(context)
        problems = {name: out[0] for name, out in outputs.items() if out[0]}
        steps.append(Step(name="검사", detail=problems or "통과"))
        if problems:
            raise ValueError(f"출력 검사 실패: {problems}")
        version = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        ready = {name: out[1:] for name, out in outputs.items()}
        folders = ({"td": settings.review_td_delivery_dir, "ue": settings.review_delivery_dir} if review_final else
                   {"td": settings.td_delivery_dir, "ue": settings.delivery_dir})
        prepared = _prepare_all(ready, version, folders, steps)
        if review_final:
            context = {**context, "version": version,
                       "reviewed": {name: path.name for name, path in prepared}}
            return _pause(session, artwork, steps, "final", context)
    except Exception as error:  # noqa: BLE001 — 이번 후보 실패만 기록하고 기존 작품 유지
        return _end(session, artwork, steps, context, "failed",
                    f"실패 — {type(error).__name__}: {error}, 지금 작품 유지")
    return _prepared(session, artwork, steps, context, prepared)


def _publish_reviewed(session: Session, artwork: Artwork, steps: list[Step], context: dict,
                      settings: Settings) -> Artwork:
    """검수 승인 파일도 전부 준비한 후 완료 처리한다. release 포인터는 유지한다."""
    review = {"td": settings.review_td_delivery_dir, "ue": settings.review_delivery_dir}
    version = context["version"]
    try:
        _validate_context(artwork, context, settings)
        if set(context["reviewed"]) != set(context["renderers"]):
            raise ValueError("최종 검수에서 필수 렌더러가 누락됨 — 다시 제작·검수해야 함")
        ready = {}
        for name, main_name in context["reviewed"].items():
            folder = review[name] / "versions" / version
            main = json.loads((folder / main_name).read_text("utf-8"))
            ready[name] = (main_name, main, {p.name: p for p in folder.iterdir() if p.name != main_name})
        prepared = _prepare_all(ready, version,
                                {"td": settings.td_delivery_dir, "ue": settings.delivery_dir}, steps)
    except Exception as error:  # noqa: BLE001
        return _end(session, artwork, steps, context, "failed",
                    f"실패 — {type(error).__name__}: {error}, 지금 작품 유지")
    return _prepared(session, artwork, steps, context, prepared)


def review(session: Session, artwork_id: int, decision: ReviewDecision, settings: Settings) -> Artwork:
    """검수 대기 작품을 승인하면 멈춘 곳부터 이어 가고, 반려하면 이번 새 소재를 정리하고 보류한다.

    DraftNotFound · DraftConflict(검수를 기다리는 작품이 아님)를 낸다.
    """
    artwork = session.get(Artwork, artwork_id)
    if artwork is None:
        raise DraftNotFound("없는 작품")
    saved = gate_step(artwork)
    waiting = artwork.status
    if waiting not in GATE_STATUS.values() or saved is None:
        raise DraftConflict("검수를 기다리는 작품이 아님")
    claimed = session.execute(update(Artwork).where(Artwork.id == artwork_id, Artwork.status == waiting)
                              .values(status="running"))
    session.commit()
    if claimed.rowcount != 1:
        raise DraftConflict("검수를 기다리는 작품이 아님")
    session.refresh(artwork)
    gate, context = saved["gate"], saved["context"]
    steps = [Step(**s) for s in artwork.steps]
    steps.append(Step(name="검수 결과", detail={"gate": gate, "approve": decision.approve,
                                              **({"note": decision.note} if decision.note else {})}))
    if not decision.approve:
        return _end(session, artwork, steps, context, "held", f"보류 — {GATE_LABEL[gate]} 반려, 지금 작품 유지")
    if gate == "final":
        return _publish_reviewed(session, artwork, steps, context, settings)
    _approve_assets(session, context)
    session.commit()   # 사람이 승인한 소재는 이후 결과와 관계없이 풀에 남긴다
    _, review_final = _gates(CreateRequest.model_validate(artwork.request))
    return _continue(session, artwork, steps, context, settings, review_final)


def gate_files(artwork: Artwork) -> dict[str, Path]:
    """소재 검수 때 운영 앱에 보여 줄 파일(이름 → 경로). 이 목록에 있는 이름만 내보낸다."""
    saved = gate_step(artwork)
    if artwork.status != GATE_STATUS["assets"] or saved is None:
        return {}
    context = saved["context"]
    paths = [*context["assets"].values(), *context["subject_files"].values()]
    return {Path(p).name: Path(p) for p in paths}


# --- 편집 모드: AI 초안 → 사용자 수정 → 확정 ------------------------------------------------------

EDIT_FIELDS = ("title", "families", "subject", "reveal", "mood", "palette", "intensity")


class DraftConflict(Exception):
    """입력을 기다리는 초안이 아니다(이미 확정됐거나 진행 중)."""


class DraftNotFound(Exception):
    """없는 작품."""


def _palette_code(names: list[str]) -> str | None:
    return next((code for code, colors in PALETTES.items() if colors == names), None)


def _draft_values(intent: Intent, families: list[str], request: CreateRequest) -> dict:
    return {"title": intent.title, "families": families, "subject": intent.subject or "",
            "reveal": intent.reveal.value, "mood": intent.mood[0] if intent.mood else None,
            "palette": _palette_code(intent.palette), "intensity": request.chips.get("intensity", "normal")}


def _draft_origins(values: dict, request: CreateRequest) -> dict:
    """칸별 출처. 요청에 사용자가 넣은 값은 user_explicit, 플래너가 채운 값은 head_selected, 못 채운 값은 unextracted."""
    given = {"families": bool(request.families) and values["families"] == [f.value for f in request.families],
             "subject": bool(prompt_subject(request)),
             "reveal": request.reveal is not None, "mood": "mood" in request.chips,
             "palette": "palette" in request.chips, "intensity": "intensity" in request.chips}
    origins = {}
    for name in EDIT_FIELDS:
        if given.get(name):
            origins[name] = "user_explicit"
        elif values[name] in (None, ""):
            origins[name] = "unextracted"
        elif name == "intensity":
            origins[name] = "template_default"
        else:
            origins[name] = "head_selected"
    return origins


def draft(session: Session, request: CreateRequest, settings: Settings, planner: Planner,
          rng: random.Random | None = None, input_agent: LLMSpecMatcher | None = None) -> Artwork:
    """편집 모드 ①: 의도·구성까지만 정하고 소재를 만들기 전에 멈춘다(status=awaiting)."""
    if request.mode is not Mode.manual:
        raise ValueError("편집 모드는 manual 요청만 받는다 (전시 요청은 자동 모드로)")
    rng = rng or random.Random()
    steps: list[Step] = []
    request, theme = _prepare(session, request, settings, planner, rng, steps)
    planner, _ = planning_feedback(session, planner, steps)
    artwork = Artwork(mode=request.mode.value, theme=theme, status="awaiting", request=request.model_dump(mode="json"))
    try:
        _check_request(request)
        asked, character = _input_agent(input_agent, request, steps)
        intent = planner.intent(asked, theme, character=character)
        elements = planner.composition(intent, request.families)
        _check_plan(request, intent, elements, _renderers(settings))
    except Exception as error:  # noqa: BLE001 — 초안도 작품 경계와 같다: 기록만 남긴다
        steps.append(Step(name="초안", detail=f"실패 — {type(error).__name__}: {error}"))
        artwork.status, artwork.steps = "failed", [s.model_dump(mode="json") for s in steps]
        session.add(artwork)
        session.commit()
        session.refresh(artwork)
        return artwork
    families = [e.family.value for e in elements]
    values = _draft_values(intent, families, request)
    artwork.title, artwork.families = intent.title, families
    steps.append(Step(name="초안", detail={
        "values": values, "origins": _draft_origins(values, request), "palette_names": intent.palette,
        "intent": intent.model_dump(mode="json"), "sources": dict(getattr(planner, "sources", {}))}))
    artwork.steps = [s.model_dump(mode="json") for s in steps]
    session.add(artwork)
    session.commit()
    session.refresh(artwork)
    return artwork


def draft_step(artwork: Artwork) -> dict | None:
    return next((s["detail"] for s in artwork.steps if s["name"] == "초안" and isinstance(s["detail"], dict)), None)


def confirm(session: Session, artwork_id: int, edit: DraftEdit, settings: Settings, planner: Planner,
            images: ImageProvider, outlines: OutlineProvider, rng: random.Random | None = None) -> Artwork:
    """편집 모드 ②: 초안에 사용자가 바꾼 칸만 덮어쓰고 이어서 만든다. 플래너의 의도·구성은 다시 부르지 않는다.

    DraftNotFound · DraftConflict(기다리는 초안이 아님) · ValueError(목록 밖 값·맞지 않는 조합)를 낸다.
    ValueError면 초안은 그대로 기다린다.
    """
    artwork = session.get(Artwork, artwork_id)
    if artwork is None:
        raise DraftNotFound("없는 작품")
    saved = draft_step(artwork)
    if artwork.status != "awaiting" or saved is None:
        raise DraftConflict("입력을 기다리는 초안이 아님")
    values, origins = dict(saved["values"]), dict(saved["origins"])
    intent = Intent.model_validate(saved["intent"])
    changes = {name: getattr(edit, name) for name in edit.model_fields_set}
    changes = {k: (v.value if hasattr(v, "value") else [f.value for f in v] if k == "families" else v)
               for k, v in changes.items()}
    changed = [name for name, value in changes.items() if value != values[name]]
    notes = []
    for name in changed:
        values[name], origins[name] = changes[name], "user_explicit"
    intent.title = values["title"]
    intent.subject = values["subject"] or None
    intent.reveal = Reveal(values["reveal"])
    intent.mood = [values["mood"]] if values["mood"] else intent.mood
    if "palette" in changed:
        intent.palette = PALETTES[values["palette"]]
    families = values["families"]
    pinned = origins["reveal"] == "user_explicit" or "reveal" in changes   # 사용자가 직접 정한 값은 고치지 않는다
    if not pinned and {"subject", "families"}.intersection(changed):
        # AI가 고른 드러내는 방식은 주인공·표현 방식이 바뀌면 플래너와 같은 규칙으로 다시 고른다.
        keep = intent.subject and intent.reveal is not Reveal.none and \
            REVEAL_FAMILIES[intent.reveal].intersection(families)
        if not keep:
            usable = [r for r in (Reveal.trace, Reveal.formation) if REVEAL_FAMILIES[r].intersection(families)]
            repicked = usable[0] if usable and intent.subject else Reveal.none
            if repicked is not intent.reveal:
                intent.reveal = repicked
                values["reveal"], origins["reveal"] = repicked.value, "head_selected"
                notes.append(f"주인공·표현 방식이 바뀌어 드러내는 방식을 {repicked.value}로 다시 고름")
    if not REVEAL_FAMILIES[intent.reveal].intersection(families):
        allowed = sorted(f.value for f in REVEAL_FAMILIES[intent.reveal])
        raise ValueError(f"드러내는 방식 {intent.reveal.value}에는 표현 방식 {allowed} 중 하나가 필요")
    if intent.subject and intent.reveal is Reveal.none:
        notes.append(f"주인공 '{intent.subject}'은 고른 표현 방식으로 드러낼 수 없어 빠짐")
    request = CreateRequest.model_validate({   # 그리기·유체·표면 변형 하나만 등 기존 검사를 다시 한다
        **artwork.request, "families": families, "subject": values["subject"], "reveal": intent.reveal.value,
        "chips": {**artwork.request.get("chips", {}),
                  **{k: values[k] for k in ("mood", "palette", "intensity") if values[k]}}})
    elements = RulePlanner().composition(intent, request.families)
    if [e.family.value for e in elements] != families:
        notes.append(f"표현 방식은 {[e.family.value for e in elements]}로 정리됨 (드러내는 방식·캔버스 규칙)")
    # 기다리는 초안을 한 문장으로 차지한다: 동시에 두 번 확정해도 하나만 진행된다.
    claimed = session.execute(update(Artwork).where(Artwork.id == artwork_id, Artwork.status == "awaiting")
                              .values(status="running"))
    session.commit()
    if claimed.rowcount != 1:
        raise DraftConflict("입력을 기다리는 초안이 아님")
    session.refresh(artwork)
    artwork.request, artwork.title = request.model_dump(mode="json"), intent.title
    artwork.families = [e.family.value for e in elements]
    session.add(artwork)
    session.commit()   # 이후 보류·실패의 rollback이 확정 값을 되돌리지 않게 먼저 저장한다
    steps = [Step(**s) for s in artwork.steps]
    steps.append(Step(name="확정", detail={"values": values, "origins": origins, "changed": changed,
                                         **({"notes": notes} if notes else {})}))
    from studio.head.feedback import for_planner

    saved_feedback = next((s.detail["snapshot"] for s in steps if s.name == "운영 피드백"), {"items": []})
    planner = for_planner(planner, saved_feedback)

    def plan() -> tuple[Intent, list[Element]]:
        steps.append(Step(name="의도", detail=intent.model_dump(mode="json", exclude_none=True)))
        steps.append(Step(name="구성", detail=[e.model_dump(mode="json") for e in elements]))
        return intent, elements

    return _build(session, artwork, steps, request, artwork.theme, plan, settings, planner, images, outlines,
                  rng or random.Random(), sources=saved.get("sources", {}))
