# 노트북에서 실행하는 인프라

중앙 서버는 노트북의 Linux Docker 컨테이너이며, 생성 PC는 Windows에서 HTTPS로 작업을 받습니다.
기존 EC2 목표 구성과 같은 FastAPI·PostgreSQL·RabbitMQ·Celery를 사용합니다.

## 이번 범위

| 항목 | 현재 실행하는 내용 | 후속 연결 |
|---|---|---|
| CI/CD | 로컬 검사/배포, GitLab Runner 최대 4개·테스트 병렬 실행·이미지 기동/복구 검사 | Registry 게시·EC2 실배포 검증 |
| 비동기 | 중앙 초안 큐, 배경/에셋 큐, 선행 작업 의존성, 진행률, 임대 만료와 실패 재시도 | Codex 생성 자동 실행·전체 작품 DAG |
| 서버 통신 | 내부 PostgreSQL/AMQP, DB 작업함 재전송, 외부 HTTPS와 노드별 인증 | EC2 실제 망에서 재검증 |
| 노드 통신 | 작업·입력 결과·출력 파일 목록 교환, 해시 파일 저장과 검증 복사 | 실제 생성 PC·공유 폴더 권한/속도 확인 |
| 전시 프로세스 | 기존 작품 보존·다음 파일 준비·실제 상태 확인 로직의 장애 검사 | UE Runtime 연결·중앙 게시·화면 전환 |

## 생성 PC 작업

HTTP 파일 전송과 UE 제작 서버 키트는 [서버 연결 문서](SERVER_EXCHANGE.md)를 참고합니다.
HTTP 작업은 `--inputs <폴더>`, `--complete --source <결과폴더>`로 중앙에 직접 전송하며
`--store` 없이 사용할 수 있습니다. 아래 SMB 설명은 기존 공유 저장소 작업에 해당합니다.

새 생성 작업 키트는 Python 표준 라이브러리만 필요합니다. 기존 접속 키트는 접속/heartbeat 용도로 계속 쓸 수 있지만
작업 명령에는 새 키트를 사용합니다. 키트에는 이 PC의 토큰만 있으며 운영자 토큰은 전달하지 않습니다.

1. `python client.py --check`로 확인합니다.
2. `python client.py --claim`으로 한 작업을 받습니다. `assignment.json`은 Codex에 전달할 요청과 입력 파일 목록입니다.
   `assignment.secret.json`은 임대 토큰이므로 Codex 프롬프트나 Git에 붙이지 않습니다.
3. 별도 창에서 `python client.py --watch`를 유지합니다. 노드 상태와 진행 중 작업의 임대를 갱신합니다.
4. 필요한 입력은 `python client.py --inputs D:\RanelInput --store D:\RanelAssets`로 가져옵니다.
   다른 PC에서는 `--store`에 같은 저장소의 SMB 경로를 사용합니다. SMB 연결 자체는 OS에서 별도로 설정합니다.
5. Codex로 생성하고 전용 결과 폴더에 저장합니다. `python client.py --progress 50`으로 진행률을 보고할 수 있습니다.
6. `python client.py --complete --source D:\RanelOutput\작업결과 --store D:\RanelAssets`로 해시 저장소에 넣고 결과를 등록합니다.
   중앙에는 파일 목록·해시·크기만 전송합니다. 중단되면 같은 완료 명령을 다시 실행할 수 있습니다.
7. 생성 실패는 `python client.py --fail generation_failed`로 보고합니다. 운영자가 작업 상태를 확인하고 재시도합니다.

저장소는 `objects/<해시 앞 2자리>/<SHA-256>` 형식입니다. 이미 있는 파일은 해시를 확인하고 재사용합니다.
원본 결과 폴더는 자동 삭제하지 않습니다. 중앙의 완료 상태는 파일 실물/화질/작품 조합 검수 통과가 아닙니다.
임대가 만료된 결과는 새 시도에 반영하지 않습니다. 통신이 복구된 뒤 재전송하더라도 소유권을 다시 확인합니다.

## 중앙 운영

`infra/central_cli.py`는 노트북에서 `.env.infra`의 운영자 토큰을 읽습니다. 화면이나 명령행 인자에 토큰을 출력하지 않습니다.

```powershell
uv run python infra/central_cli.py status
uv run python infra/central_cli.py submit --request runtime\background-request.json
uv run python infra/central_cli.py job <작업ID>
uv run python infra/central_cli.py retry <실패작업ID>
```

요청 예시는 `infra/examples/`에 있습니다. 에셋 요청의 `depends_on`에는 실제 배경 작업 ID를 넣습니다.
같은 request_id를 다시 보내면 같은 작업을 반환하며 내용이 달라지면 거부합니다.
실패 작업은 기존 시도 한도 안에서만 재시도합니다. 한도를 소진하면 원인을 해결하고 새 request_id를 사용합니다.

## 로컬 검사·배포

```powershell
uv run python infra/local_pipeline.py
uv run python infra/local_pipeline.py --deploy
```

첫 명령은 검사만 하고, 두 번째는 검사 후 현재 노트북 중앙 서비스를 교체합니다.
배포 직전에 진행 중인 작업이 있으면 중단하며, 검증 실패 시 이전 이미지를 복원합니다.
배포 중 API는 잠시 중지됩니다. 무중단 배포를 구현한 것은 아닙니다.
배포는 코드 이미지만 교체하고 DB·자산 볼륨은 보존합니다. DB 스키마의 파괴적 변경은 자동 롤백 대상으로 취급하지 않습니다.
GitLab Runner는 노트북에 등록되어 있습니다. MR/develop에서 검사·이미지 기동/복구를 검증하며,
main의 이미지 게시와 향후 EC2 수동 배포 조건은 [CI/CD 문서](../docs/CI_CD.md)를 따릅니다.

서버·노드의 연결과 EC2 주소 전환은 [통신 파이프라인](../docs/COMMUNICATION_PIPELINE.md),
생성 키트가 호출하는 요청/응답은 [통신 API 규약](../docs/COMMUNICATION_API.md)에 있습니다.

EC2 이전은 [임시 중앙 서버](./LAPTOP_SERVER.md)와 백업의 `RESTORE.txt`를 따릅니다.
