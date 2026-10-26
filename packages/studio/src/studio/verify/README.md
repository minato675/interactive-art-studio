# studio/verify — 검증

입자처럼 화면 변화 비율이 포화되는 장면은 손 궤적 영역과 궤적 밖 대조 영역의 평균 채널 변화도 함께 잰다.
무입력의 영역 간 차이 범위를 구한 뒤, 입력 중 차이가 그 범위를 얼마나 벗어나는지 `interaction_isolation`에 남긴다.
이는 국소 반응의 보조 근거이며 비결정적 입자장의 동일 시점 무입력 재생을 대신하지 않는다.
양수·음수 변화(입자 밀림으로 변화가 줄어드는 경우 포함)를 모두 기록하고, 전역 변화·잡음과 구분되지 않으면
inconclusive로 유지한다. 이 수치만으로 기존 review를 pass로 올리거나 센서 검증을 완료 처리하지 않는다.

개발용 소재 풀에 들어오는 이미지의 규칙 검사입니다. 현재 호출부는 `generation.assets.gather`이며, 검사 함수는 탈락 사유를 반환합니다. 실패 시 새 파일 정리는 중앙 파이프라인이 맡습니다. 검사 통과만으로 작품 전체의 게시나 실제 전시 적용을 승인하지 않습니다.

- **실행 위치**: 현재는 중앙의 개발용 로컬 프리뷰 경로입니다. 목표 구조의 인터랙션·성능 등 UE 실행이 필요한 검사는 제작 노드에서 수행해야 합니다.
- **가져다 쓰는 것**: [db](../../../../db/README.md)(소재 풀 중복 확인), [controller](../../../../../controller/README.md)(PNG 검사·해시). controller 구현의 출처는 [provenance](../../../../../docs/provenance/README.md)를 참고합니다.

| 파일 | 내용 |
|---|---|
| `checks.py`의 `inspect_image` | 현재 사용. PNG 파일 형식·화면 비율·최소 해상도·압축률 기반 빈 화면 추정·해시 중복을 검사 |
| `checks.py`의 `inspect_subject` | 복원한 `subjects.py`가 호출. 실루엣 점·면적과 흔적 경로를 검사 |
| `checks.py`의 `inspect_assets` | UE 작품 소재 목록(`assets.json`, [contracts](../../../../contracts/README.md))이 가리키는 파일을 검사: 있는지, PNG 규칙·4096 이하, 맨 뒤가 아닌 층과 객체 PNG의 알파, 실루엣 기준 |
| `ue_contract.py` | UE 자동 조립용 scene 정적 검사 `check_scene` — **지금 라넬 runtime 계약** 기준. vendored [controller](../../../../../controller/README.md) 검사기는 예전 runtime 기준이라 조명 · 재질 설정(`Look`)과 GPU 입자(`GpuFlow`)를 거부합니다. controller는 라넬 사본이라 고치지 않고, 최신 runtime 헤더에서 뽑은 계약(`ue_contract/scene-structs.json`)을 controller 검사기에 넘기고, `Look`은 runtime 파서 규칙 그대로 · `GpuFlow`는 범위와 장면 입자 예산 · 고정 무대(`stage.json`) 일치를 여기서 더 봅니다. 오프라인으로 알 수 없는 것(자산 실제 등록, Niagara 계약, 행동과 Look의 재질 출력 중복)은 `unverified`로 남겨 PC1 실행 검증이 맡습니다. `catalog_version`(카탈로그 JSON 해시), `runtime_drift`(핀 이후 바뀐 runtime 계약 파일), `persistence`(장면의 손 뗀 뒤 화면 반감기와 그 매질. 조립기 경고와 실행 검증이 같이 씀)도 있습니다 |
| `png.py` | PNG 읽기(8비트 RGB/RGBA, 표준 라이브러리만). UE 화면 캡처 비교와 조립기의 앞 층 불투명 비율 계산에 씁니다 |
| `ue_runner.py` | PC1 UE 실행 검증 러너. 전시(PC2)와 같은 Shipping 실행본 · 같은 `release.json` 교체 경로로 검수 폴더의 버전을 재생해 판정합니다: 이번 프로세스의 `Applied`, 준비 시간 · 메모리, 빈 화면 아님, 합성 손 2개(UDP protocol v2. 장면에 물결 범위 `DropAreaMin/Max`가 있으면 궤적을 그 물 영역 안으로 옮김. 범위가 화면 한쪽에만 있어 좌우 반전 때 벗어나면 사람 확인) 궤적 주변에서 입력 중 변화가 입력 없이 같은 간격으로 잰 평소 변화보다 충분히 큰지(루프 영상 · 유체처럼 원래 움직이는 장면도 손 반응만 가려내도록. 손 주변이 평소에도 절반 넘게 바뀌면 화면으로는 판정하지 않음. 평소 변화가 10%를 넘고 평소 3쌍의 흔들림이 반응 기준보다 크면 손 반응 미달은 손이 장면 행동에 닿았을 때 사람 확인으로), 입력 뒤 손 주변이 평소 흐름 수준으로 돌아오는지(검수 폴더의 `scene.json`에서 `persistence`로 화면 반감기 h를 구하면: h 2.5초 이상은 손 뗀 뒤 5초에 잔상이 남는지 보고, h 2.5~12초는 min(4h, 20)초 뒤 화면이 가라앉고 잔상이 옅어졌는지(가라앉음은 그 뒤 3.2초 사이 변화가 작거나, 진한 잔상처럼 아직 옅어지는 중이면 5초 → 복귀 → 3.2초 뒤로 갈수록 평소 화면에 계속 가까워지고 끝이 5초 때의 0.8배 이하인지로 봄. 기준은 [검증 문서](../../../../../docs/architecture/ue-verification.md)), h 2.5초 미만은 지금처럼 3초 뒤 복귀를 봄. h 12초 이상(남아 있는 장면)은 복귀를 보지 않음. 비교 간격을 맞추려고 잔상 검사 때만 평소 화면을 1장 더 찍음. 물결 · 입자 매질과 "줄어듦" 미달은 PC1 기준 보정 전까지 사람 확인(needs_review)으로만 둠. h를 모르면 지금처럼 3초 뒤 복귀), 두 손 수신과 손이 장면 행동에 닿았는지(`Trace.jsonl`의 `BehaviorInputs.ActiveContacts`), 입력 구간 초당 긴 프레임(16.7ms 초과, 저장분 + `DroppedLongFrames`), 프로세스 유지. 캡처는 매번 새 ID로 요청합니다(runtime은 같은 ID 반복과 이미 있는 PNG를 무시하므로 예전 화면을 재사용하지 않으려고). 리포트에는 측정값 · 스크린샷 경로 · 규칙 버전 · 번들 해시 · 카탈로그 버전 · 측정 전 GPU 사용량이 남고, 긴 프레임이 많은데 측정 전부터 GPU가 바빴으면 '환경 의심'으로 표시합니다. 실행본은 검증 사이에도 계속 띄워 두므로 계측 파일 `Trace.jsonl`이 계속 커집니다(10/22~23 PC1에서 2.26GB까지 커져 검증이 멈춤). 그래서 검증을 시작할 때마다 `Trace.jsonl`을 `Trace.previous.jsonl`로 바꿔 직전 것 하나만 남기고(runtime은 기록마다 파일을 열고 닫아 실행 중에도 바꿀 수 있음), 읽을 때는 지난번 읽은 위치 뒤에 붙은 완성된 줄만 읽습니다. 실행본은 계속 띄워 두고, central을 import하지 않아 제작 노드 Agent가 그대로 쓸 수 있습니다 |

