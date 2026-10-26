# packages/contracts — 주고받는 형식

PlaybackReport의 선택 필드 cached_verification_job_ids·cache_errors·current_repeat는 작품별 준비와 현재 반복을 보고합니다. 누락은 미확인이며 빈 목록(준비 없음)과 구분합니다.

`native_artworks.py`는 고정 native 작품 ID, 요청 ID, revision을 포함한 선택 재생/정지 계약이다. 임의 명령·파일 경로를 전달하지 않는다.

`head.OperatorHeadIntake`는 운영 앱의 고정 요청 ID와 기존 제작 입력을 전달하는 비동기 접수 계약이다.
`head.ExhibitionFeedToggle`은 운영 앱이 [전시 자동 순환](../../infra/HEAD_EXHIBITION_FEED.md)을 켜고 끄는 요청(`{"enabled": true | false}`)이다.
Head의 실제 실행 요청은 계속 `HeadRequest`를 사용한다.

`schemas.py`의 `CreateRequest.theme`은 기존 운영 입력과 같이 최대 2,000자를 허용합니다. 계정 로그인 추가로 이 한도를 줄이지 않습니다.

`auth.py`는 운영 계정 로그인·관리자 계정 발급·비밀번호 변경 요청입니다. 비밀번호 필드는 SecretStr이며 오류 응답에 원문을 포함하지 않습니다.

`show_dashboard.py`는 시연 노드별 순서·시간·반복·자동 순환 구성과 revision 갱신 모델입니다. PC2가 조회하여 로컬에서 실제 재생합니다. `infra.py`의 선택적인 playback 보고는 적용 revision·구간 시간·준비 상태를 제공합니다.
시간은 `ShowPlan.duration_seconds`의 공통 값(기본 120초)이며 각 작품은 반복·사용 여부만 따로 지정합니다.

`feedback.py`는 시연 정보와 의견의 대상·저장·활성 변경 API 모델입니다.
작품 버전과 Runtime 버전을 구분하며 [작품 피드백](../../docs/ARTWORK_FEEDBACK.md)의 대상 고정·중복 방지 규칙을 사용합니다.

UE 템플릿·배경 관찰·원본 에셋과 복제 배치·상호작용 대상·효과·입자 명세는 [Head 시나리오 감독](../../infra/HEAD_SCENARIO_DIRECTOR.md)을 따릅니다.

새 요청의 자동 생성·조립·AI 검수·재작업·게시 정책은 [Head 자동 사이클](../../infra/HEAD_AUTOMATION.md)을 따른다.

UE 조립 이후 별도 실행 검증·승인·PC2 게시 연결은 [UE 실행 흐름](../../infra/UE_RUNTIME_PIPELINE.md)를 따른다.

`infra.py`의 `AssemblySpec`과 `AssemblyFile`은 생성 결과를 참조하는 UE 조립 작업 입력입니다.
HTTP 파일 전송은 작업 메타데이터와 별도 API를 사용합니다.

`head.py`는 분산 Head 요청·배경 판단·에셋 N개 판단·배경 이미지 전송 형식입니다.
Head 요청에는 `spec`이 필요하며 객체 수는 MR !33 상한인 12개까지입니다.
`ue.py`는 제작 노드의 템플릿 리소스 보고와 생성 조건입니다. 선택·조건·지원 해시는 중앙이 고정합니다.

`ArtworkOut.preparation`은 필수 출력·버전·출력별 준비 상태/실패 원인을 제공합니다. 모든 출력 성공 시 `status=prepared`이며 `runtime_phase=Unknown`입니다. 실제 Show 보고는 별도 `ShowReport`의 Staged·Ready·Applied로 구분합니다.

현재 `schemas.py` 기반 동기 개발 경로는 `td`·`ue`·`both`를 유지합니다. 새 작품 명세(`spec.py`)와 원문 매칭은 별도 구현이며 운영 화면과의 전체 연결은 후속 작업입니다. 필드의 존재를 실제 UE 구현 완료로 해석하지 않습니다. [복원 기록](../../docs/refactoring/post-mr23-restoration.md)을 참고합니다.

