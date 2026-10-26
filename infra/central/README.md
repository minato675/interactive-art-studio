# central — 노트북 임시 중앙 서버 · EC2 중앙 서비스

native PC2 기기 경로 `/infra/earth-pc2/*`와 계정 운영 경로 `/infra/native-artworks`는 [아카이브 연결](../../docs/NATIVE_ARCHIVE_PLAYBACK.md)을 따른다. 기기 토큰과 계정 권한은 분리한다.

2026-10-23 현재 배포 대상은 develop → 운영 443 한 곳입니다. 테스트 8444는 폐기하고 보존 자료만 남깁니다. [계정 접속 안내](../../docs/OPERATOR_LOGIN.md)를 따릅니다.

API 기반 Head의 설정·작업 순서·운영 API·검증 범위는 [Head 운영 문서](HEAD_NODE.md)에 있습니다.
GitLab에서 소스를 전달해 EC2에서 빌드·배포하는 절차는 [CI → EC2 배포](CI_EC2.md)를 따릅니다.
팀원 PC에서 테스트 8444·운영 443에 접속하는 방법은 [접속·테스트 안내](../../docs/ENVIRONMENT_ACCESS.md)에 있습니다.

FastAPI·PostgreSQL·RabbitMQ·Celery Worker·Dispatcher를 실행하는 Docker 구성입니다.
2026-10-22에 기존 노트북의 데이터·노드 설정을 유지해 SSAFY EC2로 이전했습니다.
실제 서버 주소·검증·운영 명령과 남은 작업은 [EC2 중앙 서버](../EC2_SERVER.md)를 따릅니다.
Windows 운영 앱·생성 도구·UE 제작/시연 프로세스는 이 Compose에 포함하지 않습니다.

## 컨테이너와 이미지

| 서비스 | 기본 컨테이너 이름 | 역할 |
|---|---|---|
| `central` | `ranel-central-1` | 운영·작업·노드 FastAPI |
| `worker` | `ranel-worker-1` | Celery 진단·초안 기획 |
| `dispatcher` | `ranel-dispatcher-1` | DB 대기 작업의 큐 전송·재전송 |
| `postgres` | `ranel-postgres-1` | PostgreSQL 상태 저장 |
| `rabbitmq` | `ranel-rabbitmq-1` | 작업 메시지 전달 |
| `gateway` (선택) | `ranel-gateway-1` | Caddy HTTPS 진입점 |

Compose 프로젝트 이름은 `ranel`로 유지합니다. `-p`·`COMPOSE_PROJECT_NAME`으로 바꾸면 접두사가 달라집니다.
API·Worker·Dispatcher는 `ranel-central:local` 또는 `.env.infra`의 `APP_IMAGE` 한 개를 공유하고 실행 명령만 다릅니다.
기본 상주 서비스는 5개, HTTPS 추가 시 6개이며 `test`는 선택 실행입니다.
DB·큐·파일 볼륨 이름도 기존 `ranel_*`를 유지합니다. 폴더 이동은 데이터 이전이 아닙니다.

모든 명령은 `pyproject.toml`이 있는 **저장소 루트**에서 실행합니다. Windows에서는 Linux containers,
EC2에서는 Linux Docker Engine·Compose·Python 3가 필요합니다.
서버 설정 파일 `.env.infra`·`.env.https`는 저장소 루트에 유지하며 Git에 넣지 않습니다.

## 신규 중앙 서버 시작

```bash
# 최초 한 번만 실행. 이미 .env.infra가 있으면 이 줄은 실행하지 않습니다.
python3 infra/central/init_local.py

docker compose --env-file .env.infra -f infra/central/compose.yaml config --quiet
docker compose --env-file .env.infra -f infra/central/compose.yaml up -d --build --wait
docker compose --env-file .env.infra -f infra/central/compose.yaml ps
```

