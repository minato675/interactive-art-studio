# UE 제작 서버 담당자 전달문

대상 PC는 `70.12.246.196`, 노드 ID는 `ue-exchange-1`입니다.
중앙 주소는 `https://k15a504.p.ssafy.io`(HTTPS 443)입니다.
새 `Ranel-UE-Production-EC2-443-20261022.zip`을 받아 `C:/Ranel/ue-kit`에 압축 해제하세요.
기존 키트를 받거나 설치했을 필요는 없습니다. 토큰·공개 CA·클라이언트·UE 실행기를 모두 포함합니다.
접속 확인용 client.py는 Python 3.10 이상, 실제 조립은 프로젝트 기준 Python 3.12 이상과 uv가 필요합니다.

## 접속 확인

```powershell
cd C:/Ranel/ue-kit
python --version
Test-NetConnection k15a504.p.ssafy.io -Port 443
python client.py --check
```

`TcpTestSucceeded: True`이고 `connection_verified`, `node_id: ue-exchange-1`,
`role: production`, `tls: verified`가 출력되면 접속 확인 성공입니다.
이 결과를 중앙 담당자에게 알려주세요.

UE 담당자가 실제 출발 주소 `211.192.210.162`를 확인했고 EC2 서버의 443 허용 목록에 추가했습니다.
생성 PC 신고 주소 `70.12.245.47`과 운영 노트북 `211.192.210.218`도 유지합니다.
지문 기준은 MR !33 `2812b85`이며 [중앙·노드 갱신 순서](UE_CATALOG_UPDATES.md)를 따릅니다.

## 프로젝트 준비

UE 담당자가 작업 중인 MR !33 모듈이 있는 프로젝트를 사용합니다.
아래 `C:/Ranel/project`는 실제 프로젝트 루트로 바꿉니다. 키트 폴더에서 uv를 실행하지 않습니다.
키트가 UE 프로젝트나 Shipping 실행 파일을 설치하지는 않습니다.

```powershell
cd C:/Ranel/project
uv sync
uv run python -c "from contracts.assets import ArtworkAssets; from studio.assembly.ue_assemble import assemble; print('UE assembler ready')"
```

`UE assembler ready`가 출력되어야 합니다. import 오류가 나면 MR !33의 조립 모듈과 환경 설치를 확인합니다.
중앙 변경 브랜치를 UE 작업 브랜치에 병합할 필요는 없습니다.

## 조립 작업 한 건 실행

Head가 생성 작업을 시작하기 전에 새 키트로 리소스를 보고해야 합니다.

```powershell
cd C:/Ranel/project
uv run python C:/Ranel/ue-kit/ue_worker.py --report-capabilities
```

`ue_capabilities_reported`, `ok: true`를 확인합니다. 중앙은 같은 해시와 90초 이내 heartbeat를 요구하므로
Head 생성 중에는 아래 실행기에서 `--once`를 빼고 계속 대기시키는 방식으로 준비 상태를 유지합니다.
키트에 `ue_capabilities.py`가 함께 있어야 합니다. 이 보고는 Shipping 실행 성공을 뜻하지 않습니다.

중앙 담당자가 배경·에셋 완료와 조립 작업 등록을 확인한 뒤 실행합니다.

```powershell
cd C:/Ranel/project
uv run python C:/Ranel/ue-kit/ue_worker.py --once
```

실행기는 작업 임대 → 배경·에셋 다운로드 → MR !33 조립 → 결과 업로드 → 완료 보고를 수행합니다.
임대 갱신도 내부에서 처리하므로 별도 `client.py --watch`는 필요 없습니다.
`result_registered`, `ok: true`, `verification: assembly_report_only`가 성공 기준입니다.
실행기는 임시 폴더에서 조립하므로 결과 파일은 중앙에 저장됩니다. 키트의 `last-result.json`에서 파일 목록을 봅니다.

`no_ready_job`이면 현재 받을 수 있는 조립 작업이 없습니다. 중앙 담당자가 선행 작업 상태와 등록을 확인합니다.
`ue_assembly_failed`가 출력되면 중앙 작업 상태와 키트의 `last-assembly-error.json`을 확인합니다.
이 파일은 조립기 검증 오류일 때 생성되며, import·통신 오류에서는 없을 수 있습니다.
프로세스 종료 코드가 0이어도 `ue_assembly_failed`이면 작업 성공이 아닙니다.

계속 대기하며 처리하려면 `--once`를 빼고 실행하고, 종료는 Ctrl+C로 합니다.
동일 노드 키트로 실행기를 여러 개 띄우지 않습니다.

## 이번 테스트의 완료 범위

이번 실행은 Python의 scene.json 조립과 파일 왕복까지입니다.
Shipping 실행·Niagara 실제 표시·성능·화면 전환은 별도 확인하며 `publish_approved`는 `false`입니다.
작업 ID와 완료 이벤트를 중앙 담당자에게 알려주세요. 토큰이 든 `*.secret.json`은 공유하지 않습니다.

생성 쪽은 배경 전담 1개·에셋 전담 2개를 사용합니다. 생성과 UE 키트의 토큰을 서로 복사하지 않습니다.
실행 절차는 새 키트의 `START_HERE.md`와 `SERVER_EXCHANGE.md`를 따릅니다.
