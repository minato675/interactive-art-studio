# tests — 흐름 테스트

CI의 `operator-tests`는 PySide6를 설치한 뒤 `test_operator_show_operations.py`까지 실행해 운영 제어·실제 상태 표시를 확인합니다. 해당 Qt 검사는 `unit-tests`에서 제외하여 설치되지 않은 PySide6 때문에 건너뛴 결과를 검증 완료로 취급하지 않습니다.

시연 운영 테스트는 저장과 재생 분리, 보고 만료·버전 불일치, 실제 구간 진행률, 미지원 제어 비활성, 복구 요청 확인과 편집 보존을 확인합니다. Qt 화면 검증은 실제 UE·센서 검증이 아닙니다.

`test_native_artworks.py`와 `test_operator_native_artworks.py`는 최근 아카이브 목록·인증·revision·멱등성·PC2 보고·선택 UI를 확인한다. 시험 기기 보고를 실제 PC2 실행으로 표시하지 않는다.
Head 오류 진단은 HTTP 오류 코드·요청 ID 보존과 키·프롬프트 비노출을 확인한다. 동적 장면의 반응 분리는
전역 변화만 있는 경우, 손 주변 변화 증가·감소, 잡음·대조 영역 부족을 각각 검사한다.

운영 앱 분산 접수 검사는 응답 유실 재시도의 멱등성, UE 지문 불일치 대기·재개, 임대 소유권과 기존 동기 API 보존을 확인한다.
합성 모델·노드 테스트는 실제 생성 PC·UE/GPU 실행 성공으로 기록하지 않는다.

`test_account_auth.py`는 실제 암호 해시·계정 권한·세션 만료/폐기·로그인 제한·오류 비밀 제거와 기존 토큰 호환을 검사합니다. `test_operator_login.py`는 비동기 로그인·관리자 계정 지정·비밀번호 비저장·세션 헤더를 검사합니다.

`test_artwork_feedback.py`는 시연 버전·보고 신선도·인증·의견 멱등 저장·다음 기획 문맥을 검사합니다.
전시 디렉터와 후속 기획이 같은 피드백을 받고, 다시 쓰기 중 새 의견이나 참고 중지로 문맥이 바뀌지 않는지도 확인합니다.
`test_operator_feedback.py`는 Qt 화면의 대상 고정·오류 시 초안 보존·저장·참고 중지를 검사합니다.
`test_infra_postgres.py`는 실제 PostgreSQL에서 활성 버전 JSON 조회·의견 동시 중복 등록·revision 경합도 확인합니다.

`test_operator_monitor.py`는 네이티브 운영 현황의 API 조회·화면·검색·오래된 Applied·401 삭제·늦은 응답 거부를 검사합니다.
`test_exhibition_scheduler.py`는 재생 정책·완료 이벤트의 멱등성·상태 복원·가상 시간 8시간·빈 목록 시작을 검사합니다.
두 검사는 실제 UE 화면이나 생산 환경 재생 스케줄러 API가 구현됐다는 근거가 아닙니다.

`test_show_supervisor.py`는 시연 키트의 단일 프로세스 잠금, 실제 자식 프로세스 실행·강제 종료·마지막 정상 작품 복구, 새 후보 실패·오프라인 감시·PID 소유권·파일 손상 거부를 검사한다. 합성 상태 파일을 쓰며 실제 UE/GPU 화면 검증과 구분한다.

`test_runtime_kit.py`는 두 자동 관리 설정의 동시 지정 거부와 키트 자체 포함 구성을 검사합니다.
Windows에서는 두 관리자 사이의 호스트 잠금도 확인하며, 실제 UE/GPU 화면 검증과 구분합니다.

UE 템플릿·배경 관찰·원본 에셋과 복제 배치·상호작용 대상·효과·입자 명세는 [Head 시나리오 감독](../infra/HEAD_SCENARIO_DIRECTOR.md)을 따릅니다.

