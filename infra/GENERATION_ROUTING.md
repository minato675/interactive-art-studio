# 생성 실행기 배정 정책

실제 발급 파일·접속 방법·배포 검증은 [실행기 3개 안내](GENERATION_3_EXECUTORS.md)에 기록합니다.

생성 PC 한 대에서 배경 전담 1개와 에셋 전담 2개를 실행합니다. 각 실행기는 서로 다른
노드 ID·토큰·키트 폴더·출력 폴더를 사용합니다. 공통 파일 저장소만 공유합니다.

| 노드 ID | 서버의 job_kinds | 권장 키트 폴더 |
|---|---|---|
| generation-background-1 | background_generate | C:\RanelKit\background |
| generation-assets-1 | assets_generate | C:\RanelKit\assets1 |
| generation-assets-2 | assets_generate | C:\RanelKit\assets2 |

서버의 `AGENT_KEYS_JSON`이 권한의 기준입니다. 클라이언트가 전담 종류를 바꿔 요청해도
서버가 등록한 종류만 임대합니다. 각 노드는 작업 하나만 실행하며, 두 에셋 노드는 같은 큐를
공유합니다. 배경 성공 전 에셋 임대는 불가합니다. DB 스키마 변경은 없습니다.
이 설정은 실행기 3개의 배정 제한이며 임의로 추가한 모든 노드까지 전역 동시성 3으로 제한하지는 않습니다.

`job_kinds`가 없는 기존 등록은 호환성을 위해 기존 역할의 기본 작업을 받습니다.
전담 구성으로 전환할 때 기존 범용 생성 노드는 실행 중 작업이 없는지 확인한 뒤
`enabled: false`로 비활성화합니다. 기존 토큰은 보관하지만 API 인증은 거부합니다.
종류 변경 전에 받은 작업의 완료 보고는 기존 소유권으로 처리할 수 있으므로 전환 전에 작업을 비웁니다.

## 발급

`infra/issue_client.py`의 `--job-kind`로 허용 종류를 지정합니다. 여러 종류는 옵션을 반복합니다.
기존 ID에 명시하면 토큰을 교체하지 않고 정책을 갱신하며, 생략하면 기존 정책을 보존합니다.
생성 역할에는 배경·에셋 종류만, 제작 역할에는 bundle_verify·ue_assemble만 허용합니다.

```powershell
uv run python infra/issue_client.py --node generation-background-1 --job-kind background_generate --env <서버설정사본> --url https://k15a504.p.ssafy.io --ca <공개CA> --output <개인폴더1>
uv run python infra/issue_client.py --node generation-assets-1 --job-kind assets_generate --env <서버설정사본> --url https://k15a504.p.ssafy.io --ca <공개CA> --output <개인폴더2>
uv run python infra/issue_client.py --node generation-assets-2 --job-kind assets_generate --env <서버설정사본> --url https://k15a504.p.ssafy.io --ca <공개CA> --output <개인폴더3>
```

설정 세 개를 모두 등록한 뒤 중앙 서비스를 한 번 재구성합니다. 서버 비밀 설정·키트 ZIP은 Git에 넣지 않습니다.
키트는 서버가 알려준 ID·역할·전담 종류가 예상과 같은지 검사합니다. Codex 세션은 자동 실행하지 않습니다.
각 키트에서 `python client.py --check` 후 별도 창에 `--watch`를 유지하고 `--claim`으로 작업을 받습니다.

## HTTPS 443

EC2의 `.env.https`에서 `CENTRAL_HTTPS_PORT=443`을 사용합니다. 도메인과 기존 CA는 유지합니다.
키트 주소는 `https://k15a504.p.ssafy.io`입니다. AWS 보안 그룹과 호스트 접근 제어는
생성 담당자가 정정한 주소 `70.12.245.47/32`를 허용합니다. 이전에 전달된 `211.192.252.228`은 제거합니다.
이 정정은 생성 PC의 실제 EC2 접속 원본 주소를 확인했다는 뜻은 아닙니다.
NAT로 접속 주소가 달라지면 생성 PC의 실제 접속 검사로 확인한 뒤 허용 목록을 갱신합니다.
Docker 공개 포트는 UFW를 우회할 수 있어 UFW 설정만으로 접근 제한이 검증됐다고 말하지 않습니다.
실제 접근 제어와 외부 TLS 검증을 별도로 기록하고 SSH 22·기존 공개키·DB 볼륨을 보존합니다.

`configure_ec2_https.py`는 관리자 권한으로 IPv4 클라이언트 CIDR별 UFW 허용과
Docker `DOCKER-USER`의 원래 목적 포트 검사를 적용합니다. 자신이 관리하는 체인만 갱신하며
`--persist`로 Docker 시작·재시작 시 재적용할 전용 systemd 서비스를 설치합니다.
현재 운영은 사용자 요청에 따라 `--public --port 443 --persist` 모드를 사용합니다.
이 모드는 443의 IP 제한만 해제하며 계정·운영 도구·Agent 인증은 유지합니다.
SSH·DB·작업 큐 규칙과 리스너는 변경하지 않습니다. 위 출구 IP 등록 설명은 제한 모드의 이력입니다.
생성 PC와 운영 노트북의 공인 출구 IP가 다르면 둘 다 명시합니다. AWS 보안 그룹 자체는 변경하지 않습니다.
허용된 출구 IP가 바뀌면 규칙과 접근 검증을 갱신합니다.

첫 통합 테스트는 N≤12, 생성 전 Spec·choice 공유, 배경 결과의 운영자 업로드를 사용합니다.
생성 완료는 작품 검수·UE 화면 검증·게시 승인이 아닙니다.
