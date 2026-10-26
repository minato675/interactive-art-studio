# packages/studio — 작품 제작 로직

요청을 해석해 작품을 기획하고, 소재를 만들고, 검사하고, 렌더러가 읽을 데이터로 조립해 전달하는 코드입니다. 실행 순서는 central의 `pipeline.py`가 정하고, 각 단계는 여기서 가져다 씁니다.

- **실행 장비**: 지금은 모두 central 서버 안에서 돕니다. 목표 구조에서는 `head` · `generation` · `verify`는 중앙, `assembly`는 제작 노드, `exhibition`은 시연 노드에서 돕니다. 그래서 단계마다 하위 폴더를 나눠 두었습니다.
- **가져다 쓰는 것**: [contracts](../contracts/README.md), [db](../db/README.md), [controller](../../controller/README.md)(숫자 변환 · UE 검사 · 파일 교체). controller는 회사 원본과 팀 확장이 섞여 있으므로 [출처 기록](../../docs/provenance/README.md)을 확인합니다. `apps/`는 import하지 않습니다.

`infra`의 공유 파일·캐시·API 클라이언트는 제작·시연 Agent에서도 실행합니다.
기존 제작 단계 전체를 분산 Worker로 옮겼다는 뜻은 아닙니다.

| 하위 폴더 (`src/studio/`) | 팀 구성도 블록 | 하는 일 |
|---|---|---|
| [head](./src/studio/head/README.md) | 제작 Head | 의도·구성·입력·반응·안무·LLM 연결. 별도 원문 → 작품 명세 매칭은 구현돼 있으며 운영 제작 경로와 전체 연결은 후속 작업 |
| [generation](./src/studio/generation/README.md) | 배경 · 에셋 Worker | 배경 이미지 · 주인공 데이터(실루엣 점 · 흔적 경로) 생성, 풀 저장 |
| [verify](./src/studio/verify/README.md) | 독립 검증 Agent | 소재 · 주인공 데이터 규칙 검사 |
| [assembly](./src/studio/assembly/README.md) | 조립 Worker | 렌더러별 작품 데이터: UE `scene.json` / TD `artwork.json` |
| [exhibition](./src/studio/exhibition/README.md) | 전시 운영 컨트롤러 | 버전 폴더 + `release.json` 원자 교체 |
| [infra](./src/studio/infra/README.md) | 공유 파일·Agent 기반 | 해시 파일 저장, 캐시 준비, 인증 API 통신, 단일 실행 잠금 |

하위 폴더끼리는 필요한 만큼 서로 씁니다(예: `generation`이 `verify`의 입구 검사를 부름).
