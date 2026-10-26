# PC2 UE 자동 실행·복구 키트

`runtime_worker.py`의 시연 모드에 선택적으로 적용하는 기능이다. 기존 PID 관찰 모드와 PC1 검증 방식은 유지한다.
새 PC2에서는 호환 Windows Shipping 패키지 전체를 복사한 뒤 `Configure-PC2.cmd`로 **실제 Shipping EXE**와 패키지 루트를 지정한다. 런처 `Art.exe`가 아니라 `Art/Binaries/Win64/Art-Win64-Shipping.exe`를 사용한다.

공개 실행 스크립트와 키트 조합 방법은 [pc2](pc2/README.md), 수행한 검사와 현장 미검증 항목은 [검증 기록](PC2_RECOVERY_VALIDATION.md)에 있다. 토큰이 포함된 완성 ZIP은 별도 비공개 전달한다.

## 실행과 복구

1. 이전 PC2의 Agent와 UE를 종료한다. 새 PC에서도 수동 UE·다른 시연 Agent를 종료한다.
2. 키트를 새 폴더에 풀고 인터넷이 연결된 상태에서 `Prepare-Environment.cmd`를 한 번 실행한다. Python 3.12와 psutil 7.2.2를 uv 캐시에 준비한다. `Configure-PC2.cmd`로 경로·출력 해상도를 설정한다. 기존 키트에 덮어쓰지 않는다.
3. `Check-Runtime.cmd`는 설정·복구 파일 해시·TLS만 검사한다. UE를 실행하거나 작업을 받지 않는다.
4. `Start-Worker.cmd`는 UE를 자동 실행한다. 터미널을 열어 둔다. 중앙 접속이 안 돼도 로컬 복구 작품을 실행한다.
5. 종료된 UE는 마지막 정상 작품 포인터로 돌린 뒤 재시작한다. 첫 재시작 대기는 2초이고 반복 실패 시 최대 60초까지 늘린다. 시작 후 Applied 제한 시간은 기본 120초다.
6. 새 작품은 파일 무결성을 확인한 뒤 기존 UE에 전달한다. **현재 프로세스의 실제 Applied**를 확인해야 마지막 정상 작품으로 저장한다. Ready·다운로드 완료·중앙 승인만으로 갱신하지 않는다.
7. 새 작품 적용 실패·시간 초과·작업 임대 유실 시 마지막 정상 작품으로 돌아간다. 프로세스가 죽었거나 응답 없이 적용 제한 시간을 넘기면 자신이 실행한 UE만 종료·확인하고 순차 재시작한다. 기존 프로세스 종료를 확인하지 못하면 추가 실행하지 않는다.

중앙 통신과 별도인 로컬 감시 스레드가 실행·복구를 담당한다. 새 작품 처리 중에도 감시한다. 통신 장애는 현재 작품을 종료시키지 않으며, 복구 결과는 연결 회복 후 기존 heartbeat 규약으로 보고한다. 복구를 게시 작업의 성공으로 대신 보고하지 않는다.

## UE 한 개와 소유권

Windows 시스템 전역 이름의 잠금으로 자동 실행기 중복을 막고, 시작 전 다른 Shipping·Art 런처·UnrealEditor 프로세스가 있으면 실행을 보류한다. 다른 사용자가 수동으로 실행한 UE는 종료하지 않는다. 시연 PC에서는 수동 UE를 병행 실행하지 않는다.

실행 기록의 PID·생성 시각·EXE 절대 경로·release 경로·실행 식별자가 모두 일치하는 프로세스만 관리한다. PID 재사용이나 다른 프로그램을 정상 UE로 간주하지 않는다. Agent를 Ctrl+C로 종료하면 UE는 화면을 유지한다. Agent 재실행 시 기록과 일치하는 UE를 인계받아 두 번째 UE를 띄우지 않는다. UE까지 종료하려면 Agent를 먼저 종료한 뒤 `Stop-UE.cmd`를 사용한다.

## 로컬 데이터

- `delivery/versions/<version>`: 검증한 작품 파일. 자동 삭제하지 않는다.
- `show-last-good.json`: 파일별 SHA-256·크기와 실제 Applied 증거. 원자적으로 저장한다.
- `bootstrap-manifest.json`: 새 PC 첫 실행용, 이전 PC에서 Applied를 확인한 복구 작품의 파일 명세. 새 PC에서의 Applied는 별도 확인한다.
- `runtime-process.json`: 관리 중인 UE 소유권 기록. 다른 PC로 복사하지 않는다.
- `supervisor-state.json`: 실행·복구·대기·충돌 상태. `logs/ue-runtime.log`: UE 표준 출력·오류.

복구 때마다 마지막 정상 파일의 해시를 검사한다. 손상되거나 실행본·상태가 불명확하면 복구 성공으로 표시하지 않고 실행을 보류한다. 복구 파일과 Shipping 전체 폴더는 백업한다. 키트에는 노드 토큰이 있으므로 비공개로 전달하고 Git에 넣지 않는다.

## 설정

`runtime.local.json`의 `auto_launch=true`, `executable`, `working_directory`, `launch_arguments`를 설정한다. `-ArtworkRelease`와 실행 식별자는 실행기가 넣는다. 런타임 명령을 서버의 작업 데이터에서 받지 않는다. `runtime_pid` 수동 입력은 필요 없다.
`restart_initial_seconds=2`, `restart_max_seconds=60`, `startup_timeout_seconds=120`은 운영 설정이며 검증된 성능 수치가 아니다. 출력 해상도·전체 화면은 실제 LED 규격에 맞춘다.

## 현장 확인

`Get-ShowStatus.cmd`는 살아 있는 감시 프로세스, UE 소유권, 전체 UE 프로세스 수 1개, 현재 세션의 Applied와 정상 작품 버전 일치를 확인한다. 감시가 꺼진 채 UE만 남아 있으면 성공으로 표시하지 않는다.
`Test-Recovery.cmd`는 정상 상태에서 `RECOVER`를 입력했을 때만 자신이 관리하는 UE를 강제 종료한다. 이후 PID·세션이 바뀌고 동일한 정상 작품이 Applied이며 UE 한 개만 남았는지 검사하여 `recovery-test-result.json`에 기록한다. 시험 중 새 게시가 들어오지 않게 운영 시간을 잡는다.

복구 작품 Applied → 승인된 새 작품 Applied → 작업 관리자에서 해당 Shipping 강제 종료 → UE 한 개 재시작·정상 작품 Applied → 중앙 연결 해제 상태에서 재시험 → Agent 중복 실행 거부 → Agent 재시작 시 기존 UE 인계를 확인한다.
시험용 자식 프로세스 검사는 실제 GPU·UE 패키지·LED·센서 검증을 대신하지 않는다. 비정상 종료 동안 화면 공백이 생길 수 있다. Windows 부팅·로그인 자동 실행, OS 장애·GPU 드라이버 복구, 무중단 전환은 이번 범위에 포함하지 않는다. 대화형 사용자 세션에서 실행한다.
