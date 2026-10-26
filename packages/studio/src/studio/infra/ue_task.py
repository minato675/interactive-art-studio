"""통신 계약을 UE MR의 조립기에 연결한다. UE 원본 코드나 Shipping 실행을 변경하지 않는다."""
import shutil
import tempfile
from pathlib import Path

from contracts.infra import AssemblySpec, FileRef
from contracts.spec import Spec

from .storage import atomic_json, verify


def assemble_job(job, store, cache: Path, tick=lambda: None):
    # 조립 모듈은 제작 서버에서 MR !33 패키지가 설치됐을 때만 필요하다.
    from contracts.assets import ArtworkAssets
    from studio.assembly.ue_assemble import assemble, select_template

    spec = AssemblySpec.model_validate(job["payload"]["assembly"])
    parents = {p["id"]: p for p in job["inputs"]}
    root = cache / "assembly"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix=".job-") as temporary:
        folder = Path(temporary)
        for source in spec.files:
            parent = parents[source.source_job_id]
            ref = next(FileRef.model_validate(f) for f in parent["result"]["files"] if f["name"] == source.source_name)
            path = store.path(ref)
            verify(path, ref, tick)
            destination = folder / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            tick()
        work = Spec.model_validate(spec.spec)
        assets = ArtworkAssets.model_validate(spec.assets)
        result = assemble(work, assets, folder, select_template(work))
        if result.errors or result.scene is None:
            raise ValueError("ue_assembly_rejected")
        output = folder / ".output"
        output.mkdir()
        atomic_json(output / "scene.json", result.scene)
        atomic_json(output / "assembly-report.json", {"template": result.template, "warnings": result.warnings,
                                                       "substitutions": result.substitutions, "errors": [],
                                                       "publish_approved": False, "runtime_verified": False})
        for name, path in result.files.items():
            # 출력 이름도 공통 계약으로 검사하고 시스템 파일과의 충돌을 막는다.
            FileRef(name=name, sha256="0" * 64, size=0)
            if name.split("/")[0].casefold() in {"scene.json", "assembly-report.json"}:
                raise ValueError("ue_output_name_collision")
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        refs = [store.ingest(path, path.relative_to(output).as_posix()) for path in sorted(output.rglob("*"))
                if path.is_file()]
        tick()
        return {"artwork_id": spec.artwork_id, "version": spec.version, "files": [r.model_dump() for r in refs]}