새 요청의 자동 생성·조립·AI 검수·재작업·게시 정책은 [Head 자동 사이클](../infra/HEAD_AUTOMATION.md)을 따른다.

UE 조립 이후 별도 실행 검증·승인·PC2 게시 연결은 [UE 실행 흐름](../infra/UE_RUNTIME_PIPELINE.md)를 따른다.

UE 사전 확인 검사는 등록 카탈로그의 원본 revision·파일 증거·지문 일관성도 확인합니다.
현재 설치 코드에서 계산한 5개 템플릿의 지문이 중앙 목록과 같은지도 검사합니다. Head 자동 검수 검사는 잔상(`afterimage`)·가라앉음(`settled`) 캡처의 전달, 같은 파일 중복 제거와 기존 보고서 호환을 확인합니다.

`test_generation_routing.py`는 전담 노드의 교차 배정 거부·배경 의존성·두 에셋 실행기 슬롯·키트 발급을 검사합니다.

`test_server_exchange.py`는 역할별 HTTP 파일 왕복·해시 불일치·무권한 접근·UE 선행 관계와 완료 재전송을 검사합니다.

`test_head_dispatch.py`는 Head 중복 요청·이미지 결과 일치·판단 검증·자식 작업 중복 방지·인증을 검사합니다.
`test_exhibition_feed.py`는 전시 자동 순환의 범위 계산(사전 확인과 일치 · 템플릿 표 변경 반영 · 영향 없는 칸), 디렉터 작업의 접수 · 재실행 멱등성 · 다시 쓰기 소진 · 명세 덮어쓰기, 최근 기록 · 겹침 표시, Dispatcher의 자리 세기, 쉬기 조건, 켜기 · 끄기 API를 가짜 LLM · 노드로 검사합니다. 실제 GMS 응답 품질이나 현장 노드 동작의 근거가 아닙니다.
`test_ue_preflight.py`는 미지원 Spec·제작 노드 미보고·오프라인·리소스 해시 변경과 생성 조건 고정을 검사합니다.

`test_creation_policy.py`는 모드별 원문 근거, 미지원 요청 오류, TD/UE 각각의 준비 실패와 두 기존 활성 포인터 보존, 검수 승인 실패, Ready·Applied 구분을 검사합니다. 전체 후보 준비 완료는 `prepared`로 기대하며 실제 시연 적용을 합성하지 않습니다.

후속 개발 복원 검사는 `test_architecture_boundaries.py`에 있습니다. 기본 `both`에서 자동 제작과 초안 확정이 소재 검수·최종 검수를 거쳐 TD/UE 두 파일을 만들고, 인증과 `/infra/jobs`가 유지되는지 확인합니다. `test_ue_mapping.py`는 팔레트·분위기·위치 보존과 MR !23의 UE 전용 검수 기록 재개도 확인합니다. 기본 차단이나 TD 미지원 자체를 정답으로 삼던 !23 테스트는 기존 기능을 보존하는 검사로 교체했습니다.

서버를 띄우지 않고(TestClient) 입력 → Head → Worker → 검증 → 조립 → 전달 전체를 확인합니다.

