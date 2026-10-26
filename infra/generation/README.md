# infra/generation — 생성 PC 작업 도구

생성 PC(worker)가 중앙에서 받은 작업을 **UE 템플릿에 맞는 재료**(층 PNG · 객체 PNG · `assets.json`)로 만들어 넘기기 전에 쓰는 점검 · 가공 도구입니다.
작업 받기 · 결과 등록은 [`infra/client.py`](../WORKFLOW.md)가 하고, 그림 생성은 Codex가 합니다. 이 폴더의 도구는 그 사이에서 "무엇을 만들지"와 "넘겨도 되는지"를 판단합니다.

## 자동 실행기 연결: `handler.py`

중앙 Head 자동 사이클에서는 생성 키트의 `generation_worker.py`가 작업 받기 · 입력 내려받기 · heartbeat · 결과 업로드를 맡고,
작업마다 생성 프로그램을 `handler --request <task.json> --output <빈 폴더>`로 부릅니다(키트의 `GENERATION_HANDLER.md`).
`handler.py`가 그 생성 프로그램입니다. 중앙이 배경 업로드 · `assets.json` 작성 · UE 조립 요청을 하므로 생성 PC는 이 handler만 연결합니다.

| 단계 | 하는 일 |
|---|---|
| 읽기 | `task.json`의 지시문(`generation.prompt`), 조건(`generation.conditions`), 선행 결과(`inputs`, `input_root`) |
| 지시문 | Head 지시문 + 조건 + 기술 규칙(저장 경로 · 투명 배경 · 비율 · 여백 · 글자 금지)으로 Codex 지시문을 만듭니다. 화풍 · 내용은 Head 지시를 따르고 handler가 덧붙이지 않습니다. 에셋은 내려받은 실제 배경을 참조 이미지로 첨부하고, 배경의 대표 색 5개를 색상 코드로 함께 넣습니다 |
| 생성 | `codex exec`로 Codex 내장 이미지 도구를 실행합니다(ChatGPT 로그인 사용, API 키 불필요). 작업 폴더는 기록 폴더 아래에 둡니다 |
| 가공 | 배경: 가운데 16:9로 자르고 권장 크기로 맞춥니다. 에셋: 알파가 없으면 바깥 배경색을 보고 순수 검정 또는 단색 초록(#00FF00)을 투명으로 바꾸고, 반투명 가장자리의 검은 · 초록 테두리를 지웁니다. 투명 여백을 두고 권장 크기 이하로 줄입니다 |
| 검사 | 규격: `output_name`, PNG, 알파 조건, 배경 16:9, 에셋 투명 픽셀 1% 이상, 한 변 4096 · 32MiB 이하. 품질: 배경이 거의 단색이 아닌지(그림 휘젓기 캔버스는 세부까지), 에셋이 가장자리에 잘리지 않았는지 · 너무 작지 않은지 · 배경 대표 색과 색감이 동떨어지지 않았는지. 점수는 시도마다 `report.json`에 남깁니다 |
| 재생성 | 그림이 안 나오거나 가공 · 검사에 걸리면, 실패 이유를 지시문에 덧붙여 같은 작업 안에서 다시 생성합니다. 최대 3번(`--attempts`), 전체 25분(`--budget-seconds`) 안에서만 하고, 남은 시간이 부족하면 멈춥니다 |

- 결과 폴더에는 `output_name` 파일 하나만 둡니다. 지시문 · Codex 원본 · 로그는 기록 폴더(`--records`, 기본 `C:/Ranel/generation/records/<작업ID>`)에 시도별(`attempt-1`, `attempt-2` …)로 남기고, `report.json`에 시도마다의 결과를 적습니다.
- **사용하는 Codex**: PATH에서 이름이 `codex`인 첫 프로그램은 쓰지 않습니다. conda(base)가 켜진 터미널에서는 miniforge3의 동명 패키지(`codex.exe`)가 먼저 잡혀 생성이 모두 실패한 적이 있습니다. 확인된 설치만 이 순서로 씁니다.
  1. `--codex-path <절대 경로>` 또는 환경 변수 `RANEL_CODEX_PATH`: 지정하면 이것만 씁니다. `--version`이 `codex-cli`로 시작해야 합니다
  2. npm 전역 설치(`npm i -g @openai/codex`)의 `node_modules/@openai/codex/bin/codex.js`를 node로 실행
  3. Codex 앱에 포함된 공식 실행 파일 `%LOCALAPPDATA%\OpenAI\Codex\bin\<폴더>\codex.exe`: `--version`이 `codex-cli`인 것 중 가장 높은 버전(폴더 이름은 해시일 수 있음)
  - 실행 전 점검: `uv run --no-project --python 3.11 --with pillow python infra/generation/handler.py --check`가 사용할 명령과 버전을 출력합니다. 못 찾으면 종료 코드 2입니다. 작업 중에 Codex를 못 찾으면 재생성 없이 한 번에 실패합니다
- Codex는 별도 프로세스 그룹으로 실행하고, 시간 초과면 자식 프로세스(node가 띄운 Codex 본체 등)까지 끝낸 뒤 다음 시도로 넘어갑니다. 이전 생성이 남아 다음 시도와 겹치거나 사용량을 더 쓰지 않게 하기 위해서입니다.
- 횟수 제한이 있는 이유: 실행기가 handler를 최대 `timeout_seconds`(기본 1800초)까지만 기다리고, 같은 지시로는 계속 실패하는 경우(예: 지시문이 생성하지 말라고 함)가 있으며, 시도마다 Codex 사용량이 나갑니다.
- 종료 코드: 0 성공, 2 입력 오류(재생성 안 함), 3 Codex 실패 · 결과 없음, 4 규격 검사 실패(마지막 시도 기준). 모든 시도가 실패하면 실행기가 중앙에 실패로 보고하고, 이후 재작업은 중앙이 정합니다.
- 품질 기준은 명백한 불량만 거르도록 느슨하게 잡았습니다. 2026-10-23 실제 결과(숲 배경 4장, 에셋 3장)가 모두 통과하는 값이며 `handler.py` 상수에 측정값을 적어 두었습니다. 쌓인 점수를 보고 조입니다.
- 화풍 · 내용이 지시와 맞는지는 판단하지 않습니다. 중앙 Head가 UE 결과를 보고 재작업을 정합니다.
- Pillow는 프로젝트 의존성에 넣지 않고 `uv run --with pillow`로 실행합니다. 키트의 `generation.local.json` 예:

```json
{"cache": "C:/Ranel/generation/assets1/cache",
 "working_directory": "<저장소 루트>",
 "handler": ["C:/Users/<사용자>/.local/bin/uv.exe", "run", "--no-project", "--python", "3.11", "--with", "pillow",
             "python", "<저장소 루트>/infra/generation/handler.py", "--records", "C:/Ranel/generation/records"],
 "timeout_seconds": 1800}
```

실행기마다 `cache`를 다르게 둡니다. 에셋 실행기 두 개가 동시에 Codex를 부르므로 에셋 두 장이 병렬로 생성됩니다.

- **실행 장비**: 생성 PC 1대(Windows). 중앙 Docker 이미지에는 포함되지만 중앙에서 실행하지 않습니다.
- **가져다 쓰는 것**: [contracts](../../packages/contracts/README.md)의 작품 명세(`Spec`) · 소재 목록(`assets.json`), [studio/assembly](../../packages/studio/src/studio/assembly/README.md)의 UE 조립기(`select_template` · `assemble`)
- **렌더링은 하지 않습니다.** UE가 받을 재료와 값을 만들고, 조립기가 만든 `scene.json`이 UE 계약 검사를 통과하는지까지만 확인합니다. 화면에 어떻게 보이는지는 UE(PC1)에서 확인합니다.

## 도구

| 파일 | 하는 일 |
|---|---|
| `handler.py` | 자동 실행기용 생성 프로그램(위 절). 작업 하나 = 그림 하나 |
| `preflight.py` | 생성 전 점검. 명세의 필수 칸을 먼저 확인하고, 비어 있으면 템플릿을 고르지 않고 보류합니다. 그다음 템플릿 선택(조립기와 같은 `select_template`) → 만들 재료 목록. 보류면 종료 코드 1이고 생성하지 않으며, `--choice` 경로에 이전 실행의 선택 결과가 있으면 지웁니다. 성공하면 선택 결과를 `choice.json`으로 저장해 조립 때 다시 고르지 않게 합니다 |
| `crop169.py` | 생성 이미지를 가운데 기준 16:9로 자릅니다(이미지 모델은 1536×1024 같은 3:2로 냄) |
| `luma_alpha.py` | 검은 배경에 생성한 **빛나는** 요소를 알파 PNG로 바꿉니다(밝기 → 투명도). 밉맵이 없으므로 화면 표시 크기에 맞게 줄입니다. 불투명한 물체(동물 · 사람 등)에는 쓰지 않습니다 |
| `handoff_check.py` | 넘기기 전 점검. 결과 폴더(`assets.json` + PNG)를 조립기 `assemble()`로 미리 조립해 UE 계약 검사까지 통과하는지 봅니다. 실패면 종료 코드 1 |

## 템플릿별 만들 재료

`preflight.py`가 템플릿의 `slots.json`을 읽어 알려 줍니다. 요약하면 다음과 같습니다.

| 템플릿 | 생성 그림이 보이는 방식 | 만들 재료 |
|---|---|---|
| 그림 휘젓기 `image_stir` | 뒤 층 그림 전체가 손으로 휘저어지는 캔버스 | far 1장(불투명 가능, 16:9) |
| GPU 입자 장 `gpu_particles` | 층들이 투명한 입자 뒤에 깔려 보임 | far 1장(불투명 가능). mid · near는 알파 필수 · 선택 |
| 유체 · 그리기 · 표면 변형 | 불투명 효과 캔버스가 화면을 덮어 far가 거의 안 보임 | far + 알파가 있는 mid · near가 있어야 그림이 보임 |

객체(오려 낸 알파 PNG, 최대 12개)는 모든 템플릿에 넣을 수 있습니다. 따라감 · 피함(`follow` · `flee`)은 그림 휘젓기 · GPU 입자 장에서만 받습니다.

## 생성 PC 준비(최초 한 번)

1. [uv](https://docs.astral.sh/uv/)를 설치하고 저장소 루트에서 `uv sync`를 실행합니다.
   - 시스템 Python이 알파판(예: 3.12.0a5)이면 빌드가 실패합니다. 이때는 `$env:UV_PYTHON_PREFERENCE="only-managed"` 후 `uv sync --python 3.12`로 uv가 관리하는 정식 Python을 씁니다.
2. Codex를 설치하고 로그인합니다(`codex login status`). npm 설치와 Codex 앱 설치 모두 됩니다. 다른 위치에 있으면 `generation.local.json`의 handler 배열에 `--codex-path <절대 경로>`를 넣습니다. 설치 후 `handler.py --check`로 확인합니다.
3. 작업 폴더를 만듭니다. 예: `C:\RanelInput`, `C:\RanelOutput`, `C:\RanelAssets`(D 드라이브가 있으면 [WORKFLOW](../WORKFLOW.md)의 `D:\` 경로).
   `RanelAssets`는 UE 제작 · 시연 PC가 SMB로 읽는 공유 저장소입니다([저장소 설계](../STORAGE.md)). SMB 공유 설정은 OS에서 따로 합니다.
4. 중앙 운영자에게 이 PC의 접속 키트(서버 주소 `server.json` · CA · 노드 토큰)를 받아 압축을 풉니다([노트북 · EC2 이관](../LAPTOP_SERVER.md)). 토큰은 Git · 메신저 · Codex 프롬프트에 붙이지 않습니다.
5. 키트 폴더에서 `python client.py --check`로 연결을 확인합니다. 자동 실행기를 쓰면 `generation.local.json`을 설정하고(위 「자동 실행기 연결」) `python generation_worker.py --check` 후 `python generation_worker.py`를 띄웁니다. 실행기가 heartbeat를 보내므로 같은 노드로 `--watch`를 함께 띄우지 않습니다.

**네트워크**: SSAFY 망에서는 외부 8443 포트로 나가는 연결이 막혀 있었습니다(2026-10-21, 443은 연결됨). EC2 중앙은 443으로 엽니다. EC2 보안 그룹에는 생성 PC의 **공인 출구 IP**를 허용합니다(내부 IP와 다름).

## 수동 처리 순서(자동 실행기를 쓰지 않을 때)

명세 파일(`spec.json`)을 받았다고 가정합니다. 경로는 예시입니다. 이때는 별도 창에서 `python client.py --watch`를 유지합니다.

```powershell
# 1. 작업 받기
python client.py --claim

# 2. 생성 전 점검: 템플릿 · 만들 재료 확인, 보류면 여기서 멈추고 --fail
uv run python infra/generation/preflight.py spec.json --choice choice.json

# 3. Codex로 재료 생성 → 결과 폴더(넘길 파일만)에 정리
uv run --no-project --with pillow python infra/generation/crop169.py raw\far.png out\far.png
uv run --no-project --with pillow python infra/generation/luma_alpha.py raw\firefly.png out\firefly.png --size 256

# 4. out\assets.json 작성(contracts/assets.py 형식) 후 넘기기 전 점검
uv run python infra/generation/handoff_check.py spec.json choice.json out --scene scene.preview.json

# 5. 통과하면 등록(HTTP 전송 작업. 공유 저장소 작업이면 --store <저장소> 추가)
python client.py --complete --source out
```

- 결과 폴더에는 넘길 파일만 둡니다. `handoff_check.py`가 `assets.json`에 없는 파일을 `unlisted_files`로 알려 줍니다.
- 미리보기 `scene.json`은 결과 폴더 밖에 저장합니다. `client.py --complete`가 결과 폴더를 하위 폴더까지 모두 올리므로, 결과 폴더 안이면 하위 폴더라도 거부합니다.
- `choice.json`은 UE 조립 쪽에 함께 전달해 `assemble(..., choice)`로 그대로 씁니다. 전달 방법은 UE 담당과 정합니다.
- `crop169.py` · `luma_alpha.py`는 Pillow를 `uv run --with`로 임시로만 씁니다(프로젝트 의존성에 추가하지 않음).

## 확인한 것 · 하지 않은 것

- 2026-10-21 리허설: 숲 명세(stir · move · while) → `image_stir` → Codex 배경 1장(1536×864) + 빛나는 객체 1개(256×209 알파) → `handoff_check.py` 통과. 미지원 명세(bloom)는 `preflight.py`에서 보류.
- 중앙 서버와의 실제 왕복(`--claim` · `--complete`), UE 화면 표시, SMB 공유 접근은 이 리허설에서 확인하지 않았습니다.
- 불투명한 물체의 투명 배경 처리 방법, 층 여러 장 분리(depth map)는 정해지지 않았습니다.
