"""생성 전 점검: 명세 → 템플릿 선택(UE 조립기와 같은 함수) → 만들 재료 목록. 보류면 생성하지 않는다.

실행: uv run python infra/generation/preflight.py spec.json --choice choice.json
선택 결과(choice.json)는 넘기기 전 점검과 UE 조립에서 그대로 다시 쓴다(조립 때 다시 고르지 않음).
"""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from contracts.spec import Spec, unfilled_required

try:
    from studio.assembly.ue_assemble import TemplateChoice, select_template, template_slots
except ImportError:   # 조립기 패키지를 불러오지 못한 환경
    sys.exit("UE 조립기(studio.assembly.ue_assemble)를 불러오지 못했습니다. 저장소 루트에서 uv sync 후 실행하세요")


def recipe(template: str) -> list[str]:
    """템플릿 slots.json에서 만들 재료를 읽는다. far_hidden이면 뒤 층이 불투명 효과 캔버스에 가려진다."""
    slots = template_slots(template)
    layers, hidden = slots["layers"], slots["far_hidden"]
    if layers == ["far"]:
        return ["far.png 1장: 불투명 가능, 16:9. 이 그림 전체가 손으로 휘저어지는 캔버스가 됨"]
    items = ["far.png: 불투명 가능, 16:9" + (" (불투명 효과 캔버스에 거의 가려짐)" if hidden else "")]
    need = "필수" if hidden else "선택"
    items.append(f"mid.png · near.png: 알파 필수, 16:9 — {need}" + (" (그림이 보이려면 이 층이 있어야 함)" if hidden else ""))
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path)
    parser.add_argument("--choice", type=Path, help="선택 결과를 저장할 파일")
    args = parser.parse_args()
    spec = Spec.model_validate_json(args.spec.read_text(encoding="utf-8"))
    missing = unfilled_required(spec)
    if missing:   # 필수 칸이 비면 템플릿 선택 자체를 믿을 수 없으므로 고르기 전에 보류한다
        choice = TemplateChoice(holds=[f"필수 칸이 비어 있어 보류: {', '.join(missing)}"])
    else:
        choice = select_template(spec)
    ready = choice.template is not None and not choice.holds
    report = {"template": choice.template, "holds": choice.holds, "substitutions": choice.substitutions,
              "required_behaviors": choice.required_behaviors, "unfilled_required": missing,
              "generate": recipe(choice.template) if ready else []}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.choice:
        if ready:
            args.choice.write_text(json.dumps(asdict(choice), ensure_ascii=False, indent=2), encoding="utf-8")
        else:   # 이전 실행의 선택 결과를 이번 명세에 잘못 재사용하지 않게 지운다
            args.choice.unlink(missing_ok=True)
    sys.exit(0 if ready else 1)


if __name__ == "__main__":
    main()
