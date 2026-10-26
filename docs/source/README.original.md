# S15P31A504 — 간접 표현 인터랙티브 미디어아트 생성 시스템

현재 제작 정책은 [모드·지원 검사·전체 준비 규칙](docs/refactoring/creation-policy.md)을 따릅니다. 사용자 제작은 프롬프트 근거로 기획하고 전시 모드는 자율 기획합니다. 미지원 요청은 실패로 기록하며, `both`는 두 후보 파일 모두 성공해야 `prepared`입니다. 제작 API는 활성 `release.json`을 바꾸지 않습니다. 실제 Ready·Applied는 Show Agent의 런타임 보고로 구분합니다.

초기 현장은 **생성 PC 1대·UE 제작 PC 2대·시연 PC 1대, 총 4대**로 구성합니다.
공유 저장소는 생성 PC의 전용 디스크 폴더를 사용하며 UE 제작 노드는 N대로 확장합니다.
장비별 연결은 [통신 파이프라인](docs/COMMUNICATION_PIPELINE.md), 저장 경로는 [저장소 배치](infra/STORAGE.md)에 있습니다.

MR !23의 UE 전용 강제와 기본 제작 차단으로 후속 개발이 중단된 부분을 수정했습니다. 현재 개발 설정은 TD 담당자가 정한 `RENDERER=both`이며, `td`·`ue`를 명시한 선택도 유지합니다. `both`는 로컬 작품 파일을 두 형식으로 준비하는 개발 경로이며 실제 전시에서 TD를 렌더러로 운영한다는 결정은 아닙니다. `prepared`는 필수 후보 파일 준비 완료이고, 과거 `published`는 이전 게시 기록입니다. 실제 `Applied` 여부는 시연 노드에서 확인합니다.

현재 동작의 복원 기준은 !23 직전 최신 `develop`인 `8928524`입니다. [후속 개발 복원 기록](docs/refactoring/post-mr23-restoration.md)에 영향받은 기능과 유지한 변경을 구분했습니다. controller에는 회사 원본과 팀 확장이 섞여 있으므로 [파일별 출처](docs/provenance/README.md)와 [확인 대상 3개](docs/provenance/controller-differences.md)를 기준으로 판단합니다.

대상을 직접 보여 주지 않고 **흔적과 순간의 형상**으로 드러내는 미디어아트를 자동으로 구성합니다. 예를 들어 사슴 대신 사슴의 발자취를, 고래 대신 물고기 떼가 잠깐 이루는 고래 모양을 보여 줍니다.
입력 한 번으로 AI가 보이지 않는 주인공과 드러내는 방식을 정하고, 배경과 주인공 데이터를 만들어 검사한 뒤 Unreal Engine(UE)에 전달합니다. 현장의 TouchDesigner(TD)가 관람객의 손과 몸을 추적해 UE로 보내므로, 관람객의 손이 실시간으로 작품을 흔듭니다.

작품 구조와 방향은 [docs/간접표현_작품구조.md](./docs/간접표현_작품구조.md)에 정리했습니다.
제작 노드 N대와 독립 시연 노드 1대의 [분산 목표 아키텍처](./docs/architecture/README.md)는 별도 설계입니다. 현재 구현과의 차이, 기술 선택 근거, draw.io 원본을 함께 정리했습니다.

EC2 제공 전 실행할 수 있는 [인프라 준비](./infra/README.md)를 추가했습니다. Compose 중앙 서비스,
비동기 초안 작업, Agent 임대·캐시, 실제 UE 상태 확인을 포함합니다.
기존 동기식 제작 API와 분리했으며 전체 UE 자동 제작·검수·배포는 후속 연결이 필요합니다.

## 협업 규칙

- [작업 규칙 · 명령어 (AI 에이전트 공용)](./AGENTS.md)
- [Git 협업 규칙](./docs/GIT_CONVENTIONS.md)
- [Jira 협업 규칙](./docs/JIRA_CONVENTIONS.md)
- [외부 라이브러리 · 코드 출처](./docs/THIRD_PARTY.md)

## 폴더

