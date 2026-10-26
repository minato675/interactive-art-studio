# 노트북 임시 중앙 서버와 EC2 이관

## 마스터 노드 운영

노트북 마스터의 시작·전원·자원 제한·백업과 내일 진행할 PC 연결 항목은
[마스터 운영 안내](MASTER_NODE.md)를 따릅니다. 로컬 배포는 깨끗한 작업 브랜치에서 실행하며,
이미지의 `org.opencontainers.image.revision`과 검사 기록에 Git 커밋을 남깁니다.
이미지 태그의 임의 문자열을 커밋으로 해석하지 않습니다.

현재 현장 대상은 배경·에셋 생성 PC 1대입니다. 생성 도구는 Codex이며 사용자가 지정한 에이전트 모델은
6.1sol입니다. 이 모델명을 이미지 생성 API 모델 ID로 임의 변환하지 않습니다.
UE 조립·시연 실행 연결은 이후 작업입니다. `generation`은 인증·상태 보고와 배경·에셋 작업 임대·결과 등록을 지원합니다.

초기 현장 하드웨어는 생성 PC 1대·UE 제작 PC 2대·시연 PC 1대의 총 4대로 확정했습니다.
공유 저장소는 생성 PC의 전용 디스크 폴더에 두고 UE 제작은 N대로 확장합니다.
이는 [배치 결정](../docs/COMMUNICATION_PIPELINE.md)이며 실제 UE·SMB 연결 완료를 뜻하지 않습니다.

## 접속 구조

생성 PC → HTTPS 프록시(Caddy) → FastAPI → PostgreSQL / RabbitMQ / Celery.
API와 DB·브로커는 기존 Compose 구성입니다. HTTPS 프록시만 추가하며 DB·RabbitMQ 포트는 외부에 열지 않습니다.
기존 운영 API의 조회 요청에도 운영자 토큰이 필요합니다. HTTPS 공개 경로는 `/health`, `/ready`, `/api/*`,
`/infra/*`이고 웹 화면·API 문서는 노트북의 localhost 주소에서 확인합니다.

`generation` 토큰은 자기 노드의 인증·heartbeat·배경/에셋 작업에 사용합니다. 운영자 토큰과 UE 조립 노드 토큰을
생성 PC에 복사하지 않습니다. Codex 자동 실행은 후속이며 생성 결과는 명시적인 완료 명령으로 등록합니다.
현재 기존 Celery의 진단·초안 기획은 작동하지만 이미지 생성 완료와 같은 의미가 아닙니다.

## 서버 시작

`.env.infra`는 기존 DB 비밀번호와 노드 토큰입니다. 다시 생성하거나 DB 비밀번호를 임의 교체하지 않습니다.
`.env.https`에는 서버에 맞는 다음 값을 둡니다. 예시는 실제 설치에서 생성한 파일을 확인합니다.

```dotenv
CENTRAL_HOST=70.12.246.252
CENTRAL_HTTPS_BIND=70.12.246.252
CENTRAL_HTTPS_PORT=8443
```

저장소 루트에서 실행합니다.

```powershell
docker compose --env-file .env.infra --env-file .env.https -f infra/central/compose.yaml -f infra/central/compose.https.yaml -f infra/central/compose.laptop.yaml up -d --no-build --wait
```

이미 배포된 노트북에서는 위 명령으로 검증된 이미지를 다시 시작합니다. 코드 변경을 반영할 때는
`uv run python infra/local_pipeline.py --deploy`로 검사 후 새 이미지를 배포합니다.
선택한 이미지 태그는 `.env.infra`의 `APP_IMAGE`에 저장됩니다. 새 EC2에서는 `RESTORE.txt`의 이미지 빌드를 먼저 수행합니다.

`compose.https.yaml`은 노트북과 Linux EC2에서 공통으로 씁니다. Caddy의 내부 CA는 별도 볼륨에 영속 저장합니다.
생성 PC에는 공개 CA 인증서만 전달하고 CA 개인키는 중앙 서버에만 둡니다. 인증서 검증을 끄지 않습니다.
노트북 IP가 바뀌면 `.env.https`의 호스트·바인딩, Windows 방화벽 규칙, 각 PC의 `server.json` 주소를 갱신합니다.

## 생성 PC 연결

발급된 접속 ZIP을 해당 PC에만 전달하고 압축을 풉니다. 현재 전달 파일은 노트북의
기존 접속용 `Downloads/Ranel-Generation-1-Connection.zip`과 새 작업용 `Downloads/Ranel-Generation-1-Work.zip`입니다.
Python 3.10 이상이 필요하며 추가 pip 패키지는 없습니다. 작업용 키트의 전체 명령은 [현장 실행 절차](./WORKFLOW.md)에 있습니다.

```powershell
python client.py --check
python client.py --watch
```

`--check`는 인증서·중앙 준비 상태·등록된 ID와 역할을 확인합니다. 노드가 실제 작업 중이라고 보고하지 않습니다.
`--watch`는 시작할 때 등록하고 30초마다 heartbeat를 보냅니다. 종료는 Ctrl+C입니다.
작업용 키트는 임대한 작업의 소유권도 갱신합니다. Codex는 별도로 실행합니다.
작업 배정은 `--claim`, 결과 등록은 `--complete`로 명시적으로 요청하며, heartbeat만으로 생성 작업을 실행하지 않습니다.
서버 주소는 `server.json`, 노드별 토큰은 `node.secret.json`에 분리합니다. EC2로 옮기면 주소만 바꿀 수 있습니다.
현재 Caddy CA를 EC2에 이관하지 않는 경우에는 새 공개 CA 파일도 교체해야 합니다.

