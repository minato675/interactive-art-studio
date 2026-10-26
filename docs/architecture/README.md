![라넬 분산형 아키텍처 — 제작 노드 N대와 독립 시연 노드 1대](./assets/architecture-overview.png)

# 라넬 AI 인터랙티브 미디어아트 — 분산형 아키텍처

운영 PC의 **PySide6 + Qt Widgets 앱 → EC2의 Python·FastAPI**를 중심으로, **제작 노드 N대**가 서로 다른 작품을 병렬 제작하고 **독립 시연 노드 1대**가 현재 작품을 실행한다. 기준 상태는 **PostgreSQL**에 저장하고 SQLite는 필요성이 확인될 때 추가한다.

**2026-10-21 초기 배치 확정:** 생성 PC 1대·UE 제작 PC 2대·시연 PC 1대, 총 4대로 시작한다.
공유 저장소는 생성 PC의 전용 디스크 폴더에 두며 별도 공유 PC/NAS를 추가하지 않는다.
UE 제작은 N대 확장을 유지한다. 최신 장비·저장 배치는 [통신 파이프라인](../COMMUNICATION_PIPELINE.md)과
[저장소 배치](../../infra/STORAGE.md)를 따른다. 기존 이미지·draw.io는 역할 중심의 이전 버전 도면이다.

