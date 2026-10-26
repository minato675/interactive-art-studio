"""설치된 UE 조립 리소스를 조사한다. 키·DB·중앙 패키지에 의존하지 않는다."""
import argparse
import hashlib
import json
from pathlib import Path

TEMPLATES = ("gpu_particles", "image_stir", "fluid", "paint", "deformation")


def installed_catalog():
    from contracts import assets, spec
    from studio.assembly import ue_assemble as ue
    from studio.verify import ue_contract

    root = Path(ue.__file__).parent / "ue_templates"
    common = {"ue_assemble.py": Path(ue.__file__), "assets.py": Path(assets.__file__), "spec.py": Path(spec.__file__),
              "mapping-v1.json": root / "mapping-v1.json", "looks/neutral.json": root / "looks/neutral.json"}
    common["ue_contract.py"] = Path(ue_contract.__file__)
    common.update({"ue_contract/" + path.name: path for path in ue_contract.DATA.glob("*.json")})
    templates = []
    for name in TEMPLATES:
        files = {**common, "scene.json": root / name / "scene.json", "slots.json": root / name / "slots.json"}
        if not all(p.is_file() for p in files.values()) or ue.compatibility(name):
            continue
        hashes = {}
        for key, path in files.items():
            text = path.read_text("utf-8-sig")
            canonical = json.dumps(json.loads(text), sort_keys=True, separators=(",", ":")) if key.endswith(".json") else text
            hashes[key] = hashlib.sha256(canonical.encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        templates.append({"template": name, "fingerprint": fingerprint, "files": hashes,
                          "slots": json.loads(files["slots.json"].read_text("utf-8-sig"))})
    return {"version": 1, "mapping": json.loads((root / "mapping-v1.json").read_text("utf-8-sig")),
            "templates": templates}


def report():
    catalog = installed_catalog()
    return {"version": 1, "templates": [{k: item[k] for k in ("template", "fingerprint")}
                                        for item in catalog["templates"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-catalog", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--source-merge-request", type=int, default=33)
    args = parser.parse_args()
    if args.export_catalog:
        if not args.source_revision:
            parser.error("중앙 카탈로그 내보내기는 --source-revision이 필요합니다")
        catalog = installed_catalog()
        catalog["source"] = {"merge_request": args.source_merge_request, "revision": args.source_revision}
        args.export_catalog.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"exported_templates": len(catalog["templates"])}))
    else:
        print(json.dumps(report()))


if __name__ == "__main__":
    main()
