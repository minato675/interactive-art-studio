# studio/exhibition — 전시 운영 (렌더러 전달)

`scheduler.py`와 `simulation.py`는 누적 라이브러리·최초 상영 우선·최근 2시간 30% 정책을 검토하는 순수 모듈과 가상 시간 시뮬레이터입니다.
현재 central·Show Agent 실행 경로에는 연결하지 않습니다. 실제 Applied/완료 이벤트·DB·오프라인 동기화 계약은
[재생 스케줄러 설계](../../../../../docs/architecture/library-scheduler.md)에서 구현과 제안을 구분합니다.
기본 시험은 빈 라이브러리에서 시작하며 검증 완료 이벤트가 도착하면 자동 등록합니다. 초기 20개는 별도의 8시간 비교 시나리오입니다.

`prepare_version()`은 버전 폴더에만 후보를 준비하고 `release.json`을 수정하지 않습니다. 제작 파이프라인은 모든 필수 출력 성공 후 `prepared`를 기록합니다. 한쪽 실패 때도 활성 작품은 유지됩니다. 기존 `publish_version()`은 명시적인 단일 렌더러 게시 호환 함수이며 제작 경로에서는 부르지 않습니다. 전체 규칙은 [제작 정책](../../../../../docs/refactoring/creation-policy.md)을 참고합니다.

후보 작품의 파일을 렌더러별 전달 폴더에 게시하는 코드입니다. 파일 게시와 실제 전시 적용(`Applied`)은 구분합니다.

- **실행 장비**: 지금은 중앙(central)이 같은 PC의 전달 폴더에 씁니다. 목표 구조에서는 시연 노드의 [show_agent](../../../../../apps/show_agent/README.md)가 부릅니다.
- **가져다 쓰는 것**: [controller](../../../../../controller/README.md)(Windows에서 파일 교체 재시도)

| 파일 | 내용 |
|---|---|
| `delivery.py` | 후보 준비 `prepare_version`과 단일 렌더러 명시 게시 `publish_version`을 분리. 제작 API는 후보 준비만 호출하며 실제 런타임 상태를 합성하지 않음 |

시연 Agent에는 캐시·상태 관측 기반이 이미 있습니다. 전체 FIFO 자동 게시·런타임 활성화 연결과 실패 복구 검증은 후속 작업입니다. 실제 범위는 [Show Agent](../../../../../apps/show_agent/README.md)를 따르며, controller 코드의 출처는 [조사 기록](../../../../../docs/provenance/README.md)으로 구분합니다.
