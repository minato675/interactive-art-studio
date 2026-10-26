"""공유 폴더를 SHA-256 저장소로 사용한다. SMB 마운트/인증은 OS가 담당한다."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from contracts.infra import Bundle, FileRef

CHUNK = 1024 * 1024


def digest_file(path: Path, tick=lambda: None) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while block := stream.read(CHUNK):
            digest.update(block)
            size += len(block)
            tick()
    return digest.hexdigest(), size


def verify(path: Path, ref: FileRef, tick=lambda: None):
    if digest_file(path, tick) != (ref.sha256, ref.size):
        raise ValueError(f"파일 무결성 불일치: {ref.name}")


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class FileStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(self, ref: FileRef) -> Path:
        candidate = (self.root / "objects" / ref.sha256[:2] / ref.sha256).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("저장소 밖 경로")
        return candidate

    def ingest(self, source: Path, name: str) -> FileRef:
        """대용량 파일도 메모리에 통째로 올리지 않는다. 해시별 한 개만 보관한다."""
        objects = self.root / "objects"
        objects.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=objects, prefix=".upload-")
        try:
            digest, size = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as out, source.open("rb") as src:
                while block := src.read(CHUNK):
                    out.write(block)
                    digest.update(block)
                    size += len(block)
                out.flush()
                os.fsync(out.fileno())
            ref = FileRef(name=name, sha256=digest.hexdigest(), size=size)
            destination = self.path(ref)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                verify(destination, ref)
            else:
                os.replace(temporary, destination)
            return ref
        finally:
            Path(temporary).unlink(missing_ok=True)


def release_version(bundle: Bundle) -> str:
    # 구분 문자를 포함한 ID도 충돌하지 않도록 쌍 전체를 해시한다. 상태 계약의 96자 이내다.
    key = hashlib.sha256(f"{bundle.artwork_id}\0{bundle.version}".encode()).hexdigest()[:32]
    return f"{bundle.artwork_id[:12]}-{bundle.version[:12]}-{key}"


def stage_bundle(store: FileStore, cache: Path, bundle: Bundle, tick=lambda: None,
                 reserve_bytes: int = 256 * 1024 * 1024) -> Path:
    """완료된 버전만 보인다. 실패 시 현재 버전/이미 검증한 캐시는 건드리지 않는다."""
    cache = cache.resolve()
    versions = cache / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    target = (versions / release_version(bundle)).resolve()
    if not target.is_relative_to(versions.resolve()):
        raise ValueError("캐시 밖 경로")
    manifest = bundle.model_dump(mode="json")
    if target.exists():
        if json.loads((target / "bundle.json").read_text("utf-8")) != manifest:
            raise ValueError("이미 저장된 작품 버전을 수정할 수 없습니다")
        for ref in bundle.files:
            verify(target / ref.name, ref, tick)
        return target
    if any(f.name.casefold() == "bundle.json" for f in bundle.files):
        raise ValueError("bundle.json은 시스템이 사용하는 파일 이름입니다")
    needed = sum(f.size for f in bundle.files)
    if shutil.disk_usage(cache).free < needed * 2 + reserve_bytes:
        raise OSError("캐시 여유 공간 부족")
    object_cache = FileStore(cache)
    with tempfile.TemporaryDirectory(dir=versions, prefix=".prepare-") as temporary:
        staging = Path(temporary)
        for ref in bundle.files:
            cached = object_cache.path(ref)
            if cached.exists():
                verify(cached, ref, tick)
            else:
                # 네트워크 전송 중 임대 소유권이 사라지면 tick이 중단한다.
                cached.parent.mkdir(parents=True, exist_ok=True)
                fd, partial = tempfile.mkstemp(dir=cached.parent, prefix=".download-")
                try:
                    with os.fdopen(fd, "wb") as out, store.path(ref).open("rb") as src:
                        while block := src.read(CHUNK):
                            out.write(block)
                            tick()
                        out.flush()
                        os.fsync(out.fileno())
                    verify(Path(partial), ref, tick)
                    os.replace(partial, cached)
                finally:
                    Path(partial).unlink(missing_ok=True)
            destination = staging / ref.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            # 캐시와 버전은 같은 로컬 디스크. 복사는 로컬이며 원본 공유망을 다시 읽지 않는다.
            shutil.copyfile(cached, destination)
            tick()
        atomic_json(staging / "bundle.json", manifest)
        os.replace(staging, target)
    return target
