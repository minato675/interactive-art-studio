# 생성 개발자 전달 — Head 자동 사이클 연결

중앙 EC2: https://k15a504.p.ssafy.io (443). 생성 PC 한 대에서 배경 전담 1개·에셋 전담 2개를 운영한다. 기존 노드 ID·토큰을 유지한다.

## 바뀐 데이터 흐름

1. 중앙 Head가 배경 프롬프트와 conditions를 등록한다.
2. 배경 실행기가 생성·전처리한 background.png를 중앙에 업로드하고 완료한다.
3. 중앙이 저장 원본의 SHA-256을 확인해 이미지 입력으로 읽는다. **생성 개발자가 background-input을 따로 호출하지 않는다.**
4. 개수가 지정됐으면 그 개수, 미지정이면 Head가 1~max_asset_count(기본 12)의 에셋을 결정한다.
5. 에셋 실행기 둘이 각각 작업을 받는다. 각 작업의 선행 입력에는 실제 배경 이미지가 포함된다.
6. 에셋 N개가 완료되면 중앙이 assets.json 메타데이터·파일 대응표·UE 조립 작업을 자동 등록한다. 생성 개발자가 전체 assets.json을 수동 작성하거나 PC1에 직접 파일을 보낼 필요가 없다.
7. PC1 검증 후 Head가 보고서·캡처를 검수한다. 재작업이면 새 job_id로 배경 또는 에셋 작업이 다시 온다. 기존 결과 파일은 바꾸지 않는다.

## 설치·자동 실행

최신 생성 ZIP을 새 폴더에 해제한다. background·assets1·assets2의 node.secret.json을 서로 바꾸지 않는다. 진행 중 작업을 먼저 정리하고 이전 실행기·수동 heartbeat·동일 노드의 Codex 작업 루프를 종료한 뒤 전환한다.

각 폴더에서 generation.local.example.json을 generation.local.json으로 복사한다. cache는 실행기별로 다른 경로, working_directory는 생성 프로그램 저장소, handler는 해당 모델·실행 세션을 사용하는 로컬 어댑터의 argv 배열로 지정한다.

```powershell
python client.py --check
python generation_worker.py --check
python generation_worker.py
```

**생성 개발자가 연결할 부분은 handler 프로그램이다.** `--request task.json --output 빈폴더`를 받아 실제 생성 API·전처리를 수행하고 지정된 결과를 저장해야 한다. 이미 있는 생성 프로그램에 이 인터페이스를 연결하면 된다. 키트가 이미지 모델이나 Codex 실행을 대신 제공하는 것은 아니다. 상세 필드와 예시는 GENERATION_HANDLER.md에 있다.

## 반드시 지킬 규약

- task.json의 generation.prompt·conditions와 현재 작업의 입력을 사용한다. 에셋은 실제 배경을 이미지 생성기의 참조 이미지로 전달한다.
- background.png 또는 asset-001.png 등 conditions.output_name을 정확히 지킨다.
- 객체는 독립 RGBA PNG이며 투명 여백을 확보한다. 현재 2.5D도 UE Sprite용 이미지다. 메시·깊이 지도·다시점 이미지로 바꾸지 않는다.
- 이미지 수는 중앙 작업 수를 따른다. 작업 하나에서 전체 에셋 세트를 임의로 만들지 않는다.
- output에는 현재 작업의 최종 파일만 넣는다. 미리보기·로그·키·UE 프로젝트는 별도 경로에 둔다.
- 성공은 exit 0, 실패는 비정상 종료 코드다. handler가 자체 자식 프로세스를 쓰면 종료까지 기다린다.
- 중앙·임대 토큰은 task.json에 포함되지 않는다. API 모델 키는 생성 PC의 환경·보안 파일에서 읽고 보고서에 넣지 않는다.
- 자동 실행기 자체가 heartbeat·완료 재전송을 담당한다. 별도 --watch를 동시에 띄우지 않는다.

에셋 생성 실패·Head API 오류·UE 환경 이상은 보류되고 무한 생성하지 않는다. 자동 재작업은 기본 두 번, 최대 세 번이다. 세부 정책은 HEAD_AUTOMATION.md를 따른다.
