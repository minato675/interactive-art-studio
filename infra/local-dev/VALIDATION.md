# Docker 실행 검증 기록

이 문서는 검증 당시 `infra/docker/` 경로의 기록입니다. 현재 구성은 `infra/local-dev/`로 이동했으며
새 실행 방법은 [로컬 개발 README](./README.md), 이동 후 재검증은 [공통 검증 기록](../VALIDATION.md)을 따릅니다.

- 검증일: 2026-10-21
- 관련 이슈: [S15P31A504-5 — Docker 실행 환경 구성](https://ssafy.atlassian.net/browse/S15P31A504-5)
- 기준 커밋: `bdc4ed4ca787cd8e8c81eaca9740994a1763bc36` (`develop`)
- 작업 브랜치: `S15P31A504-5-docker-local`

## 환경

| 항목 | 실제 값 |
|---|---|
| 실행 장비 | Windows의 Docker Desktop, WSL2 Linux 엔진 |
| 컨테이너 플랫폼 | `linux/amd64` |
| Docker / Compose | `29.6.1` / `v5.3.0` |
| Python / uv | `3.12.12` / `0.9.30` |
| SQLAlchemy / SQLModel | `2.1.4` / `0.0.48` |
| SQLite | `3.40.1` |
| 공개 주소 | `127.0.0.1:8001` |
| 데이터 볼륨 | `a504-docker-local_runtime-data` |

Python 패키지는 기존 `uv.lock`으로 설치했습니다. 애플리케이션 코드, lock 파일, controller 사본을 수정하지 않았습니다.

## 결과

아래 명령은 프로젝트 루트에서 실행했습니다. 실제 명령의 공통 앞부분은 `docker compose --env-file infra/docker/.env -f infra/docker/compose.yaml`이며, 표에서는 `docker compose …`로 줄였습니다.

| 확인 | 명령 또는 방법 | 결과 |
|---|---|---|
| Compose 설정 | `docker compose … config --quiet` | 통과 |
| 이미지 빌드 | `docker compose … build central tests` | 두 이미지 빌드 성공 |
| central 시작 | `docker compose … up -d --no-build --wait central` | `healthy` |
| 전체 pytest | `docker compose … run --rm --no-deps tests` | **114 passed, 1 warning**, 33.15초 |
| Ruff | `docker compose … run --rm --no-deps tests uv run --no-sync ruff check .` | **All checks passed!** |
| controller unittest | `docker compose … run --rm --no-deps -e PYTHONPATH=controller/src:controller/tests tests uv run --no-sync python -m unittest discover -s controller/tests` | **184개 실행, 181개 통과·3개 건너뜀**, 68.287초 |
| Linux 네이티브 모듈 | `verify.py`로 SQLAlchemy의 `.so` 8개 import | 통과, Windows `.pyd` 없음 |
| SQLModel / SQLite | 임시 DB에 테이블 생성 → 저장 → 새 세션에서 조회 | 통과 |
| HTTP | `/health`, `/`, `/docs`, `/api/families`, `/api/artworks` | 통과, 호스트의 8001 포트에서도 health 확인 |
| 쓰기 인증 | 토큰 없이 작품 생성 API 호출 | 예상대로 `401` |
| 작품 생성 | 토큰으로 규칙 기반 작품 생성 | ID 1, `published`, scene 파일 생성 |
| 영속성 | central 컨테이너를 `--force-recreate`로 교체한 뒤 DB 행·scene SHA-256 비교 | 같은 작품과 파일 유지 |
| 실행 설정 | UID, 파일 시스템, 포트, 이미지 파일 검사 | UID 10001, 루트 읽기 전용, 호스트 loopback 바인딩 |
| 이미지 분리 | runtime 이미지 검사 | Qt·pytest, 호스트 `.env`·`.git`·Windows 바이너리 없음 |

pytest 경고 1개는 기존 FastAPI/Starlette TestClient의 `httpx` 사용 중단 예정 경고입니다. 의존성 변경은 하지 않았습니다.

controller의 건너뛴 3개는 `ART_RUNTIME_ROOT`를 지정했을 때 실행하는 UE 예제 장면·프리셋 동기화 검사입니다. 실제 UE와 연동했다는 의미는 아닙니다.

## 영속성 검사 재현

다음 검사는 현재 Docker 서버에 테스트용 작품 한 건을 만듭니다.

```powershell
docker compose --env-file infra/docker/.env -f infra/docker/compose.yaml exec -T central python infra/docker/verify.py --create-smoke-artwork
docker compose --env-file infra/docker/.env -f infra/docker/compose.yaml up -d --force-recreate --no-build --wait central
docker compose --env-file infra/docker/.env -f infra/docker/compose.yaml exec -T central python infra/docker/verify.py --check-persistence
```

## 전달 파일의 검증 로그

전달 ZIP의 `evidence/`에 다음 로그를 포함합니다. 토큰과 `.env`는 포함하지 않습니다.

- `docker-build.log`: 이미지 빌드와 최종 변경분 빌드.
- `central-start.log`: 서버 기동.
- `pytest.log`, `ruff.log`, `controller-tests.log`: 최종 이미지의 검사 결과.
- `smoke-create.log`, `persistence.log`: DB·작품 생성·컨테이너 교체 후 보존.
- `runtime-audit.log`, `versions.log`: 실행 사용자·포트·파일 포함 여부와 버전.

## 확인하지 않은 범위

- 문제가 발생했던 팀원 PC에서의 재현과 Windows DLL 차단 원인. 전달받은 Code Integrity 이벤트만으로 SQLAlchemy 차단을 확정할 수 없습니다.
- EC2 배포, 외부 공개, HTTPS, 운영 백업·복구. 이슈의 EC2 검증 조건은 남아 있습니다.
- ARM64 장비에서의 빌드와 실행.
- 실제 GMS·Ollama·GPU 모델 호출, UE·TouchDesigner 재생, 제작·시연 Agent.
- Windows PySide6 GUI의 Docker 서버 연결을 통한 수동 조작. 운영 앱 테스트는 Linux의 Qt `offscreen` 모드로 통과했습니다.

실행과 중지, 운영 앱 연결, 데이터 복사 방법은 [README.md](./README.md)에 있습니다.
