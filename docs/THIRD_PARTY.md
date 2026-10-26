# 외부 라이브러리 · 코드 출처

라넬 규칙: 오픈소스는 MIT·Apache·BSD 계열만 씁니다(GPL 금지). 새로 추가하면 이 표에 출처와 라이선스를 적습니다.

## Python 라이브러리 (`apps/*` · `packages/*`의 `pyproject.toml`)

| 이름 | 용도 | 라이선스 |
|---|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | 운영 API, 자동 문서, 운영 화면 제공 | MIT |
| [Starlette](https://github.com/encode/starlette) | FastAPI 기반 (자동 설치) | BSD-3-Clause |
| [SQLModel](https://github.com/fastapi/sqlmodel) | DB 모델 | MIT |
| [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) | SQLModel 기반 (자동 설치) | MIT |
| [Pydantic](https://github.com/pydantic/pydantic) | 입력 검사, LLM 출력 형식 (자동 설치) | MIT |
| [uvicorn](https://github.com/encode/uvicorn) | 서버 실행 | BSD-3-Clause |
| [PySide6](https://doc.qt.io/qtforpython-6/) (`pyside6-essentials`, `shiboken6`) | 운영 PC 앱 화면 (`apps/operator`만) | **LGPLv3 — 위 규칙의 예외.** 배포 조건은 [배포 체크리스트](./architecture/pyside6-distribution-checklist.md) |
| [httpx](https://github.com/encode/httpx) | 운영 앱의 central API 호출 (`apps/operator`), 테스트용 HTTP 클라이언트 | BSD-3-Clause |
| 빌드용: [uv_build](https://github.com/astral-sh/uv) | `apps/*` · `packages/*`를 패키지로 설치 (`uv sync`가 자동 사용) | MIT 또는 Apache-2.0 |
| 개발용: [pytest](https://github.com/pytest-dev/pytest) | 테스트 | MIT |
| 개발용: [ruff](https://github.com/astral-sh/ruff) | 코드 검사 | MIT |
| [Celery](https://github.com/celery/celery/blob/main/LICENSE) | Linux 비동기 작업 실행 | BSD-3-Clause |
| [Kombu](https://github.com/celery/kombu), [amqp](https://github.com/celery/py-amqp), [billiard](https://github.com/celery/billiard), [vine](https://github.com/celery/vine) | Celery 브로커·실행 기반 (자동 설치) | BSD 계열 |
| [pg8000](https://pypi.org/project/pg8000/) | PostgreSQL 연결 드라이버 | BSD-3-Clause |
| [psutil](https://github.com/giampaolo/psutil/blob/master/LICENSE) | 시연 노드의 UE PID·시작 시간 확인, 선택적 프로세스 식별·정지·복구 (studio 공통 모듈에도 직접 의존성 선언) | BSD-3-Clause |
| 개발용: [PyYAML](https://github.com/yaml/pyyaml/blob/main/LICENSE) | CI·Compose 구조 검증 | MIT |

전체 의존성과 정확한 버전은 `uv.lock`에 있습니다(`uv tree`로 확인).

## 저장소 안 사본 · 외부 도구

### 제작노드 이미지 스킬

`.agents/skills/imagegen`은 2026-10-21 기준 OpenAI Codex에 설치된 번들 스킬의 사본입니다.
Apache-2.0 라이선스 원문은 [LICENSE.txt](../.agents/skills/imagegen/LICENSE.txt)에 보존합니다.
`scripts/image_gen.py`와 `scripts/remove_chroma_key.py`는 수정하지 않으며,
저장소의 Ruff 검사에서는 이 외부 스크립트 두 파일만 제외합니다.
두 파일의 구문 검사와 이미지 생성 CLI help·네트워크 없는 dry-run은 별도로 확인합니다.
실제 이미지 생성과 크로마키 처리에 필요한 `openai`·`Pillow`는
이 변경으로 프로젝트 의존성에 추가하지 않습니다.

`ranel-ue-media-art`와 `ue-led-media-art`는 이 프로젝트를 위해 작성한 제작 지침의 스냅샷입니다.
참고 자료의 공개 출처 URL은 원본 미디어의 재배포 허가를 뜻하지 않으며, 회사 참고 이미지 바이너리는 동봉하지 않습니다.
배포 파일별 해시와 원본 대비 수정 여부는 `.agents/skills/distribution.json`에 기록합니다.

### 기존 사본과 실행 도구

| 이름 | 위치 | 출처 | 라이선스 |
|---|---|---|---|
| 라넬 interactive-controller | `controller/` | lanael-studio/interactive-controller | 라넬 코드 (팀 저장소 사용 허락받음) |
| 라넬 interactive-runtime (코드만) | `ue/runtime/` | lanael-studio/interactive-runtime `a5f0290` | 라넬 코드 (팀 저장소 사용 허락받음) |
| 라넬 interactive-sensors (TD 프로젝트) | `td/project/` | lanael-studio/interactive-sensors `06cdf0c` | 라넬 코드 (팀 저장소 사용 허락받음) |
| MediaPipe TouchDesigner (`MediaPipe.tox`) | 저장소에 없음, 받아서 사용 | [torinmb/mediapipe-touchdesigner](https://github.com/torinmb/mediapipe-touchdesigner) v0.5.3 | MIT |
| MediaPipe (위 tox 안의 손 추적 모델) | 위 tox에 포함 | [google-ai-edge/mediapipe](https://github.com/google-ai-edge/mediapipe) | Apache-2.0 |
| Ollama (선택, 로컬 LLM 실행기) | PC에 따로 설치 | [ollama/ollama](https://github.com/ollama/ollama) | MIT |
| SSAFY GMS (선택, 외부 LLM API) | 설치 없음, HTTP로 호출 (`studio/head/llm.py`) | gms.ssafy.io (OpenAI Responses API 중계, 기본 모델과 전시 디렉터 모두 `gpt-6.1-sol`) | SSAFY 제공 서비스. 사용한 토큰만큼 크레딧 차감, 라이브러리 추가 없음 |
| PostgreSQL | Compose 별도 서버 | [PostgreSQL](https://www.postgresql.org/about/licence/) | PostgreSQL License (허용적 라이선스) |
| RabbitMQ | Compose 별도 브로커 | [RabbitMQ LICENSE](https://github.com/rabbitmq/rabbitmq-server/blob/main/LICENSE) | MPL-2.0. 사용자가 확정한 스택이며 MIT·Apache·BSD만 허용한다는 일반 규칙과 별도의 채택 기록이 필요함 |
| Docker Engine/Compose, BuildKit | 로컬·서버·CI 실행 도구 | [Moby](https://github.com/moby/moby), [Compose](https://github.com/docker/compose), [BuildKit](https://github.com/moby/buildkit) | Apache-2.0. Docker Desktop의 이용 조건은 별도 |
| Caddy | 중앙 API 앞의 HTTPS 프록시, 내부 인증서 발급·갱신 | [Caddy LICENSE](https://github.com/caddyserver/caddy/blob/master/LICENSE) | Apache-2.0. 공식 컨테이너 이미지의 digest를 고정 |
| GitLab Runner 19.5.0 | 노트북 Docker의 프로젝트 CI 작업 실행 | [GitLab Runner LICENSE](https://gitlab.com/gitlab-org/gitlab-runner/-/blob/main/LICENSE) | MIT. 공식 이미지 digest 고정; 이미지 내 OS·보조 도구의 고지는 별도 |
| Moby seccomp 프로필 | `infra/runner/seccomp-buildkit.json`, namespace·mount·hostname 호출 7개 허용 및 keyctl 오류 코드 변경 | [moby/profiles 고정 커밋](https://github.com/moby/profiles/blob/4b8214fc4971e06172938a1f1554ccff6647ee80/seccomp/default.json) | Apache-2.0. 원본 고지는 `infra/runner/LICENSE.moby-profiles`에 보존 |

이번 인프라 작업은 사용자가 이미 선택한 RabbitMQ와 PySide6를 다른 제품으로 변경하지 않는다.
RabbitMQ를 앱에 정적으로 포함하지 않고 별도 서비스로 실행한다. 재배포 시에는 실제 이미지에 포함된
OS·Erlang·라이브러리 고지까지 확인해야 하며 위 표가 이미지 전체의 라이선스 목록을 대신하지 않는다.

로컬 LLM 모델은 모델마다 라이선스가 다르므로 쓰기 전에 확인합니다.

| 모델 (Ollama 태그) | 용도 | 라이선스 |
|---|---|---|
| EXAONE 3.5 (`exaone3.5:7.8b`, 코드 기본값) | GMS 실패 시(네트워크 끊김 포함) Ollama로 실행하는 우회 모델 (`LLM_FALLBACK=ollama`). 2026-10-20 정민지 결정, S15P31A504-82 | **EXAONE AI Model License Agreement 1.1 - NC — 위 규칙의 예외. 비상업적 사용에만 씀.** 아래 「EXAONE 사용 범위」 참고 |
| Qwen3 (`qwen3:4b`) | 상업적으로 배포할 때 EXAONE 대신 쓸 우회 모델 (`OLLAMA_MODEL=qwen3:4b`) | Apache-2.0 |

### EXAONE 사용 범위

- **지금은 비상업적으로만 사용하므로 EXAONE을 우회 모델로 씁니다.** 이 저장소는 SSAFY 교육 과정의 프로젝트이고, 개발 · 시험 · 교육용 시연에만 씁니다.
- **상업적으로 배포하면 이 모델은 사용할 수 없습니다.** EXAONE 라이선스(1.1 - NC)는 연구 · 비상업 목적만 허용하고, 1.2 NC 판은 모델 · 파생 모델 · **출력물**을 수익을 내는 제품 · 서비스에 쓰는 것을 금지합니다. 다음은 모두 상업적 배포로 보고 EXAONE을 쓰지 않습니다.
  - 라넬 등 기업에 설치본 · 서버를 넘기거나 납품하는 경우
  - 유료 전시 · 행사, 수익을 내는 서비스에서 작품을 만드는 경우
  - EXAONE이 만든 작품 명세 · 결과물을 상업적으로 쓰는 경우
- **상업적 배포 전에 할 일**: 코드 기본값이 EXAONE(`exaone3.5:7.8b`)이므로, 배포 설정에서 `OLLAMA_MODEL`을 Apache-2.0 모델(예: `qwen3:4b` · `qwen3:8b`)로 **반드시 지정**합니다. 그 모델을 미리 받아 둡니다(`ollama pull qwen3:4b`). EXAONE으로 만든 기존 작품은 작품 명세의 추출 근거(`provenance.model_ref`가 `ollama:exaone3.5:7.8b`)로 찾을 수 있습니다.
- 상업적 배포 여부가 애매하면 쓰기 전에 팀과 라넬에 확인합니다.
