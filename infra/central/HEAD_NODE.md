# API 기반 Head 노드

Head는 중앙 Worker에서 API로 판단하고, 검증된 JSON을 기존 PostgreSQL 작업함에 등록합니다.
Codex CLI 로그인이나 ChatGPT 사용 크레딧을 사용하지 않습니다. 현재 운영 공급자는 GMS·`gpt-6.1-sol`입니다.
2026-10-22에 GMS의 실제 구조화 응답을 확인하고 운영 서버의 직접 OpenAI 테스트 키를 제거했습니다.
기존 코드의 `profile: test`는 과거 직접 OpenAI·`gpt-6-luna` 경로이며 현재 운영에서는 키가 없어 연결할 수 없습니다.
기존 `LLM_PROVIDER=none` 설정과 독립적입니다.

## 사람이 보는 처리 순서

신규 자동 흐름은 [Head 자동 제작 사이클](../HEAD_AUTOMATION.md)과 [시나리오 감독](../HEAD_SCENARIO_DIRECTOR.md)을 따른다. UE 템플릿 사전 확인 → 배경 지시·생성 → 중앙에 보존한 실제 배경 이미지 분석 → 원본 에셋·복제 배치·효과 계획 → 생성 → UE 조립·실행 검증 → AI 검수로 이어진다. 게시 여부는 요청의 auto_publish와 기존 기술 검증 조건을 따른다.

이전 수동 흐름(automation=false)은 배경 성공 후 background-input으로 등록된 원본을 전송하고 에셋 생성을 진행한다. generation_complete는 이 수동 흐름의 생성 완료이며 전시 납품 완료가 아니다. 자동 흐름은 중앙 업로드 원본을 사용하므로 별도 background-input 호출이 필요 없다.

Dispatcher는 DB 상태만 주기적으로 확인하고 판단 단계에서만 AI를 호출한다. MD 파일 수정이나 디렉터리 감시가 제작 트리거가 아니다.

## 설정

Head API 실패는 작업 조회의 `result.error_detail`에서 확인합니다. HTTP 상태, 공급자 오류 코드·유형,
요청 ID와 재시도 안내(공급자가 제공했을 때), 요청 바이트·이미지 수·소요 시간을 보존합니다.
시도별 진단은 데이터 볼륨의 `head-errors/<작업 ID>/attempt-<번호>.json`에도 남겨 재시도와 구분합니다.
공급자의 원문 메시지·키·URL·프롬프트·이미지는 진단 로그에 넣지 않습니다.
429만으로 크레딧 소진을 단정하지 않으며, 명시적인 부족 응답이 있을 때만 quota로 분류합니다.
시각 검수는 [검수 전송·원본 보존 규칙](../../packages/studio/src/studio/head/README.md)을 따릅니다.

저장소 루트의 무시되는 `.env.head`를 사용합니다. Compose의 API·Worker·Dispatcher가 읽습니다.
`HEAD_PROVIDER=gms`, `HEAD_MODEL=gpt-6.1-sol`, `GMS_KEY=...`가 운영 설정입니다.
`HEAD_URL=https://gms.ssafy.io/gmsapi/api.openai.com/v1/responses`를 사용합니다.
`HEAD_TIMEOUT_SECONDS=600`이 기본 응답 대기 한도입니다. `.env.head`에서 양의 정수(초)로 변경할 수 있으며
배경 기획·에셋 기획·AI 검수와 production/test 프로필에 공통 적용합니다. 실행 중인 요청에는 소급 적용하지 않습니다.
이는 클라이언트의 네트워크 대기 설정으로, GMS의 호출량 제한이나 게이트웨이의 제한 시간을 늘리지는 않습니다.
설정을 바꾼 뒤 API·Worker·Dispatcher 컨테이너를 다시 생성해야 합니다. 기존 동기 Input Agent와 이미지 생성기의 시간 한도는 별도입니다.
인증 헤더는 `Authorization: Bearer <GMS_KEY>`이며 요청 모델은 `gpt-6.1-sol`입니다.
제공받은 Responses 사용 예제와 같은 엔드포인트·인증 방식이며, `input`·구조화 JSON 응답의 실제 호출을 확인했습니다.
GMS를 쓰는 요청은 `profile: production`입니다. 이 이름은 EC2의 운영/테스트 배포 환경 이름과 독립적입니다.
운영 서버의 `.env.head`와 API·Worker·Dispatcher에서 `OPENAI_API_KEY`를 제거했으며 OpenAI로 자동 대체하지 않습니다.
테스트 환경에 유료 키를 자동 복사하지 않습니다. `.env.head`나 Docker 설정 전체를 출력하거나 커밋하지 마세요.

