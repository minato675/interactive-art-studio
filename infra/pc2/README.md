# PC2 자동 실행·복구 키트의 공개 실행 스크립트

시연 PC(Windows)에서 쓰는 `.cmd` 원본이다. 자동 실행·소유권·복구 로직은 상위 폴더의 `show_supervisor.py`, `show_check.py`, `runtime_worker.py`에 있다. [설치·실행·강제 종료 시험](../UE_SHOW_RECOVERY.md)과 [검증 결과](../PC2_RECOVERY_VALIDATION.md)를 함께 읽는다.

이 폴더만 실행 가능한 완성 키트는 아니다. 아래 공개 파일을 **발급받은 시연 키트의 루트 폴더**에 배치한다. ZIP을 새 PC에 설치할 때는 기존 실행기를 먼저 종료하고 새 폴더에 푼다.

| 저장소의 원본 | 키트 루트의 이름 |
|---|---|
| `infra/client.py`, `runtime_delivery.py`, `runtime_worker.py`, `show_supervisor.py`, `show_check.py` | 원래 파일명 |
| `packages/studio/src/studio/infra/runtime_process.py` | `runtime_process.py` — 저장소 어댑터 대신 공통 원본을 복사 |
| `infra/show_playback.py` | `show_playback.py` — 재생목록 동기화·파일 준비·시간 전환 |
| `infra/Start-Runtime.ps1` | `Start-Runtime.ps1` |
| `infra/Configure-PC2-Auto.ps1` | `Configure-PC2.ps1` |
| `infra/pc2/*.cmd`, `runtime.local.example.json` | 원래 파일명 |

노드 운영자가 별도로 제공하는 `node.secret.json`, 서버 주소·CA, 실제 Applied 이력에 근거한 `bootstrap-manifest.json`과 대응하는 작품 버전 폴더가 필요하다. 새 토큰·임의의 정상 작품 기록을 만들지 않는다. 이 파일들과 Shipping 실행본은 저장소에 포함하지 않는다. `runtime.local.example.json`의 경로는 예시이며 `Configure-PC2.cmd`가 실제 경로를 저장한다.

## 설치와 실행

1. 기존 PC2의 Agent·UE를 종료한다. 같은 노드 ID를 두 PC에서 동시에 실행하지 않는다.
2. 호환 Shipping Windows 폴더 전체를 새 PC에 복사한다. `Art.exe` 런처 대신 `Art/Binaries/Win64/Art-Win64-Shipping.exe`를 설정한다.
3. `Prepare-Environment.cmd` → `Configure-PC2.cmd` 순서로 실행한다. Python 3.12·psutil 7.2.2를 준비하고 실제 EXE·패키지 루트·출력 해상도를 입력한다. PID 입력은 필요 없다.
4. `Check-Connection.cmd` → `Check-Runtime.cmd`로 TLS·노드 역할·로컬 복구 파일을 확인한다. 검사만으로 UE를 실행하거나 작업을 임대하지 않는다.
5. `Start-Worker.cmd` 한 개를 실행하고 창을 유지한다. `Get-ShowStatus.cmd`에서 `passed: true`, `ue_process_count: 1`, `Applied`, `Running`을 확인한다.

## 강제 종료 후 복구 확인

새 작품 게시가 없는 시간에 `Test-Recovery.cmd`를 실행하고 `RECOVER`를 입력한다. 이 키트 소유권을 확인한 UE만 종료한다. 도구는 새 PID·세션, 종료 직전 마지막 정상 작품과 같은 버전, 실제 Applied, UE 한 개 실행을 검사한다. 결과는 키트의 `recovery-test-result.json`에 저장된다. 실제 화면·센서·LED도 사람이 확인한다.

Agent를 Ctrl+C로 종료하면 UE는 화면을 유지하며 복구 감시가 일시 정지된다. Agent 재실행은 기록과 일치하는 UE를 인계받는다. UE까지 종료하려면 Agent를 먼저 종료한 뒤 `Stop-UE.cmd`를 실행한다.

노드 토큰이 포함된 완성 ZIP, 로컬 설정·임대·프로세스 기록, 작품·로그·캐시를 Git에 올리지 않는다. 새 PC2의 실제 GPU·화면·센서 확인 전까지 현장 적용 완료로 기록하지 않는다.