장비와 단계 사이에 오가는 데이터의 모양을 정의합니다. 작품 요청, 계획(의도 · 구성 · 안무), API 응답, 표현 방식 목록이 여기 있습니다.

`infra.py`는 비동기 작업 · 임대 · 파일 해시와 크기 · 작품 번들 · 시연 상태의 공통 형식입니다.
메시지에는 바이너리나 장비별 절대 경로를 넣지 않습니다. 공유 폴더 위치는 각 Agent가 설정합니다.
생성 작업은 작품 ID·버전·프롬프트와 선행 작업 ID를 포함합니다. 생성 결과는 상대 파일명·해시·크기이며,
배경 생성과 에셋 생성을 구분합니다. 런타임 작품 번들과 생성 중간 결과는 다른 계약입니다.

- **실행 장비**: 모든 장비(central · 운영 PC 앱 · 제작 노드 · 시연 노드)가 같은 형식을 써야 하므로 공통입니다.
- **가져다 쓰는 것**: Pydantic만 씁니다. 우리 다른 패키지는 import하지 않습니다.
- 형식을 바꾸면 이 형식을 읽는 쪽(central API, 운영 화면, `studio`)을 함께 고칩니다. 렌더러로 보내는 파일 형식(`artwork.json` · `scene.json`)은 `studio`의 `assembly`가 만들며 [docs/간접표현_작품구조.md](../../docs/간접표현_작품구조.md)에 정리돼 있습니다.

| 파일 (`src/contracts/`) | 내용 |
|---|---|
| `schemas.py` | 입력 · 계획 · 응답 모델. 작품 요청(`CreateRequest`), 의도(보이지 않는 주인공 · 드러내는 방식), 안무 단계, 입력 · 반응 매핑 |
| `families.py` | 8가지 표현 방식(UE 모듈 · 필요한 소재 · 반응 메뉴), 드러내는 방식별로 쓸 수 있는 표현 방식 |
| `spec.py` | Head 원문 매칭과 테스트가 사용하는 작품 명세. 값 목록(계열·입력·반응·매질·2D/2.5D 등), 자연어 표현, 계열별 칸 상태(필수·선택·막힘), 검사 규칙(막힌 칸은 `null`, 의도적으로 비운 선택 칸은 `"none"`). 운영 제작 경로 전체에는 아직 연결되지 않음 |
| `assets.py` | UE 작품 소재 목록 `assets.json` v1(자동 조립 입력). 생성 쪽이 보내는 파일마다 역할을 적습니다: 층 PNG(뒤 · 중간 · 앞, 층 하나에 이미지 하나), 층 재질 값(허용 이름과 범위), 오려 낸 객체 PNG(화면 좌표 · 크기 · 행동: 떠다님 · 따라감 · 피함 · 빛남 · 돌아다님(`wander`: 화면을 날아다니며 손이 오면 피함. 위에서 본 좌우 대칭 그림만, 그림 위쪽이 머리), 공용 카탈로그 범용 Sprite에 얹힘), 깊이 지도(보관만), 실루엣 JSON. 무엇을 만들지 · 드러내는 방식 · 인터랙션은 작품 명세(`spec.py`)가 맡고 여기에는 넣지 않습니다. 모르는 칸은 거부하지 않고 경고로 돌려줍니다. 파일 자체 검사는 [verify](../studio/src/studio/verify/README.md)의 `inspect_assets` |

`spec.py`의 설계는 [분산형 설계](../../docs/architecture/distributed-system-design.md)의 3.3절(칸 상태)과 6절(값 목록·추출 근거)입니다. Head 원문 매칭은 이 형식을 사용하며 운영 PC 전체 명세 편집 UI 연결은 후속 작업입니다. 기존 `schemas.py`와 별도 계약이므로 `field`를 `Reveal.none`으로 대응시키는 것만으로 두 경로가 연결되지는 않습니다.

예정: 분산 구조의 작업 · 임대 · 결과 보고 · 게시 명령 형식. 설계는 [분산형 설계](../../docs/architecture/distributed-system-design.md)의 「PostgreSQL 데이터 모델」입니다.
