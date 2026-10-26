# 중앙 API를 통한 생성 서버 ↔ UE 제작 서버 연결

공유 폴더 없이 중앙 HTTPS를 통해 작업과 실제 파일을 주고받습니다. PostgreSQL·RabbitMQ에는
외부 서버가 직접 접속하지 않습니다. 노드별 토큰과 임대한 작업의 소유권 토큰을 함께 사용합니다.

## 오늘의 실행 경로

1. 생성 서버가 `client.py --claim`으로 작업을 받습니다. 제작 프로그램 실행은 생성 서버가 담당합니다.
2. `--complete --source <결과폴더>`로 파일을 중앙에 업로드하고 결과를 등록합니다.
3. 운영자가 생성 결과를 참조하는 `ue_assemble` 작업을 등록합니다.
4. UE 제작 서버가 작업과 입력 파일을 받아 MR !33의 `ue_assemble`로 장면을 조립합니다.
5. 조립 결과와 리포트를 중앙에 업로드합니다. 중앙에서 결과 파일을 확인할 수 있습니다.

이 경로는 작업·파일 통신과 장면 조립까지입니다. Shipping 실행·품질 검수·시연 게시 승인을
합성하지 않습니다. UE 모듈이 없는 제작 서버는 조립할 수 없습니다. MR !33 모듈을 설치한
제작 서버에서 실행해야 하며 중앙서비스에는 UE 에디터나 Shipping 실행본이 필요하지 않습니다.

## 작업 계약

기존 공유 저장소 작업은 유지합니다. 새 요청은 `file_transport: http`를 지정합니다.
HTTP 작업은 성공 완료 전에 결과 파일 전부를 업로드해야 합니다. 결과 파일은 기본 64MiB/개,
256개/작업으로 제한합니다. `ARTIFACT_MAX_BYTES`로 파일 크기 상한을 조절할 수 있습니다.

`ue_assemble` 요청은 `assembly`에 작품·버전·카탈로그, `spec`(MR !33의 Spec JSON),
`assets`(assets.json 내용), `files`를 지정합니다. 각 파일은 `source_job_id`, `source_name`,
`name`(조립 폴더에서 사용할 상대 이름)으로 원본과 목적지를 연결합니다. 부모 작업 ID를 모두
`depends_on`에 넣습니다. 부모가 모두 성공해야 조립 작업을 임대합니다. 다른 작품·버전·카탈로그를
섞지 못하며 중복 파일명과 시스템 파일 이름을 거부합니다.

생성 성공 → 조립 대기 → 제작 서버 임대 → 입력 다운로드 → 조립 → 출력 업로드 → 완료 보고 순서입니다.
조립 성공은 `assembly_report_only`, `publish_approved=false`입니다. 완료 재전송은 같은 시도·같은
결과만 허용합니다. 만료한 시도는 새 결과나 업로드를 확정하지 못합니다.

## 파일 API

- `PUT /infra/agent/jobs/{id}/files?name=...&sha256=...&size=...`: 바이너리 본문.
  `X-Agent-Token`과 `X-Lease-Token` 필요. 임시 파일로 받으며 크기·해시·소유권을 검사한 뒤 보관합니다.
- `GET /infra/agent/jobs/{id}/files/{sha256}`: 같은 두 헤더 필요. 현재 임대한 작업의 출력이나
  성공한 직접 선행 작업의 결과 파일만 내려받습니다. 해시를 알아도 다른 작품 파일은 읽을 수 없습니다.
- `GET /infra/jobs/{id}/files/{sha256}`: 운영자 토큰으로 해당 작업의 등록된 결과 파일 조회.

중앙의 `/data/artifacts`에 해시로 저장하므로 기존 central-data 백업에 포함됩니다. 임시 전송이 실패하면
부분 파일을 제거합니다. 실패·만료 시 남은 원본 객체 정리 정책과 저장 용량 알림은 후속 운영 작업입니다.

## 실행 준비

