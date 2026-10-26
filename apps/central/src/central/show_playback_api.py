"""시연 노드 자신의 승인된 재생목록·불변 파일 조회. 임의 결과 파일 조회는 허용하지 않는다."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from contracts.show_dashboard import ShowPlan
from db import jobs

from .api import SessionDep
from .artifacts import checked_path, result_refs
from .infra_api import AgentDep
from .show_dashboard_api import candidate, configuration

router = APIRouter(prefix="/infra/agent/show-playlist", tags=["show-playback"])


def own_configuration(session, identity):
    if identity["role"] != "show" or "ue_publish" not in identity.get("job_kinds", []):
        raise HTTPException(403, "등록된 시연 실행기만 재생목록을 조회합니다")
    return configuration(session, identity["node_id"])


def entry(session, verification_id, identity):
    item = candidate(session, session.get(jobs.Job, verification_id))
    if not item or item["catalog_version"] != identity["catalog_version"]:
        raise HTTPException(409, "작품 승인·원본·시연 카탈로그가 변경되었습니다")
    source = session.get(jobs.Job, item["source_job_id"])
    return {**item, "runtime_version": "ue-" + source.id,
            "files": [ref.model_dump() for ref in result_refs(source)]}


@router.get("")
def playlist(session: SessionDep, identity: AgentDep):
    config = own_configuration(session, identity)
    entries = []
    for item in ShowPlan.model_validate(config["plan"]).items:
        try:
            entries.append(entry(session, item.verification_job_id, identity))
        except HTTPException:
            # 한 작품이 반려돼도 중지·제거 등 새 구성과 나머지 작품을 동기화한다.
            entries.append({"verification_job_id": item.verification_job_id, "error": "artwork_not_approved"})
    return {"node_id": identity["node_id"], "catalog_version": identity["catalog_version"],
            "revision": config["revision"], "plan": config["plan"], "entries": entries}


@router.get("/{verification_id}/files/{sha256}")
def download(verification_id: str, sha256: str, request: Request, session: SessionDep, identity: AgentDep):
    config = own_configuration(session, identity)
    if verification_id not in {item["verification_job_id"] for item in config["plan"]["items"]}:
        raise HTTPException(404, "이 시연 노드의 재생목록에 없는 작품입니다")
    item = entry(session, verification_id, identity)
    from contracts.infra import FileRef

    ref = next((FileRef.model_validate(ref) for ref in item["files"] if ref["sha256"] == sha256), None)
    if ref is None:
        raise HTTPException(404, "이 작품에 등록된 파일이 아닙니다")
    path = checked_path(request.app.state.settings, ref)
    session.rollback()
    return FileResponse(path, media_type="application/octet-stream", filename=ref.name.rsplit("/", 1)[-1])