`test_infra.py`는 작업 재요청, 임대 만료, 늦은 결과 거부, 노드 슬롯, 캐시 무결성,
UE 상태 증거를 검사합니다. `test_infra_postgres.py`는 실제 PostgreSQL의 동시 임대를 검증합니다.
이 테스트는 `TEST_POSTGRES_URL`이 없으면 건너뛰며, 이름이 `_test`로 끝나는 테스트 DB만 사용합니다.
실제 RabbitMQ 연결은 [infra/central/smoke.py](../infra/central/smoke.py)로 별도 확인합니다.
CI에서는 일반 기능·Qt 운영 앱·controller·DB/큐 통합 검사를 네 job으로 분리합니다.
빌드한 이미지와 실제 `deploy.sh`의 교체·실패 복구는 [CI/CD 문서](../docs/CI_CD.md)의 `build-smoke`가 검사합니다.
controller 리소스 로더의 기본값·명시 경로는 `test_controller_resources.py`에서 확인합니다.
wheel 설치 후 JSON 누락·경로 오류는 운영 이미지에서 실행하는
[`check_packaged_scene.py`](../infra/check_packaged_scene.py)가 실제 장면 조립·검사로 검출합니다.
같은 검사는 Head 감독 코드·지침이 설치된 wheel 안에 있는지, 지침이 wheel의 파일 목록·해시와 일치하고 세 판단 단계에 로딩되는지도 확인합니다. `test_packaged_director.py`는 소스 체크아웃 혼입·파일 누락·변조·외부 경로를 거부하는지 검사합니다.
원격 접속 관련 검사는 운영 API 조회 인증, 생성 노드의 역할 분리, 상태를 바꾸지 않는 접속 확인을 포함합니다.
생성 작업 검사는 배경→에셋 의존성, 역할별 배정, 완료 재전송, 임대 만료·실패 재시도와
해시 저장소 파일 전달을 포함합니다. 시연 테스트는 파일 준비 실패·통신 장애 시 기존 상태 보존을 검사하며
UE 실제 화면 출력 검증을 대신하지 않습니다.
- 간접 표현(형상·흔적), TD 작품 데이터와 읽기 도구, UE scene 검사, 풀 재사용, 실패 시 정리, 운영자 토큰, LLM 대체 등.
- `test_spec.py`: 작품 명세 스키마(`contracts.spec`) 검사. 설계 예시(고래·호랑이·비)가 통과하는지, 막힌 칸 · 필수 칸 · `"none"` 규칙을 어기면 거절되는지, 값 목록과 칸 상태 표가 빠짐없는지 확인합니다.
- `test_spec_matching.py`: 원문 → 작품 명세 매칭(`studio.head.spec_matching`). 같은 설계 예시 원문을 가짜 LLM 응답(한 번 호출)으로 매칭해 예시 명세와 같아지는지, 원문 구절 유무로 원문에서 뽑은 값과 AI가 고른 값이 나뉘는지, 확신이 낮은 구절이 `unmatched_phrases`에 남는지 확인합니다. 값 표에 없는 코드 · 원문에 없는 구절 · 빈 필수 칸을 다시 묻는지, 그래도 틀리면 규칙으로 대체하는지, 막힌 칸 요구를 보류하는지도 봅니다. LLM 없이 규칙만으로도 빈 필수 칸 없이 명세가 나오는지 봅니다.
- `test_llm.py`: SSAFY GMS 연결(`studio.head.llm.GMSLLM`). 실제 GMS를 부르지 않고 HTTP 호출을 가짜로 바꿔, OpenAI Responses API 형식의 요청(주소 · 인증 헤더 · JSON 스키마)을 보내는지, 응답에서 답 글을 꺼내는지, 잘린 답 · 거절 · HTTP 오류를 LLM 오류로 바꾸는지(오류에 키가 나오지 않는지) 확인합니다. GMS에 닿지 못하면 대체 LLM(Ollama로 실행하는 EXAONE)으로 넘기고 정해진 시간 동안 GMS를 건너뛰는지, 실제로 답한 모델이 추출 근거에 남는지도 봅니다.
- `test_generation_tools.py`: 생성 PC 도구([infra/generation](../infra/generation/README.md)). 16:9 자르기 영역과 밝기 → 알파 계산을 확인합니다. 생성 전 점검이 지원 명세에서 템플릿과 `choice.json`을 내고, 미지원 반응과 필수 칸(주제 · 분위기 · 배경 · 차원 · 입력 · 반응 · 반응 방식 · 복귀) 누락은 보류하며 이전 선택 결과를 지우는지, 선택 칸 누락은 허용하는지 봅니다. 넘기기 전 점검이 층 · 빛나는 객체 결과 폴더를 통과시키고 빠진 파일 · 목록 밖 파일을 잡는지, 미리보기를 결과 폴더 안(하위 폴더 · `..` 포함)에 쓰지 못하게 막는지도 봅니다. 렌더링은 검사하지 않습니다.
- `test_generation_codex_handler.py`: 생성 PC가 키트 실행기에 연결하는 Codex handler(`infra/generation/handler.py`). 중앙 쪽 실행기(`generation_worker.py`)를 보는 `test_generation_handler.py`와 대상이 다릅니다. 실제 Codex 대신 합성 이미지를 만드는 가짜 생성기로, Head 지시문 · 출력 경로 · 배경 참조가 지시문에 들어가는지, 배경이 16:9 · 권장 크기로 맞춰지는지, 검은 배경 객체가 알파로 바뀌고 실제 배경이 참조로 첨부되는지, 그림이 안 나오거나 규격에 걸리면 실패 이유를 지시문에 덧붙여 다시 생성하고(두 번째 시도 성공), 끝까지 실패하면 최대 횟수(3번)에서 멈추며, `--attempts` · 시간 예산이 재생성을 막는지, 시간 초과 때 생성기가 띄운 자식 프로세스까지 멈춘 뒤 다음 시도로 넘어가는지, 지시문에 고정 화풍 없이 결함 금지와 실제 배경 대표 색이 들어가는지, 단조로운 배경 · 잘린 에셋 · 너무 작은 에셋 · 배경과 색감이 동떨어진 에셋을 거르고 점수를 남기는지, 초록 배경 에셋을 초록 번짐 없이 투명으로 바꾸고 검정 키의 반투명 가장자리 색을 되살리는지, 꽉 찬 불투명 객체 · 생성 실패 · 잘못된 입력이 실패 코드로 끝나고 결과 폴더가 비는지 봅니다. Codex 선택은 PATH의 동명 프로그램(conda의 `codex.exe`) 대신 npm의 `@openai/codex`를 고르는지, npm이 없으면 Codex 앱의 공식 `codex.exe`(폴더 이름이 해시여도 `--version`으로 가장 높은 버전)를 쓰는지, `--codex-path` · `RANEL_CODEX_PATH`는 `codex-cli` 버전이 확인될 때만 쓰는지, `--check`가 사용할 명령과 버전을 내는지, 못 찾으면 재생성 없이 한 번에 실패하는지 봅니다. 이미지 검사는 Pillow가 있을 때만 돕니다(`uv run --with pillow pytest`). 화풍 품질은 검사하지 않습니다.
- `test_assets.py`: UE 작품 소재 목록 `assets.json` v1(`contracts.assets`)과 파일 검사(`studio.verify.checks.inspect_assets`). 생성 쪽에 요청한 세 모양(유체 배경 + 층 · 그리기 · 입자 형상)이 통과하는지, 맨 뒤 층만 불투명을 허용하는지(파일 순서가 아니라 깊이로 판단), 없는 · 깨진 · 4096보다 큰 파일과 기준 미달 실루엣을 거부하는지 봅니다. 필수 칸 누락 · 경로가 든 파일 이름 · 깊이 중복 · 없는 층을 가리키는 재질 값 · 허용 범위 밖 재질 값 · 화면 밖 객체 좌표 · 위치를 쓰는 행동 둘 이상 · 객체 12개 초과는 거부하고, 모르는 칸은 경고로만 돌려주는지 확인합니다.
- `test_td_tracking.py`: TD 관람객 ID 부여(D1). `td/project/tests`의 추적 규칙 테스트(`test_tracking.py`: 엇갈림 · 가림 · 진입 · 퇴장 · 번호 재사용 없음 · 8손과 9번째 손 · 장비 번호 우선 · 좌우 묶음 · 검출 튐)와 저장소 설정 그대로 송신 콜백 → 여러 손 어댑터 → 추적기 → UDP 패킷 통합 테스트(`test_multi_hand_pipeline.py`)를 파일 경로로 불러와 같이 돌립니다. 테스트 내용은 `td/project/tests`에만 있고, TD 없이 가짜 노드로 확인하므로 카메라 인식은 검사하지 않습니다.
- `test_ue_contract.py`: UE scene 정적 검사(`studio.verify.ue_contract`). `fixtures/ue/`의 라넬 runtime 검증 템플릿 사본(조명 설정 `SceneLookProof`, GPU 입자 `GpuLuminousField`, 그림 휘젓기 `FlowStir`)이 통과하는지로 옮긴 규칙이 runtime과 같은지 확인하고, 예전 controller 계약은 이들을 거부한다는 것도 확인합니다. 조명 설정(모르는 칸 · 범위 · 중복 재질 대상 · 조명 수 · 환경 안개 · 없는 배치), GPU 입자(입자 수 · 영역 · 색 · 장면 예산 262144), 고정 무대 일치(생략한 칸은 헤더 기본값), 카탈로그 해시, 핀 이후 runtime 계약 파일 변경 감지를 봅니다. 공용 전시 맵의 장면 사본(`fixtures/ue/RuntimeIntegration-*.json`: 새로 합친 ImageFlow · GpuFlow · FlowStir와 기존 Paint)이 공용 카탈로그 · 고정 무대까지 통과하는지, 카탈로그에 층 객체 3개와 새 패턴이 있는지도 확인합니다.
- `test_ue_assemble.py`: UE 자동 조립(`studio.assembly.ue_assemble`). 대응표가 전용 템플릿 반응만 바로 고르고 follow · glow는 대체 기록, 그 밖은 보류하는지, 생성 전에 기록한 선택을 조립이 다시 고르지 않고 쓰는지, 반응 방식 대체 기록, 템플릿 5개가 공용 카탈로그 · 고정 무대를 통과하고 필요한 패턴 · 객체가 있는지, 조립 결과가 정적 검사를 통과하고 같은 입력이면 같은 결과인지 봅니다. 층 배치(입자 장: 배경과 효과 사이 / 불투명 캔버스 템플릿: 캔버스 앞 + 경고 / 그림 휘젓기: 뒤 층 1장을 캔버스로), 화면 채우는 층 크기, 명세 값(반응별 표면 변형 모드 · 입자 밀기 · 소용돌이 값, 반응 반경 · 입자 수 · 복귀 시간), 불투명 캔버스 템플릿에 걸리는 follow의 생성 전 보류, 제스처 입력의 좌표 대체, 재질 값의 Look 전체 프로필, 객체의 Sprite 배치 깊이 · 화면 좌표 · 행동 바인딩 · 행동별 조건, 불투명 캔버스 템플릿의 따라감 · 피함 거부, 화면 밖 · 투명 픽셀 없는 객체 경고, 알파 없는 객체 거부, 객체 없는 입력의 장면 이름 유지, 형상 · 대응 없음 · 파일 없음 · 카탈로그 비호환 거부를 확인합니다. 대응표에 연결하지 않은 새 템플릿 3개는 대응표가 고르지 않고, 직접 지정하면 조립 · 정적 검사를 통과하는지(그림 위 물결은 뒤 층이 원화 캔버스, 알파 없는 원화는 경고) 봅니다.
- `test_ue_runner.py`: PC1 UE 실행 검증 러너(`studio.verify.ue_runner`). UE 대신 `fakes/fake_shipping.py`(runtime 파일 계약을 흉내 냄: `release.json` → 중간 단계 → `status.json`, UDP 손 → `Trace.jsonl`의 Contacts · BehaviorInputs, `Capture.json` → PNG, 같은 캡처 ID와 이미 있는 PNG는 무시)를 띄워 봅니다. 반응하는 장면 · 배경이 흐르면서 손에도 반응하는 장면 · 화면 전체가 계속 바뀌지만 행동 입력은 닿는 장면은 통과하고, 반응 없음 · 손과 무관한 움직임(루프 영상 같은 장면) · 빈 화면 · 복귀 없음 · 적용 실패 · 준비 시간/메모리 초과 · 긴 프레임(버린 것 포함) 초과 · 캡처 크기 다름 · 실행 파일 없음은 실패로 보고하는지, 긴 프레임을 초당으로 세는지, 빠진 캡처를 다시 요청하는지, 예전 캡처를 재사용하지 않는지, 다른 PID가 남긴 상태를 무시하는지, 먼저 게시된 버전 · 같은 버전 재검증 · 죽은 실행본 재시작 · stop 뒤 재사용이 되는지, 시작 실패 때 프로세스를 남기지 않는지, Windows가 아닌 환경(Linux CI)에서 창 숨김 설정 없이 뜨는지 확인합니다. PNG(`studio.verify.png`) 필터 5종 · RGBA 알파 · 모르는 필터 거부, 화면 변화 비율, 번들 해시(하위 폴더 포함)도 봅니다.
- 실행 명령(이 테스트와 따로 도는 controller 테스트)은 [AGENTS.md](../AGENTS.md)의 「명령어」에 있습니다.

