"""명시적 사람 승인과 Head AI 승인이 공유하는 게시 작업 등록."""
from contracts.infra import JobRequest
from db import jobs


def queue_publication(session, verification, *, request_id, target_node, reviewer, reason, registry, commit=False):
    if not verification or verification.kind != "ue_verify" or verification.status != "succeeded":
        raise jobs.Conflict("완료된 실행 검증 작업을 지정하세요")
    if verification.result.get("verdict") not in ("pass", "review"):
        raise jobs.Conflict("fail 판정은 게시할 수 없습니다")
    previous = verification.result.get("human_review")
    if previous and previous["request"]["decision"] == "reject":
        raise jobs.Conflict("사람 검수에서 반려된 작품은 게시할 수 없습니다")
    node = registry.get(target_node, {})
    if (node.get("role") != "show" or not node.get("enabled", True)
            or "ue_publish" not in node.get("job_kinds", [])):
        raise jobs.Conflict("등록된 시연 실행기가 필요합니다")
    runtime = {**verification.payload["runtime"], "target_node": target_node}
    if node.get("catalog_version") != runtime["catalog_version"]:
        raise jobs.Conflict("시연 실행기의 카탈로그가 다릅니다")
    payload = JobRequest(request_id=request_id, kind="ue_publish", runtime=runtime, file_transport="http",
                         depends_on=[runtime["source_job_id"], verification.id]).model_dump(mode="json")
    payload["_approval"] = {"reviewer": reviewer, "reason": reason, "verification_job_id": verification.id,
                            "verdict": verification.result["verdict"]}
    return jobs.enqueue(session, payload, commit=commit)
