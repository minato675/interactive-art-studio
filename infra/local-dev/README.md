# local-dev — 한 PC의 로컬 개발용 Docker

SQLite를 사용하는 단일 개발 서버입니다. Compose 프로젝트는 `a504-docker-local`, 기본 컨테이너는
`a504-docker-local-central-1`입니다. PostgreSQL·RabbitMQ 중앙 서버는 [central/](../central/README.md),
전체 구성 선택은 [infra 안내](../README.md)를 따릅니다.
기존 `infra/docker/.env`를 사용하던 PC는 그 파일을 `infra/local-dev/.env`로 옮겨 같은 토큰을 유지합니다.
컨테이너 이름·포트·볼륨은 변경하지 않았습니다.

작품 생성 검증은 `prepared`를 기대합니다. TD·UE 모든 필수 후보 파일이 준비된 상태이며 자동 전시 적용은 하지 않습니다. 두 활성 포인터 유지와 실제 Ready·Applied 구분은 [제작 정책](../../docs/refactoring/creation-policy.md)을 따릅니다.

이 로컬 개발 구성은 TD 담당자가 정한 `RENDERER=both`를 사용합니다. `prepared`는 모든 필수 후보 파일 준비이며 실제 `Applied`는 아닙니다. 기본 검증 스크립트는 인증 없는 조회·제작이 401인지도 확인합니다. 분산 PostgreSQL Compose의 기존 `RENDERER=ue`와 구분합니다.

Jira: [S15P31A504-5 — Docker 실행 환경 구성](https://ssafy.atlassian.net/browse/S15P31A504-5)

중앙 서버와 DB 접근 코드를 Linux 컨테이너 안에서 실행합니다. Windows의 Python·uv·SQLAlchemy `.pyd` 파일을 사용하지 않습니다. Docker 이미지 안에서 기존 `uv.lock`으로 Linux용 패키지를 설치합니다.

요청 대화의 Code Integrity 이벤트는 `msedge.exe`와 `f_lph.dll`에 관한 기록입니다. 이것만으로 pytest 또는 SQLAlchemy가 차단됐다고 확정할 수는 없습니다. 이 구성은 Linux 실행 경로를 제공하며, 해당 PC의 보안 설정을 변경하지 않습니다.

## 준비

- Docker Desktop이 설치된 Windows에서는 **Linux containers** 엔진을 실행합니다. Docker Engine과 Compose 플러그인이 있는 Linux에서도 같은 Compose 파일을 사용합니다.
- 아래 명령은 `pyproject.toml`이 있는 프로젝트 루트에서 실행합니다.
- 최초 빌드에는 이미지와 패키지를 내려받을 네트워크 연결이 필요합니다.
- `docker version`에 Client와 Server가 모두 나오고, `docker compose version`이 성공해야 합니다.

## Windows에서 시작

PowerShell에서 최초 한 번 로컬 설정을 만듭니다. 토큰은 웹 화면과 운영 앱의 쓰기 요청에 사용하며 Git이나 전달 ZIP에 포함하지 않습니다.

```powershell
if (-not (Test-Path 'infra/local-dev/.env')) {
    $dockerToken = [guid]::NewGuid().ToString('N')
    "OPERATOR_TOKEN=$dockerToken`nCENTRAL_PORT=8001" |
        Set-Content -LiteralPath 'infra/local-dev/.env' -Encoding ascii
}

