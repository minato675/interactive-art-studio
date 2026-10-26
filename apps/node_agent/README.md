# apps/node_agent — 제작 노드 Agent

HTTP 전송을 선택하면 중앙 API에서 입력을 받고 `ue_assemble` 작업을 실행할 수 있습니다.
MR !33의 UE 모듈이 별도로 설치되어 있어야 합니다. 역할·능력 설정은
[서버 연결 문서](../../infra/SERVER_EXCHANGE.md)를 참고합니다.

제작 노드(Windows PC) N대에서 중앙 작업을 임대받고 파일을 준비·검사하는 프로그램입니다.
UE 조립 · 프리뷰 · 기술 검사 실행부는 이후 이 임대 루프에 연결합니다.

- **실행 장비**: 제작 노드 N대. 모든 노드가 같은 코드 · 엔진 · 플러그인을 갖고, 특정 작품이나 계열을 전담하지 않습니다.
- **목표 설계**: central의 API로 작업을 임대(lease)하고, 일하는 동안 heartbeat를 보내고, 끝나면 결과를 보고합니다. 노드당 조립 작업 1개 · UE 1개만 실행합니다. Celery Worker로 직접 붙지 않습니다. 자세한 내용은 [분산형 설계](../../docs/architecture/distributed-system-design.md)의 「작업 임대 · heartbeat」, 「제작 노드 N대의 공통 내부 구성」입니다.
- **가져다 쓸 것**: [contracts](../../packages/contracts/README.md)(작업 · 결과 형식), [studio](../../packages/studio/README.md)의 `assembly`(scene 조립) · `verify`(검사). central 코드는 import하지 않습니다.

이번 구현은 API 작업 임대 · heartbeat · 소유권 토큰 · 공유 파일 캐시와 무결성 검사까지입니다.
`bundle_verify`와 등록된 `ue_assemble`을 실행합니다. `ue_assemble`은 MR !33의 Python 조립기를
호출하며 UE 에디터 빌드·쿠킹·Shipping 실행·시연 게시를 수행하지 않습니다.
한 Agent가 한 번에 작업 하나만 처리하고, 중앙에서도 노드별 실행 슬롯 하나를 검사합니다.
Agent를 중복 실행하지 못하도록 로컬 잠금을 사용합니다.
기존 central의 동기식 `studio.assembly` 호출을 이 검증 작업과 동일하게 취급하지 않습니다.

실행과 환경 변수는 [infra 운영 준비](../../infra/OPERATIONS.md)에 있습니다. Windows에서 Celery를 실행하지 않습니다.