이미지를 EC2에서 빌드하므로 별도 이미지 Registry 없이 시작할 수 있습니다.
새 노트북에서 같은 구성을 준비할 때는 첫 줄 대신 `uv run python infra/central/init_local.py`를 사용합니다.
이 스크립트는 임의 비밀번호·토큰을 생성하고, 기존 `.env.infra`가 있으면 덮어쓰지 않고 실패합니다.
기본 노드 등록은 UE 제작 2대·시연 1대이므로 생성 노드와 추가 노드는 실제 ID·역할·서로 다른 토큰을 별도로 등록합니다.

기본 API는 서버 자신의 `http://127.0.0.1:18080`이며 `/docs`에서 API 문서를 확인합니다.
`/health`는 프로세스 응답, `/ready`는 DB 연결 준비를 확인하며 실제 비동기 완료 검사는 [운영 준비](../OPERATIONS.md)를 따릅니다.
다른 PC의 접속은 아래 HTTPS 구성과 접근 제어가 필요합니다. DB·RabbitMQ 포트는 호스트에 공개하지 않습니다.
현재 EC2의 설치·복원·인증·비동기 완료 검증은 수행했습니다. 현장 PC의 실접속·UE 실행 검증은 별도입니다.

```bash
# 중앙 서버용 로그 조회 / 중지
docker compose --env-file .env.infra -f infra/central/compose.yaml logs --tail 100 central worker dispatcher
docker compose --env-file .env.infra -f infra/central/compose.yaml stop
```

