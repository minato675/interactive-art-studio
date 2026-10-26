"""중앙 파일 저장소의 완료·조회 검사. 인증은 API 경계에서 수행한다."""
from pathlib import Path

from sqlmodel import select

from contracts.infra import FileRef
from db import jobs
from db.files import UploadedFile
from studio.infra.storage import FileStore, verify


def store(settings):
    return FileStore(settings.data_dir / "artifacts")


def require_uploaded(session, settings, row, result):
    if row.payload.get("file_transport") != "http":
        return
    receipts = {r.name: r for r in session.exec(select(UploadedFile).where(
        UploadedFile.job_id == row.id, UploadedFile.attempt == row.attempt)).all()}
    for ref in result.files:
        receipt = receipts.get(ref.name)
        if receipt is None or (receipt.sha256, receipt.size) != (ref.sha256, ref.size):
            raise jobs.Conflict("완료 전에 결과 파일을 현재 시도로 업로드해야 합니다")
        checked_path(settings, ref)


def checked_path(settings, ref: FileRef) -> Path:
    path = store(settings).path(ref)
    try:
        verify(path, ref)
    except (OSError, ValueError):
        raise jobs.Conflict("중앙 결과 파일이 없거나 무결성이 다릅니다") from None
    return path


def result_refs(row):
    return [FileRef.model_validate(f) for f in row.result.get("files", [])] if row.status == "succeeded" else []
