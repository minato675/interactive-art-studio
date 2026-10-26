# 인프라 실행·배포 준비

작업 브랜치: `S15P31A504-6-infra-foundation`. CI 보완 시 `develop`의 `e82897a`까지 통합했다.
관련 작업은 CI `S15P31A504-6`, EC2 배포 `S15P31A504-7`, 비동기 큐 `S15P31A504-20`이다.
초기 로컬 준비 이후 EC2 이관·develop 테스트 자동 배포를 연결했다. 전체 제작 자동화와 전시 성능 검증을 완료한 상태는 아니다.

현재 EC2 접속·테스트는 [팀원 안내](../docs/ENVIRONMENT_ACCESS.md)를 따른다.
과거 노트북 임시 중앙의 접속·생성 PC 연결·이관 절차는 [임시 중앙 서버](./LAPTOP_SERVER.md)에 남긴다.
현재 연결 대상은 배경·에셋 생성 PC이며 UE 연결은 후속이다.

## 지금 실행되는 범위

| 항목 | 구현 범위 | 후속 작업 |
|---|---|---|
| CI/CD | lint·네 테스트 병렬·실제 이미지 기동/실패 복구·DB/파일 보존, Runner 최대 4개, EC2 소스 빌드·develop 테스트 자동 배포 | main 릴리스 통합·운영 수동 배포 검증 |
| 비동기 | 작업 ID 즉시 반환, Celery의 진단·실제 초안 기획, DB 영속 작업함·재전송 | 전체 배경→에셋→전처리→UE→검수→게시 의존성 연결 |
| 중앙↔Agent | 노드별 토큰, 작업 임대·heartbeat·시도·늦은 결과 거부, 상태 조회 | 토큰 회전 UI, 역할 권한 세분화, progress의 운영 앱 표시 |
| 제작 노드 | 공통 대기열, 노드당 한 작업, 파일 무결성·캐시 검사 | 실제 UE 조립·프리뷰·성능 검사 어댑터 |
| 시연 노드 | 캐시 준비, 기존 UE 상태 파일의 세션·PID·버전 확인, 상태 보고 | 중앙 게시 명령 수신, Ready 후 별도 commit, 프로세스 자동 재기동 |
| 저장 | 로컬 폴더/OS가 연결한 공유 폴더, 해시 저장·파일 단위 캐시·용량 확인 | 실제 SMB 공유·계정·백업 장비·보관 정책 |

기존 제작·확정·검수 API는 동기 제작 경로이며 기본 제작 차단은 해제되어 있다.
최신 [제작 정책](../docs/refactoring/creation-policy.md)에 따라 필수 후보 파일을 준비하고 `prepared`를 반환한다.
후보 준비는 활성 release.json을 변경하지 않으며 실제 전시 적용과 구분한다.
새 비동기 API를 기존 운영 앱의 전체 작업 흐름에 연결하는 작업은 아직 포함하지 않았다.
`bundle_verify` 통과는 **파일 검사만 통과**한 것이며 자동 게시 승인으로 사용하지 않는다.

## 로컬 Compose 실행

저장소 루트 PowerShell에서 실행한다. OneDrive 아래에서 uv 하드 링크 오류가 나면 `UV_LINK_MODE=copy`를 사용한다.

```powershell
$env:UV_LINK_MODE="copy"
uv sync --locked --all-packages
uv run python infra/central/init_local.py
docker compose --env-file .env.infra -f infra/central/compose.yaml config --quiet
docker compose --env-file .env.infra -f infra/central/compose.yaml up -d --build --wait
uv run python infra/central/smoke.py
uv run python infra/smoke_agents.py
```

`init_local.py`는 비밀 값을 화면에 출력하지 않고 `.env.infra`를 한 번만 만든다. 이미 있으면 실패하므로 재실행하지 않는다.
`.env.infra`는 Git에서 제외되며 토큰은 개발 PC에만 있다. 개발용 값은 배포 전에 별도로 발급한다.
`smoke_agents.py`는 다른 제작 작업이 없는 로컬 개발 환경에서 실행한다. 한 Windows PC에서 두 Agent를 실행해
서로 다른 작업과 캐시를 검사하며 실제 2대 장비의 SMB 전송·UE 병렬 실행을 검증하는 것은 아니다.