**이미 배포된 노트북**을 재시작할 때는 새 EC2의 초기화·빌드 절차를 반복하지 않습니다.
검증된 이미지의 재시작과 코드 변경 배포는 [노트북 운영 절차](../LAPTOP_SERVER.md#서버-시작)를 따릅니다.
기존 데이터를 EC2로 이전할 때도 신규 빈 서버 기동 대신 해당 문서의 백업·복원 절차를 사용합니다.

## HTTPS 추가 — 다른 PC 접속

서버에 맞는 `.env.https`를 저장소 루트에 준비합니다. 아래 호스트 값은 실제 IP 또는 DNS 이름으로 바꿉니다.

```dotenv
CENTRAL_HOST=k15a504.p.ssafy.io
CENTRAL_HTTPS_BIND=0.0.0.0
CENTRAL_HTTPS_PORT=443
```

기본 중앙 이미지를 위에서 빌드했거나 기존 검증 이미지가 있는 상태에서 실행합니다.
다음 한 줄은 Linux와 PowerShell에서 모두 사용할 수 있습니다.

```bash
docker compose --env-file .env.infra --env-file .env.https -f infra/central/compose.yaml -f infra/central/compose.https.yaml up -d --no-build --wait
```

위 설정은 현재 EC2 운영용이며 접속 주소는 `https://k15a504.p.ssafy.io`입니다.
테스트 환경은 별도 `/opt/ranel-staging/.env.https`에서 8444를 지정합니다. Compose의 일반 기본 포트와
과거 노트북 임시 서버 예시는 8443이므로 EC2의 실제 설정과 구분합니다. Caddy의 내부 CA를 사용하는 현재 구성은
클라이언트의 공개 CA 인증서 신뢰 설정도 필요하며 인증서 검증을 끄지 않습니다.
허용할 접속 IP와 방화벽·EC2 보안 그룹을 적용한 뒤 사용합니다. 노트북에서는 특정 서버 IP에만 바인딩할 수도 있습니다.
전체 설정과 HTTPS 구성을 포함한 중지·이전 절차는 [임시 중앙 서버 안내](../LAPTOP_SERVER.md)를 따릅니다.

`stop`은 해당 구성의 컨테이너만 멈춥니다. HTTPS까지 중지하려면 같은 두 env 파일과 두 Compose 파일을 지정한 명령의
`up -d --no-build --wait` 부분을 `stop`으로 바꿉니다. 데이터가 필요한 환경에서 `down -v`는 사용하지 않습니다.
서버 설정 파일·토큰·DB/파일 볼륨은 Git에 올리지 않습니다.

## 중앙 서버의 구현 범위와 관련 파일

중앙 서버는 Docker Compose로 FastAPI · PostgreSQL · RabbitMQ · Celery Worker · 재전송 Dispatcher를 실행합니다.
서버·현장 노드가 작업과 파일을 주고받는 구조는 [통신 파이프라인](../../docs/COMMUNICATION_PIPELINE.md),
엔드포인트·인증·데이터·오류/재전송 규칙은 [통신 API 규약](../../docs/COMMUNICATION_API.md)이 기준입니다.

초기 현장 배치는 **생성 PC 1대·UE 제작 PC 2대·시연 PC 1대, 총 4대**입니다.
공유 저장소는 생성 PC의 전용 디스크 폴더이며, UE 제작 노드는 N대로 확장합니다.
이번 실제 연결 확인 대상은 **배경·에셋 생성 PC**이며 UE 조립 노드와 구분합니다.
`generation` 역할은 배경·에셋 작업 임대, 진행률, 결과 파일 목록 등록과 heartbeat를 지원합니다.
Codex는 사용자가 별도로 실행하며 기존 `production` 역할의 UE 조립 작업은 받지 않습니다.
노트북에서 검사·배포하고 생성 작업을 주고받는 방법은 [현장 실행 절차](../WORKFLOW.md)를 따릅니다.

기존 `/api/artworks`, `/api/drafts`는 기존 동기식 API입니다. 새 `/infra/jobs`는 즉시 작업 ID를 돌려주고,
`draft` 작업은 Linux Celery Worker가 실제 초안을 생성합니다. `bundle_verify` 작업은 Windows 제작 Agent가
공통 대기열에서 한 건씩 임대받아 공유 저장소 → 로컬 캐시 복사와 무결성을 검사합니다.
UE 에디터 조립 · 전체 자동 검수 · 자동 게시를 완료했다고 취급하지 않습니다.

| 파일 | 용도 |
|---|---|
| `compose.yaml`, `Dockerfile` | 중앙 서비스 실행, 같은 이미지의 API/Worker/Dispatcher |
| `local.env.example`, `init_local.py` | 설정 형식과 무작위 로컬 비밀번호 생성 |
| `smoke.py` | HTTP → RabbitMQ → Worker → PostgreSQL 비동기 완료 확인 |
| `../check_packaged_scene.py` | 설치된 운영 이미지의 controller JSON 리소스로 UE 장면 조립·검사 확인 |
| `deploy.sh`, `preflight.sh` | Linux 사전 확인, 커밋 이미지 배포와 API 준비 확인 |
| `../local_pipeline.py` | 노트북에서 Linux 검사·이미지 빌드·배포·실패 복구 |
| `../central_cli.py`, `../client.py` | 중앙 작업 운영과 생성 PC의 임대·진행률·파일 결과 보고 |
| `../check_pipeline.py`, `../smoke_generation.py` | 실제 서버/Worker 재시작과 생성 파일 전달 검사 |
| `../ci_deploy_test.py`, `compose.ci.yaml` | CI의 일회성 Docker에서 실제 이미지 기동·교체·두 실패 복구와 DB/파일 보존 검사 |
| [Runner 실행 설정](../runner/README.md) | 프로젝트 전용 Docker Runner, 동시 실행 4개와 자원 제한 |
| [운영 준비 문서](../OPERATIONS.md) | 실행·통신·배포·SSH 연결·남은 작업 |
| [저장소 설계](../STORAGE.md) | 생성 PC 공유 디스크, UE 제작 초기 2대→N대·시연 1대의 전송·캐시와 보관 원칙 |
| [검증 기록](../VALIDATION.md) | 실제 통과한 검사와 아직 검증하지 않은 범위 |

제작·시연 Agent는 FastAPI에 outbound HTTP(S)로 연결합니다. DB와 RabbitMQ에 직접 접속하지 않습니다.
시연 Agent는 파일 준비(`Staged`)와 런타임 준비(`Ready`), 실제 활성화(`Applied`)를 구분합니다.
현재 UE의 release watcher는 자동 commit 방식이므로 독립적인 중앙 commit 명령 구현과 동일하다고 표현하지 않습니다.

원본 파일은 현장 생성 PC의 전용 디스크 공유 폴더에 두며 EC2를 경유시키지 않습니다. 초기 테스트는 일반 폴더를
같은 저장 인터페이스로 사용합니다. 실제 공유 설정·계정·방화벽은 장비 경로를 확인한 뒤 적용합니다.
이미지/영상 생성 Worker를 붙일 때도 파일을 만드는 실행부는 공유 폴더에 접근 가능한 현장에 둡니다.
EC2에서 현장 공유 폴더를 직접 마운트할 수 있다고 가정하지 않습니다.

기술 선택은 [기존 설계](../../docs/architecture/README.md)를 따릅니다. JSON 로그를 먼저 제공하며,
Alloy · Loki · Grafana 설치와 외부 AI 제공자 연결은 이번 초기 배포에 포함하지 않습니다.

## 운영 이미지의 controller 리소스

운영 이미지는 `--no-editable`로 설치합니다. `controller/contracts/`의 JSON은
`interactive_controller.contracts` 패키지 데이터로 함께 배포하며, 프리셋·카탈로그·장면 계약을
읽는 코드는 `importlib.resources`로 설치된 리소스를 찾습니다. 원본 JSON의 경로와 내용은 유지합니다.
설치 없이 `PYTHONPATH=controller/src`로 실행하는 기존 개발 방식에서는 소스의 `contracts/`를 읽고,
호출자가 파일 경로를 명시하면 해당 파일을 사용합니다.

CI `build-smoke`는 빌드 직후 `infra/check_packaged_scene.py`를 운영 이미지의 Python에서 실행합니다.
네트워크 없이 저장소 밖 작업 경로에서 설치 패키지를 읽고, 네 테마의 UE 장면 조립·검사를 확인합니다.
개발용 editable 설치의 테스트 성공과 구분하는 검사이며, 실제 UE 실행·화면 검증은 포함하지 않습니다.

**상업적으로 배포하는 설정에는 `OLLAMA_MODEL`을 Apache-2.0 모델(예: `qwen3:4b`)로 반드시 지정합니다.** 코드 기본값인 EXAONE(`exaone3.5:7.8b`)은 비상업 라이선스라 상업 배포에서는 쓸 수 없습니다. 기준과 바꿀 모델은 [THIRD_PARTY의 「EXAONE 사용 범위」](../../docs/THIRD_PARTY.md#exaone-사용-범위)에 있습니다.

비밀번호 · 토큰은 이 폴더에 넣지 않습니다. 예시 파일에는 자리표시 값만 씁니다.

## 관리자 조회 화면

HTTPS `/infra/monitor/`에서 작업·노드·실제 시연 적용 상태를 확인합니다. 화면 파일은 공개 정적 파일이지만 작업 데이터는 기존 `X-Operator-Token` 인증을 통과해야 합니다. 토큰은 현재 탭의 메모리에만 두고 새로고침·로그아웃 때 사라집니다. 브라우저 URL·저장소에 넣지 않습니다. 운영자의 기존 CA 신뢰 설정과 네트워크 허용 목록을 사용합니다.

5초마다 최근 작업 최대 200개와 노드 상태를 조회합니다. 작품 ID·상태로 목록을 좁히고 작업을 선택하면 시도·선행 작업·결과를 봅니다. Head 흐름 ID를 입력하면 감독 판단과 보류 이유를 조회합니다. 페이지 이동은 작업 200개 단위이며 서버 전체 집계로 표시하지 않습니다. 작업 성공은 보고 수신이며 검수 통과·실제 Applied와 구분합니다. 미리보기 게시도 정식 검수 승인으로 표시하지 않습니다. 연결 실패 때 기존 화면을 최신이라고 표시하지 않습니다.

이 화면은 GET 요청만 사용하며 재시도·승인·작품 생성 기능은 없습니다. FPS·CPU/GPU 프레임 시간은 현재 시연 heartbeat에 없으므로 측정 불가로 표시합니다. `monitor/`를 gateway에 읽기 전용으로 마운트하므로 API 이미지 변경 없이 정적 화면을 갱신할 수 있습니다. 화면 접속 자체로 생성 작업을 재개하지 않습니다.