키를 변경할 때는 실제 연결을 확인한 뒤 실행 중 작업이 없는지 검사하고, 현재 이미지와 Compose 설정을 보존해
API·Worker·Dispatcher에 새 환경을 반영합니다. `docker restart`만으로는 변경한 환경 파일이 다시 로드되지 않습니다.
DB·브로커·gateway·데이터 볼륨은 그대로 둡니다. 현재 운영 파일은 `/opt/ranel/.env.head`(600 권한)입니다.
이전 키를 환경 파일에서 제거해도 발급처에서 키 자체가 폐기되지는 않습니다.
OpenAI 발급처의 폐기는 별도 키 삭제 작업이며, [공식 프로젝트 키 삭제 규약](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/projects/subresources/api_keys/methods/delete)을 참고합니다.

## 개발자용 API

모든 요청은 `X-Operator-Token` 인증이 필요합니다.
아래 항목은 develop의 기본 계약입니다. 현재 운영 이미지 c18d2591의 Head 요청에는 추가로 `spec`이 필수이며,
해당 필드가 빠진 요청은 422로 거부됩니다. 실제 호출은 적용 이미지의 계약을 확인하며, 이 오류를 GMS 키 오류로 해석하지 않습니다.

- `POST /infra/head/workflows`: `request_id`, `artwork_id`, `version`, `prompt`, `asset_count`, `spec`(필수),
  `catalog_version`(기본 v1), `profile`(production/test, 기본 production). 202로 흐름 ID를 반환합니다.
  같은 request_id와 동일 입력은 기존 흐름을 반환하고, 다른 입력은 409입니다.
- `GET /infra/head/workflows/{id}`: 흐름 상태·판단 기록·자식 작업 상태·오류를 조회합니다.
- `GET /infra/head/templates`: 중앙 지원 목록과 제작 노드 보고·온라인 상태를 조회합니다.
- `POST /infra/head/preflight`: 같은 요청으로 지원 여부와 생성 조건만 조회합니다. AI 호출·작업 등록이 없습니다.
- `POST /infra/head/workflows/{id}/background-input`: `name`과 `data_url`을 보냅니다.
  PNG/JPEG/WebP, 최대 5MiB. 성공한 배경 결과에 등록된 파일의 이름·해시·크기와 일치해야 합니다.
  원본은 `/data/head-inputs/{sha256}`에 보관하며 DB에는 이미지 바이트를 넣지 않습니다.

배경/에셋 제작은 기존 `/infra/agent/*`와 `/infra/jobs/{id}/retry`를 사용합니다.
AI 판단 실패는 해당 `head_decide` 작업에 실패로 기록하며 같은 retry API로 재시도합니다.
수동 재시도는 추가 API 비용이 발생할 수 있습니다. 네트워크 응답 수신 전에 프로세스가 죽으면
재실행 시 유료 호출이 중복될 수 있지만, 자식 작업 등록은 트랜잭션과 작업 소유권으로 중복을 막습니다.
판단용 내부 작업은 공개 `/infra/jobs` 입력으로 임의 생성할 수 없습니다.

AI는 배경·에셋의 의미와 제작 프롬프트를 정합니다. 서버가 개수·ID·형식·작업 의존성·소유권을 검사합니다.
AI 출력으로 셸 명령·임의 경로·게시 명령을 실행하지 않습니다. 오류 응답에는 키나 원문을 기록하지 않습니다.
새 `infra_head_workflow` 테이블을 추가하며 기존 테이블의 열은 바꾸지 않습니다.

## 로컬 검증

`infra/central/head_smoke.py`는 과거 직접 OpenAI 테스트 키로 test 흐름을 등록하고 두 번의 Luna 판단을 확인하던 도구입니다.
현재 키를 제거한 운영 서버에서는 이 도구를 GMS 검사로 실행하지 않습니다.
합성 배경 이미지와 합성 제작 결과를 사용하므로 실제 이미지 생성 품질이나 생성 PC 연결 검증은 아닙니다.
테스트는 `--catalog-version head-smoke-...`로 별도 카탈로그를 지정합니다. 같은 카탈로그로 등록된
UE 테스트 키트를 MR !33 환경에서 계속 실행해 리소스 보고와 heartbeat를 유지해야 합니다.
이미 실행 중인 실제 생성 PC가 없는 환경에서 실행합니다. 사전 확인을 우회하거나 제작 보고를 합성하지 않습니다.
해당 과거 검사는 별도 카탈로그와 합성 생성 결과를 사용했습니다.
GMS의 실제 이미지 기반 판단·배경→에셋 흐름 검증은 별도로 수행해야 합니다.
일반 CI·진단/수동 초안 smoke는 외부 AI를 호출하지 않습니다. 이번 전환 결과는 [검증 기록](HEAD_VALIDATION.md)에 있습니다.

테스트는 `--catalog-version head-smoke-...`로 별도 카탈로그를 지정합니다. 같은 카탈로그로 등록된
UE 테스트 키트를 MR !33 환경에서 계속 실행해 리소스 보고와 heartbeat를 유지해야 합니다.
이미 실행 중인 실제 생성 PC가 없는 환경에서 실행합니다. 사전 확인을 우회하거나 제작 보고를 합성하지 않습니다.