## 실제 LLM 시험

`llm_trial.py`는 위 테스트와 달리 **실제 LLM(Ollama 또는 GMS)을 불러** 원문 → 작품 명세 매칭(`LLMSpecMatcher`)과 계획(`LLMPlanner`)이 어떤 답을 내고 어디서 틀리는지 기록하는 시험 도구입니다. 이름이 `test_`로 시작하지 않아 `uv run pytest`와 CI에는 들어가지 않습니다(크레딧 · 시간을 쓰지 않게). 명세 프롬프트를 바꿀 때 같은 원문 묶음으로 전후를 비교하는 데 씁니다.

- 원문 묶음: `llm_trial_cases.json`. 명세 10건(설계 예시 · 값 표에 없는 표현 · 막힌 칸 요구 · 부정형 · 원문 속 지시문 · 규칙 5개 등)과 계획 5건. 원문마다 무엇을 확인하려는지 `note`에 적었습니다.
- 공급자: `LLM_PROVIDER`(`ollama` / `gms`)와 central과 같은 환경 변수(`OLLAMA_MODEL` · `OLLAMA_URL` · `GMS_MODEL` · `GMS_KEY`)를 씁니다. 대체 LLM(`LLM_FALLBACK`)은 쓰지 않습니다. 한 공급자의 답만 보려는 것이기 때문입니다.
- 기록: 원문마다 단계별 출처(`llm` 한 번에 맞음 / `llm-repaired` 다시 물어 맞음 / `rule` 규칙으로 대체), 호출마다 걸린 시간 · LLM 답 원본 · 서버 검사에서 틀린 이유, 보류 사유, 최종 명세 값을 남깁니다. 결과는 `runtime/llm_trial/<시각>-<공급자>/results.jsonl`과 표로 정리한 `summary.md`이며 저장소에 넣지 않습니다.
- 같은 원문도 매번 답이 달라지므로(temperature 0.6) 수정 전후는 `--repeat 3` 이상으로 돌려 비율로 비교합니다.
- 전시 모드 디렉터는 `llm_trial.py`가 다루지 않습니다. 실제 통과율을 보려면 `RENDERER=ue`, `LLM_PROVIDER=gms`로 서버를 띄워 테마 없는 전시 요청을 몇 번 보내고, 작품 기록 `디렉터` 단계의 `source`(`llm` · `llm-repaired` · `preset`)로 통과율과 대체 횟수를 셉니다. 크레딧을 씁니다.
- Ollama는 CPU만 있으면 명세 1건에 2~7분 걸려 `--timeout`을 늘려야 합니다. GPU(RTX 4050 Laptop)에서는 호출 한 번에 20~70초였습니다.

`test_runtime_kit.py`는 PC2 실행기가 재생목록 서비스를 시작하고 종료하며, 중앙 단절 때 캐시된 구성으로 시작할 수 있는지도 검사합니다. 실제 UE 화면 확인과 구분합니다.
