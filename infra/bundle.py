"""UE가 읽을 완성 폴더를 공유 저장소에 넣고 전송용 manifest를 만든다. 자동 검수 승인이 아니다."""
import argparse
import json
from pathlib import Path

from contracts.infra import Bundle
from studio.infra.storage import FileStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--artwork", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--catalog", default="v1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.source.resolve(strict=True)
    destination = args.store.resolve()
    if destination.is_relative_to(root) or args.output.resolve().is_relative_to(root):
        parser.error("저장소·출력 manifest는 source 폴더 밖에 둡니다")
    if args.output.exists():
        parser.error("기존 manifest는 덮어쓰지 않습니다")
    files = sorted(p for p in root.rglob("*") if p.is_file())
    if not files or len(files) > 256:
        parser.error("현재 계약은 1~256개 파일을 지원합니다")
    if any(not p.resolve().is_relative_to(root) for p in files):
        parser.error("source 밖을 가리키는 심볼릭 링크를 허용하지 않습니다")
    store = FileStore(destination)
    refs = [store.ingest(path, path.relative_to(root).as_posix()) for path in files]
    bundle = Bundle(artwork_id=args.artwork, version=args.version, catalog_version=args.catalog, files=refs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(bundle.model_dump_json(indent=2))
    print(json.dumps({"files": len(refs), "total_bytes": sum(ref.size for ref in refs),
                      "manifest": str(args.output), "publish_approved": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
