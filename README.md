# Interactive Art Studio — 제작·검증·전시 운영

사용자 요청에서 미디어아트의 방향을 기획하고 소재를 생성·검사한 뒤, 제작 노드와 시연 노드의 상태를 관리하는 팀 프로젝트입니다.
이 저장소는 SSAFY 자율 프로젝트 S15P31A504의 **회사 원본을 제외한 비공개 포트폴리오 발췌본**입니다.

## 참여 범위와 근거

김지호의 GitLab 작업 기록에서 운영 화면·실제 상태 표시, 아카이브 작품 선택 재생,
제작 요청의 분산 Head 접수·대기 복구, Head 오류 진단에 참여한 기록을 확인했습니다.
다음 커밋은 원본 GitLab에 고정된 근거이며, 이 저장소로 원본 커밋 이력을 가져오지는 않았습니다.

| 작업 | 원본 근거 |
| --- | --- |
| 시연 운영 대시보드와 실제 상태 표시 | [362102a](https://lab.ssafy.com/s15-final/S15P31A504/-/commit/362102a) |
| 아카이브 작품의 시연 노드 선택 재생 | [09a551a](https://lab.ssafy.com/s15-final/S15P31A504/-/commit/09a551a) |
| 제작 요청을 Head 접수·대기 복구로 연결 | [6a9fdc3](https://lab.ssafy.com/s15-final/S15P31A504/-/commit/6a9fdc3) |
| Head 오류 진단과 검수 개선 | [79c29cb](https://lab.ssafy.com/s15-final/S15P31A504/-/commit/79c29cb) |

커밋 기록은 작업 참여의 근거이며, 팀 코드 전체의 개인 단독 작성이나 수작업 구현 비중을 뜻하지 않습니다.
전시 출력에 사용하는 회사 UE 런타임과 TD 센서는 이번 포트폴리오 구현 범위에 포함하지 않습니다.

## 포함한 내용

| 경로 | 역할 |
| --- | --- |
| [apps/central](apps/central/README.md) | 운영 API·진행 상태·작업 조정 |
| [apps/operator](apps/operator/README.md) | PySide6 운영 화면과 작품 요청·진행 기록 |
| [apps/node_agent](apps/node_agent/README.md) | 제작 노드의 임대·heartbeat·캐시 관리 |
| [apps/show_agent](apps/show_agent/README.md) | 시연 노드의 파일 준비·실제 상태 보고 |
| [packages/contracts](packages/contracts/README.md) · [packages/db](packages/db/README.md) | 팀 앱 사이의 요청·응답 모델과 상태 저장 |
| [packages/studio](packages/studio/README.md) | 기획·생성·검사·조립·전달을 나눈 팀 개발 로직 |
| [infra](infra/README.md) · [tests](tests/README.md) | 장비 연결·배포 설계와 소프트웨어 테스트 소스 |

```text
운영 앱 → 중앙 API → 기획·생성·검증 → 제작 노드 → 시연 노드
             └─ 작업 상태 · 임대 · heartbeat · 캐시 · 실제 적용 결과
```

후보 파일의 `prepared`와 실제 시연 장치가 적용한 `Applied`를 구분하는 것이 주요 설계 지점입니다.
기획·생성 단계의 완료가 실제 UE 화면 출력이나 작품 품질 검증 완료를 의미하지 않습니다.

## 회사 원본 제외 범위

- `controller/`, `ue/`, `td/`: 회사 원본 또는 회사 원본과 팀 확장이 섞인 디렉터리.
- `archive/`, `docs/provenance/`: 회사 코드·비교 원문·diff를 포함할 수 있는 사본 보관 영역.
- 위 경로 밖에서 회사 Git 객체와 일치한 UE fixture, 본문이 겹친 변형 fixture.
- 회사 코드가 포함된 과거 Git 이력 전체.

비교 기준과 파일별 제외 사유는 [게시 범위](docs/EXPORT_SCOPE.md)와 [선별 목록](docs/EXPORT_MANIFEST.json)에 기록했습니다.

## 실행·검증 경계

이 발췌본에는 회사 모듈이 포함되지 않습니다. 해당 모듈을 import하거나 회사 계약·fixture를 사용하는
조립·검사·시연 테스트는 원본 프로젝트의 승인된 의존성이 있어야 실행할 수 있습니다.
전체 시스템의 독립 실행용 패키지로 배포한 것이 아니라 팀 개발 부분을 검토하기 위한 소스입니다.

원본 환경은 Python 3.12+, uv workspace, FastAPI·Pydantic·SQLModel과 PySide6를 사용합니다.
이번 게시에서는 소스 선별·구문·회사 원본 제외·비공개 설정을 확인합니다.
전체 pytest·ruff·UE/TD 실기 검증은 이 발췌본에서 다시 수행한 결과로 주장하지 않습니다.

[포트폴리오 설명](docs/PORTFOLIO.md) · [원본 팀 README](docs/source/README.original.md)
