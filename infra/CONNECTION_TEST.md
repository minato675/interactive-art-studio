# 생성·중앙·UE 합동 연결 테스트 순서

중앙은 `70.12.246.252:8443`, 생성 서버는 `70.12.245.47`, UE 제작 서버는 `70.12.246.196`입니다.
두 담당자에게 각자의 키트를 전달합니다. 키트의 토큰은 채팅이나 Git에 붙이지 않습니다.

| 순서 | 담당자 | 실행·확인할 내용 | 통과 기준 |
|---|---|---|---|
| 1 | 생성·UE 각각 | `Test-NetConnection 70.12.246.252 -Port 8443`, 키트의 `python client.py --check` | TCP True, 올바른 노드 ID, TLS verified |
| 2 | UE | 프로젝트 루트에서 조립 모듈 import 확인 | UE assembler ready |
| 3 | 중앙 | HTTP 배경 작업 등록, 반환한 작업 ID 공유 | queued |
| 4 | 생성 | claim → 지시 확인 → 별도 watch → 배경 PNG 업로드·완료 | background_generate succeeded |
| 5 | 중앙 | 위 배경 작업 ID를 참조하는 에셋 작업 등록 | queued |
| 6 | 생성 | claim → 입력 다운로드 → 배경 파일 열기 → 알파 PNG 업로드·완료 | assets_generate succeeded |
| 7 | 중앙 | 두 작업과 파일을 참조하는 UE 조립 작업 등록 | queued |
| 8 | UE | 프로젝트 루트에서 `uv run python C:/Ranel/ue-kit/ue_worker.py --once` | result_registered |
| 9 | 중앙 | 조립 작업 조회, 결과 파일·검증 상태 확인 | succeeded, scene.json·assembly-report.json 존재, publish_approved=false |

생성 담당자 명령은 [생성 서버 안내](GENERATION_HANDOFF.md), UE 담당자는 [UE 안내](UE_HANDOFF.md)를 따릅니다.
키트에는 실제 AI 호출 없이 사용할 `test-files/background.png`와 `test-files/sprite.png`가 들어 있습니다.
처음에는 이 파일로 통신을 확인하고 다음 회차에 실제 생성 결과로 교체합니다.

## 중앙 담당자의 작업 등록

중앙 프로젝트 루트에서 `infra/examples/background-http.json`, `assets-http.json`,
`ue-assembly-http.json`을 `runtime/` 아래 복사합니다. 세 요청의 작품·버전·카탈로그를 동일하게 맞춥니다.
예: artwork_id는 `connection-20261022-01`, version과 catalog_version은 `v1`.
각 request_id는 서로 다르게 하고 다음 회차에도 새 값을 사용합니다.

```powershell
uv run python infra/central_cli.py status
uv run python infra/central_cli.py submit --request runtime/background-http.json
uv run python infra/central_cli.py job <배경작업ID>
```

에셋 JSON의 `depends_on`을 실제 배경 작업 ID로 바꾸고 등록합니다.

```powershell
uv run python infra/central_cli.py submit --request runtime/assets-http.json
uv run python infra/central_cli.py job <에셋작업ID>
```

UE JSON의 `depends_on`과 `assembly.files[*].source_job_id`를 실제 배경·에셋 작업 ID로 바꿉니다.
기본 예시의 파일 이름은 배경 `background.png`, 객체 `sprite.png`입니다.
배경은 UE 조립 폴더의 `far.png`로 매핑합니다. 다른 이름의 실제 생성물은 source_name과 소재 목록도 맞춥니다.

```powershell
uv run python infra/central_cli.py submit --request runtime/ue-assembly-http.json
uv run python infra/central_cli.py job <조립작업ID>
```

꺾쇠로 표시한 작업 ID는 실제 값으로 바꿉니다. 명령을 그대로 복사하지 않습니다.
처음에는 단계별 완료 후 다음 작업을 등록하면 확인하기 쉽습니다.
의존성을 함께 등록하는 경우에도 부모 작업이 모두 성공하기 전에는 자식 작업을 배정하지 않습니다.

중앙은 claim 이전에 기존 대기·진행 작업을 확인하고 테스트 작업이 배정되도록 일정을 맞춥니다.
담당자는 받은 assignment.json이 테스트 작품인지 확인합니다. 운영 작업을 임의로 실패시키거나
테스트 이미지로 완료하지 않습니다. 테스트 작업은 명시적으로 생성 역할·제작 역할에 배정되며
노드 ID 하나에 특정 작업을 지정하는 API는 이번 범위에 없습니다.

## 문제가 생기면

| 증상 | 우선 확인 |
|---|---|
| TCP 연결 실패 | 현재 PC IP, 중앙 노트북 실행 상태, 방화벽, 기관 망의 PC 간 통신 |
| 인증서 오류 | server.json 주소·중앙 CA. 인증서 검증은 유지 |
| HTTP 401 | 담당 PC에 맞는 노드 키트인지 확인 |
| no_ready_job | 작업 등록, 역할·카탈로그, 선행 작업 성공 여부 |
| HTTP 409 | 임대 만료·소유권·업로드 변경 여부. 중앙 담당자와 상태 확인 후 재시도 |
| HTTP 413 | 파일당 기본 64MiB, 작업당 256개 상한 |
| UE import 실패 | MR !33 모듈이 있는 프로젝트에서 uv 환경 설치 여부 |
| UE 조립 실패 | last-assembly-error.json과 중앙 작업 오류 확인 |

공유할 정보는 PC IP·노드 ID·작업 ID·실행 명령·오류 이벤트입니다.
실제 원격 PC 간 왕복 성공 여부는 각 담당자의 실행 결과로 확정합니다.
