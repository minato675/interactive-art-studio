"""렌더러(TD·UE) 공통 전달: 버전 폴더를 다 쓴 뒤 옮기고, 마지막에 release.json을 원자 교체한다.

  <delivery>/versions/<V>/<main 파일> (+ 함께 보낼 파일)   공개 후 수정하지 않음
  <delivery>/release.json                                 {"SchemaVersion":1,"Version":"<V>"}
렌더러는 release.json이 바뀌면 새 버전을 미리 준비한 뒤 교체하고, 실패하면 지금 작품을 유지한다.
"""
import json
import shutil
from pathlib import Path

from interactive_controller.delivery.publisher import replace_with_retry


def prepare_version(delivery_dir: Path, version: str, main_name: str, main: dict, files: dict[str, Path]) -> Path:
    """불변 후보 파일만 준비한다. 활성 release.json은 읽거나 쓰지 않는다."""
    if not version or Path(version).name != version or version in (".", ".."):
        raise ValueError("버전은 경로가 아닌 이름이어야 합니다")
    if Path(main_name).name != main_name or main_name in (".", ".."):
        raise ValueError("주 파일명에 경로를 쓸 수 없음")
    staging = delivery_dir / "staging" / f"{version}.partial"
    target = delivery_dir / "versions" / version
    if target.exists():
        raise RuntimeError(f"버전 {version}이 이미 있음 (게시한 버전은 수정하지 않음)")
    staging.mkdir(parents=True)
    try:
        for name, source in files.items():
            if Path(name).name != name or name == main_name or name in (".", ".."):
                raise RuntimeError(f"파일명에 경로를 쓸 수 없음: {name}")
            shutil.copyfile(source, staging / name)
        (staging / main_name).write_text(json.dumps(main, ensure_ascii=False, indent=2), "utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        replace_with_retry(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target / main_name


def publish_version(delivery_dir: Path, version: str, main_name: str, main: dict, files: dict[str, Path]) -> Path:
    """기존 단일 렌더러용 명시적 게시. 제작 파이프라인은 prepare_version만 사용한다."""
    result = prepare_version(delivery_dir, version, main_name, main, files)
    pointer = delivery_dir / "release.json"
    temporary = pointer.with_name(f"release.json.{version}.tmp")
    temporary.write_text(json.dumps({"SchemaVersion": 1, "Version": version}), "utf-8")
    replace_with_retry(temporary, pointer)   # 렌더러가 release.json을 읽는 순간 Windows가 거부하면 잠깐 뒤 재시도
    return result
