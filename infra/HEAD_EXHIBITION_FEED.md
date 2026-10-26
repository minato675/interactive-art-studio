# 전시 자동 순환 설계: AI가 쓴 작품 요청을 분산 제작 흐름에 끊임없이 넣기

| 항목 | 내용 |
|---|---|
| Jira | [S15P31A504-27 전시 모드 자동 순환](https://ssafy.atlassian.net/browse/S15P31A504-27) |
| 관련 Jira | [S15P31A504-212 전시 모드 AI 미디어아트 디렉터](https://ssafy.atlassian.net/browse/S15P31A504-212) (선행 작업), [S15P31A504-218 멈춘 작품 제외 기준 결정](https://ssafy.atlassian.net/browse/S15P31A504-218) (후속 결정) |
| 작성일 | 2026-10-22 (2026-10-23 수정) |
| 상태 | 구현(S15P31A504-27). 구현하면서 정한 값은 [10. 구현 기록](#10-구현-기록) |
| 선행 설계 | [AI 미디어아트 디렉터 설계](../docs/superpowers/specs/2026-10-22-exhibition-director-design.md) (`docs/superpowers/specs/2026-10-22-exhibition-director-design.md`) |
| 주의 | 이 문서가 가리키는 `docs/superpowers/specs/2026-10-22-exhibition-director-design.md`, `packages/studio/src/studio/head/exhibition_director.py`, `packages/studio/src/studio/head/capabilities.py`는 S15P31A504-212(MR !48)가 `develop`에 병합된 뒤에 생깁니다 |

## 0. 한눈에 보기

전시(약 8시간) 동안 사람이 손대지 않아도 작품이 계속 만들어지게 합니다.

```
[중앙 서버]
  AI 디렉터가 작품 요청 문장을 씀
        ↓
  문장을 작품 명세로 바꿈 (Input Agent)
        ↓
  지금 UE로 만들 수 있는 명세인지 확인 (사전 확인)
        ↓
  분산 제작 흐름에 접수  ──→  배경 생성 → 에셋 생성 → UE 조립 → 실행 검증 → AI 검수 → 시연 PC 적용
        ↓                                         (기존 자동 제작 사이클, 이번에 바꾸지 않음)
  접수되면 바로 다음 요청을 쓰기 시작
  (진행 중인 작품이 10개면 하나가 끝날 때까지 기다림)
```

- 작품이 완성되거나 중간에 실패하면, 그 자리를 바로 새 작품으로 채웁니다.
- AI 디렉터는 자기가 최근에 쓴 요청 10개를 보고, 그것과 다른 결의 작품을 쓰도록 요청받습니다.
- GMS(SSAFY가 제공하는 LLM 게이트웨이)가 끊기거나 필요한 PC가 꺼져 있으면, 새 작품 넣기만 60초 쉬었다가 저절로 다시 시도합니다. 그동안 시연 화면은 마지막 작품을 계속 보여 줍니다.
- 운영 앱의 "전시 모드" 체크박스로 켜고 끕니다. 운영 앱을 꺼도 중앙 서버에서 계속 돕니다.

## 1. 용어

이 문서에서 쓰는 말입니다. 코드 이름은 괄호 안에 적었습니다.

| 용어 | 뜻 |
|---|---|
| AI 디렉터 (`ExhibitionDirector`) | 전시 모드에서 작품 요청 문장(원문)을 대신 써 주는 LLM 역할. S15P31A504-212에서 만들었고 코드는 `packages/studio/src/studio/head/exhibition_director.py` |
| 원문 | 작품을 설명하는 자연어 문장. 예: "새벽 안개 속에서 사람을 따라 흩어지는 반딧불" |
| 작품 명세 (`Spec`) | 원문을 칸으로 나눠 정리한 JSON. "무엇이 보이나(`content`) · 아무도 없을 때(`idle`) · 사람이 오면(`interactions`)"으로 나뉩니다. 정의는 `packages/contracts/src/contracts/spec.py` |
| 템플릿 계열 (`template_family`) | 작품 명세의 큰 갈래. `scene_family`(배경·캐릭터 이미지를 만들어 조립)와 `flow_family`(입자·궤적만, 이미지 생성 없음) |
| Input Agent (`LLMSpecMatcher`) | 원문을 작품 명세로 바꾸는 LLM 단계. `packages/studio/src/studio/head/spec_matching.py`. 설명은 `packages/studio/src/studio/head/README.md`의 「원문 → 작품 명세」 |
| 분산 Head | 작품 하나를 배경 생성부터 시연 PC 적용까지 여러 PC에 나눠 진행시키는 중앙 서버의 제작 관리자. 접수 API는 `POST /infra/head/workflows`(`apps/central/src/central/head_api.py`), 진행 로직은 `packages/studio/src/studio/head/dispatch.py` · `packages/studio/src/studio/head/automation.py` |
| 흐름 (Head workflow) | 분산 Head에 접수된 작품 한 건의 진행 기록. DB 표 `infra_head_workflow`(`packages/db/src/db/head.py`)의 한 줄 |
| 자동 제작 사이클 | 접수된 흐름이 사람 승인 없이 배경 → 에셋 → UE 조립 → 실행 검증 → AI 검수 → 시연 PC 적용까지 가는 기존 정책. `infra/HEAD_AUTOMATION.md` |
| UE 템플릿 | UE 플레이어에 미리 만들어 둔 작품 틀 5종(`gpu_particles` · `image_stir` · `fluid` · `paint` · `deformation`). 목록과 고르는 표는 `packages/studio/src/studio/head/ue_templates.json` |
| 사전 확인 (preflight) | 접수 전에 "이 명세로 고를 수 있는 UE 템플릿과, 그 템플릿을 설치한 온라인 제작 PC가 있는가"를 확인하는 단계. `packages/studio/src/studio/head/ue_support.py`의 `preflight` · `select_template`. 설명은 `infra/HEAD_UE_PREFLIGHT.md` |
| 생성 노드 · 제작 노드 · 검증 노드 · 시연 노드 | 작업을 나눠 맡는 PC. 생성 노드(`role=generation`)는 배경·에셋 이미지를 만들고, 제작 노드(`role=production`)는 UE 조립, 검증 노드(`ue-review-1`, PC1)는 실행 검증, 시연 노드(`ue-show-1`, PC2)는 전시 화면에 적용합니다. 구조는 `infra/README.md` |
| 작업 · 작업함 | 중앙 DB의 작업 목록(`infra_job`, `packages/db/src/db/jobs.py`). 노드나 Worker가 하나씩 가져가 처리합니다 |
| Dispatcher | 중앙 서버에서 2초마다 도는 반복 프로그램. 진행 중인 흐름을 다음 단계로 넘기고, 작업함의 작업을 Worker에게 보냅니다. `apps/central/src/central/worker.py`의 `dispatch_once` |
| Worker | Dispatcher가 보낸 작업을 실제로 처리하는 중앙 서버 프로그램(Celery). LLM 호출처럼 오래 걸리는 일을 맡습니다. 같은 파일의 `execute` |
| 개발용 동기 경로 | `POST /api/artworks`. 요청 하나가 작품 완성까지 기다리는 개발·시험용 경로(`apps/central/src/central/pipeline.py`). 지금 AI 디렉터는 여기에만 연결되어 있습니다 |
| GMS | SSAFY가 제공하는 LLM 게이트웨이. AI 디렉터와 분산 Head의 판단 모두 `gpt-6.1-sol`을 GMS로 부릅니다(설정은 각각 `DIRECTOR_MODEL` · `HEAD_MODEL`) |
| 최근 디렉터 기록 | AI 디렉터가 쓴 원문과 고른 값 중 최근 10회차. 검사 통과 여부와 상관없이 디렉터가 답을 낸 회차는 모두 들어가고, 한 회차는 한 건으로 셉니다. 디렉터 작업의 결과 칸(`infra_job.result`)에서 읽습니다(5.4 · 5.7) |
| `completed` · `needs_attention` | 흐름이 끝난 상태. `completed`는 시연 PC 적용까지 성공, `needs_attention`은 어느 단계에서 실패·보류되어 멈춤 |

## 2. 왜 필요한가

- 전시는 약 **8시간** 열리고, 사람의 개입은 최소로 합니다. 토큰 비용은 따지지 않습니다.
- AI 디렉터(S15P31A504-212)는 지금 개발용 동기 경로에만 연결되어 있습니다. 이 경로는 요청 하나가 작품 완성까지 기다리므로, 다음 요청은 앞 작품이 끝난 뒤에야 나옵니다.
- 담당자가 원하는 동작:
  - AI 디렉터가 쓴 요청이 **분산 Head에 접수되면, 완성을 기다리지 않고 바로 다음 요청을 쓴다.**
  - 작품 하나는 화면에 **1분 내외** 보입니다.
  - 뒤쪽 검증 단계가 더 엄격해질 예정이라 **완성되는 작품보다 중간에 실패하는 작품이 더 많을 것**으로 봅니다. 그래서 실패한 작품의 자리는 바로 다시 채워야 합니다.

## 3. 목표 · 하지 않을 것 · 실패로 볼 것

**목표**
- 중앙 서버에서 도는 자동 순환을 만든다. 운영 앱이 꺼져 있어도 계속 채운다.
- 순서: AI 디렉터가 원문을 씀 → Input Agent가 작품 명세로 바꿈 → 사전 확인 → 분산 Head에 접수.
- 접수되면 곧바로 다음 디렉터 작업을 시작한다. 단, 진행 중인 자동 작품이 상한(10개)에 닿으면 기다린다.
- 자동 작품이 `completed`가 되거나 `needs_attention`으로 멈추면 그 자리를 바로 다시 채운다.
- AI 디렉터가 고를 수 있는 범위는 **코드에서 계산한다**. UE 템플릿 표(`packages/studio/src/studio/head/ue_templates.json`)나 사전 확인 규칙(`packages/studio/src/studio/head/ue_support.py`의 `select_template`)이 바뀌면, 디렉터 쪽을 고치지 않아도 범위가 따라 바뀌어야 한다(S15P31A504-212에서 정한 "범위를 지시문에 하드코딩하지 않는다" 원칙).
- AI 디렉터가 **최근 10개 원문과 다른 결로** 쓰게 한다. 겹치는 답을 막지는 않고 피하도록 요청하며, 겹쳤는지는 기록한다(5.4).
- 켜기·끄기는 중앙 서버 API로 한다. 운영 앱의 전시 모드 체크박스는 이 API를 부르도록 바꾼다.
- **GMS가 끊기면 새 작품 넣기를 잠시 멈춘다.** 미리 정해 둔 예시 테마(프리셋)로 대신 넣지 않는다. 시연 노드는 다음 작품이 적용될 때까지 마지막 작품을 계속 보여 주므로(작품 명세의 `display_policy: until_next_applied`) 화면은 유지된다. GMS가 돌아오면 다시 채운다.

**하지 않을 것 (이번 범위 밖)**
- 시연 화면의 표시 순서·시간 관리(1분마다 다음 작품으로 바꾸기, 완성작 대기열). 별도 작업으로 다룬다. 이번에는 지금처럼 시연 PC에 적용(`Applied`)되는 순서대로 바뀐다.
- 다양성을 코드로 강제하기(최근과 겹치는 답을 거부하기). 이번에는 지시문으로 피하게만 하고, 전시에서 겹침이 잦으면 그때 막는 규칙을 검토한다.
- 색 계열·에셋 종류처럼 디렉터 답 밖의 다양성 관리. 나중에 다룬다.
- 개발용 동기 경로의 AI 디렉터 동작 변경. 그대로 둔다.
- 자동 제작 사이클(`infra/HEAD_AUTOMATION.md`)의 정책 변경. 그대로 따른다.
- 새 LLM 공급자. AI 디렉터는 기존 설정 `LLM_PROVIDER=gms` · `GMS_KEY`와 디렉터 전용 모델 설정 `DIRECTOR_MODEL`(`gpt-6.1-sol`, S15P31A504-212에서 추가)을, 분산 Head는 기존 설정 `HEAD_MODEL`(`gpt-6.1-sol`)을 쓴다. 설정 설명은 루트 `README.md`의 환경 변수 표.

**실패로 볼 것 (돌아가도 실패)**
- 8시간 중 사람이 다시 켜거나 손대지 않으면 순환이 멈추는 것. 예: 중앙 서버 재시작 뒤 꺼진 채로 남음, GMS 장애 한 번 뒤 다시 채우지 않음.
- AI 디렉터가 고른 값이 사전 확인에서 거부되는 일이 반복되는 것. 디렉터와 사전 확인이 서로 다른 범위를 보고 있다는 뜻이다.
- GMS 장애나 PC 꺼짐 동안 접수만 되고 진행되지 못하는 작품이 쌓이는 것.
- AI 디렉터의 LLM 호출이 Dispatcher의 2초 반복을 막아, 다른 작품의 진행까지 멈추는 것.
- 멈춘 작품 하나가 자리를 계속 차지해서 순환이 느려지는 것.

## 4. 지금 코드 상태 (2026-10-22, S15P31A504-212 브랜치 `49bda3e` 기준)

**AI 디렉터** — `packages/studio/src/studio/head/exhibition_director.py`
- `ExhibitionDirector(llm).propose(caps, rejected)`는 개발용 동기 경로의 계획 어휘(표현 방식 · 분위기 · 색 · 주인공 · 드러내는 방식 `none`/`formation`/`trace`)로 답한다.
- `problems()`가 답을 코드로 검사하고, 통과하지 못하면 이유를 붙여 최대 3번(`MAX_ATTEMPTS`) 부른다. LLM에 닿지 못하면 `LLMUnavailable` 예외가 난다.
- 지원 범위는 `packages/studio/src/studio/head/capabilities.py`의 `capabilities(renderers)`이며, 개발용 동기 경로 기준이라 분산 Head에는 맞지 않는다.

**분산 Head 접수** — `apps/central/src/central/head_api.py`(API), `packages/studio/src/studio/head/dispatch.py`의 `submit`(처리)
- 요청 형식은 `HeadRequest`(`packages/contracts/src/contracts/head.py`)다. 필드는 다음과 같다.
  - `request_id` · `artwork_id` · `version`: 부르는 쪽이 정한다.
  - `prompt`: 원문. `spec`: 작품 명세(필수).
  - `automation`(기본 true), `auto_publish`(기본 true), `show_node`, `max_reworks`, `profile`.
- 모델이 정의되지 않은 필드를 거부(`extra="forbid"`)하므로 필드를 더 붙일 수 없다.
- 같은 `request_id`로 같은 내용을 다시 보내면 기존 흐름을 돌려주고, 내용이 다르면 거부(`jobs.Conflict`)한다.
- 접수할 때 사전 확인(`ue_support.preflight`)을 부른다. `select_template`가 명세로 UE 템플릿을 고르고, `ready_nodes`가 그 템플릿을 설치했다고 보고한 온라인 제작 노드를 찾는다. 하나라도 실패하면 `jobs.Conflict`.
- `automation=true`면 검증 노드(`ue-review-1`)와 시연 노드가 노드 등록표(`AGENT_KEYS_JSON` 설정)에 있는지도 확인한다. 등록만 보고, 지금 켜져 있는지는 보지 않는다(노드는 켜져 있는 동안 주기적으로 생존 신호인 heartbeat를 중앙에 보내고, 중앙은 이것으로 켜짐 여부를 판단한다). 생성 노드는 확인하지 않는다.

**사전 확인 규칙** — `packages/studio/src/studio/head/ue_support.py`의 `select_template`
- 드러내는 방식 `formation`(형상) · `trace`(흔적)는 받지 않는다.
- "사람이 오면" 규칙은 1개만, 반응 방식은 `while`(사람이 있는 동안 반응)만, 입력은 `appear` · `move`만 받는다.
- 평상시 움직임은 비우거나 `ignore`만 받는다.
- 반응 `press` · `swirl` · `scatter`는 아직 보류한다.
- 템플릿은 `ue_templates.json`의 표(반응·매질 → 템플릿)로 고르고, 반응 `follow`는 `gpu_particles` · `image_stir`에서만 된다.
- 위 규칙 중 일부는 표에서, 일부는 코드의 조건문에서 온다.

**Input Agent** — `packages/studio/src/studio/head/spec_matching.py`의 `LLMSpecMatcher(llm).match(원문, 템플릿 계열)`
- 원문을 작품 명세로 바꾼다. 답이 틀리면 한 번 다시 묻고, 그래도 틀리면 규칙으로 채운다.
- 원문이 그 계열에서 쓰지 않는 칸을 요구하거나 계열과 맞지 않으면 보류 사유(`holds`)를 낸다. 보류가 있으면 작업을 만들지 않는 것이 규칙이다.
- LLM에 닿지 못해도(`LLMUnavailable`) 예외를 내지 않고 규칙으로 채운다.

**Dispatcher와 Worker** — `apps/central/src/central/worker.py`
- Dispatcher(`dispatch_once`, 2초마다)는 ① 진행 중인 흐름을 다음 단계로 넘기고(`head_dispatch.advance`), ② 작업함에서 중앙이 처리할 작업(`planning` 큐)을 메시지 브로커(RabbitMQ)를 거쳐 Worker에게 보낸다. 배경 생성 · 조립 같은 다른 큐의 작업은 각 노드가 가져간다.
- Worker(`execute`)는 초안 작성(`draft`), 분산 Head 판단(`head_decide`) 같은 LLM 작업을 처리한다.
- Worker는 LLM을 `with_fallback(GMS, LLM_FALLBACK)`으로 만든다. `LLM_FALLBACK=ollama`면 GMS가 끊겨도 이 PC의 EXAONE이 대신 답하므로, AI 디렉터는 `LLMUnavailable`을 내지 않는다.

**흐름 상태** — `packages/studio/src/studio/head/automation.py`, `packages/studio/src/studio/head/review.py`
- 진행 중: `planning_*` · `waiting_*` · `generation_complete`.
- 사람 대기: `awaiting_publication_approval` · `awaiting_background_input`.
- 끝: `completed` · `needs_attention`.
- 멈춘 이유는 대개 흐름의 `payload._flow.hold`에 남는다. 예: 분산 Head 판단 실패 `head_decision_failed`, 실행 검증 실패 `runtime_execution_failed`. 다만 진행 중 예외는 `_automation_error`, 요청 재검사 실패는 `_ue_hold`에 남는다.
- `head_decision_failed`에는 GMS 연결 실패뿐 아니라 "AI 계획이 템플릿 규칙을 어김" 같은 실패도 섞여 있다. 구분하려면 분산 Head 판단 작업(`head_decide`)의 `error` 칸을 본다. GMS에 닿지 못했으면 `LLMUnavailable`이 남는다(`packages/studio/src/studio/head/responses.py`).

**운영 앱의 전시 모드** — `apps/operator/src/operator_app/window.py`의 `toggle_exhibition`
- 앱이 켜져 있는 동안 개발용 동기 경로에 `{"mode": "exhibition"}` 요청을 계속 보낸다.
- 코드 주석과 `apps/operator/README.md`에 "중앙 자동 순환(S15P31A504-27)이 생기면 그 API로 바꾼다"고 적혀 있다.

## 5. 설계

### 5.1 전체 흐름

```
Dispatcher (2초마다) — apps/central/src/central/worker.py
  ├─ 진행 중인 흐름을 다음 단계로 넘기기          ← 지금 그대로
  ├─ 자동 순환 확인                              ← 새로 추가
  │    켜져 있나? 쉬는 중이 아닌가?
  │    (진행 중 자동 작품 + 대기 중 디렉터 작업) < 상한(10)?
  │    → 그렇다면 "디렉터 작업"(작업 종류 exhibition_feed) 하나를 작업함에 넣음
  └─ 작업함 → Worker로 보내기                     ← 지금 그대로

Worker: 디렉터 작업 하나 처리                      ← 새로 추가
  0. 이 작업으로 이미 접수한 흐름이 있으면 그대로 끝 (5.5)
  1. 필요한 노드가 모두 켜져 있는지 확인. 아니면 쉬기 (5.6)
  2. AI 디렉터가 고를 수 있는 범위 계산 (5.3)과 최근 디렉터 기록 10개 읽기 (5.4)
  3. AI 디렉터가 최근 기록과 다른 결로 원문 + 고른 값을 제안   ← LLM (GMS gpt-6.1-sol)
  4. 코드로 검사. 틀리면 이유를 붙여 3번으로 (최대 3번)
  5. Input Agent: 원문 → 작품 명세                ← LLM
     AI 디렉터가 고른 값으로 명세를 덮어쓰고 다시 검증 (5.4)
  6. 보류 사유가 있거나 사전 확인에 실패하면 이유를 붙여 3번으로
  7. 분산 Head에 접수 (5.5)
  8. 결과 기록 후 끝. 다음 Dispatcher 확인(최대 2초 뒤)에서 자리가 있으면 바로 다음 디렉터 작업
```

- 디렉터 작업은 **한 번에 하나만** 돈다(담당자 결정, 2026-10-23). 앞 작업이 접수를 마치면 최대 2초 뒤 다음 작업이 들어가므로, "접수되면 바로 다음 요청"이 된다.
- LLM 호출은 모두 Worker에서 한다. Dispatcher는 DB만 읽고 작업을 넣는다. LLM은 한 번에 수 초에서 수십 초가 걸리는데, Dispatcher가 기다리면 그동안 모든 작품의 진행이 멈추기 때문이다.
- Dispatcher와 Worker는 분산 배포(`BROKER_URL` 설정, `infra/central/compose.yaml`)에서만 돈다. 개발 서버(`uv run uvicorn central.main:app`)만 켠 상태에서는 자동 순환이 돌지 않는다. 이 점을 `apps/central/README.md`에 적는다.

### 5.2 켜기·끄기와 상태 저장

**상태 저장**
- 자동 순환 상태는 DB의 새 표 `exhibition_feed`(한 줄짜리, `packages/db`)에 둔다(설계 기본값).
  - API 서버 · Dispatcher · Worker가 서로 다른 프로그램이라 메모리로는 상태를 나눌 수 없다.
  - 8시간 중 중앙 서버가 재시작돼도 켜진 상태가 유지되어야 한다.
  - `infra/HEAD_AUTOMATION.md`의 "DB 열은 추가하지 않는다"는 흐름 표(`infra_head_workflow`)에 대한 규칙이라, 새 표를 만드는 것과 부딪히지 않는다.
- 저장할 것: 켜짐 여부, 쉬는 끝 시각과 쉬는 이유, 마지막 디렉터 작업 ID.

**API** (운영자 토큰 인증 필요, `apps/central/src/central/infra_api.py`의 `operator`와 같은 방식)
- `GET /infra/exhibition/feed`: 켜짐 여부, 쉬는 중이면 이유와 끝 시각, 진행 중 자동 작품 수, 상한, 최근 디렉터 작업 몇 개의 결과.
- `PUT /infra/exhibition/feed` `{"enabled": true | false}`: 켜기·끄기. 끄면 새 디렉터 작업만 넣지 않고, 이미 접수된 작품은 끝까지 진행한다.

**운영 앱** — `apps/operator/src/operator_app/window.py`
- 전시 모드 체크박스를 켜고 끌 때 위 API를 부른다. 앱 안에서 요청을 반복해 보내던 부분은 없앤다.
- 앱을 켜면 `GET`으로 현재 상태를 읽어 체크박스에 보여 준다.

**새 설정** (비밀값이 아니므로 `apps/central/src/central/config.py`의 `Settings`에 일반 설정으로 추가하고, 루트 `README.md` 환경 변수 표에 적는다)

| 환경 변수 | 기본값 | 뜻 | 정한 사람 |
|---|---|---|---|
| `EXHIBITION_FEED_MAX_IN_FLIGHT` | 10 | 동시에 진행할 자동 작품 수 상한 | 담당자 (2026-10-23) |
| `EXHIBITION_FEED_STALE_SECONDS` | 1800 (30분) | 접수 후 이 시간이 지난 작품은 상한 계산에서 뺀다 (5.6) | 임시값. S15P31A504-218에서 확정 |
| `EXHIBITION_FEED_RETRY_SECONDS` | 60 | 쉬기 시간 (5.6) | 설계 기본값 |

### 5.3 AI 디렉터가 고를 수 있는 범위 계산 (하드코딩하지 않기)

범위는 분산 Head가 실제로 받는 작품 명세의 값으로 계산한다. S15P31A504-212의 `capabilities(renderers)`는 개발용 동기 경로용이라 쓰지 않는다.

- **방법**: 명세에서 UE 템플릿 선택에 쓰이는 칸들의 값 조합을 만들고, 각 조합으로 만든 시험용 명세를 **`select_template`에 실제로 넣어 통과한 조합만** 남긴다. 사전 확인과 AI 디렉터가 같은 함수를 보므로 둘이 어긋날 수 없고, 템플릿 표나 조건문이 바뀌면 범위가 저절로 따라간다.
- **조합 수 줄이기**: 관련 칸(템플릿 계열 · 매질 · 드러내는 방식 · 2d/2.5d · 입력 · 반응 · 반응 방식 · 복귀 방식 · 평상시 움직임)을 모두 곱하면 수십만 개다. 그래서 `select_template`가 **실제로 통과 여부를 가르는 칸**만 조합하고, 통과 여부에 영향이 없는 칸(예: 복귀 방식, 2d/2.5d)은 따로 허용값 목록으로 둔다. 어느 칸이 영향을 주는지는 구현자가 `select_template`를 읽고 정한다. 대신 "따로 둔 칸을 어느 값으로 바꿔도 통과 여부가 같다"를 테스트로 확인한다.
- **템플릿 표 한 번만 읽기**: `select_template`는 부를 때마다 `ue_templates.json`을 다시 읽는다(`catalog()`). 범위 계산에서는 수백 번 부르므로, 이미 읽은 표를 넘길 수 있는 선택 인자를 추가한다. 기존 호출은 그대로 동작해야 한다. 계산은 디렉터 작업마다 한 번 한다.
- **지금 켜진 PC로 줄이기**: 통과한 조합 중, 그 템플릿을 설치했다고 보고한 온라인 제작 노드가 있는 것만 남긴다(`ready_nodes`와 같은 판단, 설계 기본값). 지금 만들 수 있는 것만 주어 접수 실패를 줄이기 위해서다. 남는 조합이 없으면 AI 디렉터를 부르지 않고 쉰다(5.6).
- **AI 디렉터에게 주는 방식**: 조합 목록을 그대로 주지 않고, 칸별 허용값과 조합 규칙(예: "반응 follow는 매질 particle · light와만")으로 요약해 지시문과 응답 형식(JSON 스키마의 `enum`)에 넣는다. 요약 방식은 구현자가 정한다. 판정은 5.4의 코드 검사에서 **원래 조합 목록에 있는지**로 한다.
- **자유 칸**: 분위기 · 색 · 테마 · 배경 · 조명은 템플릿 선택에 쓰이지 않으므로 AI 디렉터가 자유롭게 쓴다. 색(`palette`)은 형식(역할 + `#RRGGBB` 색 코드 목록, `PaletteColor`)만 검사한다.

### 5.4 AI 디렉터의 답과 작품 명세

**AI 디렉터의 답**
- 한 번에 **원문 한 개 + 템플릿 선택 칸의 값 + 분위기 · 색**을 JSON으로 답한다. 원문은 고른 값이 드러나게 쓴다.
- 답의 필드 이름과 클래스 위치는 구현자가 정한다. 방식은 S15P31A504-212의 `ExhibitionDirector`를 따른다: 응답 형식에 허용값을 넣고, 코드로 먼저 검사하고(`problems()`), 거부 이유를 붙여 다시 묻고(최대 3번), 시도마다 사용한 모델을 기록한다.
- 이 경로의 지시문은 S15P31A504-212의 지시문(보이지 않는 주인공을 형상·흔적으로 드러내기 위주)을 그대로 쓰지 않는다. UE가 형상·흔적을 받지 않으므로, 드러내는 방식과 주인공 안내도 5.3에서 계산한 허용값으로만 만든다.

**최근 작품과 다른 결로 쓰기** (담당자 결정, 2026-10-23)

AI 디렉터는 부를 때마다 처음 보는 상태라(LLM은 이전 호출을 기억하지 않는다), 같은 지시문이면 비슷한 작품을 반복한다. S15P31A504-212 디렉터를 실제 GMS로 3번 불렀을 때 3번 모두 "어두운 수면 + 빛 입자"였다. 그래서 최근 기록을 지시문에 넣어 다르게 쓰도록 요청한다.

- **기록 읽기**: 디렉터 작업(`kind = "exhibition_feed"`)을 만든 순서(`created_at`) 최신부터 읽어, 결과 칸에 **AI 디렉터가 쓴 원문**이 있는 작업 **10개**를 고른다(5.7). 검사(5.1의 4번 코드 검사 · 5~6번 작품 명세 · 보류 사유 · 사전 확인) 통과 여부는 따지지 않는다(담당자 결정, 2026-10-26: 접수한 원문만이 아니라 생성한 원문 전체와 다르게 쓴다).
  - 한 작업(회차)은 한 건으로 센다. 대표 원문은 접수 직전까지 모든 검사를 통과한 원문이 있으면 그것, 없으면(다시 쓰기 소진 · 디렉터 호출 중 쉼) 그 회차에서 마지막으로 쓴 답이다.
  - 들어가는 것: 접수에 성공한 원문(그 뒤 작품이 완성됐든 `needs_attention`으로 멈췄든), 노드 문제로 접수가 거부된 원문, 3번 안에 통과하지 못한 회차의 마지막 답, 디렉터가 답을 낸 뒤 GMS 장애로 쉰 회차의 마지막 답.
  - 빠지는 것: 디렉터가 답을 하나도 내지 못한 작업(디렉터를 부르기 전에 쉼, 첫 호출부터 GMS 장애 등).
  - 10개보다 적으면 있는 만큼만 넣는다.
- **기록을 따로 저장하지 않는 이유**: 5.7의 기록이 이미 DB에 남아 서버를 재시작해도 이어진다. GMS의 대화 이어 붙이기 기능은 쓰지 않는다. Conversations API는 GMS에서 막히고(2026-10-23 확인, 대화를 만드는 요청에 모델 칸이 없어 GMS가 거부), 대화를 이어 붙이면 호출마다 쌓인 내용을 전부 다시 보내 비용이 커지기 때문이다.
- **지시문에 넣을 것**:
  1. 최근 기록 목록(최신순): 고른 템플릿 칸 값 · 분위기 · 원문.
  2. 코드가 센 요약: 템플릿 선택 칸 조합별로 최근 10개 중 몇 번 썼는지와 아직 안 쓴 조합. LLM이 10개를 읽고 스스로 세는 것보다 정확하다. 분위기 · 색 · 장면 소재는 자유 칸이라(5.3) 코드로 세지 않고 목록에서 LLM이 비교한다.
  3. 요청: "최근 작품과 다른 결로 정하라. 장면 소재(물, 빛, 숲, 바람, 도시, 천체, 종이, 금속 등)는 최근 3개와 다르게 고르고, 많이 쓴 조합은 피하고 안 쓴 조합을 우선한다."
- **답에 칸 하나 추가**: 최근 작품과 무엇을 다르게 했는지 한 문장(필드 이름은 구현자가 정한다). 스스로 설명하게 하면 실제로 다르게 고를 가능성이 높아지고, 기록을 볼 때 이유가 남는다. 코드 검사에는 쓰지 않는다.
- **지시문 예시의 치우침 없애기**: S15P31A504-212 지시문의 예시(사슴 발자취, 물고기 떼가 이루는 고래 모양)가 물·바다 쪽으로 끌었다. 이 경로의 지시문 예시는 여러 소재에 고르게 걸치게 쓰거나 넣지 않는다.
- **막지 않는다**: 최근과 겹쳐도 코드 검사에서 거부하지 않는다. 다시 쓰기와 쉬는 일이 늘지 않게 하기 위해서다. 대신 최근 3개 중 같은 템플릿 선택 칸 조합이 있었는지를 기록한다(5.7, 설계 기본값). 전시 중 겹침이 잦으면 그때 막는 규칙을 검토한다.

**LLM 연결**
- 디렉터 작업의 LLM은 **대체 LLM 없이 GMS만** 쓴다(설계 기본값). 분산 Head도 GMS에 의존하므로, GMS가 끊겼는데 EXAONE이 대신 답해 작품을 계속 넣으면 분산 Head에서 모두 멈춘다. GMS만 쓰면 AI 디렉터의 `LLMUnavailable`이 곧 "GMS에 닿지 못함"이 된다(5.6).
- 같은 LLM을 Input Agent에도 쓴다. 개발용 동기 경로의 대체 설정은 바꾸지 않는다.

**코드 검사 (LLM 없이)**
- 고른 조합이 5.3의 목록에 있는가.
- 원문이 비었거나 공백뿐이지 않은가.
- 원문이 `HeadRequest.prompt` 길이 제한(8000자) 안인가.

**작품 명세 만들기**
1. Input Agent(`LLMSpecMatcher.match(원문, 템플릿 계열)`)가 원문을 작품 명세로 바꾼다. 자유 칸(테마 · 배경 · 조명 · 재질 등)은 원문에서 채우고, 원문과 계열이 어긋나면 보류 사유(`holds`)를 낸다.
2. **AI 디렉터가 고른 칸의 값으로 명세를 덮어쓴다.** 그 칸의 출처 기록(`provenance`)은 "AI가 고름"(`head_selected`)과 디렉터 모델로 남긴다. S15P31A504-212에서 정한 "디렉터가 고른 값은 뒤 단계가 바꾸지 않는다"(`to_request`)와 같은 원칙이다.
3. 덮어쓴 값끼리 명세 규칙이 어긋날 수 있다. 예: 디렉터가 드러내는 방식 `field`(주인공 없이 장면 전체)를 골랐는데 Input Agent가 원문에서 주인공(`main_character`)을 뽑은 경우, 명세 규칙상 함께 둘 수 없다. 그래서:
   - 디렉터가 고른 드러내는 방식이 주인공을 두지 않는 방식이면, 주인공 칸을 비우는 것까지 덮어쓰기에 포함한다.
   - 덮어쓴 뒤 **명세를 처음부터 다시 검증**한다(`Spec.model_validate`로 다시 만들기). 칸에 값을 대입하는 것만으로는 검증이 다시 돌지 않는다.
4. 다시 검증한 명세로 `select_template`를 한 번 더 부른다. 실패하거나 보류 사유가 있으면 그 이유를 AI 디렉터에게 돌려주고 다시 쓰게 한다(같은 최대 3번 안에서).
5. 3번 안에 통과하지 못하면 그 작업은 "다시 쓰기 소진"으로 끝낸다. 프리셋으로 대신 넣지 않는다. 다음 Dispatcher 확인에서 새 디렉터 작업이 들어간다.

### 5.5 분산 Head에 접수

**요청 만들기** (`HeadRequest`)
- `request_id`: `exhibit-<디렉터 작업 ID>`. 자동 순환이 넣은 작품을 이 접두어로 구분한다(`HeadRequest`에 필드를 더 붙일 수 없기 때문).
- `artwork_id` · `version`: 구현자가 정한다(`packages/contracts/src/contracts/infra.py`의 `Identifier` 규칙을 따를 것).
- `prompt`: AI 디렉터의 원문. `spec`: 5.4의 작품 명세.
- `automation=true`, `auto_publish=true`, `profile="production"`, 나머지는 기본값. 사람 승인 없이 8시간 동안 돌아야 하기 때문이다.

**중복 접수 막기**
- Worker가 같은 디렉터 작업을 다시 실행할 수 있다(예: 처리 중 Worker가 죽어 작업이 다시 배정됨).
- 이때 AI 디렉터는 다른 원문을 쓰므로, 같은 `request_id`라도 내용이 달라 접수가 거부된다.
- 그래서 디렉터 작업은 **AI 디렉터를 부르기 전에** `exhibit-<자기 작업 ID>` 흐름이 이미 있는지 찾고, 있으면 그 흐름을 결과로 돌려주고 끝낸다.

**접수 방법**
- Worker가 `dispatch.submit(session, 요청, settings.head_connection(...), 노드 등록표)`를 직접 부른다. HTTP로 자기 서버를 부르지 않는다.
- `apps/central/src/central/head_api.py`의 `submit`처럼, DB 중복 오류(`IntegrityError`)가 나면 한 번 다시 부른다.

**접수 거부(`jobs.Conflict`) 처리**
- 명세 때문(사전 확인 실패): 5.3이 맞다면 일어나지 않는다. 일어나면 이유를 AI 디렉터에게 돌려준다.
- 노드 때문(검증·시연 노드 미등록, 맞는 제작 노드 없음): AI 디렉터 탓이 아니므로 쉬기(5.6)로 처리한다.

### 5.6 자리 세기 · 다시 채우기 · 쉬기

**자리 세기**
- 진행 중 자동 작품 = `request_id`가 `exhibit-`로 시작하고, 상태가 `completed` · `needs_attention`이 아닌 흐름. 여기에 대기·실행 중인 디렉터 작업을 더한다.
- **오래된 흐름은 세지 않는다**: 접수 후 `EXHIBITION_FEED_STALE_SECONDS`(임시 30분)가 지난 흐름은 상한 계산에서 뺀다. 노드가 죽어서 끝나지도 실패하지도 못하는 흐름이 8시간 내내 자리를 차지하지 않게 하려는 것이다. 흐름을 취소하지는 않으므로, 늦게라도 끝나면 그대로 화면에 올라간다. 기준(무엇을 기준으로 할지, 몇 초인지, 빼기만 할지 정리할지)은 [S15P31A504-218](https://ssafy.atlassian.net/browse/S15P31A504-218)에서 실제 소요 시간을 잰 뒤 정한다.
- `awaiting_publication_approval`(사람의 게시 승인 대기)은 `auto_publish=true`라 생기지 않아야 한다. 생기더라도 진행 중으로 세고, 위 규칙으로 빠진다.

**다시 채우기**
- 작품이 끝나든(`completed`) 멈추든(`needs_attention`) 다음 Dispatcher 확인(최대 2초 뒤)에서 자리가 생기면 바로 디렉터 작업을 넣는다.
- 실패가 많을 것으로 예상하므로, 품질 실패(예: 실행 검증 실패 `runtime_execution_failed`)로는 쉬지 않는다.

**쉬기 (새 작품 넣기만 잠시 멈춤)**
- 쉬는 동안에도 이미 접수된 작품은 계속 진행되고, 화면은 마지막 작품을 유지한다.
- `EXHIBITION_FEED_RETRY_SECONDS`(60초)가 지나면 저절로 다시 시도한다. 사람이 다시 켤 필요가 없다.
- 쉬는 조건:
  1. **AI 디렉터가 GMS에 닿지 못함**(`LLMUnavailable`). AI 디렉터는 GMS만 쓰므로(5.4) 곧 GMS 장애다. 다시 묻지 않고 바로 쉰다.
  2. **분산 Head가 GMS에 닿지 못함**. AI 디렉터와 분산 Head는 같은 모델(`gpt-6.1-sol`)을 쓰지만 호출 경로와 설정이 달라, 디렉터는 닿아도 분산 Head만 막혀 있을 수 있다. 자동 작품의 분산 Head 판단 작업 중 **가장 최근에 끝난 2개가 모두 `error == "LLMUnavailable"`로 실패**했으면 쉰다(설계 기본값).
     - "AI 계획이 규칙을 어김" 같은 품질 실패는 세지 않는다.
     - "최근"은 작업이 끝난 시각 순서다. 끝난 시각은 작업 시도 기록(`packages/db/src/db/jobs.py`의 `Attempt`)에서 읽는다. 정확한 칼럼은 구현자가 확인한다.
     - 쉬는 시간이 끝나면 그 뒤에 끝난 작업만 본다.
     - 1건은 일시적일 수 있어 2건으로 정했다.
  3. **필요한 노드가 꺼져 있음**. AI 디렉터를 부르기 전에 확인한다. 최근에 생존 신호(heartbeat)를 보낸 다음 노드가 모두 있어야 한다. "최근"의 기준은 사전 확인과 같은 값(`packages/studio/src/studio/head/ue_support.py`의 `MAX_HEARTBEAT_AGE`)을 쓴다.
     - 생성 노드(`role=generation`) 1대 이상
     - 5.3에서 템플릿을 보고한 제작 노드
     - 검증 노드(`ue-review-1`)
     - 시연 노드(`show_node`, 기본 `ue-show-1`)

     접수 단계는 검증·시연 노드의 등록만 보고 켜져 있는지는 보지 않으며, 생성 노드는 보지 않는다. 이 확인이 없으면 노드가 꺼진 동안 진행하지 못할 작품이 쌓인다.
  4. **접수가 노드 문제로 거부됨**(5.5).
- 쉬는 이유와 끝 시각은 상태 표에 남기고 `GET /infra/exhibition/feed`로 보인다.

### 5.7 기록

디렉터 작업의 결과 칸(`infra_job.result`)에 다음을 남긴다. S15P31A504-212가 개발용 동기 경로의 작품 기록에 남기는 "디렉터" 단계와 같은 내용이다.
- AI 디렉터 지시문 버전, 사용 모델, 지원 범위 요약(또는 같은 범위인지 비교할 수 있는 해시값)
- 시도마다 AI 디렉터의 답, 거부 이유, 사용 모델
- **접수 직전까지 모든 검사를 통과해 분산 Head에 보낸 원문**과 그때 고른 값(템플릿 선택 칸 값 · 분위기 · 색), "다르게 한 점" 한 문장, 최근 3회차와 템플릿 조합이 겹쳤는지. 한 작업에 최대 하나이며, 접수가 노드 문제로 거부돼도 남긴다. 검사에서 거부된 답은 이 칸이 아니라 시도 기록에만 남긴다. 5.4가 다음 디렉터 작업에서 이 칸을, 이 칸이 없으면 시도 기록의 마지막 답을 그 회차의 최근 기록으로 읽는다.
  - 다시 실행된 작업이 0번 단계에서 기존 흐름을 찾고 끝나면(5.5), 그 흐름의 요청(`payload`의 원문과 명세)에서 같은 값을 채워 기록이 빠지지 않게 한다.
- Input Agent 처리 결과(`sources`: `llm` 한 번에 맞음 / `llm-repaired` 다시 물어 맞음 / `rule` 규칙으로 대체)와 보류 사유
- 결과: 접수한 흐름 ID와 `request_id`, 또는 "다시 쓰기 소진", 또는 "쉼(이유)"

비밀값(`GMS_KEY` 등)과 예외 메시지 원문은 남기지 않는다. 지금 Worker처럼 예외는 종류 이름만 남긴다.

## 6. 먼저 고칠 문서

`AGENTS.md`의 "기능이 바뀌는 작업은 문서를 먼저 고친다" 규칙에 따라 코드보다 먼저 고친다.

| 문서 | 고칠 내용 |
|---|---|
| `apps/central/README.md` | Dispatcher · Worker가 하는 일에 자동 순환 추가, 새 API, 새 설정 3개, 개발 서버만으로는 돌지 않는다는 점 |
| `packages/studio/src/studio/head/README.md` | 분산 Head용 범위 계산과 디렉터 작업 |
| `infra/HEAD_AUTOMATION.md` | 자동 순환이 자동 제작 사이클 앞에서 요청을 넣는다는 문단과 쉬기 규칙 |
| `packages/db/README.md` | 새 표 `exhibition_feed` |
| `apps/operator/README.md` | 전시 모드 체크박스가 중앙 자동 순환을 켜고 끈다 |
| `README.md` (루트) | 환경 변수 표에 새 설정 3개 |

## 7. 확인할 것 (테스트)

LLM과 노드는 가짜로 대신한다. 준비 방식은 기존 `tests/test_head_dispatch.py` · `tests/test_head_automation.py`를 따르고, 테스트 규칙은 `tests/README.md`를 따른다.

**범위 계산**
- 계산된 모든 조합이 `select_template`를 통과하고, 범위 밖 값(예: 드러내는 방식 `trace`, 반응 `scatter`, 입력 `wave`)은 들어 있지 않다.
- 시험용으로 템플릿 표를 바꾸면 범위가 따라 바뀐다.
- 따로 둔 칸(통과 여부에 영향 없는 칸)의 값을 바꿔도 `select_template` 통과 여부가 같다.

**디렉터 작업**
- 디렉터 작업 한 번이 접수까지 가서 `exhibit-` 흐름을 만든다.
- 같은 작업을 다시 실행하면(가짜 디렉터가 두 번째에는 다른 원문을 냄) AI 디렉터를 부르지 않고 기존 흐름을 돌려준다.
- AI 디렉터가 범위 밖 값을 내면 이유를 붙여 다시 묻고, 3번 실패하면 접수하지 않는다.
- Input Agent가 다른 값을 내도 AI 디렉터가 고른 칸이 명세에 남고, 출처가 디렉터로 기록된다.
- AI 디렉터가 `field`를 고르고 가짜 Input Agent가 주인공을 뽑아도, 접수된 명세는 검증을 통과하고 주인공 칸이 비어 있다.
- `LLM_FALLBACK`이 설정되어 있어도 디렉터 작업은 GMS만 쓴다.

**최근 작품과 다른 결로**
- 디렉터 지시문에 최근 디렉터 기록이 최신순으로 최대 10회차 들어간다. 접수한 원문, 노드 문제로 접수가 거부된 원문, 다시 쓰기 소진 회차의 마지막 답이 모두 들어가고, 한 회차는 한 건이다. 디렉터를 부르지 않고 쉰 작업은 빠진다.
- 템플릿 조합별 사용 횟수와 안 쓴 조합 요약이 기록과 맞다.
- 최근 3개와 같은 조합을 골라도 거부하지 않고 접수되며, 겹침 표시가 결과에 남는다.
- 기록이 하나도 없을 때(첫 작품)도 디렉터 작업이 정상으로 돈다.

**Dispatcher**
- 켜져 있고 자리가 있으면 디렉터 작업을 하나 넣는다.
- 꺼짐 · 상한 도달 · 디렉터 작업 진행 중 · 쉬는 중이면 넣지 않는다.
- 작품이 `needs_attention`이 되면 다음 확인에서 다시 넣는다.
- 접수 후 `EXHIBITION_FEED_STALE_SECONDS`가 지난 흐름은 세지 않는다.

**쉬기**
- AI 디렉터 `LLMUnavailable` → 쉼. 쉬는 시간이 지나면 다시 넣는다.
- 가장 최근에 끝난 분산 Head 판단 2개가 `LLMUnavailable` → 쉼. 1개뿐이거나 다른 오류(예: `ValueError`)면 쉬지 않는다.
- 생성 · 제작 · 검증 · 시연 노드 중 하나라도 꺼져 있으면 AI 디렉터를 부르지 않고 쉰다.

**API와 운영 앱**
- 켜기 · 끄기 · 상태 조회가 되고, 운영자 토큰 없이는 거부된다.
- 운영 앱 체크박스가 API를 부른다(`tests/test_operator.py`).

## 8. 구현자에게 맡기는 것

- AI 디렉터 답의 필드 이름, 새 클래스 · 모듈 위치(`packages/studio/src/studio/head/exhibition_director.py` 확장 또는 새 파일)
- 범위를 AI 디렉터 지시문에 요약하는 방식
- 최근 기록 · 사용 횟수 요약을 지시문에 넣는 형식과 "다르게 한 점" 필드 이름
- 시험용 명세를 만들 때 필수 칸을 채우는 방법
- `artwork_id` · `version` 만드는 방식
- 새 표의 칼럼 이름과 한 줄만 유지하는 방법
- 운영 앱 화면 문구

## 9. 결정 기록

| 결정 | 값 | 정한 사람 · 날짜 |
|---|---|---|
| 접수되면 완성을 기다리지 않고 다음 요청 | — | 담당자 · 2026-10-22 |
| 실행 위치 | 중앙 서버(운영 앱과 무관) | 담당자 · 2026-10-22 |
| GMS 장애 시 | 새 작품 넣기만 멈추고 화면 유지, 프리셋 대체 없음 | 담당자 · 2026-10-22 |
| 진행 중 작품 상한 | 10 | 담당자 · 2026-10-23 |
| 디렉터 작업 동시 실행 | 한 번에 하나 | 담당자 · 2026-10-23 |
| 멈춘 작품 제외 기준 | 임시 30분 | S15P31A504-218에서 확정 |
| 쉬기 시간 | 60초 | 설계 기본값 |
| 분산 Head GMS 장애 판단 | 최근 끝난 판단 2개가 연결 실패 | 설계 기본값 |
| 디렉터 작업의 LLM | 대체 없이 GMS만 | 설계 기본값 |
| 범위를 켜진 제작 노드로 줄이기 | 예 | 설계 기본값 |
| 상태 저장 위치 | 새 DB 표 `exhibition_feed` | 설계 기본값 |
| 최근 작품과 다르게 | 최근 디렉터 기록 10개를 지시문에 넣어 피하게 함. 막지 않음 | 담당자 · 2026-10-23 |
| 최근 기록 위치 | 디렉터 작업 결과 칸(`infra_job.result`). 새 표나 GMS 대화 기능을 쓰지 않음 | 설계 기본값 |
| 최근 기록에 넣는 원문 | AI 디렉터가 생성한 원문 전체(검사 · 제작 성공 여부와 무관). 한 회차는 한 건, 대표 원문은 접수한 원문 또는 마지막 답 | 담당자 · 2026-10-26 (2026-10-23 결정 변경) |
| 겹침 표시 기준 | 최근 3개 중 같은 템플릿 선택 칸 조합 | 설계 기본값 |
| AI 디렉터 모델 | `gpt-6.1-sol` (`DIRECTOR_MODEL`) | 담당자 · 2026-10-23 (S15P31A504-212) |

## 10. 구현 기록

8절에서 구현자에게 맡긴 것과, 설계에 없어서 구현하면서 정한 것입니다(S15P31A504-27).

**코드 위치**

| 하는 일 | 위치 |
|---|---|
| 범위 계산 · AI 디렉터 · 명세 덮어쓰기 · 디렉터 작업 · Dispatcher 확인 · 상태 조회 | `packages/studio/src/studio/head/exhibition_feed.py` |
| 상태 표 `exhibition_feed` | `packages/db/src/db/exhibition_feed.py` |
| 켜기 · 끄기 API | `apps/central/src/central/exhibition_feed_api.py`, 요청 모델 `ExhibitionFeedToggle`(`packages/contracts/src/contracts/head.py`) |
| Dispatcher · Worker 연결 | `apps/central/src/central/worker.py`의 `dispatch_once` · `execute` |
| 운영 앱 체크박스 | `apps/operator/src/operator_app/window.py`, `client.py` |

**범위 계산 (5.3)**
- 템플릿 계열은 장면 계열(`scene_family`)과 흐름 계열(`flow_family`)을 모두 씁니다(담당자 결정, 2026-10-26). 범위는 계열마다 따로 계산합니다. 흐름 계열은 드러내는 방식 · 주인공 · 화면 깊이가 막힌 칸이라 디렉터가 드러내는 방식을 `null`로 답하고, 명세에서도 그 칸들을 비워 둡니다.
- `select_template`를 읽어 보면 반응 · 매질이 템플릿을 정하고, 드러내는 방식 · 입력 · 반응 방식 · 평상시 움직임은 그와 상관없이 통과 여부만 가릅니다. 그래서 **계열 × 반응 × 매질은 조합으로**, 나머지 칸은 **계열별 · 칸별 허용값 목록으로** 계산합니다. 복귀 방식 · 화면 깊이는 통과 여부에 영향이 없어 디렉터가 고르지 않고 Input Agent 값을 그대로 씁니다. 이 분리가 맞는지(허용값을 어떻게 섞어도 통과, 영향 없는 칸을 바꿔도 결과가 같음)는 테스트로 확인합니다.
- 디렉터에게는 비운 값(`null`) 대신 실제 값만 줍니다(예: 평상시 움직임은 비움 대신 `ignore`).
- `select_template`에 이미 읽은 템플릿 표를 넘기는 선택 인자 `table`을 추가했습니다. 기존 호출은 그대로입니다.

**AI 디렉터의 답 (5.4)** — `FeedChoice`
- `prompt`(원문) · `family`(템플릿 계열) · `reaction` · `medium` · `reveal`(흐름 계열은 `null`) · `input` · `reaction_mode` · `idle_motion` · `mood` · `palette`(역할 + `#RRGGBB` 목록) · `difference`(최근 작품과 다르게 한 점 한 문장).
- 지시문에는 계열별 · 칸별 허용값과 "계열 · 반응마다 쓸 수 있는 매질" 요약, 최근 디렉터 기록(최신순 최대 10개), 조합(계열 · 반응 · 매질)별 사용 횟수 · 안 쓴 조합을 넣습니다. 지시문 버전은 `exhibition-feed-1`입니다.
- Input Agent는 디렉터가 고른 계열로 원문을 해석합니다(`match(원문, family)`).
- 명세 덮어쓰기 대상: 매질 · 드러내는 방식(장면 계열만) · 분위기 · 색 · 평상시 움직임 · 첫 규칙의 입력 · 반응 · 반응 방식. 규칙은 첫 규칙 하나만 남깁니다(사전 확인이 규칙 1개만 받음). 드러내는 방식이 `field`면 주인공 칸도 비웁니다.
- 예전 기록에 계열이 없으면 장면 계열로 봅니다.
- Input Agent는 디렉터와 같은 GMS 전용 연결을 쓰고, GMS에 닿지 못하면 규칙으로 대체하지 않고 쉽니다(디렉터가 닿지 못한 경우와 같음). 운영 앱 원문 접수(`intake.py`)와 같은 `fail_on_unavailable` 동작입니다.

**접수 (5.5)**
- `request_id` · `artwork_id`는 모두 `exhibit-<디렉터 작업 ID>`, `version`은 `v1`입니다.

**쉬기 · 기록 (5.6 · 5.7)**
- 쉬는 이유 코드: `gms_unavailable`(디렉터 · Input Agent가 GMS에 닿지 못함), `head_unavailable`(분산 Head 판단 최근 2개가 `LLMUnavailable`), `nodes_offline`(필요한 노드가 꺼짐 또는 템플릿을 보고한 켜진 제작 노드 없음), `submit_rejected`(접수가 노드 문제로 거부됨), `feed_error`(디렉터 작업이 예상 못 한 오류로 실패).
- `feed_error`는 설계에 없던 규칙입니다. 디렉터 작업이 코드 오류 · 설정 누락(예: `GMS_KEY` 없음)으로 실패하면 2초마다 새 작업이 실패를 반복하므로, 그 실패 뒤 한 번 쉽니다.
- 다시 쓰기 소진은 디렉터 작업을 `failed`(`error = "director_exhausted"`)로 끝냅니다. 운영 현황에서 실패로 보이게 하려는 것입니다. 이 실패로는 쉬지 않습니다.
- 디렉터 작업 결과 칸의 `outcome`: `submitted`(접수함) · `exhausted`(다시 쓰기 소진) · `paused`(쉼). 모든 검사를 통과해 보낸 원문은 `submitted` 칸에 남습니다.

**상태 표 · API · 운영 앱**
- `exhibition_feed`는 `id = 1` 한 줄만 씁니다. 칼럼: `enabled` · `paused_until` · `pause_reason` · `last_job` · `updated_at`. 켜기(`PUT … {"enabled": true}`)는 쉬는 상태도 지웁니다.
- 운영 앱은 앱을 켤 때와 기록을 새로 고칠 때 상태를 읽어 체크박스와 "켜짐 · 진행 중 n/10" · "쉬는 중(이유)" 문구로 보여 줍니다. 앱 안의 반복 요청과 "새 작품 따라보기"는 없앴습니다. 자동 순환 작품은 운영 앱의 원문 접수 기록(`head_intake`)이 아니므로 작품 기록 목록에 나오지 않고, `GET /infra/exhibition/feed`의 최근 디렉터 작업과 Head 흐름 조회(`GET /infra/head/workflows/{id}`)로 확인합니다.
- 전시 모드를 켜도 「소재 검수」 · 「최종 검수」 체크박스를 끄지 않습니다. 이 체크박스는 운영자가 직접 제출하는 요청에만 쓰이고, 자동 순환 작품은 늘 자동 게시이기 때문입니다.
