"""보고 근거만 사용한 시연 표시 모델. 편집 목록이나 UI 시계로 재생을 만들어내지 않는다."""

PHASES = {"Idle": "적용 전", "Staged": "파일 준비 · UE 적용 대기", "Ready": "UE 준비 완료",
          "Applied": "활성 작품 적용 보고", "Failed": "적용 실패", "Unknown": "UE 상태 미확인"}
PLAYBACK = {"Disabled": "자동 전환 꺼짐 · 현재 화면 유지", "Empty": "재생목록 비어 있음",
            "WaitingForRuntime": "UE 준비 대기", "Playing": "재생 중", "WaitingForNext": "다음 작품 준비 대기",
            "Applying": "다음 작품 적용 중", "Completed": "목록 완료 · 마지막 화면 유지", "Error": "재생 오류"}


def clock_text(value):
    seconds = max(0, int(value))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def state(node, configuration, artworks, now, connected):
    report_at = node.get("report_at")
    fresh = bool(connected and node.get("online") and isinstance(report_at, (float, int))
                 and 0 <= now - report_at < 90)
    playback = node.get("playback") or {}
    plan = (configuration or {}).get("plan") or {}
    revision = (configuration or {}).get("revision", 0)
    synced = fresh and playback.get("revision") == revision
    current_id = playback.get("current_verification_job_id")
    item = artworks.get(current_id) or {}
    applied = fresh and node.get("phase") == "Applied"
    matching = applied and bool(item) and "ue-" + item["source_job_id"] == node.get("active_version")
    process = fresh and bool(node.get("runtime_pid") and node.get("runtime_session"))
    # 기존 서버의 can_review는 PID·세션을 확인한 게시 근거다. 목록 재생은 별도 버전 대조가 필요하다.
    publication = node.get("artwork") if fresh and node.get("can_review") else None
    title = item.get("title") if matching else publication.get("title") if publication else None
    timing = bool(matching and process and playback.get("state") in ("Playing", "WaitingForNext", "Applying"))
    elapsed = playback.get("elapsed_seconds", 0) if timing else None
    duration = playback.get("duration_seconds", 0) if timing else None
    items = [x for x in plan.get("items", []) if x.get("enabled")]
    next_id = None
    if synced and matching:
        index = next((i for i, x in enumerate(items) if x["verification_job_id"] == current_id), None)
        if index is not None:
            repeat = playback.get("current_repeat")
            if repeat is not None and repeat < items[index]["repeat"]:
                next_id = current_id
            elif repeat is not None or items[index]["repeat"] == 1:
                if index + 1 < len(items):
                    next_id = items[index + 1]["verification_job_id"]
                elif plan.get("loop") and items:
                    next_id = items[0]["verification_job_id"]
    cached = playback.get("cached_verification_job_ids") if fresh else None
    errors = playback.get("cache_errors") or {} if fresh else {}
    return {"fresh": fresh, "synced": synced, "process": process, "applied": applied,
            "title": title, "current_id": current_id if matching else None, "next_id": next_id,
            "elapsed": elapsed, "duration": duration,
            "remaining": max(0, duration - elapsed) if timing else None,
            "progress": min(100, int(100 * elapsed / duration)) if timing and duration else 0,
            "phase": PHASES.get(node.get("phase"), "UE 상태 미확인") if fresh else "적용 상태 미확인",
            "playback": PLAYBACK.get(playback.get("state"), "재생 보고 대기") if fresh else "현재 재생 미확인",
            "cached": cached, "errors": errors,
            "error": (node.get("error") or playback.get("error") or "") if fresh else "보고 연결을 확인하세요"}
