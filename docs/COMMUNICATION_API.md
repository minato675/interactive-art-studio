# 서버·노드 통신 API 규약

기준일 2026-10-21, 대조 코드 `develop bfd9b07`. **현재 구현된 규약**을 기록하며 새로운 API를 구현하는 문서는 아니다.
서버 배치·단계별 연결·EC2 이전은 [통신 파이프라인](COMMUNICATION_PIPELINE.md)을 따른다.

정의의 기준은 [infra_api.py](../apps/central/src/central/infra_api.py),
[contracts/infra.py](../packages/contracts/src/contracts/infra.py),
[db/jobs.py](../packages/db/src/db/jobs.py)다. 아래 JSON의 이름·토큰·해시·ID는 가상 예시다.

## 1. 공통 전송·인증

| 항목 | 현재 규약 |
|---|---|
| 주소 | 중앙 HTTPS 기본 주소에 아래 경로를 붙임. 경로에 /api를 추가하지 않음: 예를 들어 `/infra/jobs` |
| 본문 | `Content-Type: application/json`, UTF-8 JSON. 파일 본문·base64·장비 절대 경로를 싣지 않음 |
| 운영자 인증 | `X-Operator-Token: <운영자 토큰>`. /infra 운영 API는 토큰 미설정 상태도 401 |
| 노드 인증 | `X-Agent-Token: <노드별 토큰>`. 중앙 AGENT_KEYS_JSON에서 node_id·role·catalog_version을 확인 |
| 토큰과 ID | 노드 ID/역할은 요청 본문에서 선택하지 않음. 인증된 등록값 사용. Agent 토큰은 노드마다 다르고 16자 이상 |
| 임대 토큰 | 작업 응답의 `token`. 노드 인증 토큰과 별개이며 heartbeat/complete 본문에 보냄. 로그·공개 assignment·프롬프트에 넣지 않음 |
| 연결 방식 | Agent가 먼저 요청하는 폴링 방식. WebSocket·SSE·중앙의 현장 inbound 호출은 미구현 |
| 내부 통신 | DB는 SQL, 브로커는 Celery/AMQP. 별도 HTTP 엔드포인트를 만들지 않았으며 Windows 노드는 두 서비스에 직접 접속하지 않음 |

원격 Agent는 평문 HTTP를 거부한다. localhost HTTP는 SSH 터널/로컬 시험에 사용할 수 있다.
생성 키트는 `server.json`의 CA 파일을 신뢰하며 리다이렉트·시스템 프록시로 토큰을 전달하지 않는다.
현재 인증은 정적 토큰이며 OAuth·토큰 회전 UI·역할별 운영자 권한 세분화는 미구현이다.

