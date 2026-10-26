# 팀원 운영 접속

현재 접속·로그인은 [계정 접속 안내](OPERATOR_LOGIN.md)를 따릅니다.
운영 주소는 https://k15a504.p.ssafy.io (443), 배포 브랜치는 develop입니다.
8444 테스트 환경은 폐기합니다. 아래 내용은 전환 이전 이력이며 신규 PC 설정에 사용하지 않습니다.

<details><summary>전환 이전 8444·토큰 접속 이력</summary>

# 테스트·운영 환경 접속과 확인 절차

팀원이 자신의 Windows PC에서 EC2에 배포된 중앙 서버를 확인하는 안내입니다.
서버를 PC에 새로 띄울 필요 없이 PySide6 운영 앱이 EC2의 FastAPI에 연결합니다.
아래 주소·포트는 2026-10-22 실서버 기준입니다. 현재 운영은 HTTPS 기본 포트 **443**, 테스트는 **8444**입니다.

## 1. 환경 구분

| 항목 | 테스트 (staging) | 운영 (production) |
|---|---|---|
| 접속 주소 | `https://k15a504.p.ssafy.io:8444` | `https://k15a504.p.ssafy.io` (443) |
| 배포 | 보호된 develop 병합 → 검사 성공 → `deploy_staging` 자동 실행 | main 릴리스 반영 → 검사 성공 → `deploy_ec2` 수동 실행 |
| 서버 경로 | `/opt/ranel-staging` | `/opt/ranel` |
| Compose 프로젝트 / 컨테이너 접두사 | `ranel-staging` / `ranel-staging-` | `ranel` / `ranel-` |
| 서버 내부 API | `127.0.0.1:18081` | `127.0.0.1:18080` |
| DB·큐·파일·토큰·CA | 테스트 전용 | 기존 운영 데이터·설정 |

두 환경은 같은 EC2 안에 있지만 DB·RabbitMQ·파일 볼륨과 인증 정보를 따로 사용합니다.
테스트 작업은 8444에서 진행합니다. 테스트에 운영 토큰·노드 키트·DB 덤프를 재사용하지 않습니다.
로컬 개발의 `http://127.0.0.1:8001`은 이 두 환경과 별도입니다.

운영 최초 이관 때 사용했던 8443과 현재 주소를 혼동하지 않습니다. 노트북 임시 서버의 8443 기록은
해당 시점의 기록이며, 현재 EC2 운영 접속 주소는 위 표를 기준으로 합니다.

## 2. 팀원이 준비할 것

1. GitLab 저장소 접근 권한, Windows Python 3.12 이상과 uv.
2. 접속할 환경의 **운영자 토큰**과 **공개 CA 인증서**. 서버 담당자에게 해당 환경용으로 받습니다.
3. 서버의 HTTPS 포트로 연결 가능한 네트워크. 접근이 제한된 망에서는 담당자가 현재 공인 출구 IP의 허용 여부를 확인합니다.
4. 서버 명령까지 실행할 담당자만 SSH 접속 권한이 필요합니다. 앱 사용자는 PEM이나 CI 전용 SSH 키가 필요하지 않습니다.

CA는 서버 인증서를 검증하는 공개 인증서입니다. 토큰과 구분해 전달받고 담당자에게 인증서 지문을 확인합니다.
CA 개인키·GitLab 배포 개인키·서버 전체 `.env`는 팀원 PC용 접속 자료에 포함하지 않습니다.
GMS 토큰도 중앙 서버의 비밀 설정이며 일반 운영 앱에는 넣지 않습니다.

저장소를 처음 받는 PC에서는 다음을 실행합니다.

```powershell
git clone https://lab.ssafy.com/s15-final/S15P31A504.git
cd S15P31A504
git switch develop
git pull --ff-only
uv sync --locked --all-packages
```

이미 작업 중인 저장소가 있으면 수정 중인 파일을 보존하고 팀의 브랜치 운영 규칙에 따라 갱신합니다.
운영자가 앱 버전을 고정해 안내한 경우에는 해당 검증 버전을 사용합니다.
현재 안내는 소스에서 실행하는 방식이며 설치형 exe 배포 완료를 뜻하지 않습니다.