이미지 제작 공용 스킬은 [.agents/skills](./.agents/skills/README.md)에 보관합니다.
생성 PC·UE 제작 PC에서의 사용 방법은 [제작노드 스킬 안내](./docs/NODE_SKILLS.md)를 참고합니다.

코드가 **실행되는 장비**를 기준으로 나눴습니다. `apps/`는 장비마다 설치해서 실행하는 앱이고, `packages/`는 여러 앱이 가져다 쓰는 라이브러리입니다. 폴더마다 README에 하는 일을 적었습니다.
장비 열은 [분산 목표 설계](./docs/architecture/README.md)의 배치입니다. 지금은 한 PC에서 `central` 서버가 기획부터 렌더러 전달까지 모두 실행합니다.

| 폴더 | 하는 일 | 장비 (목표) |
|---|---|---|
| [apps/central](./apps/central) | 운영 API · 운영 화면(웹) · 작품 한 건의 제작 순서와 실패 처리 · 설정 | EC2 중앙 서버 |
| [apps/operator](./apps/operator) | 운영 PC 앱(PySide6): 작품 요청 · 자동/편집 모드 · 진행 단계 · 기록. 설치는 `uv sync --all-packages` | 운영 PC |
| [apps/node_agent](./apps/node_agent) | 제작 노드 Agent: 임대·heartbeat·파일 캐시 검사. UE 조립 연결 예정 | 제작 노드 N대 |
| [apps/show_agent](./apps/show_agent) | 시연 노드 Agent: 파일 준비·UE watcher 전달·실제 상태 보고, 선택적 프로세스 관리 | 시연 노드 1대 |
| [packages/contracts](./packages/contracts) | 장비 사이에 주고받는 형식: 작품 요청 · 계획 · 응답 모델, 표현 방식 목록 | 공통 |
| [packages/db](./packages/db) | DB 테이블(`artwork` · `asset`)과 연결 | 중앙 |
| [packages/studio](./packages/studio) | 작품 제작 로직: 기획 · 안무(`head`), 소재 생성(`generation`), 검사(`verify`), 조립(`assembly`), 렌더러 전달(`exhibition`) | 중앙 · 제작 노드 · 시연 노드 |
| [td](./td) | TouchDesigner: 관객 입력 센서(카메라 → UDP 7001 → UE), 라넬 TD 프로젝트 사본. TD 렌더러 코드는 보관 중 | 시연 노드 |
| [ue](./ue) | UE 플레이어: 라넬 UE 런타임 코드 사본, 전달 규약 | 제작 노드 · 시연 노드 |
| [controller](./controller) | 회사 원본과 팀 확장이 혼재. `packages/studio`의 숫자 변환·UE 검사·파일 교체에 사용하며 출처를 파일별로 확인 | 공통 |
| [infra](./infra) | Compose·CI/CD·실행 검증·현장 공유 저장소 설계 | 로컬 검증 → EC2 |
| [docs](./docs) | 작품 구조 · 분산 목표 설계 · 협업 규칙 | |
| [tests](./tests) | 흐름 테스트 | |

```
apps/central ─> head(의도·안무) ─> generation(배경·주인공 데이터) ─> verify ─> assembly ─> exhibition
               └──────────────────── packages/studio ─────────────────────────────┘    ├─> UE: scene.json
                                                                                       └─> TD: artwork.json (보관 경로)
관객 ─> 카메라 ─> td(MediaPipe·깊이) ─> UDP 7001 ─> 렌더러
```

## 실행

설치 · 서버 실행 · 테스트 · 코드 검사 명령은 [AGENTS.md](./AGENTS.md)의 「명령어」에 있습니다.

Windows에서 Python 확장 모듈을 불러오지 않고 중앙 서버와 테스트를 실행하려면 [Docker 실행 안내](./infra/local-dev/README.md)를 따릅니다. Docker 서버는 기본 `http://127.0.0.1:8001`에서 실행되며, 호스트의 Python·uv 설치는 필요하지 않습니다.

