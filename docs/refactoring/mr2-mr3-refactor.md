# MR !2·!3 후속 아키텍처 정리

> 이 문서는 MR !23 당시의 변경·검증 이력입니다. 이후 사용자 확인으로 `both`와 후속 제작·검수 기능을 복원했습니다. 현재 실행 정책은 [후속 개발 복원 기록](post-mr23-restoration.md)을 따릅니다. 아래의 UE 전용·기본 503·이동 보관 설명을 현재 정책으로 적용하지 않습니다.

작성일: 2026-10-21. 시작점: `develop`의 `5b807c07e0637c3a87f99af4e428d9e6cd776350`.
기존 체크아웃과 회사 저장소의 미게시 변경을 보존하기 위해 별도 작업트리에서 수정했습니다.
MR !2·!3이 만들어진 당시 요구를 소급해 평가하기보다, 확정된 분산 설계와 충돌하는 활성 경로를 정리했습니다.

## 게시한 리뷰

- [MR !2 리뷰](https://lab.ssafy.com/s15-final/S15P31A504/-/merge_requests/2#note_2901789): controller 출처 분류, 입력 프로필 고정, 같은 경로의 원문 차이.
- [MR !3 리뷰](https://lab.ssafy.com/s15-final/S15P31A504/-/merge_requests/3#note_2901751): 동기 제작, TD 대체 성공, 숨은 주인공 정책, 명시한 연출 손실, 손 입력 출처, 파일 게시와 실제 적용 혼동.

두 MR은 이미 병합되어 있어 후속 리뷰 의견으로 게시했습니다. 초기 검토 작성 시점에는 새 변경을 커밋·push하거나 MR을 생성하지 않았으며, 이후 사용자 요청에 따라 후속 MR로 제출합니다.

## 변경 결과

| 이전 경로 또는 가정 | 이번 처리 | 결과 확인 |
|---|---|---|
| 중앙 서버가 요청 안에서 생성·조립·파일 게시를 수행 | `ENABLE_LOCAL_PREVIEW=false` 기본값. 제작·확정·검수 POST는 기본 503 | 초안·기록 조회와 `/infra/jobs` 유지. 기존 UI는 개발 모드를 명시해야 제작 가능 |
| UE 미지원 작품도 TD로 보내면 성공 | UE 전용 설정. `RENDERER=td/both`는 시작 시 거절 | TD 단독·혼합 검수 기록의 승인도 보류하고 기존 후보 포인터 유지 |
| 테마마다 고래·사슴 등 숨은 주인공과 형상·흔적 자동 지정 | 기본 `subject=None`, `reveal=none` | 명시한 미지원 주인공·형상·흔적은 생성 전에 보류 |
| 별도 TD 작품 조립·군집·고정 안무·실루엣 생성 | 파일 5개를 `archive/mr2-mr3/`로 이동 | 활성 import·기본 테스트·패키지·Docker 빌드 대상에서 분리 |
| 선택한 색감·배치가 기본 테마·순번 배치로 바뀜 | Intent 팔레트·mood를 브리프에, Element.position을 UE 변환기에 전달 | UE 색 필드·좌표를 테스트. 알려지지 않은 팔레트·위치는 명시 오류 |
| 아직 연결하지 않은 구도·초점·여백·참고 이미지 입력을 무시 | `composition/focus/space/reference` 요청을 생성 전에 보류 | `direction=none`과 색감·분위기 동시 지정도 모순으로 보류 |
| 모의 손 입력 `td_main`과 scene 조건의 출처 불일치 | 기본 `td_main/forest_hand`, `--source` 명시 옵션 | 실제 조립 scene 조건 및 TD 설정과 비교 |
| `published`를 전시 도착처럼 표시 | Qt·개발 웹·서버 기록에 후보 파일 준비 및 실제 적용 미확인 표시 | 시연 Agent의 실제 `Applied` 관측과 구별 |
| controller 전체를 회사 원본으로 보호 | 파일별 출처와 수정 책임 문서화 | 16개 일치·3개 내용 차이·34개 조사 범위 미확인 |

분산 기준은 [목표 설계](../architecture/README.md)입니다. PostgreSQL, RabbitMQ·Celery, Python Agent, 제작 N대·시연 1대라는 기술 선택을 바꾸지 않았습니다. 기존 `infra` 작업·임대·heartbeat·캐시·상태 관측 구현은 보존했습니다.

## 격리한 파일

활성 경로에서 이동한 파일은 다음 5개입니다. 물리 삭제하지 않았습니다.

- `packages/studio/src/studio/assembly/td_artwork.py`
- `packages/studio/src/studio/generation/subjects.py`
- `packages/studio/src/studio/head/choreography.py`
- `td/artwork_reader.py`
- `td/formation_sim.py`

수정 전 사본 9개도 함께 보관했습니다. [전체 14개 목록](../../archive/mr2-mr3/README.md)과 [해시 manifest](../../archive/mr2-mr3/manifest.json)를 참고합니다. 원문과 보관본은 CRLF/LF 차이 외 본문이 같습니다. 기존 테스트 중 TD 렌더러·강제 주인공 정책을 검증하던 부분은 보관 원문에 남기고, 현행 테스트에는 새 경계·실패 보호 검증을 넣었습니다.

`archive/`는 uv workspace의 패키지에 포함되지 않으며 pytest 수집·Ruff·Docker context에서도 제외합니다. 보관 원문은 현재 설계나 지원 기능을 증명하지 않습니다.

## 같은 경로지만 내용이 다른 파일

[상세 비교](../provenance/controller-differences.md)에 각 원문·diff·고정 커밋·현재 영향·확인 항목을 정리했습니다.

| 파일 | 핵심 차이 | 이번 처리 |
|---|---|---|
| `controller/README.md` | GitHub의 미구현 범위와 GitLab의 자동 생성·게시 구현 설명 차이 | 현행 원문 보존, 상위 문서에서 출처 구분 |
| `controller/src/interactive_controller/__main__.py` | 읽기 전용 상태 조회 1개 명령 → 생성·게시 등을 포함한 10개 명령 | 파일 보존. 중앙·Agent의 정식 실행 경로와 구형 CLI를 구분 |
| `controller/contracts/runtime-source.json` | runtime pin 및 검사 대상 4개→7개 차이 | 옛 실제 원본 pin 보존. 최신 pin 자동 복사하지 않음 |

34개 원격 미확인 파일은 AI 작성으로 단정하지 않습니다. 현행 조립·검사에 필요한 모듈과 CLI 의존성이 혼재하므로 일괄 이동하지 않았습니다. 구형 controller CLI는 여전히 저장소에 있으며, 사용하면 이번 중앙 API의 개발 모드 차단을 거치지 않습니다. 운영 실행 안내·Compose·Agent 경로에 구형 CLI를 새로 연결하지 않습니다. CLI의 폐기·최신 원본 채택·의존 모듈 분리는 위 3개 차이 확인과 함께 결정해야 합니다.

## 남아 있는 제한과 후속 연결

1. 기존 PySide6·웹 제작 화면은 동기 프리뷰 API를 호출합니다. `/infra/jobs` 등록·진행률·재접속 복구·실제 전시 버전 UI 연결은 후속 작업입니다. 기본 서버에서 503이 보이는 것은 의도한 경계입니다.
2. 전체 자동 생성 → 제작 Agent의 UE 조립 → 자동 검수 → 승인 번들 → 중앙 게시자 → 시연 `Applied` 연결은 아직 완료하지 않았습니다. 이번 변경은 초기 경로를 분리하고 오해를 제거한 작업입니다.
3. controller 조립기의 MediaPipe 좌표·입력 프로필은 현재 설치에 고정되어 있습니다. 모의 입력은 현재 계약에 맞췄으나, 임의의 TD 센서 설치를 지원하는 프로필 버전화는 별도 작업입니다.
4. UE runtime pin·catalog·preset·실제 UE 바이너리의 호환성은 실기 검증이 필요합니다. 현행 adapter의 최대 요소 3개·단일 캔버스 제한을 회사 엔진 전체의 제한으로 해석하지 않습니다.
5. `mood`는 브리프 메타데이터 보존입니다. 모든 감정 선택이 새로운 UE 연출 파라미터로 구현됐다는 의미는 아닙니다. 미지원 요구는 보류하거나 오류로 기록합니다.
6. `LocalImage`와 규칙 기획은 시험용입니다. 외부 이미지·영상 공급자, 스타일·구성 품질 평가, 성능 검사 완료를 주장하지 않습니다.
7. SQLite 연결은 기존 로컬 개발·테스트 호환용입니다. 분산 운영의 기준 상태 저장소는 PostgreSQL입니다. 이번 작업으로 DB 스키마나 운영 데이터를 마이그레이션하지 않았습니다.
8. 시연 노드의 기존 watcher 관측과 독립 중앙 commit 방식은 다릅니다. 실제 UE·TD 장비, 영상 성능, 무중단 전환은 이번 소프트웨어 테스트 범위 밖입니다.

## 검증

### MR 제출을 위한 최신 develop 통합

사용자 요청에 따라 `S15P31A504-195-architecture-refactor` 브랜치를 최신 `develop`의 `8928524ca9cdb094ea91235625c61732bcd55285`에서 생성했습니다. 추가된 명세 검증 코드·테스트와 Head 설명을 유지했습니다. 출처·보관 증거의 기준 커밋은 최초 조사 시점 `5b807c0`으로 고정했으며, 보관 14개 본문과 확인 대상 3개 파일의 불변을 다시 확인했습니다.

- 최신 통합본 Windows 전체 pytest: **215개 통과**, 실제 PostgreSQL 16 임시 DB 포함, 기존 Starlette/httpx 경고 1개.
- controller unittest 재실행: **184개 중 181개 통과·3개 조건부 생략**. 실제 UE 예제·프리셋 검증 제한은 동일합니다.
- Ruff 재실행: 통과. 실제 키·환경 파일·생성 데이터가 추가되지 않았는지 변경 파일을 점검했습니다.
- stage된 활성 코드·문서의 `git diff --cached --check`는 통과했습니다. 보관한 unified diff 2개에는 빈 문맥 행을 나타내는 공백으로 인해 trailing-whitespace 진단 12건이 발생합니다. 증거 형식을 유지하고 이 디렉터리에만 `core.whitespace=-blank-at-eol`을 적용한 별도 검사도 통과했습니다. 저장소 전역 설정은 변경하지 않았습니다.
- Jira [S15P31A504-195](https://ssafy.atlassian.net/browse/S15P31A504-195)는 `진행 중`으로 두고 `develop` 대상 MR을 제출합니다. 전체 분산 파이프라인과 실기 검증은 이 이슈의 완료 범위에 포함하지 않습니다.

아래 표는 최신 develop 통합 전 리팩토링 검증 기록입니다. Linux 및 컨테이너 검증은 통합 전 결과이며, 최신 통합본의 Linux 재검증으로 해석하지 않습니다.

| 검증 | 결과 |
|---|---|
| Windows 전체 pytest + 실제 PostgreSQL 16 임시 DB | 210 passed. 이후 검수 안내 문구를 수정한 Qt 테스트 14개도 재통과 |
| 최종 Linux 테스트 이미지 + 실제 PostgreSQL 16 임시 DB | 210 passed. Qt 화면·임대 동시성 테스트 포함 |
| controller unittest | 184개 실행, 181개 통과·3개 조건부 생략. `ART_RUNTIME_ROOT` 미지정으로 실제 UE 예제·프리셋 동기화는 미검증 |
| Ruff / `git diff --check` | 통과 |
| `infra/Dockerfile` test / `infra/docker/Dockerfile` runtime 빌드 | 통과. 삭제 파일 COPY 참조를 센서 fixture로 교체 |
| Linux 기본 서버 | 비 root 사용자 실행, SQLAlchemy native 모듈·임시 SQLite 읽기/쓰기·HTTP 조회·인증 401·개발 제작/확정/검수 503 확인 |
| 격리·원본 | 런타임 이미지에 archive 없음. 보관 원문 14개 내용 동일. controller 3개·UE/TD 원본 변경 없음 |
| 문서 | 갱신한 Markdown 상대 링크 존재 확인 |

Linux 첫 전체 검사에서 옛 `PC1 UE에서 버전` 문구를 기대하던 테스트 1개가 실패했습니다. 검수 안내도 후보 파일과 실제 적용을 구분하도록 보완하고 테스트를 갱신한 뒤 최종 전체 검사가 통과했습니다.
두 환경에 기존 Starlette/httpx 사용 중단 예정 경고 1개가 있습니다. 이번 변경으로 의존성 버전을 갱신하지 않았습니다.
실제 외부 AI 호출·RabbitMQ 실서버 연결·UE/TD 화면 출력·무중단 전환은 이번 검증에 포함하지 않았습니다.
소프트웨어 및 출처·해시 검사는 실기 검증을 대신하지 않습니다.
