# apps — 장비별 실행 앱

장비마다 설치해서 실행하는 프로그램입니다. 앱 하나가 장비 한 종류에 대응합니다. 장비 배치는 [분산 목표 설계](../docs/architecture/README.md)를 따릅니다.

| 폴더 | 장비 | 상태 |
|---|---|---|
| [central](./central/README.md) | EC2 중앙 서버 (지금은 개발 PC 한 대) | 동작함. 지금은 기획부터 렌더러 전달까지 혼자 실행 |
| [operator](./operator/README.md) | 운영 PC | 동작함. PySide6 앱(자동 · 편집 모드, 기록, 전시 모드 시험 기능). 설치는 `uv sync --all-packages` |
| [node_agent](./node_agent/README.md) | 제작 노드 N대 | 작업 임대·heartbeat·캐시 무결성 검사. UE 조립 어댑터는 연결 예정 |
| [show_agent](./show_agent/README.md) | 시연 노드 1대 | 파일 준비·기존 UE watcher 전달·실제 상태 확인. 자동 게시와 UE 재시작은 연결 예정 |

- 앱끼리는 서로 import하지 않습니다. 같이 쓰는 코드는 [packages](../packages/README.md)에 두고, 앱 사이에 주고받는 API · 작업 데이터 모델은 `packages/contracts`에 둡니다. 규칙 전체는 [AGENTS.md](../AGENTS.md)의 「코드 규칙」에 있습니다.
- 코드가 생긴 앱은 자기 `pyproject.toml`을 갖고 루트 `pyproject.toml`의 `[tool.uv.workspace]` 목록에 들어갑니다. 장비에는 그 앱이 쓰는 의존성만 설치됩니다.
