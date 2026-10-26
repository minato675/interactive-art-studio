# PC2 자동 실행·복구 검증 기록 — 2026-10-23

## 검증 범위

기존 작업 기준은 `c408db26cb354c3a8fbbe95d71953cb2402f603e`다. GitLab 반영은 최신 develop에서 별도 브랜치를 만든 뒤 복구·키트·통신 관련 검사를 다시 실행한다. UE 대신 실제 자식 프로세스와 합성 상태 파일을 사용했으므로 Shipping/GPU 실측과 구분한다.

| 검사 | 결과 |
|---|---|
| 전체 회귀 검사 초기 스냅샷 `uv run pytest` | 675 통과 · 19 실패 · 15 건너뜀 |
| 복구·키트·통신 `tests/test_show_supervisor.py tests/test_runtime_kit.py tests/test_runtime_pipeline.py` | 24 통과 |
| Python 3.12·psutil 7.2.2의 복구 검사 | 18 통과 |
| 전체 Ruff · `git diff --check` · 설치/실행 PowerShell 구문 | 통과 |
| 복구 작품 SHA-256·크기 | 3개 파일 통과. 작품 데이터는 Git 제외 |
| 관리 노트북의 EC2 TLS·시연 역할 조회 | 통과. 새 PC2의 접속 확인과 구분 |

전체 검사 15개 건너뜀은 PostgreSQL 테스트 DB 미지정 8개, Pillow 미설치 6개, Qt 운영 앱 모듈 1개다. 전체 통과로 기록하지 않는다.

## 실패 19건과 이번 변경의 관련성

실패는 모두 `tests/test_generation_tools.py`의 preflight·handoff 자식 프로세스 한글 출력 검사다. 기본 출력은 `cp949`인데 테스트는 `encoding="utf-8"`로 읽는다. reader thread의 UnicodeDecodeError 후 stdout/stderr가 None이 되어 JSON 파싱·문구 검사도 실패했다.

변경하지 않은 기준 커밋을 별도 worktree와 독립 가상환경으로 실행했을 때 **동일한 19개 항목이 실패**했다(5 통과). 실패 테스트와 `infra/generation/preflight.py`, `handoff_check.py`의 SHA-256도 변경 전후가 같다. 두 작업 환경 모두 아래 UTF-8 설정 후 해당 파일의 **24개 검사가 통과**했다. 이 근거로 해당 19건은 이번 PC2 변경에 의한 회귀가 아님을 확인했다.

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
uv run pytest tests/test_generation_tools.py -q
```

위 설정은 실행하는 PowerShell 세션에만 적용했다. 생성 도구·테스트 소스나 시스템 전역 인코딩은 변경하지 않았다. 원본 XML과 실패 집합·소스 해시 비교는 비공개 전달 키트의 검증 자료에 보관한다.

## 구현 검사에서 확인한 동작

- UE 대역 자동 실행·강제 종료·재시작, 반복 실패 대기 증가.
- 현재 세션 Applied 확인 후 정상 작품 갱신, 이후 종료 시 해당 정상 작품 복구.
- 후보 크래시·Failed·적용 시간 초과·임대 유실 시 이전 정상 작품 복구.
- 중앙 접속 실패 시 시작, HTTP 처리와 독립적인 감시·복구.
- Windows 전역 중복 잠금, 다른 UE 감지, PID 재사용·실행 경로·실행 식별자 불일치 거부.
- 손상 파일·Applied 증거 없는 정상 작품 기록 거부.
- Agent 정지 시 UE 유지, 재실행 시 기존 UE 인계, 소유 UE만 종료.
- 현장 검사 도구의 전후 PID·세션·작품 버전·프로세스 수 기록.

## 새 PC2에서 남은 확인

- 새 PC의 TCP 443·TLS·노드 역할 및 실제 heartbeat.
- 호환 Shipping 전체 패키지·필수 런타임 설치, 올바른 EXE·패키지 루트 설정.
- 최초 복구 작품과 승인된 새 작품의 실제 화면·Applied·중앙 보고 확인.
- 강제 종료 후 UE 한 개·새 PID/세션·마지막 정상 작품 Applied 확인.
- 오프라인 복구와 통신 재연결 후 실제 활성 버전 일치.
- Agent 중복 실행 거부, Agent 재시작 시 기존 UE 인계.
- 카메라·TouchDesigner 손 입력, LED 해상도·전체 화면·장시간 안정성.

Windows 재부팅·로그인 자동화, OS/GPU 드라이버 장애 복구, 무중단 전환은 검증 범위 밖이다. 위 확인 전에는 실제 새 PC2 적용 완료로 기록하지 않는다.
