"""작업 소유권으로 제한하는 스트리밍 파일 전송. 임의 해시 조회는 허용하지 않는다."""
import hashlib
import os
import tempfile
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlmodel import select

from contracts.infra import FileRef
from db import jobs
from db.files import UploadedFile

from .api import SessionDep
from .artifacts import checked_path, result_refs, store
from .infra_api import AgentDep, operator

router = APIRouter(prefix="/infra", tags=["files"])
LeaseToken = Annotated[str, Header(alias="X-Lease-Token", min_length=16, max_length=128)]
DigestQuery = Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")]


@router.put("/agent/jobs/{job_id}/files")
async def upload(job_id: str, request: Request, session: SessionDep, identity: AgentDep, lease: LeaseToken,
                 name: Annotated[str, Query(min_length=1, max_length=200)], sha256: DigestQuery,
                 size: Annotated[int, Query(ge=0)]):
    try:
        ref = FileRef(name=name, sha256=sha256, size=size)
    except ValueError:
        raise HTTPException(422, "잘못된 파일 이름") from None
    settings = request.app.state.settings
    if size > settings.artifact_max_bytes:
        raise HTTPException(413, "파일 크기 상한 초과")
    row = jobs.owned(session, job_id, identity["node_id"], lease)
    if row.kind not in ("background_generate", "assets_generate", "ue_assemble", "ue_verify", "ue_publish"):
        raise HTTPException(403, "이 작업은 결과 파일 업로드를 지원하지 않습니다")
    attempt = row.attempt
    session.rollback()  # 전송 대기 중 행 잠금을 잡지 않는다.
    objects = store(settings).root / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=objects, prefix=".upload-")
    from pathlib import Path

    path = Path(temporary)
    try:
        digest, received = hashlib.sha256(), 0
        with os.fdopen(fd, "wb") as output:
            async for block in request.stream():
                received += len(block)
                if received > size or received > settings.artifact_max_bytes:
                    raise HTTPException(413, "선언한 파일 크기 초과")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if (digest.hexdigest(), received) != (ref.sha256, ref.size):
            raise HTTPException(422, "파일 해시 또는 크기 불일치")
        row = jobs.owned(session, job_id, identity["node_id"], lease)
        if row.attempt != attempt:
            raise jobs.Conflict("파일 전송 중 시도가 변경되었습니다")
        prior = session.get(UploadedFile, (job_id, attempt, name))
        if prior and (prior.sha256, prior.size) != (sha256, size):
            raise jobs.Conflict("같은 시도에서 업로드한 파일을 변경할 수 없습니다")
        count = len(session.exec(select(UploadedFile).where(
            UploadedFile.job_id == job_id, UploadedFile.attempt == attempt)).all())
        if not prior and count >= 256:
            raise HTTPException(413, "작업별 파일 수 상한 초과")
        store(settings).ingest(path, name)
        if prior is None:
            session.add(UploadedFile(job_id=job_id, attempt=attempt, **ref.model_dump()))
        session.commit()
        return ref.model_dump()
    finally:
        path.unlink(missing_ok=True)


@router.get("/agent/jobs/{job_id}/files/{sha256}")
def download(job_id: str, sha256: str, request: Request, session: SessionDep, identity: AgentDep, lease: LeaseToken):
    row = jobs.owned(session, job_id, identity["node_id"], lease)
    refs = [FileRef(name=r.name, sha256=r.sha256, size=r.size) for r in session.exec(select(UploadedFile).where(
        UploadedFile.job_id == job_id, UploadedFile.attempt == row.attempt)).all()]
    refs.extend(FileRef.model_validate(f) for f in (row.payload.get("bundle") or {}).get("files", []))
    for parent_id in row.payload.get("depends_on", []):
        refs.extend(result_refs(session.get(jobs.Job, parent_id)))
    ref = next((f for f in refs if f.sha256 == sha256), None)
    if not ref:
        raise HTTPException(404, "이 작업에 허용된 입력·출력 파일이 아닙니다")
    path = checked_path(request.app.state.settings, ref)
    session.rollback()
    return FileResponse(path, media_type="application/octet-stream", filename=ref.name.rsplit("/", 1)[-1])


@router.get("/jobs/{job_id}/files/{sha256}", dependencies=[Depends(operator)])
def operator_download(job_id: str, sha256: str, request: Request, session: SessionDep):
    row = session.get(jobs.Job, job_id)
    ref = next((f for f in result_refs(row) if f.sha256 == sha256), None) if row else None
    if not ref:
        raise HTTPException(404, "등록된 작업 결과 파일이 아닙니다")
    return FileResponse(checked_path(request.app.state.settings, ref), media_type="application/octet-stream",
                        filename=ref.name.rsplit("/", 1)[-1])