노트북 방화벽은 관리자 PowerShell 7(`pwsh`)에서 `Configure-LaptopFirewall.ps1`로 관리합니다.
현재 `70.12.246.252:8443`에 대해 생성 PC `70.12.245.47`과 UE 제작 PC `70.12.246.196`의 허용 규칙과
나머지 원격 IP의 차단 규칙을 적용했습니다.
노트북 자신의 주소는 자체 점검을 위해 차단에서 제외합니다. 기존 Docker의 광범위 허용 규칙이 있어도 이 포트는
차단 규칙이 우선하며 다른 Docker 포트의 기존 규칙은 바꾸지 않습니다.
노드 IP를 추가할 때는 기존 IP를 포함한 전체 목록으로 아래 스크립트를 다시 실행합니다.
기존 규칙을 갱신할 때는 `Set-NetFirewallRule`의 `NewDisplayName`을 사용합니다.

```powershell
.\infra\Configure-LaptopFirewall.ps1 -ServerAddress 70.12.246.252 -ClientAddresses @('70.12.245.47','70.12.246.196') -Port 8443
```

Windows PowerShell 5.1의 스크립트 실행 정책은 변경하지 않았습니다. 이 PC에서는 PowerShell 7로 적용했습니다.
기관 Wi-Fi의 단말 간 차단은 이 규칙으로 해제할 수 없습니다. 다른 PC의 실제 접속 검사는 그 PC에서 `--check`로 확인합니다.
보안 프로그램·기관 정책은 변경하지 않습니다.

## 노트북 운영

Docker Desktop이 실행되어 있어야 하며 전원·네트워크·절전 상태에 따라 중앙 접속이 끊길 수 있습니다.
전원 어댑터를 연결하고 임시 서버를 쓰는 동안 절전으로 들어가지 않도록 운영합니다.
OS 재부팅 뒤에는 위 시작 명령으로 상태를 확인합니다. `restart: unless-stopped`는 Docker가 켜진 뒤 적용됩니다.
대용량 배경·에셋은 현장 디스크/공유 폴더에 남깁니다. SMB 공유 설정은 이 HTTPS 연결과 별도입니다.

## EC2로 이전

1. 생성 PC의 heartbeat 프로그램과 신규 요청을 잠시 멈춥니다. 진행 중인 작업을 완료합니다.
2. `backup_central.py --output <새 백업 폴더> --leave-stopped`로 마지막 스냅샷을 만듭니다.
   기본 실행은 백업 후 원래 실행 중이던 서비스를 재개합니다. 최종 이전에서는 `--leave-stopped`로
   기존 중앙 API/Worker/Dispatcher/HTTPS를 멈춘 채 유지해 두 서버의 동시 처리를 방지합니다.
3. 백업 폴더에는 PostgreSQL dump, 중앙 `/data`, Caddy CA, 서버 전용 환경 파일, 현재 코드 스냅샷이 있습니다.
   노드에 배포할 파일이 아니며 SSH/SCP로 새 서버의 제한된 디렉터리에만 전달합니다.
4. EC2의 OS·Docker·디스크·SSH를 확인하고 코드 스냅샷을 풉니다. 서버 환경 파일은 권한 600으로 보관합니다.
5. `.env.https`의 `CENTRAL_HOST`를 EC2 주소로, `CENTRAL_HTTPS_BIND`를 `0.0.0.0`으로 설정합니다.
   EC2 보안 그룹은 현장의 공인 출구 IP에서 HTTPS 포트로 접속하는 것만 허용합니다.
   현장 PC의 내부 주소와 공인 출구 IP가 같다고 가정하지 않습니다.
6. **비어 있는 새 환경**에서 DB·데이터·CA를 복원합니다. 자세한 명령은 백업에 포함된 `RESTORE.txt`를 따릅니다.
   운영 중인 DB 위에 덮어쓰지 않습니다. RabbitMQ 메시지는 옮기지 않으며 DB의 대기 작업을 Dispatcher가 재전송합니다.
7. HTTPS·인증·DB·실제 Celery 진단 작업을 확인한 뒤 생성 PC의 `server.json` 주소를 새 주소로 변경합니다.
   새 서버에서 heartbeat를 확인할 때까지 기존 노트북의 데이터 볼륨은 유지합니다.

신규 IP에 맞는 서버 인증서는 기존 CA로 Caddy가 발급합니다. 외부 공개 도메인·공인 인증서 도입은 별도 선택입니다.
이번 이관은 동일 코드·동일 PostgreSQL 16 스키마를 기준으로 합니다. 스키마 변경과 데이터 이전을 동시에 하지 않습니다.

## 확인 범위

서버 자체 HTTPS 테스트와 Docker 내부에서 서버 IP로 접근하는 테스트는 실제 다른 PC에서의 통신을 대신하지 않습니다.
생성 PC 접속 완료는 해당 PC에서 반환한 `--check` 결과와 중앙 heartbeat로 확인합니다.
현재 서버 자체 검사와 백업 DB의 격리 복원 검사는 통과했습니다. 생성 PC 실접속은 사용자 요청에 따라 나중에 확인합니다.

근거: [Caddy 내부 HTTPS](https://caddyserver.com/docs/automatic-https#local-https),
[IP 접속 시 기본 인증서 이름](https://caddyserver.com/docs/caddyfile/options#default-sni),
[Windows 방화벽 규칙](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule).
