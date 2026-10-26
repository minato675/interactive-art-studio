# packages/db — DB 테이블과 연결

`accounts.py`는 운영 계정의 PBKDF2 비밀번호 해시, SHA256 세션 해시·만료, 로그인 시도 제한을 새 테이블에 보관합니다. 비밀번호·세션 원문은 저장하지 않습니다.

`show_dashboard.py`는 노드별 시연 구성·revision을 저장하는 새 테이블입니다. 기존 테이블 열을 변경하지 않습니다.

`feedback.py`의 `infra_artwork_feedback`는 시연 작품·버전의 유지/개선 의견, 활성 여부·revision을 보존합니다.
새 테이블만 추가하며 [피드백 계약](../../docs/ARTWORK_FEEDBACK.md)에 따라 request_id unique로 재전송을 처리합니다.

새 요청의 자동 생성·조립·AI 검수·재작업·게시 정책은 [Head 자동 사이클](../../infra/HEAD_AUTOMATION.md)을 따른다.

UE 조립 이후 별도 실행 검증·승인·PC2 게시 연결은 [UE 실행 흐름](../../infra/UE_RUNTIME_PIPELINE.md)를 따른다.

작업 임대의 `kinds` 필터는 중앙 인증 레지스트리에서 전달합니다. 배경·에셋 전담 노드도
기존 노드별 슬롯과 의존성 검사를 그대로 사용하며 새 DB 열은 추가하지 않습니다.

`ue.py`의 `infra_ue_capability`는 제작 노드의 설치 리소스 보고입니다. 기존 테이블의 열은 바꾸지 않습니다.

`files.py`의 업로드 영수증은 작업·시도별 파일명·해시·크기를 보관합니다. 바이너리는 중앙 파일 볼륨에 둡니다.

`head.py`의 `infra_head_workflow`는 Head 요청·단계별 판단·자식 작업 ID를 보관합니다.

`exhibition_feed.py`의 `exhibition_feed`는 [전시 자동 순환](../../infra/HEAD_EXHIBITION_FEED.md)의 상태 한 줄(`id = 1`)입니다. 켜짐 여부, 쉬는 끝 시각과 이유, 마지막 디렉터 작업 ID를 둡니다. API 서버 · Dispatcher · Worker가 같은 상태를 보고, 중앙 서버를 다시 켜도 켜짐 상태가 남게 하려고 DB에 둡니다. 기존 테이블의 열은 바꾸지 않습니다.

작품 상태 `prepared`는 모든 필수 후보 파일의 준비 완료입니다. 기존 `published` 기록은 그대로 읽지만 실제 적용으로 승격하지 않습니다. 출력별 결과는 기존 JSON 단계 기록에 저장하므로 DB 열 추가는 없습니다.

작품 기록과 소재 풀을 저장하는 DB 테이블, DB 연결 함수입니다.

`jobs.py`는 작업 · 시도 · 노드 테이블입니다. PostgreSQL 행 잠금으로 임대와 완료 처리를 직렬화하고,
만료된 시도의 토큰으로 늦게 온 결과를 거부합니다. Compose에서는 PostgreSQL이 기본이며
SQLite는 기존 단일 프로세스 테스트 호환용입니다. PostgreSQL 드라이버는 BSD 라이선스의 pg8000을 사용합니다.
`infra_dependency`는 생성 작업의 선행 관계를 기록합니다. 기존 작업만 참조하고 생성 이후 관계를 바꾸지 않아
순환을 막습니다. 같은 작품·버전의 배경 성공 이후 에셋을 배정하며 다른 작품은 병행할 수 있습니다.
완료 재전송은 같은 소유자·시도·동일 결과일 때만 수락합니다. 실패 재시도는 새 시도 ID를 만듭니다.

- **실행 장비**: 중앙 서버(central)와, central이 부르는 `studio`의 소재 생성 · 검사 단계에서 씁니다. 목표 구조에서는 제작 노드 · 시연 노드가 DB에 직접 붙지 않고 central API를 거칩니다.
- **가져다 쓰는 것**: SQLModel(SQLAlchemy)만 씁니다. 우리 다른 패키지는 import하지 않습니다. DB 주소는 central 설정에서 받아 `make_engine(url)`로 넘깁니다.
- 기본은 SQLite `runtime/studio.db`이고, `DATABASE_URL`로 PostgreSQL로 바꿀 수 있습니다([루트 README](../../README.md#실행)의 환경 변수 표).

| 파일 (`src/db/`) | 내용 |
|---|---|
| `models.py` | DB 테이블: `artwork`(작품 기록), `asset`(소재 풀). 칸 설명은 [루트 README](../../README.md#기술-선택)의 DB 테이블 표 |
| `engine.py` | DB 연결 만들기 · 테이블 만들기 · 세션 |

예정: 시작할 때 테이블을 만드는 방식을 Alembic 마이그레이션으로 바꿉니다.
작업 · 시도 · 임대 · 노드는 `jobs.py`에 구현했습니다. 작품 버전 저장소와 전체 작업 의존성은
[분산형 설계](../../docs/architecture/distributed-system-design.md)의 「PostgreSQL 데이터 모델」을 따라 추가합니다.
