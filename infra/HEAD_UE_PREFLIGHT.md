# Head의 UE 템플릿 사전 확인

생성 전에 작품 명세(Spec)로 UE 템플릿을 고르고, 실제 제작 노드가 같은 조립 리소스를
보고했는지 확인합니다. 지원되지 않거나 제작 노드의 확인이 없으면 유료 AI 판단과 생성 작업을 시작하지 않습니다.

## 작업 범위

1. MR !33의 템플릿·슬롯·조립기에서 출처와 해시를 기록한 중앙 지원 목록을 만듭니다.
2. UE 실행기가 설치된 조립 리소스를 중앙에 보고합니다. 이 보고는 Shipping 검증이 아닙니다.
3. Head 요청은 Spec을 포함해야 합니다. 서버가 지원 반응·입력·소재 개수와 노드 준비 상태를 검사합니다.
4. 선택과 입력 조건을 흐름에 고정하고 AI 입력 및 배경·에셋 작업에 전달합니다.
5. PNG 이름·알파·용도·객체 행동·배경 표시 여부를 구조화된 생성 조건으로 제공합니다.
6. 미지원 요청·보고 없는 노드·해시 변경·AI의 조건 위반이 생성 등록 전에 차단되는지 검사합니다.

기존 공유 저장소 요청과 수동 생성 작업은 유지합니다. 이미 등록된 Head 흐름을 임의로 새 명세로
해석하지 않으며, 명세·사전 확인이 없는 기존 흐름은 다음 판단 전에 보류합니다.

## 변경 관계

이 작업은 노트북 마스터 브랜치의 HTTP 파일·UE 실행기 변경에 의존합니다.
Draft MR 대상은 `S15P31A504-6-server-exchange`이며 해당 기반 변경이 통합된 뒤 develop으로 연결합니다.
MR !33은 UE 담당자의 작업으로 유지하며 이 MR에서 UE 원본이나 Shipping 파일을 수정하지 않습니다.

## 운영자가 사용하는 절차

1. 새 UE 키트의 `ue_worker.py`와 `ue_capabilities.py`를 함께 설치합니다. 토큰·CA는 기존 값을 유지합니다.
2. UE 프로젝트 루트에서 `uv run python C:/Ranel/ue-kit/ue_worker.py --report-capabilities`로 한 번 보고합니다.
   이후 `--report-capabilities` 없이 계속 실행하면 대기 중에도 실제 리소스와 heartbeat를 갱신합니다.
3. 운영자 인증으로 `GET /infra/head/templates`에서 같은 카탈로그의 제작 노드가 online인지 확인합니다.
4. `infra/examples/head-ue-request.json`의 작품 정보와 Spec을 고쳐 Head 요청 API로 전송합니다.

```powershell
uv run python infra/central_cli.py head-preflight --request infra/examples/head-ue-request.json
uv run python infra/central_cli.py head-submit --request infra/examples/head-ue-request.json
```

`head-preflight`는 AI 비용·작업 등록 없이 조건만 확인합니다. `head-submit`부터 설정된 모델의
판단 작업을 등록하므로 실제 작품 정보와 고유 request_id를 넣고 실행합니다.

제작 노드 heartbeat는 90초 이내여야 합니다. 보고된 리소스 해시는 템플릿 scene·slots·mapping·Look,
조립기·소재/Spec 계약·UE 검사기·공용 카탈로그·고정 무대·runtime 계약 핀을 포함합니다.
JSON은 정렬하고 텍스트 줄바꿈을 정규화해 Windows와 Linux의 체크아웃 차이를 제거합니다.
중앙 목록은 MR !57 `ae903ee`의 잔상 변경·계측 누적 수정 기준 스냅샷입니다. UE 코드가 바뀌면 검토 후 목록을 갱신해야 합니다.
갱신·접속 확인 결과는 [UE 지문 기준 갱신](UE_CATALOG_UPDATES.md)에 기록합니다.

## 지원 조건과 생성물

- 템플릿은 GPU 입자·그림 휘젓기·유체·그리기·표면 변형 5종입니다.
- 필수 칸이 채워진 Spec의 좌표 입력(appear/move), while, 규칙 1개를 받습니다.
  형상·흔적, 미지원 반응, 손짓 대체·다중 규칙 잘라내기는 생성 전에 거부합니다.
- `press`, `swirl`, `scatter`는 MR !33 리뷰에서 반응 구분 문제가 남아 보류합니다.
  불투명 캔버스의 follow도 거부합니다. 지원 범위를 넓히려면 UE 검증과 중앙 정책을 함께 갱신합니다.
- 배경은 `background.png`, far 한 장이며 1920x1080을 권장합니다. 유체·그리기·표면 변형은
  far가 가려져 실제 표시용이 아닌 에셋의 색·조명 참고용임을 명시합니다.
- 객체는 에셋마다 `asset-001.png`처럼 별도 RGBA PNG입니다. 512x512를 권장하고 투명 알파,
  2D/2.5D 허용 범위와 float/follow/glow 행동 조건을 지정합니다. 2.5D는 평면 스프라이트와 배치 메타데이터입니다.
- `generation.conditions`가 구조화된 기준입니다. 동일 내용을 AI 입력과 최종 제작 프롬프트에도 넣습니다.
  AI의 render_mode가 고정 조건을 벗어나면 에셋 작업을 등록하지 않습니다. 완료 API는 지정 파일명도 확인합니다.

권장 해상도는 완료 API의 픽셀 검증을 뜻하지 않습니다. 실제 파일 알파·소재 조합은 UE 조립기의 검증을 거칩니다.
사전 확인은 Python 조립 리소스의 호환성 확인이며 Shipping·Niagara 화면·GPU 성능 확인은 아닙니다.
일반 `/infra/jobs` 수동 생성은 호환성을 위해 유지하므로 이 차단 정책은 Head 흐름에 적용됩니다.
Head 생성 완료 뒤 조립 요청·소재 역할 매핑은 현재 운영자가 등록합니다.

## 기존 흐름과 배포

Spec은 새 Head 요청의 필수 입력입니다. 예전 요청 클라이언트는 예제를 참고해 수정해야 합니다.
기존 흐름에 Spec·사전 확인이 없거나 생성 중 중앙 정책·해시가 바뀌면 다음 판단에서
`needs_attention`과 `_ue_hold`로 보류하며 추가 생성 작업을 만들지 않습니다.
노드 미접속은 연결 복구 후 판단 작업을 재시도할 수 있습니다. Spec·정책이 달라지면 새 요청을 만듭니다.
새 `infra_ue_capability` 테이블만 추가하며 기존 테이블 열은 변경하지 않습니다.
