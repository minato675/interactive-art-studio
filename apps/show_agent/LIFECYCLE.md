# Show Agent의 UE 프로세스 관리

관련 이슈: S15P31A504-217 · [MR !49](https://lab.ssafy.com/s15-final/S15P31A504/-/merge_requests/49).

기존 PySide6 운영 앱과 작품 전달 규약을 유지하면서, 선택적으로 UE 실행·종료·비정상 종료 복구를 관리한다.
기존 고정 PID 관측 모드는 호환 경로로 남긴다. UI를 닫는 동작과 UE를 명시적으로 정지하는 동작은 구분한다.

## 실행 방식 선택

| 방식 | 설정 | UE 프로세스 담당 |
|---|---|---|
| 기존 관측 모드 — 기본값 | HTTP 키트의 `managed_runtime: null`과 `runtime_pid`, 또는 앱의 `UE_RUNTIME_PID` | 담당자가 미리 실행하고 PID를 지정 |
| 선택적 관리 모드 | HTTP 키트의 `managed_runtime` 객체, 또는 앱의 `--runtime-config` | Show Agent가 명시된 Shipping 실행본을 실행·감시·정지 |
| PC2 자동 복구 키트 | `auto_launch=true`, `managed_runtime`은 비우거나 생략 | [PC2 전용 관리자](../../infra/UE_SHOW_RECOVERY.md)가 부트스트랩 정상 작품·후보 교체·복구 담당 |

`managed_runtime`과 `auto_launch=true`는 함께 지정할 수 없다. 두 관리자는 서로 다른 실행·정지 및 복구 기록을 사용하므로
실행 중에 방식을 바꾸지 않는다. Windows 호스트 잠금을 공유해 두 관리자나 서로 다른 설정 폴더의 동시 실행을 거부한다.
기존 자동 복구 키트는 그대로 유지하며, 새 `runtime_worker.py`로 갱신할 때는 공통 `runtime_process.py`도 함께 갱신한다.

새 키트를 발급해도 기본값은 관측 모드다. 관리 모드는 PC2의 대화형 Windows 사용자 세션에서 실행한다.
Windows 서비스 등록·부팅 자동 실행·자동 로그인 설정은 이번 범위에 없다.
PySide6 앱은 유지되며, 이번 구현의 제어 수단은 로컬 CLI다. 새 UI 버튼과 중앙 원격 시작·정지 API는 아직 연결하지 않았다.

## HTTP 시연 키트 설정

호환 Shipping **패키지 전체**를 PC2에 설치하고 `runtime.local.json`의 예제를 실제 경로에 맞춘다.
`executable`은 부트스트랩 `Art.exe`가 아닌 실제 `Art-Win64-Shipping.exe`를 지정한다.
아래 창 크기는 로컬 확인용 예시이며 LED 해상도·성능을 확정한 값이 아니다.

```json
{
  "mode": "show",
  "delivery": "C:/Ranel/show/delivery",
  "cache": "C:/Ranel/show/cache",
  "runtime_pid": 0,
  "apply_timeout_seconds": 120,
  "managed_runtime": {
    "executable": "C:/Ranel/Shipping/Art/Binaries/Win64/Art-Win64-Shipping.exe",
    "working_directory": "C:/Ranel/Shipping",
    "arguments": ["Art", "-dx12", "-windowed", "-ResX=1280", "-ResY=720"],
    "autostart": true,
    "restart_limit": 3,
    "restart_delay_seconds": 3,
    "stable_seconds": 60,
    "stop_timeout_seconds": 5
  }
}
```

`delivery`와 `cache`는 서로 겹치지 않는 절대 경로다. Agent가 `-ArtworkRelease=<delivery>/release.json`을 붙인다.
작업 payload로 실행 파일·실행 인자를 전달하지 않는다. 설정의 `-ArtworkRelease`·`-ArtworkScene` 덮어쓰기는 거부한다.
처음에는 포인터가 없으면 `WaitingForRelease`로 기다리며, 승인된 첫 번들이 준비·게시된 뒤 실행한다.
기존 포인터를 사용한다면 검증된 버전 폴더와 함께 보존한다.

키트 폴더의 PowerShell에서 다음 순서로 실행한다. `--check`는 UE를 실행하거나 작업을 임대하지 않는다.

```powershell
& .\Start-Runtime.ps1 --check
& .\Start-Runtime.ps1
```

실행기를 켜 둔 채 **같은 키트의 다른 터미널**에서 제어한다.

```powershell
& .\Start-Runtime.ps1 --runtime-action status
& .\Start-Runtime.ps1 --runtime-action stop
& .\Start-Runtime.ps1 --runtime-action status
& .\Start-Runtime.ps1 --runtime-action start
```

시작·정지 명령의 `accepted: true, applied: false`는 **의도 파일 저장**이다. 프로세스 제어 완료 응답이 아니다.
Agent가 실행 중이어야 처리되며, `status`의 `command_id`가 요청과 같고 `phase: Stopped`이며 `process`가 없어야 정지 완료다.
`status`는 마지막 저장 상태이므로 `updated_at`도 함께 확인한다. 시작 후 `Running`은 프로세스 존재만 뜻한다.
작품의 실제 적용 성공은 기존 중앙 보고와 UE 상태 파일의 `Applied`로 따로 확인한다.
명시적으로 정지한 동안 새 게시 작업을 임대하지 않으며, 진행 중 게시도 적용 성공으로 처리하지 않는다.

Ctrl+C로 Agent를 종료해도 UE는 남는다. 감시도 종료되므로 재실행 전까지 자동 복구하지 않는다.
UE 창만 닫으면 실행 의도가 남아 있어 비정상 종료로 보고 재실행한다. 전시를 멈출 때는 Agent를 켜 둔 채 `stop`을 사용한다.
로컬 제어에는 중앙 토큰·연결이 필요하지 않지만, 최초 `uv` 의존성 설치에는 인터넷이 필요하다.

## 설치형 a504-show-agent 사용

저장소 설치형 Agent도 같은 관리 모듈을 사용한다. HTTP 키트와 **동시에 실행하지 않는다**.
`managed-runtime.json`에는 위 예제의 `managed_runtime` 내부 객체만 저장한다.
`SHOW_CACHE`가 버전 폴더와 `release.json`이 있는 경로다. HTTP 키트와 비교하면 `delivery` 역할이다.
기존 중앙 접속·Agent 인증·번들 저장소 설정은 [운영 안내](../../infra/OPERATIONS.md)를 따른다.

```powershell
$env:SHOW_CACHE = 'C:/Ranel/show/delivery'
uv run a504-show-agent --runtime-config C:/Ranel/managed-runtime.json
# 같은 SHOW_CACHE를 지정한 다른 터미널
uv run a504-show-agent --runtime-config C:/Ranel/managed-runtime.json --runtime-action stop
uv run a504-show-agent --runtime-config C:/Ranel/managed-runtime.json --runtime-action status
```

## 중복 방지와 복구 기준

- Windows 호스트 잠금, 같은 delivery의 게시 잠금, 사용자별 실행 파일 잠금을 함께 잡는다. PC2 자동 복구 키트와도 호스트 잠금을 공유해 두 번째 관리자를 거부한다.
- 실행 파일 경로·`-ArtworkRelease` 경로·PID·프로세스 시작 시각을 대조한다. 일치하는 기존 프로세스는 인계받고, 충돌·접근 거부·여러 프로세스 발견 시 `Blocked`로 남긴다. 관계없는 PID를 종료하지 않는다.
- 정지는 대상 PID의 창에 Windows `WM_CLOSE`를 보낸다. 제한 시간 안에 종료하지 않으면 해당 프로세스만 강제 종료하고 `forced_stop`을 기록한다. 실제 UE의 정상 종료 응답은 현장 검증이 남아 있다.
- 프로세스 종료는 중앙 HTTP 호출과 별도 스레드에서 감시한다. 재시도 지연은 기본 3·6·12초처럼 증가하며 최대 60초다. 초기 실행 뒤 연속 재시작 기본 상한은 3회다. 60초 생존 후 예산을 초기화하며 `Applied`만으로는 초기화하지 않는다. 명시적 `start`도 예산을 초기화한다.
- 관리 모드의 시작은 중앙 접속 확인을 기다리지 않는다. 유효한 로컬 키트·설정·정상 작품이 있으면 중앙 장애 중에도 실행·복구를 감시하고, heartbeat·작업 수신은 재연결한다. `--check`는 별도의 온라인 접속 검사다.
- 마지막 정상 작품은 **현재 프로세스의 실제 Applied**와 세션·요청·버전·시각을 확인한 뒤 기록한다. 복구 시 해당 버전의 모든 파일 크기·SHA-256을 다시 검사하고 포인터를 원자 교체한 뒤 실행한다. 무결성 오류가 있으면 실행을 보류하며 재시도 상한을 적용한다.
- 신규 게시와 정상 작품 포인터 복원은 같은 잠금으로 보호한다. 복구 중인 신규 게시 요청은 포인터 복원 뒤 대상 프로세스를 확인할 때까지 기다린다. 실제 작품 적용은 이후 기존 `Applied` 검사로 따로 확인한다.

호스트 잠금은 이 규약을 따르는 자동 관리자 사이에 적용된다. 별도의 실행 파일 잠금도 유지한다. 수동 실행한 UE·에디터까지 OS 수준에서 차단하는 기능은 아니다.
시연 PC에서는 운영 계정을 하나로 고정하고 UE 관리 주체를 하나로 둔다. 제작 노드의 검증기는 이번 시연 관리 기능과 별개다.

| 로컬 기록 | 용도 |
|---|---|
| `<delivery>/.runtime-process/intent.json` | 명시적 시작·정지 의도와 명령 ID |
| `state.json` | PID·시작 시각·실행 상태·재시도 횟수·마지막 갱신 시각 |
| `last-good.json` | 이 관리자가 확인한 정상 버전과 파일 해시 |
| `runtime.log` | 실행본 표준 출력·오류. 자동 순환·보존 기간은 후속 작업 |
| `%LOCALAPPDATA%/Ranel/ue-locks/` | 실행 파일별 잠금 |

위 표의 `state.json`·`last-good.json`·`runtime.log`는 모두 `.runtime-process` 아래에 있다.
기존 키트 루트의 `last-good.json` 보고서는 자동으로 복구 근거로 승격하지 않는다.
설정의 실행 파일·인자·delivery를 바꾸면 기존 상태와 식별자가 달라져 거부된다. Agent를 정지하고 대상 UE·기존 상태를 확인한 뒤 별도 delivery로 전환한다.

`Starting`·`Running`·`Stopped`·`Backoff`·`Failed`·`Blocked`·`WaitingForRelease`는 **로컬 프로세스 상태**다.
중앙의 Staged·Ready·Applied 프로토콜을 바꾸거나 새 상태를 전송하지 않는다.
UE가 살아 있는 상태의 멈춤·검은 화면·GPU 장애, 재부팅 후 자동 실행, 화면 레이아웃·손 입력 복구는 후속 범위다.
시연 노드에서 신규 빌드·에디터 조립·쿠킹은 수행하지 않는다.

## PC2 현장 확인 순서

검토가 끝난 코드와 호환 Shipping 패키지를 사용해 전시 전 별도 시험 시간에 확인한다.

1. 기존 수동 UE·다른 Agent를 종료하고 경로·카탈로그 호환성을 확인한다. 설정을 저장하고 `--check`로 중앙 접속을 확인한다.
2. 관리 모드 Agent를 실행한다. 정상 작품이 없다면 `WaitingForRelease`, 첫 승인 번들 게시 뒤에는 단일 PID와 UE의 실제 `Applied`를 확인한다.
3. 같은 실행 파일을 관리하는 두 번째 Agent를 실행해 잠금 거부와 UE PID 수가 늘지 않는지 확인한다.
4. 명시적 `stop` 뒤 `Stopped`·PID 소멸·재실행되지 않음을 확인한다. `start` 뒤 새 PID와 실제 작품 화면·`Applied`를 확인한다.
5. 시험용 UE 프로세스만 비정상 종료해 제한된 지연 뒤 새 PID·마지막 정상 작품·`Applied` 복구를 확인한다. 준비 중 후보가 정상 작품으로 잘못 승격되지 않는지도 확인한다.
6. 중앙 접속만 잠시 차단한 상태에서 Agent 시작과 UE 복구를 확인한다. 복구 후 heartbeat가 실제 활성 버전을 보고하는지 확인한다.

실제 화면·종료 응답·복구 시간·GPU 부하를 기록한다. 프로세스 복구는 화면의 무중단 전환을 보장하지 않는다.

## 검증 기록 — 2026-10-23

Windows에서 가짜 Runtime과 실제 일반 Python 프로세스로 실행·정지·강제 종료 뒤 새 PID 복구를 검사했다.
별도 프로세스 잠금, PID 재사용, 정지 의도 보존, 제한된 재시도, 오래된 상태 거부, 복구 파일 손상 거부도 자동 검사한다.
HTTP 키트는 중앙 API 왕복·게시·Applied 결과 등록, 복구 시 변경 PID 사용, 자체 포함 모듈 발급과 토큰 없는 로컬 정지 요청을 검사한다.
최신 develop `7fedc8a`를 통합한 Windows·CPython 3.13.15 환경에서 `uv sync --all-packages`로 PySide6까지 설치했다.
검사에는 `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, `QT_QPA_PLATFORM=offscreen`을 적용했다.
UTF-8을 지정하지 않은 첫 실행에서는 기존 생성 도구의 한글 subprocess 출력 인코딩 오류가 있었고, UTF-8 적용 후 전체 검사가 통과했다.

| 검사 | 결과 |
|---|---|
| `uv run --no-sync pytest -q --tb=short` | **732 passed · 14 skipped**, 385.80초 |
| `uv run --no-sync ruff check .` | 통과 |
| `uv lock --check` | 통과 |
| controller `unittest discover` | 184개 중 181개 통과 · 3개 건너뜀 |
| 프로세스 관리·키트 집중 검사 | 37개 통과. 실제 일반 Python 프로세스와 합성 중앙 API 사용 |
| Runner 등록 검사 | `ranel-laptop-linux-docker` 유효, 로컬 Docker 엔진 기동 확인 |

전체 검사의 14개 건너뜀은 Pillow 미설치 6개와 `TEST_POSTGRES_URL` 미지정 8개다.
controller의 3개는 `ART_RUNTIME_ROOT` 미지정으로 건너뛰었다. 기존 Starlette/httpx 사용 중단 예고 경고 1건이 있다.
이는 실제 PostgreSQL 동시성·이미지 생성·실제 UE 화면을 이번 변경에서 검증했다는 뜻이 아니다.
원격 GitLab CI 결과는 이 로컬 검사와 별개로 MR에서 확인한다.

실제 UE/GPU·LED·카메라·PC2 현장 적용은 수행하지 않았다. 이 노트북에 호환 Shipping 실행본이 없어 일반 프로세스 검증과 구분한다.
기존 PySide6 화면과 회사 UE·TD·controller 사본은 수정하지 않았다. 위 결과는 최초 구현 당시 기록이며,
이후 develop 통합과 PC2 자동 복구 키트의 충돌 해결 결과는 MR !49에 별도로 기록한다.
2026-10-23 사용자 요청으로 검증 후 Draft를 해제하고 develop에 병합하며, 실제 PC2 현장 확인은 계속 미검증으로 유지한다.

### PC2 자동 복구 키트 통합 검증 — 2026-10-23

develop `ded6910`과 통합하며 `runtime_worker.py`·PC2 설치 안내·테스트 안내의 충돌을 해결했다.
`auto_launch`와 `managed_runtime`의 별도 게시·복구 경로를 보존하고, 두 설정의 동시 지정과 Windows 호스트의 관리자 중복 실행을 거부한다.
새 시연 키트에는 공통 프로세스 관리 원본과 기존 PC2 자동 복구 모듈도 함께 넣는다.

| 검사 | 결과 |
|---|---|
| 전체 `uv run --no-sync pytest -q --tb=short` | **773 passed · 14 skipped**, 432.67초, 기존 Starlette/httpx 경고 4건 |
| 프로세스 관리·PC2 자동 복구·키트·통신 집중 검사 | **62 passed** |
| controller `unittest discover` | **181 passed · 3 skipped** |
| Ruff·의존성 잠금·diff 공백 검사 | 통과 |

Windows CPython 3.13.15에서 PySide6를 포함해 설치하고 기존과 같이 UTF-8·Qt offscreen을 적용했다.
서로 다른 관리자를 먼저 켜는 두 순서 모두 Windows 호스트 mutex가 두 번째 관리자를 거부하는 것을 검사했다.
14개 건너뜀은 Pillow 미설치 6개와 테스트 PostgreSQL URL 미지정 8개이며, controller의 3개는 `ART_RUNTIME_ROOT` 미지정이다.
이후 들어온 develop 변경은 별도로 통합하고 최종 소스 커밋의 GitLab CI 결과를 MR !49에 기록한다.
실제 Shipping·GPU·WM_CLOSE·PC2 현장 확인은 이 자동 검사로 대체하지 않는다.