- API 문서: `http://127.0.0.1:18080/docs`
- `/health`: API 프로세스 응답, `/ready`: DB 연결 확인
- `/infra/jobs`: `X-Operator-Token` 인증 후 작업 생성·조회
- `/api/*`: `OPERATOR_TOKEN`을 설정하면 조회·변경 모두 운영자 인증 필요
- PostgreSQL·RabbitMQ·관리 UI 포트는 호스트/인터넷에 공개하지 않는다.
- Compose Worker 동시성 2와 임대 120초, 최대 시도 3은 초기 실행 설정이다. 운영 성능을 확정한 수치가 아니다.
- `docker compose ... stop`으로 서비스를 중지한다. 데이터가 필요하므로 `down -v`를 사용하지 않는다.

실제 PostgreSQL 테스트는 `ranel_infra_test` DB에 임시 스키마를 만든다.

```powershell
docker compose --env-file .env.infra -f infra/central/compose.yaml --profile test run --build --rm test
docker compose --env-file .env.infra -f infra/central/compose.yaml --profile test run --rm test uv run --no-sync python infra/check_pipeline.py
```

이미 존재하던 PostgreSQL 볼륨에는 초기 SQL이 다시 실행되지 않는다. 테스트 DB가 없으면 관리자 계정으로
테스트 DB만 별도 생성한다. 운영 DB를 지워서 초기화하지 않는다.

## 파일 저장과 제작 노드 실행

공유 저장소는 생성 PC의 전용 디스크 폴더이며, 초기 UE 제작 PC 2대와 시연 PC 1대가 접근한다.
UE 제작 노드는 동일 규격으로 N대로 확장한다. 경로·용량 계산은 [저장소 설계](./STORAGE.md)에 있다.
다음은 UE 제작 PC에서 쓰는 예시이며 호스트명·경로는 실제 장비에 맞게 지정한다.
공유 폴더 연결 권한은 Agent 실행 계정에서 준비해야 한다.

```powershell
uv run python infra/bundle.py --source D:\ArtworkOutput\A --store \\GENERATION-01\RanelAssets `
  --artwork A --version v1 --catalog v1 --output runtime\A-v1.json