## 3. 다른 팀원 PC에서 테스트 앱 실행

받은 테스트용 공개 CA를 `%USERPROFILE%\RanelAccess\staging-ca.crt`에 둡니다.
새 PowerShell을 열고 저장소 루트에서 다음을 실행합니다.
토큰은 숨겨진 입력란에 입력하므로 명령 기록에 토큰 본문을 적지 않습니다.

```powershell
$env:CENTRAL_URL = 'https://k15a504.p.ssafy.io:8444'
$env:SSL_CERT_FILE = Join-Path $env:USERPROFILE 'RanelAccess\staging-ca.crt'
if (-not (Test-Path -LiteralPath $env:SSL_CERT_FILE)) { throw '테스트 CA 파일 경로를 확인하세요.' }
$stagingToken = Read-Host '테스트 운영자 토큰' -AsSecureString
$env:OPERATOR_TOKEN = ([PSCredential]::new('ranel-staging', $stagingToken)).GetNetworkCredential().Password
$stagingToken.Dispose()

try {
    # API와 PostgreSQL 준비 상태 확인. 토큰이 필요 없는 조회입니다.
    curl.exe --fail --show-error --cacert "$env:SSL_CERT_FILE" "$env:CENTRAL_URL/ready"
    if ($LASTEXITCODE -ne 0) { throw 'HTTPS 연결 또는 서버 준비 상태를 확인하세요.' }
    uv run --all-packages a504-operator
} finally {
    Remove-Item Env:OPERATOR_TOKEN, Env:CENTRAL_URL, Env:SSL_CERT_FILE -ErrorAction SilentlyContinue
}
```

`/ready`의 정상 응답은 `{"ok":true,"database":"postgresql"}`입니다.
이 응답만으로 RabbitMQ·Worker·생성 PC·UE까지 정상이라고 판단하지 않습니다.

운영 앱 접속은 **운영용 CA·운영용 토큰**을 받고 별도 PowerShell에서 같은 절차를 사용합니다.
`CENTRAL_URL`은 `https://k15a504.p.ssafy.io`, `SSL_CERT_FILE`은 받은 운영 CA 경로로 지정합니다.
포트만 바꾸고 테스트 인증서를 그대로 사용하면 안 됩니다. 운영 데이터 변경은 실제 운영 작업일 때만 수행합니다.

### 기존 담당 노트북의 실행기

아래 실행기는 이미 설정을 받은 담당 노트북에만 있습니다. 팀원 PC에 같은 파일이 있다고 가정하지 않습니다.

```powershell
# 테스트용 앱
& 'C:\Users\SSAFY\RanelMaster\Start-Operator-Staging.ps1'

# 운영용 앱: 테스트 실행기와 별도
& 'C:\Users\SSAFY\RanelMaster\Start-Operator-EC2.ps1'
```

실행기의 비밀 설정 파일은 Git에 포함하지 않습니다. 일반 팀원 PC에서는 위의 공통 설정 절차를 사용합니다.

## 4. 브라우저 접속 시 보이는 결과

| 경로·응답 | 의미 |
|---|---|
| `/` → 404, `API endpoint only` | 정상. 루트 화면은 제공하지 않으며 관리자 조회는 `/infra/monitor/` 사용 |
| `/health` → `{"ok":true}` | API 프로세스 응답 |
| `/ready` → 200, PostgreSQL | API에서 DB에 연결 가능 |
| 토큰 없이 `/api/*`·`/infra/jobs` → 401 | 보호 API가 인증되지 않은 요청을 차단함 |
| `/infra/monitor/` → 관리자 조회 화면 | 운영자 토큰 입력 후 작업·노드·시연 상태 조회 |
| 외부 `/docs` → 404 | 현재 gateway가 Swagger 문서를 외부에 전달하지 않음 |

현재는 사설 CA를 사용합니다. 일반 브라우저에 신뢰 설정이 없다면 인증서 경고가 나타날 수 있습니다.
준비된 앱 또는 `--cacert`를 지정한 명령으로 검증합니다. TLS 검증을 끄거나 경고를 우회하지 않습니다.
API 규약은 [통신 API 문서](COMMUNICATION_API.md)를 참고합니다.