노드별 HTTPS·CA·토큰 키트는 `infra/issue_client.py`로 발급합니다. 생성 역할은 `generation`,
UE 제작 역할은 `production`입니다. 발급 도구는 `job_kinds: [ue_assemble]`로 등록해
조립 실행기가 처리할 작업만 배정합니다. 기존 제작 노드는 `bundle_verify`만 받습니다.

`python client.py --check`, 별도 창의 `python client.py --watch`,
`python client.py --claim`, `python client.py --inputs <폴더>`,
`python client.py --complete --source <결과폴더>`를 사용합니다. HTTP 작업에는 `--store`가 필요 없습니다.
UE 서버의 자동 임대 루프는 `CENTRAL_URL`, `AGENT_TOKEN`, `NODE_CACHE`와
선택적인 `CENTRAL_CA`를 설정하고 `uv run python -m node_agent --once`로 시험합니다.
UE 서버에는 중앙 변경 코드와 MR !33의 조립 모듈이 모두 설치되어 있어야 합니다.

오늘 MR !33 체크아웃에서 바로 연결할 때는 키트에 포함한 `ue_worker.py`를 사용합니다.
UE 저장소 루트에서 `uv run python <UE키트경로>/ue_worker.py --once`로 실행합니다.
이 실행기는 설치된 MR !33의 Spec·ArtworkAssets·ue_assemble을 쓰므로 중앙 변경 브랜치를
UE 브랜치에 병합하지 않아도 파일 다운로드 → 조립 → 결과 업로드를 시험할 수 있습니다.
반복 실행은 `--once`를 빼면 됩니다. 같은 키트의 UE 실행기 중복 기동은 잠금으로 막습니다.

## 운영자가 작업 등록하기

`infra/examples/background-http.json`, `assets-http.json`, `ue-assembly-http.json`을
`runtime/`으로 복사해 request_id와 실제 작품 정보·선행 작업 ID·파일 이름을 바꿉니다.
중앙 노트북에서 `uv run python infra/central_cli.py submit --request <요청JSON>`으로 등록합니다.
에셋 작업은 필요한 수만큼 같은 형식으로 등록하며, 서로 다른 생성 노드가 병렬로 가져갈 수 있습니다.
조립 요청에는 모든 생성 작업 ID와 입력 파일의 역할을 넣습니다. 예시는 배경 한 장과 알파 객체 한 장입니다.
파일 목록·Spec·assets.json을 확정하는 일은 오늘 운영자가 맡습니다.

Head가 새로 등록하는 배경·에셋 작업도 HTTP 전송을 사용합니다. 기존에 등록된 공유 저장소 작업은
소급 변경하지 않습니다. Head의 배경 이미지 분석 입력 등록과 생성 완료 후 조립 작업 등록은
별도 단계입니다. 모든 작품의 AI 판단부터 UE 실행까지 자동 연결됐다는 뜻은 아닙니다.

## 재현 가능한 왕복 검사

별도 카탈로그 `exchange-smoke-v1`로 생성·제작 테스트 키트를 발급하고 중앙에 등록합니다.
`infra/smoke_exchange.py --generation-kit <생성테스트키트> --ue-kit <UE테스트키트>
--ue-checkout <MR33체크아웃> --ue-env <UE가상환경>`은 중앙 HTTPS와 PostgreSQL에 실제 작업을
등록하고, PNG 테스트 파일을 업로드해 MR !33 조립 결과를 다시 회수합니다.
실제 생성 모델이나 Shipping은 실행하지 않습니다. 현장 키트와 테스트 키트의 카탈로그를 섞지 않습니다.

외부 PC 연결 전 노트북의 실제 LAN 주소로 `.env.https`를 설정하고 해당 PC만 방화벽에 허용합니다.
인증서 검증을 끄지 않습니다. 실제 PC 왕복 이전의 로컬 역할 시뮬레이션은 현장 연결 성공과 구분합니다.
