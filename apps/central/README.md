# apps/central — 중앙 서버

시연 대시보드는 기존 ShowReport의 PID·세션·요청/활성 버전·오류와 작품 이미지 참조를 운영자에게 전달합니다. 센서 연결이나 원격 제어 능력을 추정하지 않습니다. [상태 의미](../../docs/SHOW_DASHBOARD.md).

최근 28개 native 작품의 계정 운영 API는 [아카이브 PC2 연결](../../docs/NATIVE_ARCHIVE_PLAYBACK.md)을 따른다. 기존 공용 검수·재생 기록은 변경하지 않는다.
Head Responses 호출 실패의 안전한 진단 정보는 작업 조회의 `result.error_detail`에 보존합니다.
HTTP 상태·공급자 코드·요청 ID·입력 크기를 기록하며 공급자 오류 원문이나 비밀 값은 기록하지 않습니다.

`POST /infra/head/intakes`는 운영 앱의 원문 요청을 작업함에 접수해 즉시 202를 반환한다. 명세 해석·사전 확인은 Worker가 수행한다.
`GET /infra/head/intakes`와 기존 작업 조회 API로 접수 기록을 확인한다. 설치 지문 불일치 요청은 `waiting_ue`로 남으며
Dispatcher가 30초 간격으로 DB의 설치 보고만 확인해 재개한다. LLM 호출 중 DB 잠금을 유지하지 않고, 접수 직전 임대 소유권을 다시 확인한다.
기존 `/infra/head/workflows`의 Spec 필수·사전 확인 규칙과 동기 작품 기록은 유지한다.

운영 앱 계정 로그인·관리자 발급은 [계정 접속 안내](../../docs/OPERATOR_LOGIN.md)를 따릅니다. `/auth/*`의 세션으로 기존 운영·Head API를 호출하며 Agent 토큰은 분리합니다.

`/infra/feedback`는 현재 시연 정보와 운영자 피드백 API입니다. 새 Head/동기 기획에 참고 목록을 고정해 전달합니다.
시연 버전 대조·중복 저장·API·구현 경계는 [작품 피드백](../../docs/ARTWORK_FEEDBACK.md)을 따릅니다.

새 요청의 자동 생성·조립·AI 검수·재작업·게시 정책은 [Head 자동 사이클](../../infra/HEAD_AUTOMATION.md)을 따른다.

UE 조립 이후 별도 실행 검증·승인·PC2 게시 연결은 [UE 실행 흐름](../../infra/UE_RUNTIME_PIPELINE.md)를 따른다.

배경 전담 1개·에셋 전담 2개의 서버 배정 제한과 비활성 노드 인증은
[생성 실행기 정책](../../infra/GENERATION_ROUTING.md)을 따릅니다.

생성·제작 서버 사이의 파일 업로드·다운로드와 UE 조립 작업 계약은
[서버 연결 문서](../../infra/SERVER_EXCHANGE.md)를 따릅니다.

