"""서버에 묶인 연출 지침과 계획 검증. 연출 의도 중 기존 UE 계약이 받는 값만 조립한다."""
import hashlib
import math
from pathlib import Path

from contracts.head import AssetsPlan, HeadRequest

PROMPT_PATH = Path(__file__).with_name("skills") / "ranel-ue-media-art" / "references" / "head-director-prompt.md"
SKILL_VERSION = "ranel-director-v2"
MAX_INSTANCES = 12
PHASE_INSTRUCTIONS = {
    "background": "배경 단계: 이후 객체를 배치할 공간과 선택 템플릿의 배경 용도를 고려해 BackgroundPlan을 작성한다.",
    "assets": "에셋 단계: 실제 입력 배경을 관찰하고, 원본 이미지 계획과 director의 인스턴스·연출 계획을 함께 작성한다.",
    "review": "검수 단계: 연출 계획·조립 산출물·실행 증거를 비교한다. 이미지나 계획만으로 실제 센서 반응을 승인하지 않는다.",
}


class SkillUnavailable(ValueError):
    """운영 화면에 그대로 전달할 수 있는 고정 문구의 번들 오류."""

    def __init__(self):
        super().__init__("묶음 Head director 지침이 없거나 유효하지 않습니다. 서버 패키지를 확인하세요")


def _prompt_bytes() -> bytes:
    try:
        raw = PROMPT_PATH.read_bytes()
        if not raw.decode("utf-8").strip():
            raise ValueError("empty prompt")
        return raw
    except (OSError, UnicodeError, ValueError):
        raise SkillUnavailable() from None


def skill_metadata() -> dict:
    return {"version": SKILL_VERSION, "sha256": hashlib.sha256(_prompt_bytes()).hexdigest()}


def instructions(phase: str) -> str:
    if phase not in PHASE_INSTRUCTIONS:
        raise ValueError("지원하지 않는 Head director 판단 단계입니다")
    return _prompt_bytes().decode("utf-8") + "\n\n" + PHASE_INSTRUCTIONS[phase]


def capabilities(selected: dict, request: HeadRequest) -> dict:
    """LLM에 전달할 현재 서버 경로의 능력. 기획과 현장 검증을 구분한다."""
    return {
        "chosen_template": selected["template"],
        "background_usage": selected["background_usage"],
        "requested_source_assets": request.asset_count,
        "max_source_assets": request.max_asset_count,
        "min_source_assets": 0,
        "empty_asset_plan": "assets=[] and instances=[]; no placeholder or deferred generation jobs",
        "max_instances": MAX_INSTANCES,
        "source_asset_index": "1-based; every source must be used at least once",
        "clones": "reuse the same source image file; no extra image generation",
        "compiled_instance_fields": ["id", "source_asset_index", "placement.u", "placement.v", "placement.size",
                                     "behaviors"],
        "coordinates": "normalized; top-left (0,0), bottom-right (1,1); size is screen-width ratio",
        "allowed_render_modes": selected["allowed_render_modes"],
        "allowed_behaviors": selected["behaviors"],
        "required_behaviors": [b for b in selected["behaviors"] if b in ("follow", "glow")],
        "static_behavior_list": [],
        "template_driven": {
            "effects": "glow only on instances assigned glow",
            "interactions": "coordinate input (position/appear/move) with assigned follow/glow only",
            "particles": "global gpu_particles field only; target_ids=[]; template defaults and supplied Spec only; "
                         "all proposed numeric values null; no instance-attached emitters",
        },
        "supplied_spec": request.spec.model_dump(mode="json"),
        "design_only": ["rotation", "tint", "depth", "custom effects", "custom particle numbers",
                        "timed cues", "gesture/sound input", "new interaction rules"],
        "evidence_limits": ["template_driven means representable, not applied or verified",
                            "artifact review does not prove real sensor input or exhibition readiness"],
    }


