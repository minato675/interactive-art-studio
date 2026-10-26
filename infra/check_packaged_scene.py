"""운영 이미지의 설치된 패키지만으로 UE 장면과 Head 감독 지침을 검사한다."""
import base64
import hashlib
import json
import sysconfig
from importlib.metadata import distribution
from pathlib import Path

from interactive_controller.orchestration import compiler
from interactive_controller.orchestration.brief import rule_brief

from studio.assembly import ue_scene
from studio.head import director

DIRECTOR_PACKAGE_FILES = (
    "studio/head/director.py",
    "studio/head/skills/ranel-ue-media-art/SKILL.md",
    "studio/head/skills/ranel-ue-media-art/references/scenario-director.md",
    "studio/head/skills/ranel-ue-media-art/references/head-director-prompt.md",
)


def check_scenes():
    requests = ("ocean fish", "forest birds", "space particles", "flowerfield flower")
    for request in requests:
        brief = rule_brief(request)
        scene, _ = ue_scene.compile_scene(brief, {})
        errors = ue_scene.check(brief, scene)
        if errors:
            raise RuntimeError(f"{request}: {errors}")
        if not scene["Scene"]:
            raise RuntimeError(f"{request}: empty scene")
    return len(requests)


def check_director(installed: Path):
    """소스 체크아웃의 파일로 설치 누락을 가리지 못하게 wheel 기록과 실제 경로를 함께 본다."""
    installed = installed.resolve()
    package = distribution("a504-studio")
    packaged = {str(path): path for path in package.files or ()}
    if missing := set(DIRECTOR_PACKAGE_FILES) - packaged.keys():
        raise RuntimeError(f"Missing director package data: {sorted(missing)}")
    paths = {}
    for name in DIRECTOR_PACKAGE_FILES:
        path = Path(package.locate_file(name)).resolve()
        if not path.is_relative_to(installed) or not path.is_file():
            raise RuntimeError(f"Expected installed director package file: {name}")
        record = packaged[name]
        digest = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).decode().rstrip("=")
        if record.hash is None or record.hash.mode != "sha256" or record.hash.value != digest:
            raise RuntimeError(f"Director package file differs from wheel RECORD: {name}")
        paths[name] = path
    if Path(director.__file__).resolve() != paths[DIRECTOR_PACKAGE_FILES[0]]:
        raise RuntimeError("Expected a non-editable installed module: studio.head.director")
    prompt = paths[DIRECTOR_PACKAGE_FILES[-1]]
    if director.PROMPT_PATH.resolve() != prompt:
        raise RuntimeError("Head director prompt must come from the installed wheel")
    metadata = director.skill_metadata()
    if metadata != {"version": director.SKILL_VERSION, "sha256": hashlib.sha256(prompt.read_bytes()).hexdigest()}:
        raise RuntimeError("Head director metadata does not match the installed prompt")
    text = prompt.read_bytes().decode("utf-8")
    for phase in ("background", "assets", "review"):
        if not director.instructions(phase).startswith(text):
            raise RuntimeError(f"Head director prompt was not loaded for {phase}")
    return metadata


def main():
    installed = Path(sysconfig.get_path("purelib")).resolve()
    for module in (compiler, ue_scene):
        if not Path(module.__file__).resolve().is_relative_to(installed):
            raise RuntimeError(f"Expected a non-editable installed module: {module.__name__}")
    required = {
        "interactive_controller/contracts/presets/runtime-integration.json",
        "interactive_controller/contracts/catalogs/runtime-integration.json",
        "interactive_controller/contracts/scene-structs.json",
    }
    packaged = {str(path) for path in distribution("interactive-controller").files or ()}
    if missing := required - packaged:
        raise RuntimeError(f"Missing controller package data: {sorted(missing)}")
    print(json.dumps({"packaged_scene_checks": check_scenes(), "director_skill": check_director(installed),
                      "result": "passed"}))


if __name__ == "__main__":
    main()