$env:CENTRAL_URL="http://127.0.0.1:18080"
$env:AGENT_TOKEN="<이 노드의 토큰>"
$env:ARTIFACT_ROOT="\\GENERATION-01\RanelAssets"
$env:NODE_CACHE="D:\RanelCache\production-1"
uv run --package a504-node-agent python -m node_agent
```

`AGENT_KEYS_JSON`에는 노드 ID → 토큰·역할(`generation`/`production`/`show`)·카탈로그 버전을 등록한다.
`generation`은 배경·에셋 작업 임대·진행률·결과 등록 역할이며 위 UE 제작 Agent 대신 `infra/client.py`를 사용한다.
이 역할에는 UE 조립 작업의 임대 권한이 없다. Codex 실행은 수동이며 [현장 실행 절차](./WORKFLOW.md)를 따른다.
노드를 늘릴 때 각 PC에 다른 ID·토큰과 동일한 카탈로그를 준다. 토큰은 예시 문서·MR·로그에 붙여 넣지 않는다.
같은 노드에서 두 Agent를 띄우지 않는다. 로컬 잠금 외에도 중앙 DB가 노드당 실행 슬롯 하나를 검사한다.

API 작업 예시(토큰은 HTTP 헤더로 전달):

```json
{"request_id":"unique-request-1","kind":"draft","draft":{"mode":"manual","theme":"바다"}}
```

파일 검사는 `kind: "bundle_verify"`와 `bundle`에 `infra/bundle.py`가 만든 JSON 내용을 넣는다.
상태 조회 결과에는 소유권 토큰을 공개하지 않는다. 실제 임대 응답에서만 해당 Agent에게 전달한다.
요청이 재전송되면 같은 `request_id`는 같은 작업을 돌려준다. 같은 ID에 다른 내용은 409다.
임대 만료 후 재배정된 작업에 이전 토큰으로 완료를 보고하면 409다.
임대 토큰으로 DB 결과 반영은 막지만, 긴 외부 AI 호출의 중복 과금 자체를 되돌리지는 못한다.
외부 공급자를 붙일 때 공급자 idempotency·취소·결과 재사용을 함께 구현해야 한다.

## 시연 노드 실행

UE 실행 파일은 이 저장소에 없으므로 Agent가 임의로 실행 경로를 추측하거나 두 번째 UE를 띄우지 않는다.
먼저 기존 공통 Runtime 하나를 `-ArtworkRelease=<SHOW_CACHE>\release.json`으로 실행한 다음 실제 PID를 설정한다.
코드 빌드·에디터·쿠킹은 시연 PC에서 실행하지 않는다.

```powershell
$env:SHOW_CACHE="D:\RanelCache\show"
$env:UE_RUNTIME_PID="<실제 UE Runtime 프로세스 ID>"
$env:CATALOG_VERSION="v1"
# CENTRAL_URL, AGENT_TOKEN(시연 노드 전용), ARTIFACT_ROOT도 설정
uv run --package a504-show-agent python -m show_agent --bundle runtime\A-v1.json
# 파일을 준비한 뒤 명시적으로 기존 watcher에 전달할 때:
uv run --package a504-show-agent python -m show_agent --bundle runtime\A-v1.json --activate
```

`--bundle` 없이 실행하면 현재 Runtime 상태만 보고한다. 위 두 예시를 동시에 띄우지 않는다.
Agent가 읽는 파일은 `release.json.status.json`이다. `RuntimeSessionId`, `ProcessId`,
`UpdatedAtUtc`, `RequestedVersion`, `ActiveReleaseVersion`, `Phase`, `Applied`를 확인한다.
종료된 프로세스·PID 재사용 전의 오래된 파일·요청 버전 불일치는 새 작품의 Applied로 처리하지 않는다.
통신 복구 시 파일에 기록된 실제 활성 버전을 다시 보고한다.
`Applied`도 첫 화면 출력·무중단 전환 성능을 검증했다는 뜻은 아니다. 원본 UE 상태의 `FirstDisplayVerified`는 false다.

## 서버·노드 통신 경계

연결 흐름·포트·역할별 실행 주체·현재 구현과 EC2 이전 항목은
[서버·노드 통신 파이프라인](../docs/COMMUNICATION_PIPELINE.md)에 정리한다.
HTTP 엔드포인트·인증·임대·완료·오류와 재전송은 [통신 API 규약](../docs/COMMUNICATION_API.md)을 따른다.
이 문서는 장비 실행·운영 절차를 다루며 전체 통신 규약은 위 문서에서 관리한다.

## EC2 접속·배포

2026-10-22 실제 EC2 설치·기존 데이터 이전·수동 실행 검증을 수행했습니다.
현재 주소·실행 명령·검증 범위는 [EC2 중앙 서버](./EC2_SERVER.md)를 따릅니다.
develop 테스트 자동 배포와 Protected SSH 변수 등록은 MR !37에서 완료했습니다.
main은 릴리스 통합 후 운영 수동 배포를 검증해야 합니다. Registry 없이 EC2에서 Compose로 빌드합니다.
팀원 앱 접속·CA·토큰·테스트 절차는 [접속·테스트 안내](../docs/ENVIRONMENT_ACCESS.md),
배포 담당자의 설정·실패 복구는 [CI → EC2 운영](central/CI_EC2.md)에 있습니다.

MobaXterm에 등록하는 것과 별도로, 이 PC의 OpenSSH에서 같은 서버에 접속할 수 있으면
Codex가 저장소 코드로 설치·배포·로그 확인을 실행할 수 있다. 사용자가 코드를 다시 채팅에 전달할 필요는 없다.
MobaXterm 전용 비밀번호 저장/키 형식은 OpenSSH와 바로 공유되지 않을 수 있다.

필요한 것은 서버 주소, SSH 사용자명, 이 PC의 키 파일 경로 또는 이미 만들어진 SSH 별칭이다.
개인키 본문·비밀번호를 채팅에 보내지 않는다. SSH 호스트 키 지문은 제공처/관리자가 알려준 값과 확인한다.

`~/.ssh/config` 예시(값은 실제 서버 정보로 대체):

```sshconfig
Host ranel-ec2
    HostName <서버 주소>
    User <제공된 사용자>
    IdentityFile C:/Users/SSAFY/.ssh/<키 파일>
    IdentitiesOnly yes
