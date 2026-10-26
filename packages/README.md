# packages — 앱이 가져다 쓰는 라이브러리

여러 앱([apps](../apps/README.md))이 같이 쓰는 코드입니다. 패키지마다 `pyproject.toml`이 있고 코드는 `src/<이름>/` 아래에 있습니다. `uv sync`를 하면 모두 수정 즉시 반영되는 방식(editable)으로 설치됩니다.

| 폴더 | import 이름 | 하는 일 | 가져다 쓰는 우리 패키지 |
|---|---|---|---|
| [contracts](./contracts/README.md) | `contracts` | 앱 사이에 주고받는 데이터 모델: 작품 요청 · 계획 · 응답, 표현 방식 목록 | 없음 |
| [db](./db/README.md) | `db` | DB 테이블과 연결 | 없음 |
| [studio](./studio/README.md) | `studio` | 작품 제작 로직: 기획 · 소재 생성 · 검사 · 조립 · 전달 | `contracts`, `db` |

import 방향은 `apps/*` → `studio` → `db` · `contracts`입니다. 패키지는 `apps/`를 import하지 않습니다. 규칙 전체는 [AGENTS.md](../AGENTS.md)의 「코드 규칙」에 있습니다.