```powershell
python td/fake_hands.py 30          # 카메라 없이 가짜 손 보내기
```

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `STUDIO_DATA` | `runtime` | DB·생성 소재·전달 폴더 (`delivery_td/`: TD, `delivery/`: UE) |
| `RENDERER` | `both` | `td` / `ue` / `both`. `both`는 두 후보 모두 필수. 선택한 렌더러의 미지원 요구는 오류. TD로 보내는 데이터는 지금 읽는 쪽이 없음(TD 렌더러 경로 보관 중) |
| `DATABASE_URL` | (비움 → `runtime/studio.db`) | 단일 PC 개발은 SQLite 기본. 분산 Compose는 `postgresql+pg8000://...`로 PostgreSQL 사용 |
| `OPERATOR_TOKEN` | (비움 → 검사 생략) | 설정하면 `/api/*`의 조회·변경 모두 `X-Operator-Token` 헤더 필요 (영문·숫자만). 외부 연결 시 반드시 설정 |
| `BROKER_URL` | (비움) | 비동기 Worker·Dispatcher의 RabbitMQ 주소. 비밀 값은 환경 설정으로 전달 |
| `PLANNING_QUEUE` | `planning` | 중앙 기획 작업을 전달할 Celery 큐 이름. Worker 구독 이름과 일치해야 함 |
| `AGENT_KEYS_JSON` | `{}` | 노드 ID별 역할·카탈로그·서로 다른 토큰 등록. 실제 값은 Git 제외 |
| `LEASE_SECONDS` | `120` | 작업 임대 기간(초), heartbeat로 갱신 |
| `JOB_MAX_ATTEMPTS` | `3` | 작업별 최대 시도 횟수 |
| `LLM_PROVIDER` | `none` | `none`이면 규칙으로 계획. `gms`면 SSAFY GMS(OpenAI 호환 API), `ollama`면 로컬 Ollama에 먼저 묻고 실패하면 규칙 |
| `LLM_FALLBACK` | (비움) | 주 LLM에 닿지 못할 때(네트워크 끊김 · 시간 초과 · 크레딧 소진 · 키 오류 등) 대신 물을 LLM. `ollama`로 두면 GMS 실패 시 이 PC에서 Ollama로 실행하는 EXAONE에 묻고, 한 번 닿지 못하면 5분 동안은 GMS를 건너뜀. EXAONE도 실패하면 규칙 |
| `GMS_KEY` | (비움) | SSAFY GMS API 키. `LLM_PROVIDER=gms`일 때 필수. 저장소 · Jira에 적지 않고 환경 변수로만 넣음 |
| `GMS_MODEL` | `gpt-6.1-sol` | GMS에서 쓸 모델 이름(기획 · Input Agent) |
| `DIRECTOR_MODEL` | `gpt-6.1-sol` | 전시 모드 AI 미디어아트 디렉터가 GMS에서 쓸 모델 이름. `LLM_PROVIDER=gms`일 때만 쓰고, 대체 LLM(`LLM_FALLBACK`)은 기획과 같음 |
| `GMS_URL` | `https://gms.ssafy.io/gmsapi/api.openai.com/v1/responses` | GMS 주소. OpenAI Responses API와 요청 · 응답 형식이 같음 |
| `OLLAMA_MODEL` | `exaone3.5:7.8b` | Ollama로 실행할 로컬 모델 (`ollama list`로 확인). 기본은 EXAONE 3.5로, 비상업 라이선스라 **상업적으로 배포할 때는 쓸 수 없음** → `qwen3:4b` 등 Apache-2.0 모델로 지정 ([THIRD_PARTY](./docs/THIRD_PARTY.md#exaone-사용-범위)) |
| `OLLAMA_URL` | `http://127.0.0.1:11434/api/chat` | Ollama 주소 |
| `EXHIBITION_FEED_MAX_IN_FLIGHT` | `10` | [전시 자동 순환](./infra/HEAD_EXHIBITION_FEED.md)이 동시에 진행할 자동 작품 수 상한 |
| `EXHIBITION_FEED_STALE_SECONDS` | `1800` | 전시 자동 순환에서 접수 후 이 시간(초)이 지난 작품은 상한 계산에서 뺌. 임시값(S15P31A504-218에서 확정) |
| `EXHIBITION_FEED_RETRY_SECONDS` | `60` | 전시 자동 순환이 GMS 장애 · 노드 꺼짐 때 새 작품 넣기를 쉬는 시간(초) |

GMS로 실행하는 예(PowerShell):

```powershell
$env:LLM_PROVIDER="gms"; $env:GMS_KEY="<발급받은 키>"; uv run uvicorn central.main:app
```

GMS가 안 될 때(네트워크가 끊겼을 때 포함)는 이 PC에서 **Ollama로 EXAONE을 실행**해 대신 묻습니다. Ollama는 모델을 PC에서 직접 돌리는 실행 프로그램이고, central은 `127.0.0.1`(이 PC 자신)로만 부르므로 인터넷이 없어도 동작합니다.

1. 인터넷이 될 때 [Ollama](https://ollama.com)를 설치하고 모델을 미리 받아 둡니다: `ollama pull exaone3.5:7.8b` (약 5GB, 그래픽 메모리 5~6GB 사용)
2. 시연 PC에서 Ollama가 켜져 있게 둡니다(설치하면 보통 자동 실행)
3. central과 Ollama를 같은 PC(또는 같은 내부망)에서 돌립니다. central이 외부 서버(EC2)에 있으면 인터넷이 끊겼을 때 central 자체에 닿지 못합니다
4. `$env:LLM_FALLBACK="ollama"`를 더해 서버를 띄웁니다. 모델은 기본값(`exaone3.5:7.8b`)을 씁니다

EXAONE은 지금처럼 비상업적으로 사용할 때만 씁니다. **상업적으로 배포할 때는 EXAONE을 쓸 수 없으므로** `$env:OLLAMA_MODEL="qwen3:4b"`처럼 Apache-2.0 모델로 바꿉니다. 기준은 [THIRD_PARTY의 「EXAONE 사용 범위」](./docs/THIRD_PARTY.md#exaone-사용-범위)에 있습니다. 어느 모델이 실제로 답했는지는 작품 명세의 추출 근거(`provenance.model_ref`, 예: `gms:gpt-6-luna` · `ollama:exaone3.5:7.8b`)에 남습니다.

## 흐름

```
① 입력 (전시: 지원 범위 안에서 자율 기획 / 사용자 제작: 원문·명시 입력에 근거)
   모드 · 테마 · 보이지 않는 주인공 · 드러내는 방식(형상/흔적/없음) · 칩 · 표현 방식 · 입력 종류 · 소재 선택
② 의미 (studio/head/planning · choreography)
   [전시 모드] AI 디렉터가 지원 범위 안에서 방향 제안·검사·다시 쓰기(LLM 없거나 실패하면 프리셋 규칙) → 자동 요청(프리셋 규칙으로 대체될 때만) → 의도(주인공·드러내는 방식) → 구성 → 입력·반응 → 안무(단계·시간·힘)
③ 소재 (studio/generation/assets · subjects → studio/verify/checks)
   배경: 풀에서 고르기(사용자 제작만) / 새로 생성 → 입구 검사 → 풀
   주인공 데이터: 실루엣 점(형상, 사용자 제작은 풀에서 테마와 상관없이 재사용) / 발자취 경로(흔적) → 검사
   [전시 모드] 풀에서 고르지 않고 배경·실루엣을 매번 새로 생성(새 소재는 풀에 저장)
④ 전달 (studio/assembly/td_artwork · ue_scene → studio/exhibition/delivery)
   UE ← scene.json / TD ← artwork.json(보관 경로, TD는 지금 센서 역할) → 모든 필수 버전 폴더 준비 → prepared (활성 release.json 유지)
   어느 단계든 실패하면 보류(held) 또는 실패(failed)로 기록하고, 렌더러는 지금 작품을 유지
```

선택 항목:
- **스토리**: `direction=story`일 때만 만듭니다. 기본값 `mood`는 분위기와 색만 정합니다.
- **안무**: 드러내는 방식마다 정해진 단계가 반복됩니다(형상: 흩어져 유영 → 모여듦 → 형상 유영 → 흩어짐). 반응 세기 칩으로 템포가 바뀝니다.
- **테마**: 자유 문장만 씁니다. 운영 화면과 API에는 테마를 고르는 선택지(프리셋)가 없습니다. 문장을 비우면 전시 모드는 AI 디렉터가 방향을 정하고, 디렉터가 없거나 실패하면 내부 기본 테마(바다·숲·우주·꽃밭) 중 하나를 고르는 프리셋 규칙으로 만듭니다. 사용자 제작은 내부 기본 테마 중 하나를 씁니다. 배경 풀은 이 내부 테마 단위로 묶입니다(자유 문장은 `custom`).
- **입력 종류**: 손·터치 외에 몸·소리를 고를 수 있습니다(연결 예정).

아직 연결되지 않은 것(영역·미디어·시청각 방식, 시간 구조 `stages`/`accumulate`)은 소재를 만들기 전에 오류 처리합니다.

## 로컬 실행 범위

기본 설정(`LLM_PROVIDER=none`)에서는 외부 API 없이 이 PC에서 끝까지 돕니다.
- **계획**: 기본은 규칙입니다. `LLM_PROVIDER=gms`(SSAFY GMS, 사용한 토큰만큼 크레딧 차감)나 `ollama`(로컬)로 켜면 LLM이 주인공·드러내는 방식·분위기·색·반응을 고릅니다. 틀린 답은 한 번 고쳐 묻고, 그래도 틀리면 규칙으로 대체합니다. 단계별 출처는 작품 기록의 '출처'에 남습니다.
- **배경**: 로컬 생성기([`generation/images.py`](./packages/studio/src/studio/generation/images.py))가 1280×720 PNG를 만듭니다. 흐름 시험용이라 작품 품질 소재는 아닙니다.
- **주인공 데이터**: 기본 실루엣(고래·물고기·새·나비)은 코드로 그린 윤곽에서 점을 뽑습니다. 목록에 없는 주인공의 형상 요청은 오류 처리하며 임의의 타원으로 대신하지 않습니다.
- **검사**: 배경(UE 파일 규칙·16:9·해상도·빈 화면·중복), 주인공 데이터(점 수·화면 안·차지 비율·발자국 순서), UE scene(계약·카탈로그·C++ 허용 범위).

## 기술 선택

| 영역 | 선택 | 이유 |
|---|---|---|
| API | FastAPI | 자동 API 문서(`/docs`), 요청 검사, 이후 WebSocket으로 진행 상황 전달 |
| 모델 | Pydantic | 같은 모델로 API 검사와 LLM 구조화 출력(JSON 스키마)을 처리 |
| DB | SQLite + SQLModel | 전시 PC 한 대에서 파일 하나로 운영. `DATABASE_URL`만 바꾸면 PostgreSQL로 이전 |
| 파일 | 디스크 | 이미지·주인공 데이터는 `STUDIO_DATA` 아래에 두고, DB에는 경로·해시·상태·사용 횟수만 저장 |
| 렌더러 전달 | JSON 파일 | 네트워크 대신 파일. 원자 교체로 반쯤 쓴 작품을 렌더러가 읽지 않음 |
| 관람객 입력 | UDP 7001 · JSON | 라넬 protocol v2 |

| DB 테이블 | 주요 칸 |
|---|---|
| `artwork` | 모드, 테마, 표현 방식, 상태(prepared/held/failed, 과거 published 기록), 요청·단계 기록(JSON), 전달 파일 경로 |
| `asset` | 종류(background·silhouette 등), 테마(실루엣은 주인공 이름), 경로, sha256, 상태, 사용 횟수, 세트 번호 |

## 다음에 할 일

1. TD 센서 파트를 [td/docs/기능명세.md](./td/docs/기능명세.md)대로 진행합니다(입력 계약, 관람객 ID 부여, 모의 입력).
2. 실루엣 생성기를 실제 AI 이미지 → 마스크로 바꿉니다([`generation/subjects.py`](./packages/studio/src/studio/generation/subjects.py)의 `OutlineProvider`).
3. 실제 배경 이미지 모델을 [`generation/images.py`](./packages/studio/src/studio/generation/images.py)의 같은 인터페이스로 붙입니다.
4. UE의 형상·흔적 재생 기능(C++ 또는 Niagara)을 라넬과 협의합니다. TD 렌더러 경로(`RENDERER=td`, `td_artwork.py`)는 지우지 않고 보관합니다. 서버 기본값은 임시로 `both`입니다.
