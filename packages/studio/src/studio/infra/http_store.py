"""임대한 작업 범위의 중앙 HTTP 저장소. 원본 파일은 로컬 캐시에서 검증한 뒤 노출한다."""
import os
import tempfile
from pathlib import Path

from contracts.infra import FileRef

from .storage import CHUNK, FileStore, digest_file, verify


class HTTPStore:
    def __init__(self, client, job: dict, root: Path, tick=lambda: None):
        self.client, self.job, self.local, self.tick = client, job, FileStore(root), tick

    @property
    def headers(self):
        return {"X-Lease-Token": self.job["token"]}

    def path(self, ref: FileRef):
        target = self.local.path(ref)
        if target.exists():
            verify(target, ref, self.tick)
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".download-")
        try:
            with os.fdopen(fd, "wb") as output, self.client.http.stream(
                "GET", f"/infra/agent/jobs/{self.job['id']}/files/{ref.sha256}", headers=self.headers
            ) as response:
                response.raise_for_status()
                size = 0
                for block in response.iter_bytes(CHUNK):
                    size += len(block)
                    if size > ref.size:
                        raise ValueError("다운로드 크기 초과")
                    output.write(block)
                    self.tick()
                output.flush()
                os.fsync(output.fileno())
            verify(Path(temporary), ref, self.tick)
            os.replace(temporary, target)
            return target
        finally:
            Path(temporary).unlink(missing_ok=True)

    def ingest(self, source: Path, name: str) -> FileRef:
        digest, size = digest_file(source, self.tick)
        ref = FileRef(name=name, sha256=digest, size=size)

        def blocks():
            with source.open("rb") as src:
                while block := src.read(CHUNK):
                    self.tick()
                    yield block

        response = self.client.http.put(f"/infra/agent/jobs/{self.job['id']}/files", params=ref.model_dump(),
                                        headers={**self.headers, "Content-Type": "application/octet-stream",
                                                 "Content-Length": str(size)}, content=blocks())
        response.raise_for_status()
        if FileRef.model_validate(response.json()) != ref:
            raise ValueError("중앙 업로드 영수증 불일치")
        return ref
