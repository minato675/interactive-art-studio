"""UE 사전 확인. 지원 범위와 생성 규격은 서버가 정하며 AI가 변경할 수 없다."""
import json
from pathlib import Path

from sqlmodel import select

from contracts.spec import unfilled_required
from contracts.ue import GenerationConditions
from db import jobs
from db.ue import UECapability

MAX_HEARTBEAT_AGE = 90


def catalog():
    return json.loads(Path(__file__).with_name("ue_templates.json").read_text("utf-8"))


def select_template(request, table=None):
    """table은 이미 읽은 템플릿 표. 여러 번 부르는 범위 계산(exhibition_feed)만 넘긴다."""
    spec, table = request.spec, table or catalog()
    if unfilled_required(spec):
        raise jobs.Conflict("작품 Spec의 필수 칸을 채운 뒤 UE 지원을 확인하세요")
    rule = spec.interactions[0]
    if spec.content.reveal in ("formation", "trace"):
        raise jobs.Conflict("형상·흔적을 지원하는 UE 템플릿이 없습니다")
    if len(spec.interactions) != 1 or rule.reaction_mode != "while" or rule.input not in ("appear", "move"):
        raise jobs.Conflict("현재 UE 사전 확인은 좌표 입력·while 반응·규칙 1개만 지원합니다")
    if spec.idle.motion not in (None, "ignore"):
        raise jobs.Conflict("요청한 평상시 움직임은 현재 UE 템플릿의 보장 범위 밖입니다")
    if rule.reaction in ("press", "swirl", "scatter"):
        raise jobs.Conflict("해당 반응은 MR !33의 동작 구분 검증이 남아 생성 전에 보류합니다")
    mapping = table["mapping"]
    selected = next((r["template"] for r in mapping["dedicated"] if rule.reaction in r["reaction"]
                     and ("medium" not in r or spec.content.medium in r["medium"])), None)
    if selected is None and rule.reaction in mapping["sprite_only"]:
        selected = mapping["by_medium"].get(spec.content.medium)
    entry = next((r for r in table["templates"] if r["template"] == selected), None)
    if entry is None:
        raise jobs.Conflict("요청한 반응·매질을 지원하는 UE 템플릿이 없습니다")
    slots = entry["slots"]
    if rule.reaction == "follow" and selected not in ("gpu_particles", "image_stir"):
        raise jobs.Conflict("불투명 캔버스 템플릿은 따라가는 객체를 지원하지 않습니다")
    usage = "style_reference" if slots["far_hidden"] else "canvas" if slots["far_target"] == "Canvas" else "display"
    dimension = spec.content.dimension
    return {"policy_version": 1, "template": selected, "fingerprint": entry["fingerprint"],
            "background_usage": usage, "allowed_render_modes": [str(dimension)] if dimension in ("2d", "2.5d")
            else ["2d", "2.5d"], "behaviors": [str(rule.reaction)] if rule.reaction in ("follow", "glow") else ["float"]}


def ready_nodes(session, request, selected, registry):
    ready, timestamp = [], jobs.now(session)
    for node_id, identity in registry.items():
        if (identity.get("role") != "production" or identity.get("catalog_version") != request.catalog_version
                or "ue_assemble" not in identity.get("job_kinds", [])):
            continue
        node, evidence = session.get(jobs.Node, node_id), session.get(UECapability, node_id)
        if (not node or not evidence or node.role != "production" or node.catalog_version != request.catalog_version
                or not 0 <= timestamp - node.heartbeat_at <= MAX_HEARTBEAT_AGE):
            continue
        if any(r["template"] == selected["template"] and r["fingerprint"] == selected["fingerprint"]
               for r in evidence.report["templates"]):
            ready.append(node_id)
    if not ready:
        raise jobs.Conflict("같은 카탈로그·템플릿 해시를 보고한 온라인 UE 제작 노드가 없습니다")
    return sorted(ready)


def preflight(session, request, registry):
    selected = select_template(request)
    if request.asset_count == 0 and set(selected["behaviors"]) & {"follow", "glow"}:
        raise jobs.Conflict("선택한 반응에는 객체가 필요해 asset_count=0으로 제작할 수 없습니다")
    return {"selection": selected, "compatible_nodes": ready_nodes(session, request, selected, registry)}


def conditions(selected, role, index=0):
    background = role == "background"
    notes = ["배경과 객체는 별도 PNG로 저장합니다. 글자·파일 경로·셸 명령을 이미지에 넣지 않습니다."]
    if background:
        notes += ["far 배경 한 장입니다. 중간·앞 레이어나 객체를 하나의 배경에 합성하지 않습니다.",
                  "1920x1080은 제작 권장 크기이며 실제 송출 해상도 확정은 별도입니다."]
        if selected["background_usage"] == "style_reference":
            notes.append("이 템플릿에서는 far 배경이 캔버스에 가립니다. 배경은 에셋의 색·조명 참고용입니다.")
    else:
        notes += ["투명 알파가 있는 독립 객체 한 장. 바깥 여백과 투명 픽셀을 1% 이상 확보합니다.",
                  "2.5D도 평면 RGBA 스프라이트입니다. 여러 시점 이미지·메시·깊이 지도를 요구하지 않습니다.",
                  "위치·깊이·행동은 UE 조립 메타데이터로 지정하며 이미지에 구워 넣지 않습니다."]
    return GenerationConditions(template=selected["template"], fingerprint=selected["fingerprint"], role=role,
        output_name="background.png" if background else f"asset-{index + 1:03d}.png",
        recommended_size=(1920, 1080) if background else (512, 512), alpha_required=not background,
        background_usage=selected["background_usage"], allowed_render_modes=["2d"] if background
        else selected["allowed_render_modes"], behaviors=[] if background else selected["behaviors"],
        layer_depth="far" if background else None, notes=notes)


def overview(session, registry):
    timestamp = jobs.now(session)
    nodes = []
    for evidence in session.exec(select(UECapability)).all():
        node, identity = session.get(jobs.Node, evidence.node_id), registry.get(evidence.node_id, {})
        if node and identity.get("role") == "production":
            nodes.append({"node_id": node.id, "catalog_version": node.catalog_version,
                          "online": 0 <= timestamp - node.heartbeat_at <= MAX_HEARTBEAT_AGE,
                          "report": evidence.report})
    return {"catalog": catalog(), "nodes": nodes, "runtime_verified": False}