def validate_plan(plan: AssetsPlan, selected: dict, required: bool = False) -> None:
    direction = plan.director
    if direction is None:
        if required:
            raise ValueError("새 Head 에셋 계획에는 director 연출 계획이 필요합니다")
        return
    if direction.chosen_template != selected["template"]:
        raise ValueError("director가 사전 확인에서 고정한 템플릿을 변경했습니다")
    instances = direction.instances
    ids = [instance.id for instance in instances]
    records = [*direction.effects, *direction.interactions, *direction.particles, *direction.cues]
    all_ids = ["background", *ids, *(record.id for record in records)]
    if len({value.casefold() for value in all_ids}) != len(all_ids):
        raise ValueError("director의 인스턴스·연출 ID는 background를 포함해 서로 달라야 합니다")
    sources = {instance.source_asset_index for instance in instances}
    if sources != set(range(1, len(plan.assets) + 1)):
        raise ValueError("director source_asset_index가 존재하는 원본 에셋을 모두 한 번 이상 참조해야 합니다")
    allowed = set(selected["behaviors"])
    assigned = set()
    by_id = {instance.id: instance for instance in instances}
    for instance in instances:
        behaviors = set(instance.behaviors)
        if len(behaviors) != len(instance.behaviors) or not behaviors <= allowed:
            raise ValueError("director 인스턴스 행동은 중복 없이 선택 템플릿의 behaviors 범위 안이어야 합니다")
        interactive = bool(behaviors & {"follow", "glow"})
        if (instance.role == "interactor") != interactive:
            raise ValueError("director 역할은 실제 follow/glow 행동 여부와 일치해야 합니다")
        assigned.update(behaviors)
    if not allowed.intersection({"follow", "glow"}) <= assigned:
        raise ValueError("선택된 follow/glow 행동마다 최소 한 인스턴스가 필요합니다")
    for record in records:
        if len(set(record.target_ids)) != len(record.target_ids) or not set(record.target_ids) <= by_id.keys():
            raise ValueError("director target_ids는 중복 없이 존재하는 인스턴스 ID를 참조해야 합니다")
    for effect in direction.effects:
        if effect.execution_status == "template_driven" and (
            effect.kind != "glow" or not effect.target_ids
            or any("glow" not in by_id[target].behaviors for target in effect.target_ids)
        ):
            raise ValueError("glow로 지정한 인스턴스 효과 외의 추가 효과·변형은 design_only입니다")
    for interaction in direction.interactions:
        if interaction.execution_status == "template_driven" and (
            interaction.input not in ("position", "appear", "move")
            or interaction.reaction not in ("follow", "glow") or not interaction.target_ids
            or any(interaction.reaction not in by_id[target].behaviors for target in interaction.target_ids)
        ):
            raise ValueError("좌표 기반 follow/glow 외의 새 인터랙션은 design_only입니다")
    for particles in direction.particles:
        if particles.execution_status == "template_driven" and (
            selected["template"] != "gpu_particles" or particles.target_ids
            or any(value is not None for value in (particles.count, particles.lifetime_seconds, particles.speed))
        ):
            raise ValueError("전역 입자 템플릿 기본값 외의 대상 연결·입자 수·수명·속도 지정은 design_only입니다")
    if any(cue.execution_status != "design_only" for cue in direction.cues):
        raise ValueError("시간 큐는 현재 조립기에 연결되지 않았으므로 design_only입니다")


def assembly_objects(plan: AssetsPlan, asset_jobs: list, files: list) -> list[dict]:
    """배경 다음 원본 에셋 순서의 검증된 파일 목록을 기존 assets.json v1 객체로 연결한다."""
    if len(asset_jobs) != len(plan.assets) or len(files) != len(asset_jobs) + 1:
        raise ValueError("director 조립 원본 작업·파일 수가 에셋 계획과 다릅니다")
    for index, job in enumerate(asset_jobs, start=1):
        if files[index]["source_job_id"] != job["id"]:
            raise ValueError("director 조립 파일 순서가 원본 에셋 작업과 다릅니다")
    if plan.director is not None:
        objects = []
        for instance in plan.director.instances:
            if not 1 <= instance.source_asset_index <= len(asset_jobs):
                raise ValueError("director 조립이 존재하지 않는 원본 에셋을 참조했습니다")
            objects.append({"id": instance.id, "image": files[instance.source_asset_index]["name"],
                            "position": [instance.placement.u, instance.placement.v], "size": instance.placement.size,
                            "behaviors": list(instance.behaviors)})
        return objects
    # 기존 계획을 사용하는 호출자는 사전 확인의 behaviors를 작업 메타데이터에 명시한다.
    columns = math.ceil(math.sqrt(len(asset_jobs)))
    objects = []
    for index, job in enumerate(asset_jobs):
        if "behaviors" not in job:
            raise ValueError("기존 계획 조립에는 사전 확인의 behaviors가 필요합니다")
        placement = job.get("placement") or {
            "u": (0.5 + index % columns) / columns,
            "v": (0.5 + index // columns) / math.ceil(len(asset_jobs) / columns), "size": 0.15,
        }
        objects.append({"id": f"asset_{index + 1:03d}", "image": files[index + 1]["name"],
                        "position": [placement["u"], placement["v"]], "size": placement["size"],
                        "behaviors": list(job["behaviors"])})
    return objects