API 기반 분산 Head는 [Head 운영 문서](../../infra/central/HEAD_NODE.md)를 따릅니다.
Head는 Spec과 제작 노드의 템플릿 리소스 보고를 생성 전에 확인합니다. 보고 API는 production 역할 전용입니다.
기존 동기 기획 설정과 별도로 GMS 운영·OpenAI Luna 테스트 프로필을 사용합니다.
분산 Head의 응답 대기 한도는 `HEAD_TIMEOUT_SECONDS`(기본 600초)이며 [Head 운영 설정](../../infra/central/HEAD_NODE.md#설정)에서 변경합니다.

확정한 [제작 정책](../../docs/refactoring/creation-policy.md)을 적용합니다. `prepared`는 모든 필수 후보 파일 준비 완료이며 실제 Ready·Applied는 미확인입니다. 출력별 결과는 응답 `preparation.targets`에 남깁니다. 미지원 요청과 한쪽 출력 실패는 `failed`이며 두 활성 포인터를 모두 유지합니다. 예전 부분 검수 기록도 필수 출력이 누락되면 승인하지 않습니다.

일반 개발 기본값은 `RENDERER=both`이며, 작품 생성·초안 확정·검수 API를 사용할 수 있습니다. 현재 API의 `prepared`는 모든 필수 후보 파일 준비 결과이며 실제 전시 `Applied`를 의미하지 않습니다. 목표 분산 구조와 현재 동기 개발 기능의 경계는 [복원 기록](../../docs/refactoring/post-mr23-restoration.md)을 따릅니다.

요청을 받아 작품 한 건을 순서대로 만들고 기록하는 서버입니다. 운영 화면(웹)도 여기서 제공합니다.

분산 처리 입구는 별도 `/infra` API입니다. `POST /infra/jobs`는 202와 작업 ID를 반환하고,
Celery의 `draft` 처리 또는 Windows Agent의 `bundle_verify` 임대를 사용합니다.
기존 운영 앱의 동기식 API는 그대로 유지합니다. PostgreSQL을 작업 상태의 기준으로 사용하며
RabbitMQ에는 파일 대신 작업 ID만 넣습니다. 실행 및 구현 한계는 [infra](../../infra/README.md)에 있습니다.
비동기 초안 Worker도 API와 동일한 GMS·Ollama 공급자 설정과 대체 LLM 설정을 사용합니다.
전시 자동 순환(AI 디렉터가 쓴 요청을 분산 Head에 계속 접수)은 Dispatcher·Worker(`worker.py`)가 돌립니다. Dispatcher가 2초마다 켜짐 · 쉬는 중 · 진행 중 자동 작품 수(상한 `EXHIBITION_FEED_MAX_IN_FLIGHT`)를 보고 디렉터 작업(`exhibition_feed`)을 하나씩 넣고, Worker가 AI 디렉터 → Input Agent → 사전 확인 → 분산 Head 접수를 처리합니다. 켜기 · 끄기 · 상태는 운영자 인증이 필요한 `GET` · `PUT /infra/exhibition/feed`(`exhibition_feed_api.py`)입니다. Dispatcher · Worker는 분산 배포(`BROKER_URL`)에서만 돌기 때문에 개발 서버(`uv run uvicorn central.main:app`)만 켜면 켜 두어도 작품이 들어가지 않습니다. 설계와 구현 기록은 [infra/HEAD_EXHIBITION_FEED.md](../../infra/HEAD_EXHIBITION_FEED.md)에 있습니다(S15P31A504-27).

`OPERATOR_TOKEN`이 있으면 기존 `/api`의 조회와 변경 요청 모두 운영자 인증을 확인합니다.
생성 PC는 별도의 `generation` 역할로 배경·에셋 작업을 임대하고 결과 메타데이터를 등록합니다.
선행 작업 성공 후 후속 작업을 배정하며 실패 작업은 시도 한도 안에서 운영자가 재요청할 수 있습니다.
결과 등록 성공은 생성 PC의 보고 수신이며 작품 품질 검사나 게시 승인이 아닙니다. `GET /infra/agent/identity`는
노드의 상태를 바꾸지 않고 토큰에 등록된 ID·역할을 확인하며, 생성 노드에 UE 조립 작업을 배정하지 않습니다.

- **실행 장비**: 목표는 EC2 중앙 서버이며 현재 분산 인프라는 노트북의 Linux 컨테이너에서 실행합니다. 기존 동기식 제작 API는 한 서버 안에서 기획부터 렌더러 전달까지 수행하고, 새 `/infra` 작업은 Worker·생성 Agent와 분리해 처리합니다.
- **가져다 쓰는 것**: [contracts](../../packages/contracts/README.md)(요청 · 응답 모델), [db](../../packages/db/README.md)(작품 기록 · 소재 풀), [studio](../../packages/studio/README.md)(제작 로직)
- **실행 명령**: [AGENTS.md](../../AGENTS.md)의 「명령어」 (`uv run uvicorn central.main:app`)

| 파일 (`src/central/`) | 내용 |
|---|---|
| `main.py` | FastAPI 앱. 운영 화면 `/`, API 문서 `/docs` |
| `feedback_api.py` | 실제 시연 버전 정보·운영자 의견 저장·목록·참고 활성 변경. `/infra/feedback` |
| `exhibition_feed_api.py` | 전시 자동 순환 켜기·끄기·상태(켜짐 · 쉬는 이유와 끝 시각 · 진행 중 자동 작품 수 · 상한 · 최근 디렉터 작업). `/infra/exhibition/feed` |
| `api.py` | 운영 API: 작품 만들기 · 편집 모드 초안(`POST /api/drafts`)과 확정(`POST /api/artworks/{id}/confirm`) · 사람 검수 승인·반려(`POST /api/artworks/{id}/review`)와 소재 검수 파일(`GET /api/artworks/{id}/review/files/{name}`) · 작품 기록 · 소재 풀 · 표현 방식 목록. `main.py`에서 조회·변경 모두 운영자 토큰 검사 |
| `pipeline.py` | 한 작품의 전체 순서: Head → 소재 생성 → 검사 → 조립 → 렌더러 전달. 실패하면 보류 · 실패로 기록하고 새로 만든 파일을 정리. 사용자 제작(자동 · 편집 모드)은 기획 전에 Input Agent(`spec_matching`)가 원문을 작품 명세로 정리해 `작품 명세` 단계로 남기고, 메인 캐릭터가 없다고 판단하면 기획에 '드러내기 없음'을 넘기고, 있으면 그 문구를 넘겨 기획이 주인공을 그 안에서 고르게 한다. LLM이 설정된 서버(`LLM_PROVIDER`)에서만 켜지며, 사용자가 주인공 · 드러내는 방식을 직접 정했거나 LLM 답이 규칙으로 대체되면 판단에 쓰지 않는다. Input Agent에서 예상 못 한 오류가 나도 작품을 실패시키지 않고 기록만 남긴 뒤 기획만으로 진행한다. 편집 모드는 `draft()`가 의도 · 구성까지만 정해 멈추고(`awaiting`), `confirm()`이 바꾼 칸만 덮어써 플래너를 다시 부르지 않고 이어서 만든다. 지금 편집 칸은 계획 7칸이며, 작품 명세의 막히지 않은 모든 칸을 편집하도록 바꾸기로 결정했다(S15P31A504-165, 구현은 S15P31A504-168). 전시 모드이고 테마가 비었으며 LLM이 설정된 서버(디렉터가 있음)면 `_prepare()` 전에 AI 미디어아트 디렉터(`exhibition_director`)가 작품 방향을 정한다. 디렉터 답은 코드 검사 → 요청 검사 → 기획(의도·구성) → 계획 검사를 소재 생성 전에 모두 통과해야 하고, 틀리면 이유를 붙여 다시 쓰게 하며 디렉터를 최대 3번 부른다(처음 1번 + 다시 쓰기 2번). 디렉터는 GMS일 때 기획과 다른 모델(`DIRECTOR_MODEL`, 기본 `gpt-6.1-sol`)을 쓴다. `LLMUnavailable`(연결 실패)이거나, 예상 못 한 오류가 나거나(디렉터 오류. 다시 물어도 반복될 가능성이 커 다시 묻지 않는다. 디렉터는 `_build`의 실패 기록 밖에서 돌아서, 오류를 넘기면 작품 기록 없이 HTTP 500으로 전시 순환이 끊긴다), 3번 안에 통과하지 못하면 실패가 아니라 기존 프리셋 규칙(`auto_request`)으로 만들고, 디렉터가 있는 경우 `디렉터` 단계에 출처(`llm`·`llm-repaired`·`preset`) · 모델(시도별과 통과한 시도) · 지시문 버전 · 지원 범위 · 시도별 거부 이유 · 대체 이유를 남긴다. 최종 통과한 디렉터 원문은 DB의 `artwork.request.theme`에 저장되지만 `ArtworkOut`에는 나오지 않으므로, 최종 원문과 거부된 시도는 `GET /api/artworks/{id}`의 `디렉터` 단계(`attempts[].answer.prompt`)에서 본다. 요청의 `review_assets`·`review_final`(기본 꺼짐, 전시 모드 제외)을 켜면 소재 생성 뒤(`asset_review`)·검수 폴더 게시 뒤(`final_review`)에 멈추고 `review()`로 이어 간다. 검수 중 새 소재는 `pending`이라 풀에서 뽑히지 않는다 |
| `config.py` | 환경 변수 설정(데이터 폴더, DB, 렌더러, 운영자 토큰, LLM). 변수 목록은 [루트 README](../../README.md#실행) |
| `web/index.html` | 운영 화면(만들기): 모드 · 테마 · 보이지 않는 주인공 · 드러내는 방식 · 칩 · 표현 방식 · 입력 종류 · 연출 · 소재 선택. 입력 모델은 `contracts`의 `CreateRequest` |

입력은 맨 처음에 한 번 받습니다. 전시 모드의 빈 항목은 자율 기획하고 사용자 제작에서는 원문에 근거해 계획합니다.

예정:
- 운영 화면: 홈 · 작품 기록 · 소재 풀(대기 소재 승인) · 설정 화면. 운영 PC 앱([operator](../operator/README.md))이 생기면 그쪽으로 옮깁니다.
- 분산 구조: 비동기 작업 큐(배경 · 에셋 · 조립이 각자 속도로), 제작 노드 Agent에 작업 임대, 시연 노드로 게시, 예산 · 비용 기록, 풀 정책(재사용 상한 · 비상용 예비 · 정리). 설계는 [docs/architecture](../../docs/architecture/README.md)

작품 설명(`theme`)은 웹 입력과 API 모두 최대 2,000자입니다. PySide6의 자유 문장도 같은 API 한도를 사용합니다.