`ue_contract/` 기준 데이터:

| 파일 | 내용 | 다시 만드는 법 |
|---|---|---|
| `scene-structs.json` · `runtime-pin.json` | runtime 헤더에서 뜬 scene 계약 · 따라가는 runtime 커밋과 계약 파일 해시 | 아래 갱신 명령 |
| `runtime-integration.json` | 공용 전시 맵(`RuntimeIntegrationProof`) 카탈로그. 패턴 · 객체(층 객체 `Layer_Far` · `Layer_Mid` · `Layer_Near` 포함) · 텍스처 파라미터. `shared_catalog()`로 읽고, `catalog_version()`이 이 파일의 해시 | UE 에디터에서 [ue/tools/export_catalog.py](../../../../../ue/README.md) |
| `stage.json` | 공용 전시 맵 고정 무대(Scene 행 값: 헤더 기본값 + 좌우 반전). `shared_stage()`로 읽음 | `stage_from_defaults()` + `bMirrorHorizontal: true` |

`ue_contract/runtime-pin.json`에 따라가는 runtime 커밋과 계약 파일 해시가 있습니다. runtime이 바뀌면 `runtime_drift`로 확인하고, 라넬 runtime clone을 그 커밋으로 맞춘 뒤 `uv run python -m studio.verify.ue_contract <runtime clone 경로>`로 계약 사본을 다시 뜹니다. 바뀐 파일의 규칙은 `ue_contract.py`에도 직접 반영해야 합니다.

이미지 비율·해상도·압축률 기준은 현 프로토타입과 소재 풀의 정책입니다. 회사 UE 전체의 입력 규격이나 AI 품질 판정을 대신하지 않습니다. 주인공 생성 호출부는 [subjects.py](../generation/subjects.py)이며, MR !23 당시 사본도 archive에 보존합니다.

UE scene의 정적 계약·카탈로그·수치 검사는 [assembly](../assembly/README.md)의 `ue_scene.check`가 담당합니다. 분산 Agent의 `bundle_verify`는 별도의 파일 무결성 검사이며 `publish_approved=False`를 유지합니다. 어느 쪽도 실제 UE의 인터랙션·복귀·성능을 실행 검증한 결과는 아닙니다.

후속 작업은 독립 AI 판정(테마·스타일 조합, 마스크 품질), 제작 노드의 인터랙션·복귀·성능 검사, 실패 유형별 재생성·전처리·재조립 연결과 자동 게시 승인입니다.

교체가 계속 거부되는 경우에도 검증 시작 당시 파일 끝으로 읽기 위치를 옮깁니다. 새 Runner가 큰 기존 파일을 처음부터 읽지 않고, 이후 추가되는 계측만 판정합니다. 파일 끝이 쓰는 중인 줄이면 그 줄은 제외하며 다음 완성 줄부터 사용합니다.