```

```powershell
ssh ranel-ec2
ssh -N -L 18080:127.0.0.1:18080 ranel-ec2
```

서버를 새로 준비하거나 배포 연결을 다시 구성할 때의 순서입니다. 현재 서버에서 초기화를 반복하지 않습니다.

1. OS·CPU 아키텍처·디스크·Docker/Compose·SSH 권한 확인. `preflight.sh`는 설치나 삭제를 하지 않는다.
2. 운영 `/opt/ranel`과 테스트 `/opt/ranel-staging`의 서버 전용 비밀 설정·DB·큐·파일·CA를 분리한다.
3. GitLab Runner `linux-docker` 태그와 `build-smoke`의 이미지 기동·복구 검사 통과를 확인한다.
4. 배포 전용 `SSH_PRIVATE_KEY`, 고정 `SSH_KNOWN_HOSTS`를 protected File·환경 범위 `ec2/*`로 등록한다. `EC2_USER`·`EC2_HOST`는 CI 기본값을 확인한다.
5. `EC2_DEPLOY_ENABLED=true`를 설정하고 develop 자동 테스트 배포를 검증한다. main은 릴리스 반영 후 운영 수동 배포한다.
6. API·DB readiness뿐 아니라 실제 비동기 진단·초안 작업 완료까지 확인한다.
7. TLS/도메인·운영 접근제어를 확정하고 필요한 접속만 연다. DB·AMQP·SMB는 인터넷에 공개하지 않는다.

개발 MR은 develop 대상이다. develop은 테스트 환경을 자동 갱신하고 운영은 main 반영 후 수동 배포한다.
기존 노트북 중앙은 정지하고 기록·노드 설정을 EC2로 이전했습니다. 노트북 Runner는 별도로 유지합니다.
현재 서버 수동 이전·develop 테스트 자동 배포 성공과 main 운영 배포 job의 실제 성공은 구분합니다.
CI 성공 필수 설정은 Runner에서 정상·실패 경로를 확인한 뒤 활성화한다.

### GitLab Runner 확인 — 2026-10-21

최초 조회에서는 프로젝트·그룹·인스턴스 Runner가 모두 없어 MR 파이프라인이 대기했다.
이후 사용자 요청으로 노트북에 프로젝트 전용 [Runner #2191](https://lab.ssafy.com/s15-final/S15P31A504/-/runners/2191)을
등록했다. `linux-docker` 태그 작업을 최대 4개 실행하며, 중앙 서비스와 별도의 Docker Compose로 관리한다.
자원 제한·시작/중지·등록 템플릿은 [Runner 안내](./runner/README.md)를 따른다.

실제 GitLab 작업 결과는 [검증 기록](./VALIDATION.md)에 남긴다. 로컬 검사 성공과 GitLab 작업 성공을 구분한다.
2026-10-22 프로젝트에 배포 변수를 등록하고 파이프라인 #236465의 7개 job과 실제 EC2 테스트 배포를 확인했습니다.

## 장애·백업·현재 한계

- RabbitMQ가 내려가도 이미 DB에 등록한 작업은 남는다. Dispatcher가 복구 후 재전송한다.
- publish 직후 Dispatcher가 종료되면 같은 메시지가 다시 전달될 수 있다. DB 임대로 중복 실행을 제한한다.
- Worker/Agent가 종료되면 임대 만료 후 제한 횟수 안에서 재배정된다. 이미 종료된 시도의 결과는 거부한다.
- 초안 기획 도중 Worker가 종료되면 시도별 초안 기록이 남을 수 있다. 외부 부작용까지 exactly-once는 아니다.
- 현재 스키마 생성은 `create_all`이다. 컬럼 변경·데이터 이관 전에 Alembic을 도입해야 한다.
- 배포 스크립트는 실패 시 이전 이미지 복구를 시도한다. 데이터 볼륨은 유지한다. DB 변경을 역변환하지 않는다.
- 서비스 초기 실행/배포 중 중앙 API 중단 가능성이 있다. 현장 UE의 로컬 실행과 분리되지만 무중단 배포를 검증한 것은 아니다.
- 현재 JSON 로그와 Docker 로그 순환을 사용한다. 장기 로그 보관·Alloy/Loki/Grafana는 별도 운영/라이선스 검토 후 추가한다.
- PostgreSQL 덤프·공유 폴더 파일·manifest를 함께 백업해야 한다. 백업 장비·보관 기간은 미정이다.
- 승인 작품의 자동 게시와 실제 UE 성능 검증은 이번 파일 검사만으로 통과시킬 수 없다.

근거: [Celery 작업·중복 전달](https://docs.celeryq.dev/en/stable/userguide/tasks.html),
[uv Docker workspace 설치](https://docs.astral.sh/uv/guides/integration/docker/),
[GitLab rootless BuildKit](https://docs.gitlab.com/ci/docker/using_buildkit/).
