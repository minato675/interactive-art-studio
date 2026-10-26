"""넘기기 전 점검: 결과 폴더(assets.json + PNG)를 UE 조립기 assemble()로 미리 조립해 UE 계약 검사까지 통과하는지 본다.

실행: uv run python infra/generation/handoff_check.py spec.json choice.json <결과 폴더> [--scene 미리보기.json]
결과 폴더에는 넘길 파일만 둔다. 미리보기 scene.json은 결과 폴더 밖에 쓴다. 렌더링은 하지 않는다.
"""
import argparse
import json
import sys
from pathlib import Path

from contracts.spec import Spec

try:
    from contracts.assets import ArtworkAssets
    from studio.assembly.ue_assemble import TemplateChoice, assemble
except ImportError:   # UE 입력 계약 · 조립기 패키지를 불러오지 못한 환경
    sys.exit("UE 조립기(studio.assembly.ue_assemble)를 불러오지 못했습니다. 저장소 루트에서 uv sync 후 실행하세요")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path)
    parser.add_argument("choice", type=Path)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--scene", type=Path, help="통과하면 조립된 scene.json을 여기에 저장(결과 폴더 밖)")
    args = parser.parse_args()
    # client.py --complete는 결과 폴더를 하위까지 모두 올리므로, 하위 폴더 안도 막는다(.. 등은 정규화한 실제 경로로 비교)
    if args.scene and args.scene.resolve().is_relative_to(args.folder.resolve()):
        sys.exit("미리보기 scene.json은 결과 폴더 밖에 저장합니다")
    spec = Spec.model_validate_json(args.spec.read_text(encoding="utf-8"))
    choice = TemplateChoice(**json.loads(args.choice.read_text(encoding="utf-8")))
    assets = ArtworkAssets.model_validate_json((args.folder / "assets.json").read_text(encoding="utf-8"))
    unlisted = sorted({path.name for path in args.folder.iterdir()} - set(assets.files()) - {"assets.json"})
    result = assemble(spec, assets, args.folder, choice)
    report = {"passed": result.scene is not None, "template": result.template, "errors": result.errors,
              "warnings": result.warnings, "files": sorted(result.files) if result.files else [],
              "unlisted_files": unlisted}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if result.scene is not None and args.scene:
        args.scene.write_text(json.dumps(result.scene, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(0 if result.scene is not None else 1)


if __name__ == "__main__":
    main()
