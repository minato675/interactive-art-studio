"""자동 Head 사이클의 파일 확인과 작업 연결. 네트워크 AI 호출은 여기서 하지 않는다."""
import base64
import json
import math

from sqlmodel import select

from contracts.head import AssetsPlan, BackgroundInput
from contracts.infra import FileRef, JobRequest
from db import jobs
from studio.infra.storage import FileStore, verify


def result_file(settings, job, name, maximum=5 * 1024 * 1024):
    if job is None or job.status != "succeeded":
        raise jobs.Conflict("완료된 작업 결과가 필요합니다")
    ref = next((FileRef.model_validate(r) for r in job.result.get("files", []) if r["name"] == name), None)
    if ref is None or ref.size > maximum:
        raise jobs.Conflict("필수 결과 파일이 없거나 판단 입력 크기 상한을 넘었습니다")
    path = FileStore(settings.data_dir / "artifacts").path(ref)
    verify(path, ref)
    return path, ref


def image_input(settings, job, name):
    from .dispatch import image_bytes

    path, ref = result_file(settings, job, name)
    raw = path.read_bytes()
    mime = "image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else (
        "image/jpeg" if raw.startswith(b"\xff\xd8\xff") else "image/webp")
    url = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    image_bytes(BackgroundInput(name=name, data_url=url))
    return url, ref


def flow_update(row, **values):
    row.payload = {**row.payload, "_flow": {**row.payload["_flow"], **values}}


def prepare_background(session, row, settings):
    from .dispatch import queue_decision

    background = session.get(jobs.Job, row.background_job)
    name = background.payload["generation"]["conditions"]["output_name"]
    url, ref = image_input(settings, background, name)
    path = settings.data_dir / "head-inputs" / ref.sha256
    path.parent.mkdir(parents=True, exist_ok=True)
    # 이미지 재업로드 없이 기존 중앙 원본을 AI 입력으로 연결한다.
    raw = base64.b64decode(url.split(",", 1)[1])
    if not path.exists():
        import os
        import tempfile

        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".head-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            from pathlib import Path

            Path(temporary).unlink(missing_ok=True)
    row.image = {"sha256": ref.sha256, "mime": url[5:url.index(";")], "name": name}
    queue_decision(session, row, "assets")


def queue_assembly(session, row, settings):
    from . import director, ue_support
    from .dispatch import checked_request

    registry = json.loads(settings.agent_keys_json)
    request = checked_request(session, row, registry)
    spec = {k: row.payload[k] for k in ("artwork_id", "version", "catalog_version")}
    parents = [row.background_job, *(a["id"] for a in row.asset_jobs)]
    files, objects = [], []
    for index, parent_id in enumerate(parents):
        parent = session.get(jobs.Job, parent_id)
        name = parent.payload["generation"]["conditions"]["output_name"]
        result_file(settings, parent, name, maximum=getattr(settings, "artifact_max_bytes", 64 * 1024 * 1024))
        files.append({"source_job_id": parent.id, "source_name": name, "name": name})
        if index:
            asset = row.asset_jobs[index - 1]
            placement = asset.get("placement")
            columns = math.ceil(math.sqrt(len(row.asset_jobs)))
            fallback = {"u": (0.5 + (index - 1) % columns) / columns,
                        "v": (0.5 + (index - 1) // columns) / math.ceil(len(row.asset_jobs) / columns), "size": 0.15}
            placement = placement or fallback
            objects.append({"id": f"asset_{index:03d}", "image": name,
                            "position": [placement["u"], placement["v"]], "size": placement["size"],
                            "behaviors": row.payload["_ue"]["selection"]["behaviors"]})
    scenario = None
    if row.payload.get("_director"):
        latest = next(d["plan"] for d in reversed(row.decisions) if d["phase"] == "assets")
        plan = AssetsPlan.model_validate(latest)
        director.validate_plan(plan, row.payload["_ue"]["selection"], required=True)
        objects = director.assembly_objects(plan, row.asset_jobs, files)
        scenario = plan.director.model_dump(mode="json")
    assets = {"format": "artwork-assets", "version": 1,
              "layers": [{"id": "background", "image": files[0]["name"], "depth": "far"}], "objects": objects}
    body = JobRequest(request_id=f"head-{row.id}-assemble-r{row.payload['_flow']['round']}", kind="ue_assemble",
                      file_transport="http", depends_on=parents, assembly={**spec, "spec": request.spec.model_dump(
                          mode="json"), "assets": assets, "files": files, "target_node":
                          ue_support.preflight(
                              session, request, registry)["compatible_nodes"][0]})
    payload = body.model_dump(mode="json")
    payload["_head"] = {"workflow_id": row.id, "round": row.payload["_flow"]["round"],
                        "template": row.payload["_ue"]["selection"],
                        "director_skill": row.payload.get("_director"), "scenario": scenario}
    # 고정 request_id로 조립 작업의 중복 등록을 막는다.
    child = jobs.enqueue(session, payload, commit=False)
    flow_update(row, assembly_job=child.id, asset_count=len(objects), source_asset_count=len(row.asset_jobs))
    row.status = "waiting_assembly"


def advance_one(session, row, settings):
    from .dispatch import queue_decision

    if row.status == "needs_attention":
        return
    if row.status.startswith("planning_"):
        decision = session.get(jobs.Job, row.decision_job)
        if decision and decision.status == "failed":
            row.status = "needs_attention"
            flow_update(row, hold="head_decision_failed")
    elif row.status in ("waiting_background", "awaiting_background_input"):
        background = session.get(jobs.Job, row.background_job)
        if background and background.status == "succeeded":
            prepare_background(session, row, settings)
        elif background and background.status == "failed":
            row.status = "needs_attention"
            flow_update(row, hold="background_generation_failed")
    elif row.status == "waiting_assets":
        states = [session.get(jobs.Job, a["id"]).status for a in row.asset_jobs]
        if (states or row.payload.get("_director")) and all(s == "succeeded" for s in states):
            queue_assembly(session, row, settings)
        elif "failed" in states:
            row.status = "needs_attention"
            flow_update(row, hold="asset_generation_failed")
    elif row.status in ("waiting_assembly", "waiting_verification"):
        assembly = session.get(jobs.Job, row.payload["_flow"]["assembly_job"])
        if assembly.status == "failed":
            row.status = "needs_attention"
            flow_update(row, hold="assembly_failed")
        elif assembly.status == "succeeded":
            verification = session.exec(select(jobs.Job).where(jobs.Job.request_id == f"verify-{assembly.id}")).first()
            if verification:
                flow_update(row, verification_job=verification.id)
                row.status = "waiting_verification"
                if verification.status == "succeeded":
                    queue_decision(session, row, "review")
                elif verification.status == "failed":
                    row.status = "needs_attention"
                    flow_update(row, hold="runtime_execution_failed")
    elif row.status == "waiting_publication":
        publication = session.get(jobs.Job, row.payload["_flow"]["publication_job"])
        if publication.status == "succeeded" and publication.result.get("phase") == "Applied":
            row.status = "completed"
        elif publication.status == "failed":
            row.status = "needs_attention"
            flow_update(row, hold="publication_failed")
    session.add(row)
