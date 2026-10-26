# 생성 서버 담당자 전달문

대상 PC는 `70.12.245.47`, 노드 ID는 `generation-1`입니다.
중앙 주소는 `https://70.12.246.252:8443`입니다.
`generation-http-20261022-handoff.zip`을 받아 `C:/Ranel/generation-kit`에 압축 해제하세요.
Python 3.10 이상이 필요하며 별도 Python 패키지는 설치하지 않아도 됩니다.
키트는 중앙 작업·파일 통신을 담당합니다. Codex·이미지 생성 도구는 별도로 실행합니다.

## 접속 확인

PowerShell에서 실행합니다.

```powershell
cd C:/Ranel/generation-kit
python --version
Test-NetConnection 70.12.246.252 -Port 8443
python client.py --check
```

`TcpTestSucceeded: True`이고 `connection_verified`, `node_id: generation-1`,
`role: generation`, `tls: verified`가 출력되면 접속 확인 성공입니다.
`--check`는 작업을 배정받지 않습니다. 이 결과를 중앙 담당자에게 알려주세요.

## 배경 작업 한 건 처리

Head 작업에는 `assignment.json`의 `payload.generation.conditions`에 템플릿별 필수 조건이 있습니다.
`output_name` 파일명, `alpha_required`, `allowed_render_modes`, `behaviors`, `notes`를 제작 프로그램에 함께 전달합니다.
배경의 `background_usage: style_reference`는 UE 화면용이 아닌 에셋 색·조명 참고용이라는 뜻입니다.

중앙 담당자가 작업을 등록했다고 알린 뒤 실행합니다.

```powershell
python client.py --claim
Get-Content assignment.json -Encoding utf8
```

`job_claimed`와 `kind: background_generate`를 확인하고 `assignment.json`의 작품·버전·지시를 확인합니다.
`no_ready_job`은 현재 받을 작업이 없다는 뜻입니다. 중앙 담당자에게 등록 상태를 확인하세요.
예상과 다른 작업을 받았다면 테스트 이미지를 업로드하지 말고 작업 ID를 중앙 담당자에게 알려주세요.

별도 PowerShell 창을 열어 임대를 갱신합니다. 작업 중에는 이 창을 유지합니다.

```powershell
cd C:/Ranel/generation-kit
python client.py --watch
```

본래 창에서 생성 프로그램을 실행해 해당 작업의 결과만 전용 폴더에 저장합니다.
첫 연결 테스트는 키트에 포함한 작은 PNG로 진행할 수 있습니다. 실제 AI 생성물은 아닙니다.

```powershell
New-Item -ItemType Directory -Path C:/Ranel/output/connection-background -Force | Out-Null
Copy-Item test-files/background.png C:/Ranel/output/connection-background/background.png
python client.py --complete --source C:/Ranel/output/connection-background
```

`result_registered`, `ok: true`가 성공 기준입니다. 실제 생성 시에는 `--source`를 실제 결과 폴더로 바꿉니다.
공유 폴더나 `--store`는 필요 없습니다. 중앙이 파일 크기와 SHA-256을 검사합니다.

## 에셋 작업 한 건 처리

배경 완료 후 중앙 담당자가 에셋 작업을 등록하면 다음 명령을 실행합니다.

```powershell
python client.py --claim
Get-Content assignment.json -Encoding utf8
python client.py --inputs C:/Ranel/input/connection-asset
```

`kind: assets_generate`인지 확인합니다. 배경은 입력 폴더 아래 `<배경작업ID>/background.png`로 내려옵니다.
이 파일을 생성 모델에 제공해 어울리는 에셋을 만들고 전용 폴더에 저장합니다.
첫 연결 테스트에서는 다음 고정 알파 PNG를 사용합니다.

```powershell
New-Item -ItemType Directory -Path C:/Ranel/output/connection-asset -Force | Out-Null
$assignment = Get-Content assignment.json -Raw -Encoding utf8 | ConvertFrom-Json
$assetFileName = $assignment.payload.generation.conditions.output_name
if (-not $assetFileName) { $assetFileName = 'sprite.png' }
Copy-Item test-files/sprite.png (Join-Path C:/Ranel/output/connection-asset $assetFileName)
python client.py --complete --source C:/Ranel/output/connection-asset
```

완료 후 배경·에셋 작업 ID와 `result_registered` 결과를 중앙 담당자에게 알려주세요.
Head 작업은 `asset-001.png`처럼 지정한 파일명을 사용합니다. 수동 통신 테스트는 기존 `sprite.png`를 사용합니다.
UE 조립 요청의 source_name도 실제 완료 파일명에 맞춰야 합니다.
진행 중 실패는 `python client.py --fail generation_failed`로 보고합니다.
작업을 모두 마치고 대기를 종료할 때 heartbeat 창에서 Ctrl+C를 누릅니다.

## 주의할 파일과 전달할 정보

- 같은 키트를 여러 작업 프로세스에 복제해 동시에 실행하지 않습니다. 병렬 작업에는 서로 다른 노드 ID가 필요합니다.
- `node.secret.json`, `assignment.secret.json`, `completion.secret.json`은 토큰이므로 전달하지 않습니다.
- 문제 보고에는 PC IP, 노드 ID, 작업 ID, 실행 명령, 오류 코드만 포함합니다.
- 인증서 오류가 나면 중앙 주소·CA를 확인합니다. 인증서 검증을 끄지 않습니다.

전체 합동 테스트 순서는 [연결 테스트](CONNECTION_TEST.md)를 따릅니다.