FastAPI의 `/docs`·`/openapi.json`은 localhost 또는 SSH 터널에서 확인한다.
현재 Caddy는 /health·/ready·/api/*·/infra/*만 전달해 외부 HTTPS로는 API 문서를 제공하지 않는다.
요청 모델은 OpenAPI에 있으나 여러 응답은 `dict`로 직접 만들고 있어, 모든 응답이 명시적 response_model로 고정된 상태는 아니다.

## 2. 운영자·상태 확인 API

| Method · 경로 | 인증 | 입력 | 성공 응답 |
|---|---|---|---|
| GET /health | 없음 | 없음 | 200 `{ok:true}`. API 프로세스 응답 |
| GET /ready | 없음 | 없음 | 200 `{ok:true, database:"postgresql"}`. 실제 DB SELECT 확인. DB 장애는 503 |
| POST /infra/jobs | 운영자 | JobRequest | **202**, PublicJob. 같은 요청의 재전송도 202 |
| GET /infra/jobs | 운영자 | query `limit=50`(1~200), `offset=0`(0 이상) | 200, 최신 생성 순 PublicJob 배열 |
| GET /infra/jobs/{job_id} | 운영자 | path 작업 ID | 200, PublicJob + attempts 배열 + dependencies 배열. 없으면 404 |
| POST /infra/jobs/{job_id}/retry | 운영자 | 본문 없음 | 200, queued로 돌린 PublicJob. 실패 상태·남은 시도 한도 확인, 불가하면 409 |
| GET /infra/nodes | 운영자 | 없음 | 200, Node 배열 + online. 유효한 임대가 있을 때만 active_job 표시 |

`/ready`는 RabbitMQ·Worker·현장 노드의 준비를 확인하지 않는다.
실제 처리 가능 여부는 진단 작업이 완료되는지까지 확인해야 한다.

### JobRequest

| 필드 | 형식·규칙 |
|---|---|
| request_id | 필수 Identifier. 생성 재전송을 식별하는 호출자 지정 키 |
| kind | `diagnostic / draft / background_generate / assets_generate / bundle_verify` |
| draft | draft일 때만 CreateRequest. 형식은 [schemas.py](../packages/contracts/src/contracts/schemas.py) |
| generation | 생성 종류일 때만 GenerationSpec |
| bundle | bundle_verify일 때만 Bundle |
| depends_on | 기본 []. 중복 없는 작업 ID 최대 16개. 현재 생성 작업만 지원 |

Identifier는 `^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$`다. JobRequest JSON은 64 KiB 이하이며
정의되지 않은 추가 필드를 거부한다. 에셋에는 선행 배경 작업이 반드시 있어야 한다.
모든 선행 작업은 같은 작품·버전·카탈로그의 생성 작업이며 DB에 존재해야 한다.

| kind | DB 작업함 | 실행 주체 |
|---|---|---|
| diagnostic, draft | planning | Dispatcher → RabbitMQ planning(기본) → Linux Celery Worker |
| background_generate | background | generation 역할 Windows 클라이언트의 HTTPS claim |
| assets_generate | assets | generation 역할. 모든 선행 작업 성공 후 claim |
| bundle_verify | assembly | production 역할 Node Agent. UE 조립 실행과는 별개인 파일 검사 |

### 생성 요청 예시

배경 등록:

```json
{
  "request_id": "demo-background-A-v1",
  "kind": "background_generate",
  "generation": {
    "artwork_id": "artwork-A",
    "version": "v1",
    "catalog_version": "v1",
    "executor": "manual_codex",
    "prompt": "작품 명세에 맞는 배경을 생성합니다."
  }
}
```

GenerationSpec의 artwork_id·version·prompt는 필수다. catalog_version 기본은 v1,
executor는 현재 manual_codex만 허용한다. prompt 길이는 1~16,000자다.

배경 응답의 `id`를 다음 depends_on에 넣어 에셋을 등록한다:

```json
{
  "request_id": "demo-assets-A-v1",
  "kind": "assets_generate",
  "depends_on": ["background_job_id"],
  "generation": {
    "artwork_id": "artwork-A",
    "version": "v1",
    "catalog_version": "v1",
    "executor": "manual_codex",
    "prompt": "선행 배경을 참고하여 에셋을 생성합니다."
  }
}
```

`background_job_id`는 실제 배경 작업 ID로 치환한다. background와 assets를 등록하는 것까지는 운영자 책임이며,
초안 완료가 이 요청들을 자동 생성하지 않는다.

### PublicJob·조회 응답

| 필드 | 의미 |
|---|---|
| id, request_id, kind, queue | 중앙 작업 ID, 호출자 중복 키, 작업 종류, DB 작업함 |
| status | `queued / running / succeeded / failed`. 의존 작업 대기도 queued이며 별도 blocked 상태는 없음 |
| artwork_id, artwork_version, catalog_version | 생성/번들 소속. diagnostic 등은 작품 값이 null일 수 있음 |
| attempt, max_attempts, node_id | 현재/최근 시도 번호·최대 횟수·실행 주체. attempt_id는 일반 PublicJob이 아닌 상세 attempts/임대 응답에서 확인 |
| progress, error, result | 진행률·오류·결과 메타데이터. heartbeat 진행률은 최대 99, 종료 시 100 |
| created_at, dispatch_after, lease_until | Unix epoch 초. lease_until=0이면 유효 임대 없음 |
| depends_on | 선행 작업 ID 목록 |
| attempts(상세 조회만) | `id, job_id, number, node_id, started_at, ended_at, status`. 시도 status에는 expired도 있음 |
| dependencies(상세 조회만) | `[{id, status}]`. 선행 실패·대기 원인 확인용 |

PublicJob에는 payload·임대 token·fingerprint가 없다. result의 `_`로 시작하는 내부 재전송 검증값도 제거한다.
생성 결과에는 아래 GenerationResult와 `verification:"node_report_only", publish_approved:false`가 들어간다.
Node 조회의 `online`은 마지막 heartbeat로부터 90초 미만이라는 현재 기준이며 장비/UE 정상 동작 보증은 아니다.

## 3. Agent API와 실행 순서

모든 경로는 노드 인증 헤더를 사용한다. 각 노드의 임대 슬롯은 하나다.

| Method · 경로 | 입력 | 성공 응답·제약 |
|---|---|---|
| GET /infra/agent/identity | 없음 | 200, 등록된 node_id·role·catalog_version. 토큰 제외. heartbeat/노드 등록을 변경하지 않음 |
| POST /infra/agent/heartbeat | 생성/제작은 본문 생략, 시연은 ShowReport 선택 | 200 `{node_id, role}`. 최초 노드 등록 또는 상태 갱신 |
| POST /infra/agent/claim | 본문 없음 | 200 Assignment 또는 JSON `null`. 먼저 heartbeat 등록 필요. show 역할은 작업 임대 불가(409) |
| GET /infra/agent/current | 없음 | 200 현재 Assignment 또는 null. **generation 역할 전용**, 다른 역할은 403 |
| POST /infra/agent/jobs/{job_id}/heartbeat | LeaseUpdate | 200 `{ok:true}`. 같은 노드·토큰·유효 임대 확인 |
| POST /infra/agent/jobs/{job_id}/complete | Completion | 생성은 200 `{ok:true, verification:"node_report_only", publish_approved:false}`. bundle_verify는 `{ok:true}` |

노드 최초 등록 뒤 역할·카탈로그가 중앙 등록과 다르면 409다. 카탈로그가 다른 작업은 claim하지 않는다.
작업이 없으면 204나 오류가 아니라 **200 + null**이다.

권장 순서:

1. identity로 주소·토큰·역할 확인 → node heartbeat.
2. 생성 노드 재접속 시 current로 유효한 기존 임대를 조회 → 없을 때 claim.
3. Assignment를 비밀 영역에 저장 → 입력 파일 준비·작업 수행 중 작업/노드 heartbeat.
4. 파일 저장 후 complete. 실패는 error를 채워 보고. 유효 임대가 사라졌다면 이전 결과를 강제로 완료하지 않음.

### Assignment

PublicJob 필드에 다음을 추가한다:

| 필드 | 의미 |
|---|---|
| payload | 정규화한 JobRequest 전체. generation/bundle 등 작업에 필요한 내용 |
| token | **해당 실행 시도의 비밀 임대 토큰** |
| attempt_id | infra_attempt의 ID. 본문 heartbeat/complete에는 이 필드를 보내지 않으며 서버가 token으로 소유권 확인 |
| heartbeat_seconds | 권장 갱신 간격. 현재 `max(1, LEASE_SECONDS // 3)` |
| inputs | depends_on 각각의 PublicJob 배열. 성공한 선행 결과의 파일 참조를 가져오는 데 사용 |

current도 유효한 본인 임대가 있으면 같은 형태로 토큰을 반환하므로 안전한 로컬 파일에만 보관한다.
production Agent에는 current를 이용한 복구 경로가 없으며 전체 노드에 같은 복구 기능이 있다고 가정하지 않는다.

### 임대 갱신

```json
{
  "token": "example-lease-token-not-a-real-key",
  "progress": 50
}
```

LeaseUpdate의 token은 필수 16~128자, progress는 기본 0·0~100, error는 기본 빈 문자열·최대 500자다.
현재 heartbeat는 error로 작업을 실패시키지 않으며 progress를 단조 증가시키되 99까지만 저장한다.
실패 종료는 complete를 사용한다. LEASE_SECONDS 기본 120초는 초기 실행 설정이며 보장된 처리 시간이 아니다.

### 생성 완료와 실패

파일을 먼저 저장한 뒤 실제 해시와 크기로 성공을 보고한다:

```json
{
  "token": "example-lease-token-not-a-real-key",
  "result": {
    "artwork_id": "artwork-A",
    "version": "v1",
    "files": [
      {
        "name": "background.png",
        "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        "size": 12345
      }
    ]
  }
}
```

GenerationResult는 artwork_id·version·files가 필수이며 요청의 작품/버전과 일치해야 한다.
성공 시 result가 필수, 실패 시 result를 넣지 않는다. 서버가 받은 result 메타데이터는 64 KiB 이하여야 한다.
중앙은 현재 현장 파일 실물을 읽어 검수하지 않으며 완료 응답의 publish_approved는 false다.

```json
{
  "token": "example-lease-token-not-a-real-key",
  "error": "generation_failed"
}
```

bundle_verify는 성공 시 token만, 실패 시 token과 error를 보고한다. 반환 result는 서버가
`artwork_id, version, check:"file_integrity_only", publish_approved:false`로 만든다.
이 작업에서 클라이언트 result를 게시 승인으로 사용하지 않는다.

## 4. 파일·번들 규약

HTTP는 아래 파일 참조만 전달한다. 실제 읽기/쓰기는 현장 폴더/SMB에서 한다.

| 계약 | 필드·제약 |
|---|---|
| FileRef | name(1~200자 상대 POSIX 경로), sha256(소문자 16진수 64자), size(0 이상 바이트) |
| GenerationResult | artwork_id, version, files(1~256개). 중간 생성 결과이며 Runtime 번들과 구분 |
| Bundle | schema_version=1, artwork_id, version, catalog_version 필수. kind 기본 runtime_data, files 1~256개 |
| Bundle.kind | runtime_data 또는 pre_cooked_ue. 현재 Show Agent는 runtime_data만 허용하고 scene.json을 요구 |

절대 경로·`..`·역슬래시·Windows 예약 파일명·대소문자 중복·파일/폴더 충돌을 거부한다.
추가 필드도 허용하지 않는다. 저장 위치는 `objects/<sha256 앞 2자리>/<sha256>`이며
공유 호스트는 생성 PC이며, 생성 PC는 로컬 경로로, UE 제작·시연 PC는 SMB 경로로 같은 저장소를 가리킨다.
초기 배치와 경로 예시는 [저장소 배치](../infra/STORAGE.md)를 따른다.
파일 복사는 SHA-256·크기 확인 후 완성된 파일/버전만 노출한다.

버전 폴더명과 UE에 넘기는 release 버전은 단순 v1이 아니라
[release_version(Bundle)](../packages/studio/src/studio/infra/storage.py)로 계산한다.
같은 artwork_id/version의 다른 내용으로 이미 준비된 버전 폴더를 덮어쓰지 않는다.
생성 파일 목록을 Bundle로 조립하고 검수·게시하는 후속 단계는 아직 자동 연결되지 않았다.

## 5. 시연 상태 규약

show 역할은 `POST /infra/agent/heartbeat` 본문에 ShowReport를 보낸다.
다른 역할이 전시 상태를 보고하면 403이다.

```json
{
  "phase": "Applied",
  "desired_version": "example-release-version",
  "active_version": "example-release-version",
  "runtime_session": "example-runtime-session",
  "runtime_pid": 12345,
  "error": ""
}
```

| 필드 | 형식·의미 |
|---|---|
| phase | 필수. Idle, Staged, Ready, Applied, Failed, Unknown |
| desired_version | 목표 release 버전, null 허용·최대 96자 |
| active_version | 관측한 실제 활성 release 버전, null 허용·최대 96자 |
| runtime_session | Runtime 세션 ID, null 허용·최대 128자 |
| runtime_pid | 프로세스 ID, null 또는 양의 정수 |
| error | 기본 빈 문자열, 최대 500자 |

현재 중앙은 형식·역할을 확인해 저장한다. 실제 PID·세션·버전 검사는 현장 Show Agent가 수행한다.
보고 수락 자체가 서버의 독립적인 UE 화면 검증은 아니다.

로컬 파일 계약:

| 파일 | 교환 내용 |
|---|---|
| Show Agent → UE: release.json | `{"SchemaVersion":1,"Version":"<계산한 release 버전>"}`. 명시적 활성화 시 원자 교체 |
| UE → Agent: release.json.status.json | RuntimeSessionId, ProcessId, UpdatedAtUtc, RequestedVersion, ActiveReleaseVersion, Phase, Applied를 관측 |

Agent는 살아 있는 PID·프로세스 시작 이후의 상태·비어 있지 않은 세션을 확인한다.
요청 버전이 다르거나 Applied와 실제 활성 버전이 일치하지 않으면 Unknown으로 본다.
다음 파일만 준비한 경우는 Staged이며, 활성화 실패나 통신 장애 때 현재 작품을 새 버전으로 오인하지 않는다.

**중앙 게시/준비 명령, Ready 후 별도 commit, 명령 ACK API는 아직 없다.**
지금은 CLI의 bundle/activate 옵션과 UE watcher의 자동 commit을 사용한다. 이 문서에서 미구현 URL을 규약으로 확정하지 않는다.
센서 입력은 [TD protocol v2](../td/README.md)에 정의된 별도의 현장 UDP 계약이다.

## 6. 오류와 재전송

| HTTP | 의미·대표 상황 | 클라이언트 처리 |
|---|---|---|
| 401 | 운영자/노드 토큰 누락·불일치 | 주소·노드별 비밀 설정 확인. 다른 역할의 토큰을 대신 사용하지 않음 |
| 403 | 생성 전용 current를 다른 역할로 호출, 다른 역할의 전시/생성 결과 보고 등 | 역할·호출 경로 수정 |
| 404 | 작업 상세 조회 대상 없음 | ID 확인. 소유권 관련 API의 없는 작업은 409일 수 있음 |
| 409 | request_id 내용 충돌, 임대 만료/소유권 불일치, 노드 중복 작업, 잘못된 의존성, 재시도 한도 | 현재 상태 조회 후 구분 처리. 동일 요청을 무조건 반복하지 않음 |
| 422 | JSON/필드/제약 위반, 성공 결과 누락, 결과 작품/버전 불일치, 큰 메타데이터 | 요청 수정. FastAPI 검증 detail은 배열, 명시적 검사 detail은 문자열일 수 있음 |
| 503 | /ready의 DB 연결 장애 | 재접속 후 준비·작업 상태 확인. 실제 Worker 처리는 진단 작업으로 별도 확인 |

업무 오류는 보통 `{"detail":"설명"}`이며 검증 오류는 `{"detail":[...]}`다.
네트워크 timeout·서버 오류는 완료 여부가 불명확할 수 있어 상태 확인 뒤 재전송한다.

동기 제작 /api 경로의 최신 정책은 [제작 정책](refactoring/creation-policy.md)에 있다.
현재 기본 제작을 ENABLE_LOCAL_PREVIEW로 차단하는 규칙은 없다. 신규 후보의 `prepared`와
과거 `published`는 실제 런타임 `Applied`와 구분한다.

- **생성 등록**: 같은 request_id와 같은 요청을 다시 보내 기존 작업을 받는다. 새 ID를 만들면 별도 작업이다.
- **claim 응답 유실**: 생성 노드는 current로 기존 임대를 복구한다. current=null이면 유효 작업이 없는 것이므로 이후 claim한다.
- **완료 응답 유실**: 생성 작업만 같은 노드·시도 토큰·같은 result/error의 재전송을 허용한다.
  저장해 둔 완료 본문을 그대로 재전송한다. 이미 재시도됐거나 내용이 바뀌면 409다.
- **bundle_verify 완료**: 일반 finish 경로라 성공 후 동일 완료 재전송은 409다. 생성 작업의 멱등 보장과 구분한다.
- **명시적 실패**: failed로 남고 운영자가 retry한다. 임대 만료는 시도 한도 내 queued로 돌아가며 새 토큰이 발급된다.
  선행 작업 실패를 후속 작업 실패로 자동 전파하거나 원인별 재생성/재조립을 선택하는 기능은 후속이다.

노드당 한 슬롯은 동시에 결과를 반영할 권한을 제한한다. 연결이 끊긴 외부 생성 프로세스 자체를 강제로 취소하거나
외부 API 중복 과금을 방지하는 보장은 없으며, 공급자 연결 시 별도 멱등 키·취소·결과 재사용을 설계해야 한다.

## 7. 변경·확장할 때 확인할 것

- 현재 /infra 경로에는 API 버전 접두사가 없다. 변경 시 중앙·생성 키트·제작/시연 Agent·계약·테스트를 함께 대조한다.
- 향후 UE 조립·검수·게시 작업 종류와 명령 API는 별도 계약·실행부·통합 검증을 갖춘 뒤 추가한다.
- generation/production/show 역할, 전체 작품 명세와 현재 CreateRequest, 생성 중간 결과와 Runtime Bundle을 섞지 않는다.
- EC2 이전은 기본 주소·TLS 신뢰·노드 등록 설정을 바꾸는 작업이며 동일 API를 유지한다.
  OAuth·WebSocket·브로커 외부 공개를 이전의 필수 조건으로 임의 추가하지 않는다.