docker compose --env-file infra/local-dev/.env -f infra/local-dev/compose.yaml up --build -d --wait central
```

설정을 직접 관리하려면 `local.env.example`을 `.env`로 복사하고, `OPERATOR_TOKEN`을 32자 이상의 임의 영문·숫자로 채웁니다. 토큰을 바꾸면 서버를 다시 생성하고 운영 앱에도 새 값을 넣습니다.

- 웹 운영 화면: <http://127.0.0.1:8001>
- API 문서: <http://127.0.0.1:8001/docs>
- 상태 확인: <http://127.0.0.1:8001/health>

웹 화면의 **운영자 토큰** 칸에는 `.env`의 `OPERATOR_TOKEN` 값을 넣습니다. 기본 포트 8001이 사용 중이면 `.env`의 `CENTRAL_PORT`를 바꿉니다. Docker 서버와 기존 `uv run` 서버는 DB를 공유하지 않습니다.

Linux에서는 `cp -n infra/local-dev/local.env.example infra/local-dev/.env`로 파일을 준비하고 토큰을 채운 뒤 같은 `docker compose` 명령을 실행합니다.

## pytest와 SQLAlchemy 확인

호스트에서 `uv run pytest`를 실행할 필요가 없습니다. 테스트 이미지에는 모든 workspace 패키지와 PySide6가 들어 있어 운영 앱 테스트도 실행됩니다. Qt는 `offscreen` 모드로 동작하므로 Windows 창을 만들지 않습니다.

```powershell
# SQLAlchemy Linux 확장 모듈 + SQLModel/SQLite 저장·조회 + 실행 중인 서버의 읽기 API
docker compose --env-file infra/local-dev/.env -f infra/local-dev/compose.yaml exec -T central python infra/local-dev/verify.py

# 전체 pytest
docker compose --env-file infra/local-dev/.env -f infra/local-dev/compose.yaml build tests
docker compose --env-file infra/local-dev/.env -f infra/local-dev/compose.yaml run --rm --no-deps tests

# 코드 검사
docker compose --env-file infra/local-dev/.env -f infra/local-dev/compose.yaml run --rm --no-deps tests uv run --no-sync ruff check .