## 5. 테스트 순서와 합격 기준

### 5.1 확인할 커밋의 배포 완료

1. [GitLab Pipelines](https://lab.ssafy.com/s15-final/S15P31A504/-/pipelines)에서 브랜치가 develop인 파이프라인을 선택합니다.
2. 확인하려는 커밋 SHA와 파이프라인 SHA가 일치하는지 봅니다.
3. `lint`, 네 테스트, `build-smoke`, **`deploy_staging`까지 성공**했는지 확인합니다.
   MR 파이프라인 성공만으로 테스트 서버가 갱신됐다고 판단하지 않습니다.
4. 배포 실패 시에는 직전 성공 버전이 실행될 수 있으므로 새 기능을 시험하기 전에 배포 로그를 확인합니다.

서버 담당자는 다음으로 실제 적용 버전을 조회할 수 있습니다. 모두 조회 명령입니다.

```bash
sudo cat /opt/ranel-staging/current-image
sudo readlink -f /opt/ranel-staging/current
sudo docker inspect --format '{{.Config.Image}}' ranel-staging-central-1
```

CI 배포 성공 이후 세 결과의 커밋 SHA가 일치해야 합니다.

### 5.2 운영 앱의 연결·초안·기록

1. 테스트용 앱을 실행해 표현 방식 목록과 작품 기록을 확인합니다.
2. `Shift+Tab`으로 **편집 모드**로 바꿉니다. 예: `푸른 바다의 물고기 떼가 고래의 윤곽을 드러내는 장면`.
3. `제출 — 편집 모드`를 누르고 초안과 단계 기록, `원본 데이터 보기`를 확인합니다.
4. 초안의 입력 대기 상태(`awaiting`)와 작품 ID를 기록합니다. 앱을 다시 열어 기록을 조회할 수 있는지 확인합니다.

이 과정은 `/api/drafts`를 사용하는 기존 초안 경로입니다. 현재 기본 `LLM_PROVIDER=none`에서는
규칙으로 초안을 만듭니다. 화면의 「AI 초안」 문구만으로 실제 GMS 호출을 검증했다고 기록하지 않습니다.
`이 내용으로 진행` 이후의 파일 준비도 실제 UE 실행·전시 적용과 구분합니다.

### 5.3 실제 비동기 작업 완료 — 서버 담당자

SSH 권한이 있는 담당자가 EC2에 접속합니다. 예시 키 경로를 자신의 관리 키 경로로 바꿉니다.
첫 접속 시 서버의 SSH 호스트 키 지문을 담당자가 확인한 값과 비교합니다.

```powershell
ssh -i "$env:USERPROFILE\.ssh\K15A504T.pem" ubuntu@k15a504.p.ssafy.io
```

아래는 **접속 후 EC2의 Bash에서** 실행합니다. 테스트용 진단·수동 초안 작업 2건을 새로 저장합니다.
외부 AI를 호출하지 않으며, CI 배포가 성공해 `/opt/ranel-staging/current`가 있는 서버 기준입니다.

```bash
sudo docker exec -i ranel-staging-central-1 \
  python - --env /dev/null --url http://127.0.0.1:8000 \
  < /opt/ranel-staging/current/infra/central/smoke.py
```

`diagnostic`과 `draft` 모두 `"status": "succeeded"`여야 합니다.
이는 FastAPI → PostgreSQL 작업 등록 → Dispatcher → RabbitMQ → Celery Worker → PostgreSQL 결과 저장을 확인합니다.
내부 smoke 성공과 3절의 외부 HTTPS 접속 성공을 함께 확인합니다.

### 5.4 외부 AI·현장 장비 검사는 별도

- GMS 키 등록·모델/엔드포인트 검증과 실제 판단 호출은 [Head 운영 문서](../infra/central/HEAD_NODE.md)를 따릅니다.
  일반 CI와 위의 비동기 smoke는 GMS나 직접 OpenAI를 호출하지 않습니다.
  배포 환경 staging/production과 Head 요청의 `profile`은 서로 다른 설정입니다. 현재 코드의
  `profile: production`은 GMS 경로이고 `profile: test`는 과거 직접 OpenAI 테스트 경로입니다.
  GMS 전환 확인에 `profile: test`를 사용하지 않습니다. 2026-10-22 운영에 GMS 키를 등록하고
  실제 구조화 응답을 확인했으며 직접 OpenAI 키는 제거했습니다. 테스트 환경의 유료 키는 별도 설정 대상입니다.
- 테스트 노드가 0대이면 노드 연결 전 상태입니다. 생성/UE/시연 장비에는 테스트 환경용 ID·토큰·CA를 별도로 준비합니다.
- 실제 배경·에셋 생성, UE 조립, 시연 Ready·Applied·화면 출력은 해당 장비에서 추가로 확인합니다.
  `prepared`·`generation_complete`만으로 실제 전시 적용이나 무중단 전환을 통과 처리하지 않습니다.

## 6. 상태·로그와 장애 확인

EC2에서 테스트 환경만 조회합니다. 운영은 컨테이너 접두사가 `ranel-`이며 조회 대상을 구분합니다.

```bash
sudo docker ps --filter label=com.docker.compose.project=ranel-staging
sudo docker logs --tail 100 ranel-staging-central-1
sudo docker logs --tail 100 ranel-staging-worker-1
sudo docker logs --tail 100 ranel-staging-dispatcher-1
sudo docker logs --tail 100 ranel-staging-gateway-1
```

| 증상 | 확인할 내용 |
|---|---|
| 접속 시간 초과 | 주소·8444/443 구분, PC 인터넷, 현재 공인 출구 IP 접근 허용, 서버 컨테이너/포트 바인딩 |
| 인증서 오류 | 접속 환경의 CA인지, 파일 경로·도메인·시각이 맞는지. 검증 우회 대신 설정 수정 |
| 401 | 운영자 토큰 누락·다른 환경 토큰인지. 노드 토큰은 운영자 토큰과 다름 |
| PowerShell에서 ps1 실행이 차단됨 | 실행 정책을 변경하지 않고 3절의 명령을 콘솔에 직접 입력. 기존 담당 노트북 실행기는 해당 PC에서 허용된 PowerShell로 실행 |
| `/ready` 503 | API의 DB 연결 상태, PostgreSQL 컨테이너 로그 |
| 작업이 queued/running에서 끝나지 않음 | Worker·Dispatcher·RabbitMQ 상태, 작업 ID로 API/Worker 로그와 실패 원인 조회 |
| 새 기능이 보이지 않음 | 해당 SHA의 develop `deploy_staging` 성공 여부, 실제 적용 이미지 SHA |
| 루트 또는 `/docs` 404 | 4절의 외부 API 전용 gateway 정책인지 확인 |

초기화·`down -v`·운영 재배포는 접속 확인 절차에 포함하지 않습니다.
오류를 공유할 때 환경, 커밋 SHA, 작업/작품 ID, 재현 순서, 비밀 값을 가린 오류만 남깁니다.

## 7. 검증 기록 양식

```text
일시 / 확인자 / PC:
환경 / 접속 주소:
GitLab 파이프라인 / 커밋 SHA / deploy_staging 결과:
HTTPS·ready / 앱 목록·기록 / 초안 ID:
비동기 diagnostic·draft 작업 ID / 성공 또는 오류:
외부 AI·실제 노드·UE 검증 여부와 미실행 사유:
```

2026-10-22 기준 [MR !37](https://lab.ssafy.com/s15-final/S15P31A504/-/merge_requests/37) 병합 후
[파이프라인 #236465](https://lab.ssafy.com/s15-final/S15P31A504/-/pipelines/236465)의 7개 job과 EC2 테스트 자동 배포가 성공했습니다.
후속 조회에서 운영 443·테스트 8444의 `/ready` 200, 운영자 인증 200, 무인증 401과 CA 검증을 확인했습니다.
이는 모든 팀원 PC와 실제 UE 화면의 검증을 대신하지 않습니다. main 운영 수동 릴리스의 실제 실행 검증은 별도입니다.


</details>