**이 자료는 사용자와 합의한 목표 설계다. 현재 GitLab 코드의 구현 완료를 뜻하지 않는다.** 문서 기준일은 2026-10-19이며, 대조한 `develop` 커밋은 [`ccd14e9`](https://lab.ssafy.com/s15-final/S15P31A504/-/commit/ccd14e9e08cfe5088fef226f5bbb574639b6136a)다. 이번 변경에는 문서·구성도·미리보기만 포함한다.

## 문서와 구성도

| 자료 | 읽는 목적 |
|---|---|
| [분산형 설계 본문](./distributed-system-design.md) | 장비별 역할, 작업 임대·재시도, 자동 검수, 자산 계약, 실제 전시 적용까지의 상세 설계 |
| [누적 작품 재생 스케줄러 설계](./library-scheduler.md) | 빈 시작·신규 최초 상영·최근 2시간 30/70 정책, 제안 API·오프라인 복구, 운영 모니터의 실제 구현 범위 |
| [재생 정책 시뮬레이션 결과](./library-scheduler-simulation.md) | 빈 시작과 초기 20개 비교, 지연·오프라인·실패·재시작 가상 검증 |
| [기술스택 채택 근거](./technology-decisions.md) | 기술별 담당 역할과 선택 이유, 확정·미정 항목 |
| [LGPL 배포 체크리스트](./pyside6-distribution-checklist.md) | PySide6 이용 경로와 실제 배포본에서 확인할 고지·소스 제공·교체 조건 |
| [결정 기록](./decisions.json) | 확정한 기술, DB 도입 방침, 미정 사항의 구조화된 기록 |
| [편집 가능한 draw.io 원본](./assets/ranel-ai-architecture.drawio) | diagrams.net에서 열어 도형·텍스트·연결선을 개별 편집 |
| [PNG 미리보기](./assets/architecture-overview.png) · [SVG 미리보기](./assets/architecture-overview.svg) | 한눈에 구조 확인 / 확대해서 읽기 |
| [UE 작업물 검증 절차](./ue-verification.md) | 조립된 UE 작품을 게시 전에 확인하는 순서(소재 검사 → 조립 → 정적 검사 → PC1 실행 검증 → 게시)와 통과 기준, 자동 검증과 사람 검수의 범위. **지금 구현 기준** |
| [검증 기록](./validation.md) | 문서·도면 검사와 저장소 테스트 결과 및 검증하지 않은 범위 |

draw.io 파일을 내려받아 diagrams.net에서 연 뒤 **다섯 번째 `분산형 설계 · 도형 중심` 페이지**를 선택한다. 이전 4개 페이지는 수정 없이 보존한 이력이다. 현재 선택은 새 페이지와 설계 본문을 기준으로 읽는다. PNG·SVG는 새 페이지의 미리보기다.

## 현재 구현과 목표 설계

| 영역 | 대조한 GitLab 프로토타입 | 이번 목표 설계 |
|---|---|---|
| 운영 화면 | FastAPI가 제공하는 웹 화면 | 운영 PC의 PySide6·Qt Widgets 설치형 앱 |
| DB | 기본 SQLite `runtime/studio.db`; DB URL 설정 경로 존재 | PostgreSQL을 작품·작업·임대·버전의 기준 상태로 사용; SQLite 초기 제외 |
| 실행 배치 | 한 서버 프로세스에서 제작·조립·전달을 순차 호출 | EC2 중앙 서비스, 동일한 Windows 제작 N대, 독립 Windows 시연 1대 |
| 작업 전달 | `apps/central`의 `pipeline.py` 직접 함수 호출 (대조 당시 경로: `server/pipeline.py`) | RabbitMQ·Celery 중앙 작업 처리 + FastAPI를 통한 Windows Python Agent 임대·보고 |
| 렌더러 | TD 기본, UE 선택; UE의 형상·흔적 표현은 추가 구현 필요 | UE 공통 실행기에서 작품 실행, TD는 현장 손 입력 전달 |
| 교체 확인 | 버전 폴더와 `release.json` 원자 교체 경로 | Show Agent·캐시·Ready/Applied 분리·재연결 시 실제 활성 버전 대조 |
| 생성 공급자 | 규칙 기획·테스트 이미지 등 프로토타입 경로 | 라넬 보유 도구를 확인한 후 외부 AI 공급자 어댑터 연결 |

현재 구현의 설명은 [루트 README](../../README.md), [파이프라인](../../apps/central/src/central/pipeline.py), [DB 구성](../../packages/db/src/db/engine.py), [설정](../../apps/central/src/central/config.py), [간접 표현 작품 구조](../간접표현_작품구조.md)를 따른다. 기존의 간접 표현 방향을 폐기하는 문서가 아니다. 형상·흔적 등 실제 작품 기능을 UE 공통 카탈로그와 런타임에 반영하는 작업은 별도로 필요하다. 목표 설계를 현재 실행 방법으로 오인해 설정만 바꾸지 않도록 주의한다.

GitHub 런타임 계약 검토는 `interactive-runtime`의 `613e6a86ab04d3f87435557ef9186846fce88a8b`를 기준으로 했다. GitLab에 포함된 사본의 출처는 [THIRD_PARTY.md](../THIRD_PARTY.md)를 따르며, 두 코드 기준이 같거나 호환성 검증을 마쳤다고 가정하지 않는다. 상세 설계의 GitHub 링크는 확인 당시 커밋을 고정하며, 비공개 저장소는 접근 권한이 필요하다.

## 구현 시 지킬 경계

- 제작 노드는 같은 기능을 공유하고 노드당 조립 작업 1개·UE 1개를 실행한다. 전체 조립 동시성은 가용 제작 노드 수 이하다.
- Windows Agent는 FastAPI를 통해 작업을 임대한다. Windows 노드를 Celery Worker로 직접 배치하지 않는다.
- 자동 검수 통과를 기본 게시 승인으로 삼고, 실패 원인에 따라 재생성·전처리·재조립한다.
- 시연 노드는 현재 작품 실행과 다음 작품 준비를 구분한다. Ready는 준비 완료이며 Applied는 실제 활성화 후 보고한다.
- 카메라 → TouchDesigner → UE 입력은 현장에서 처리한다. 제작 실패·AI 지연·중앙 통신 단절이 현재 작품 실행을 멈추지 않도록 설계한다.
- 시연 노드에서는 신규 코드 빌드·에디터 조립·쿠킹을 수행하지 않는다. 런타임 미디어·설정 번들과 사전 쿠킹 콘텐츠를 구분한다.

이는 구현·시험으로 충족해야 할 요구사항이다. 무중단 교체, UE 5.8.3 목표 버전의 호환성, 실제 장비 성능은 아직 검증 완료로 표시하지 않는다. 초기 저장소 호스트·방식은 생성 PC 전용 디스크·SMB로 확정했으며, 실제 경로·권한·용량·백업, AI 공급자, EC2 세부 배치, 운영 UI 인증·진행 갱신, 원격 교체 어댑터는 확인·구현 항목이다.

## 라이선스 정합성

사용자는 **PySide6 + Qt Widgets의 LGPLv3 이용 경로**를 선택했다. 앱 고유 소스 비공개 유지와 LGPL 라이브러리의 대응 소스·고지·교체 조건은 [배포 체크리스트](./pyside6-distribution-checklist.md)로 관리한다. 기업에 프로토타입 설치본을 전달할 때도 실제 배포 조건을 확인한다.

2026-10-19 확인한 [AGENTS.md](../../AGENTS.md)는 “오픈소스는 MIT · Apache · BSD 계열만 씁니다. GPL 계열은 쓰지 않습니다.”라고 명시하며, [THIRD_PARTY.md](../THIRD_PARTY.md)에도 GPL 제한이 있다. 이전에 멘토 발언으로 전달받았던 내용과 별개로, **현재 저장소에도 제한 문구가 존재한다.**

이번 문서에서는 사용자 선택을 유지하되 저장소 규칙의 적용 범위·예외가 승인됐다고 기록하지 않는다. 실제 의존성을 도입하기 전 PySide6, Loki·Grafana, RabbitMQ, 패키징 도구 등 선택한 구성의 정확한 제품·버전·사용 및 배포 범위를 규칙과 대조하고 예외 또는 대체 결정을 기록해야 한다. 이 문서 추가는 `AGENTS.md`·`THIRD_PARTY.md`의 정책 변경이나 실제 배포본 준수 확인을 대신하지 않는다.

## 관련 작업

Story [S15P31A504-56 — 작품 방향·구조 문서](https://ssafy.atlassian.net/browse/S15P31A504-56), Task [S15P31A504-133 — 흐름도와 아키텍처 그림 갱신](https://ssafy.atlassian.net/browse/S15P31A504-133)에 연결한다. 이 변경은 합의한 분산 목표 설계를 공유하는 범위다. 현재 구현과의 일치·후속 통합 확인을 포함한 Task 전체의 완료 판정을 대신하지 않는다.
