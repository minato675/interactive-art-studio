"""합성 Head 응답: 실제 이미지를 관찰한 결과가 아닌 통합 테스트용 명세."""
def add_director(plan, context, clone_first=False):
    selection = context["ue_preflight"]["selection"]
    behavior = selection["behaviors"]
    instances = [{
        "id": f"asset_{i:03d}", "source_asset_index": i,
        "placement": asset.get("placement") or {"u": i/(len(plan["assets"])+1), "v": .6, "size": .1},
        "behaviors": behavior if i == 1 else [],
        "role": "interactor" if i == 1 and set(behavior) & {"follow", "glow"} else "noninteractive",
        "purpose": "합성 배경의 여유 영역에 배치"
    } for i, asset in enumerate(plan["assets"], start=1)]
    if clone_first:
        instances.append({"id": "source_one_copy", "source_asset_index": 1,
                          "placement": {"u": .8, "v": .7, "size": .08}, "behaviors": [],
                          "role": "noninteractive", "purpose": "새 생성 없이 같은 원본을 다른 크기로 사용"})
    plan["director"] = {
        "title": "합성 감독 테스트", "intent": "원본과 장면 배치를 분리해 전달",
        "chosen_template": selection["template"], "template_rationale": "사전 확인에서 선택한 지원 템플릿",
        "background_observation": {"palette": ["청록"], "lighting": "합성 관찰", "perspective": "합성 깊이",
                                  "occupied_regions": [], "usable_regions": ["중하단"]},
        "instances": instances, "effects": [], "interactions": [], "particles": [], "cues": [],
        "limitations": ["실제 시각·센서 품질을 검증하는 자료가 아닌 합성 테스트"],
        "acceptance_criteria": ["같은 파일의 복제 위치와 선택된 행동을 정확히 전달"],
    }
    return plan