# controller 자체 테스트
docker compose --env-file infra/local-dev/.env -f infra/local-dev/compose.yaml run --rm --no-deps -e PYTHONPATH=controller/src:controller/tests tests uv run --no-sync python -m unittest discover -s controller/tests
```

`tests`는 별도 profile에 있어 서버를 시작할 때 자동으로 실행되지 않습니다. 위처럼 서비스를 명시한 `run` 명령으로 실행합니다. 테스트 컨테이너에는 운영 DB 볼륨과 운영자 토큰을 전달하지 않습니다.
controller의 CLI 테스트가 쓰는 `controller/runtime-data/`는 테스트 이미지 안에서만 쓰기 가능하게 만들며, 컨테이너를 삭제하면 함께 사라집니다.

## Windows 운영 앱 연결

GUI 앱은 Windows에서 실행하고 HTTP로 Docker 서버에 연결합니다. 운영 앱 자체가 Windows에서 실행 가능한 환경에서 사용합니다. Windows Python 확장 차단 문제가 있으면 우선 위 웹 화면으로 사용합니다.

운영 앱이 설치된 저장소의 PowerShell에서 다음 값을 설정합니다. `.env`의 실제 토큰을 사용합니다.

```powershell
$env:CENTRAL_URL = 'http://127.0.0.1:8001'
$env:OPERATOR_TOKEN = '<infra/local-dev/.env의 OPERATOR_TOKEN 값>'
uv run --package a504-operator a504-operator
```

기존에 켜 둔 앱에는 새 환경 변수가 적용되지 않으므로 새로 실행한 창을 사용합니다.

## 상태·로그·중지

```powershell
docker compose --env-file infra/local-dev/.env -f infra/local-dev/compose.yaml ps
docker compose --env-file infra/local-dev/.env -f infra/local-dev/compose.yaml logs --tail 100 central
docker compose --env-file infra/local-dev/.env -f infra/local-dev/compose.yaml down
```

`down` 뒤에도 DB와 생성 파일은 `a504-docker-local_runtime-data` 볼륨에 남습니다. 다시 `up`하면 이어서 사용합니다. `down -v`는 이 데이터를 삭제하므로 초기화가 필요한 경우에만 사용합니다.

생성·전달 파일을 호스트에 복사할 때:

```powershell
docker compose --env-file infra/local-dev/.env -f infra/local-dev/compose.yaml cp central:/data/delivery ./docker-delivery
docker compose --env-file infra/local-dev/.env -f infra/local-dev/compose.yaml cp central:/data/delivery_td ./docker-delivery-td
```

해당 렌더러로 생성한 기록이 있어야 복사할 폴더가 생깁니다. 컨테이너에서 만든 JSON과 이미지가 Windows UE에 자동으로 게시되는 구성은 아닙니다.

## 구성과 현재 범위

| 파일 | 역할 |
|---|---|
| `Dockerfile` | digest로 고정한 Python 3.12·uv 기반 Linux 이미지. `runtime`과 `test` 빌드 단계 |
| `compose.yaml` | 로컬 서버, 데이터 볼륨, 상태 검사, 테스트 서비스 |
| `local.env.example` | 로컬 토큰·포트 설정 예시 |
| `start.py` | 토큰 설정을 확인한 뒤 central 실행 |
| `verify.py` | Linux 확장 모듈, DB 저장·조회, HTTP 응답 검사 |
| `VALIDATION.md` | 실제 검증 결과와 미검증 범위 |

- 서버와 테스트 모두 일반 사용자 UID 10001로 실행합니다. 호스트에는 `127.0.0.1` 포트만 게시합니다.
- workspace 패키지는 이미지에 복사한 `/app` 소스에서 읽습니다. controller가 소스 위치를 기준으로 찾는 `contracts/`와 그 안의 `catalogs/` 파일도 함께 포함하며, 호스트 소스를 마운트하지 않습니다.
- Windows에서 복사한 Python 소스의 실행 비트를 이미지 안에서 해제합니다. 테스트 캐시는 `/tmp`에 기록하므로 일반 사용자로 Ruff와 pytest를 실행할 수 있습니다.
- DB는 현재 코드가 사용하는 **SQLite**이며, SQLAlchemy·SQLModel은 같은 central 컨테이너에 있습니다. PostgreSQL로의 전환은 포함하지 않습니다.
- `LLM_PROVIDER=none`, `LLM_FALLBACK` 빈 값으로 실행합니다. GMS·Ollama 호출 없이 규칙과 로컬 소재 생성기를 사용합니다.
- UE·TouchDesigner, GPU 모델, 제작·시연 Agent, EC2 운영 배포는 포함하지 않습니다.
- 기본 `RENDERER=both`는 저장소의 기존 동작을 따릅니다. 실제 UE/TD 프로그램의 재생 성공까지 의미하지 않습니다.

## 문제가 생기면

| 증상 | 확인 |
|---|---|
| Docker named pipe 또는 daemon 연결 오류 | Docker Desktop의 Linux 엔진이 실행 중인지 `docker version`으로 확인 |
| `OPERATOR_TOKEN` 관련 오류 | `.env` 파일 생성과 32자 이상 토큰 설정, `--env-file` 경로 확인 |
| 주소가 이미 사용 중 | `.env`의 `CENTRAL_PORT` 변경 후 다시 `up` |
| 웹에서 생성 요청이 401 | 웹 토큰 칸에 `.env`와 같은 값 입력 |
| `pytest`에서 Qt 관련 오류 | `test` 이미지를 사용했는지 확인. `runtime`에는 Qt가 없음 |
| 코드를 수정했는데 결과가 같음 | 소스 bind mount를 사용하지 않으므로 `up --build` 또는 `build tests`로 재빌드 |

오류를 전달할 때는 실패한 명령, 종료 코드, `docker version`, 빌드·테스트 로그를 함께 남깁니다. `.env`와 `docker compose config`의 전체 출력에는 토큰이 포함될 수 있으므로 공유하지 않습니다.

## 참고

- [uv Docker 가이드](https://docs.astral.sh/uv/guides/integration/docker/): 이미지 내부 설치와 workspace 동기화.
- [Docker Compose profiles](https://docs.docker.com/compose/how-tos/profiles/): 필요할 때만 테스트 서비스 실행.
- [Docker volumes](https://docs.docker.com/reference/compose-file/volumes/): 컨테이너와 별도로 데이터 보존.
- [SQLAlchemy 설치 안내](https://docs.sqlalchemy.org/en/20/intro.html#building-the-cython-extensions): 플랫폼별 wheel에 포함된 네이티브 확장 모듈.
