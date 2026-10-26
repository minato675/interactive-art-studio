# infra — Docker 실행 구성 선택

show_playback.py는 중앙 대시보드에 현재 반복·작품별 캐시·다음 작품을 추가 보고합니다. 중앙 계약과 함께 키트를 갱신해야 하며 원격 현장 설치를 가정하지 않습니다. [운영 UI](../docs/SHOW_DASHBOARD.md).

2026-10-23부터 develop 기준 운영 443 한 곳으로 통합합니다. 8444는 폐기 대상이며 과거 이력과 구분합니다. 현재 접속은 [계정 접속 안내](../docs/OPERATOR_LOGIN.md)를 따릅니다.

새 요청의 자동 생성·조립·AI 검수·재작업·게시 정책은 [Head 자동 사이클](HEAD_AUTOMATION.md)을 따른다.
전시 동안 AI 디렉터가 작품 요청을 계속 써서 이 사이클에 넣는 전시 자동 순환은 [설계 · 구현 기록](HEAD_EXHIBITION_FEED.md)을 따른다(S15P31A504-27).

운영 앱 원문 접수·UE 설치 대기·PC1 공동 전환은 [제출 연동 안내](OPERATOR_HEAD_INTAKE_HANDOFF.md)를 따른다.

UE 조립 이후 별도 실행 검증·승인·PC2 게시 연결은 [UE 실행 흐름](UE_RUNTIME_PIPELINE.md)를 따른다.
새 PC2 키트의 UE 자동 실행·단일 프로세스 관리·마지막 정상 작품 복구는 [시연 자동 복구](UE_SHOW_RECOVERY.md)를 따른다. `auto_launch=true`인 키트에만 적용하며 기존 PID 관찰 모드도 유지한다.
현장 담당자에게는 [키트 전달 안내](UE_RUNTIME_HANDOFF.md), 적용 증거는 [배포 기록](UE_RUNTIME_DEPLOYMENT.md)을 제공한다.
PC2의 선택적 UE 자동 실행·정지·중복 방지·복구는 [Show Agent 관리 모드](../apps/show_agent/LIFECYCLE.md)를 따른다.

생성 PC의 배경 전담 1개·에셋 전담 2개 발급·운영은 [생성 실행기 정책](GENERATION_ROUTING.md)을 따릅니다.

작품 누적 라이브러리·신규 우선 재생은 [스케줄러 설계](../docs/architecture/library-scheduler.md)를 검토 중입니다.
`simulate_exhibition.py`는 기본 작품 0개로 시작하는 가상 시간 정책 비교 도구이며 실제 생성·UE·중앙 API를 호출하지 않습니다.
시연 관리의 수동 목록·공통 시간·PC2 자동 순환 연결은 [실제 재생 연동](../docs/SHOW_PLAYBACK.md)을 따릅니다. 최근 30/70 자동 작품 선택 정책과 구분합니다.

로컬 개발용과 중앙 서버용을 **각각 별도 폴더**에 둡니다. 실행하려는 폴더의 Compose 파일을 선택합니다.

SSAFY EC2의 실제 접속·이전 결과와 실행 방법은 [EC2 중앙 서버](./EC2_SERVER.md)를 따릅니다.
팀원 PC의 테스트(8444)·운영(443) 앱 접속과 확인은 [접속·테스트 안내](../docs/ENVIRONMENT_ACCESS.md)에 있습니다.
노트북 중앙의 기록·노드 설정은 유지하고, 노트북 Runner와 로컬 개발 서버는 별도로 운영합니다.

```text
infra/
  local-dev/   # 한 PC의 SQLite 개발 서버 — a504-docker-local
  central/     # 노트북 임시 중앙 서버 → EC2 — ranel
  runner/      # GitLab CI 실행 — ranel-gitlab-runner
  examples/    # 현장 생성 작업 요청 예시
```

## 구성별 용도

| 구분 | [로컬 개발용](./local-dev/README.md) | [중앙 서버용](./central/README.md) |
|---|---|---|
| 용도 | 기존 제작 API·운영 화면·파일 준비를 한 PC에서 개발 | 운영 앱·현장 Agent 접속, 비동기 작업·상태 관리 |
| Compose / Dockerfile | `infra/local-dev/compose.yaml` / `infra/local-dev/Dockerfile` | `infra/central/compose.yaml` / `infra/central/Dockerfile` |
| Compose 프로젝트 이름 | `a504-docker-local` | `ranel` |
| 기본 상주 컨테이너 | `a504-docker-local-central-1` 한 개 | `ranel-central-1`·`ranel-worker-1`·`ranel-dispatcher-1`·`ranel-postgres-1`·`ranel-rabbitmq-1` |
| HTTPS 추가 | 별도 구성 없음 | `infra/central/compose.https.yaml`을 함께 지정하면 `ranel-gateway-1` 추가 |
| DB / 작업 큐 | SQLite / 별도 큐 없음 | PostgreSQL / RabbitMQ·Celery |
| 설정 파일 | `infra/local-dev/.env` | 저장소 루트 `.env.infra`·`.env.https` |
| 기본 접속 | `http://127.0.0.1:8001` | 서버 자신의 `http://127.0.0.1:18080`; 다른 PC는 설정한 HTTPS 주소 |
| 렌더러 설정 | `RENDERER=both` — TD·UE 후보 파일 준비 | 기존 분산 Compose의 `RENDERER=ue` 유지 |
| 데이터 볼륨 | `a504-docker-local_runtime-data` | `ranel_postgres-data`·`ranel_rabbitmq-data`·`ranel_central-data` |

