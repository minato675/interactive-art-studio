# 오늘 생성 서버와 UE 제작 서버 연결하기

오늘의 목표는 **생성 결과를 중앙으로 보내고, UE 제작 서버가 받아 조립한 결과를 다시 중앙에 보내는 것**입니다.
생성 모델의 품질 평가와 Shipping 실행·전시 게시 승인은 이 연결 확인 다음에 진행합니다.

## 준비된 것

| 장비 | 주소·역할 | 준비 상태 |
|---|---|---|
| 현재 노트북 | `https://70.12.246.252:8443` 중앙 API | 새 이미지 배포·서버 자체 HTTPS 검사 완료 |
| 생성 서버 | `70.12.245.47` | 중앙 방화벽 허용·새 HTTP 접속 키트 발급 |
| UE 제작 서버 | `70.12.246.196` | 중앙 방화벽 허용·별도 제작 키트 발급. 해당 PC에서 접속 확인 필요 |

전달할 키트는 노트북의 개인 폴더 `RanelMaster/kits/`에 있습니다.
생성 서버에는 `generation-http-20261022-handoff.zip`, UE 서버에는 `ue-http-20261022-preflight.zip`을 전달합니다.
각 키트의 `START_HERE.md`를 먼저 읽습니다. [생성 담당자 안내](GENERATION_HANDOFF.md),
[UE 담당자 안내](UE_HANDOFF.md), [합동 테스트 순서](CONNECTION_TEST.md)도 함께 제공합니다.
각 키트에는 해당 노드의 접속 토큰이 있습니다. Git이나 공개 링크에 올리지 않고 담당 PC에만 전달합니다.
`smoke-exchange-*`는 로컬 검증 전용 키트이므로 현장에 배포하지 않습니다.

중앙 상태와 작업 기록은 PostgreSQL에 저장합니다. 중앙 Worker의 비동기 작업은 RabbitMQ·Celery로 처리하고,
생성·UE 제작 서버는 중앙 HTTPS API에서 작업을 임대합니다. 외부 PC가 DB나 RabbitMQ에 직접 접속하지 않습니다.
바이너리 파일은 중앙 저장 볼륨에, 파일 목록과 업로드 기록은 PostgreSQL에 보관합니다.

## 1. 중앙 노트북에서 작업 등록

`infra/examples/background-http.json`, `assets-http.json`, `ue-assembly-http.json`을
`runtime/` 아래 복사하고 실제 작품 정보·작업 ID·파일명으로 수정합니다.

```powershell
uv run python infra/central_cli.py submit --request runtime/background-http.json
uv run python infra/central_cli.py submit --request runtime/assets-http.json
uv run python infra/central_cli.py submit --request runtime/ue-assembly-http.json
uv run python infra/central_cli.py job <작업ID>
```

앞 명령이 반환한 작업 ID를 다음 요청의 `depends_on`과 `source_job_id`에 넣습니다.
에셋이 여러 개면 에셋 작업을 필요한 수만큼 등록하고 조립 작업에 모두 연결합니다.
에셋 작업은 배경 성공 뒤, 조립 작업은 모든 입력 작업 성공 뒤에만 배정됩니다.
오늘은 운영자가 Spec·소재별 역할과 파일 매핑을 정합니다. AI가 조립 요청까지 자동 확정하는 흐름은 후속입니다.

## 2. 생성 서버에서 받기·생성하기·보내기

키트를 전용 폴더에 압축 해제한 뒤 그 폴더에서 실행합니다. Python 3.10 이상이면 됩니다.

```powershell
python client.py --check
python client.py --claim
python client.py --watch
```

`--watch`는 별도 창에서 유지합니다. 작업이 오래 걸려도 임대가 만료되지 않게 갱신합니다.
`assignment.json`에 제작 지시가 있습니다. 배경 파일이 필요한 에셋 작업은 먼저 입력을 받습니다.

```powershell
python client.py --inputs C:/Ranel/input
python client.py --complete --source C:/Ranel/output/현재작업
```

현재 작업의 결과만 담은 전용 폴더를 지정합니다. 중앙이 크기·SHA-256을 확인한 뒤 완료를 받습니다.
이 키트가 Codex나 이미지 생성 프로그램을 자동으로 실행하지는 않습니다. 생성 프로그램이 작업을 읽고
결과 폴더를 만든 뒤 완료 명령을 호출하도록 연결해야 합니다. 실패는 `--fail generation_failed`로 보고합니다.
에셋 작업을 동시에 처리하려면 각 실행 프로세스에 서로 다른 노드 ID의 키트가 필요합니다.

## 3. UE 제작 서버에서 조립하기

중앙 노트북 방화벽에 UE 제작 서버 `70.12.246.196`을 허용했습니다. 해당 PC에서 접속을 확인합니다.
UE 서버에는 MR !33 조립 모듈이 있는 프로젝트 체크아웃과 Python 개발 환경이 필요합니다.
키트만으로 UE 조립 모듈이 설치되지는 않습니다.

```powershell
python C:/Ranel/ue-kit/client.py --check
# 아래 명령은 MR !33 모듈이 있는 프로젝트 루트에서 실행
uv run python C:/Ranel/ue-kit/ue_worker.py --once
```

실행기는 **작업 임대 → 입력 다운로드 → scene.json 조립 → 결과 업로드 → 완료 보고**를 수행합니다.
임대 갱신도 내부에서 처리합니다. 계속 대기하며 처리하려면 `--once`를 빼고 실행합니다.
오류가 나면 키트의 `last-assembly-error.json`과 중앙 작업 상태를 확인합니다.
조립 성공은 Shipping 검증·게시 승인이 아닙니다. `publish_approved`는 계속 `false`입니다.

## 확인한 범위와 다음 확인

노트북에서 서로 다른 생성·제작 노드 ID와 별도 UE 실행 프로세스로 중앙 HTTPS에 접속했습니다.
PNG 테스트 파일로 배경 → 에셋 → MR !33 실제 Python 조립기를 실행했고, 결과 파일 4개를 중앙에서
회수해 해시를 확인했습니다. 세 작업 모두 성공했습니다. 이미지 생성 모델과 Shipping은 실행하지 않았습니다.

이제 두 실제 PC에서 같은 명령으로 접속·파일 왕복을 확인해야 합니다.
자세한 API·제한·재현 방법은 [서버 연결 문서](SERVER_EXCHANGE.md)를 따릅니다.