각 폴더 README에 초기 설정·실행·로그·중지·컨테이너 역할을 정리했습니다. **모든 명령은 저장소 루트에서 실행합니다.**
`a504-docker-local`과 `ranel`은 파일 이름이 아니라 Compose 프로젝트 이름입니다.
두 구성은 DB와 파일 볼륨이 독립적이며 한쪽의 기록이 다른 쪽에 자동 공유·이전되지 않습니다.
두 기본 Compose 파일을 한 명령에 섞지 않습니다. 중앙 서버의 HTTPS 파일만 중앙 기본 파일과 함께 사용합니다.

## 기존 경로에서 바뀐 점

| 이전 경로 | 현재 경로 |
|---|---|
| `infra/docker/` | `infra/local-dev/` |
| `infra/Dockerfile`, `infra/compose*.yaml` | `infra/central/` 아래 같은 이름 |
| `infra/Caddyfile.internal`, `infra/postgres-init.sql`, `infra/local.env.example` | `infra/central/` 아래 같은 이름 |
| `infra/deploy.sh`, `infra/preflight.sh`, `infra/init_local.py`, `infra/smoke.py` | `infra/central/` 아래 같은 이름 |

로컬 개발의 기존 `infra/docker/.env`가 있는 PC는 그 파일을 `infra/local-dev/.env`로 옮긴 뒤 새 명령을 사용합니다.
실제 비밀 설정은 Git에 포함하지 않습니다. 중앙의 `.env.infra`·`.env.https` 위치는 그대로입니다.
프로젝트 이름·이미지 태그 정책·포트·볼륨은 바꾸지 않으며, 폴더 정리만으로 실행 중인 컨테이너를 재기동하지 않습니다.
기존 데이터가 필요한 환경에서는 `down -v`로 볼륨을 삭제하지 않습니다.

## CI Runner와 공통 운영 도구

오늘 생성 서버·UE 제작 서버 연결 순서는 [현장 연결 안내](TODAY_CONNECTION.md),
작업·파일 계약과 재현 검사는 [서버 연결 문서](SERVER_EXCHANGE.md)를 따릅니다.

[Runner 구성](./runner/README.md)은 두 서버 구성과 별도입니다. `ranel-gitlab-runner`가 CI job을 최대 4개 실행하며,
중앙 서버를 EC2로 이전한다고 Runner도 자동 이전되지 않습니다. CI의 일회성 검사 환경은 [CI/CD 문서](../docs/CI_CD.md)를 따릅니다.
Windows 운영 앱·생성 도구·UE 제작/시연 프로세스는 중앙 Docker에 포함하지 않습니다.

| 공통 도구·문서 | 용도 |
|---|---|
| `local_pipeline.py`, `ci_deploy_test.py`, `check_packaged_scene.py` | 새 `central/` 경로로 이미지 빌드·설치 리소스·교체·실패 복구 검사. 설치된 wheel의 Head 감독 지침·파일 해시·단계별 로딩도 검사해 지침 누락 시 배포를 중단 |
| `backup_central.py`, `RESTORE.txt`, `check_https.py` | 중앙 데이터 백업·복원·HTTPS 확인 |
| `client.py`, `central_cli.py`, `bundle.py`, `smoke_generation.py`, `smoke_agents.py` | 현장 생성/Agent 접속·임대·진행률·파일 전달과 검사 |
| [생성 PC 작업 도구](./generation/README.md) | 생성 전 템플릿 확인, 16:9 자르기·알파 변환, 넘기기 전 UE 조립 검사와 생성 PC 준비 절차 |
| [노트북·EC2 이관](./LAPTOP_SERVER.md), [운영 준비](./OPERATIONS.md), [현장 실행](./WORKFLOW.md) | 기존 검증 이미지 재시작·백업·데이터 이전과 실제 작업 연결 |
| [저장소 설계](./STORAGE.md) | 초기 생성 1대·UE 제작 2대·시연 1대, 생성 PC 공유 디스크와 UE 제작 N대 확장 |
| [통신 파이프라인](../docs/COMMUNICATION_PIPELINE.md), [API 규약](../docs/COMMUNICATION_API.md) | 구현된 연결·데이터·오류/재전송과 남은 작업 |
| [검증 기록](./VALIDATION.md) | 실제 통과한 검사와 미검증 범위. 이전 기록의 경로는 검증 당시 기준 |
