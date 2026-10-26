# apps/show_agent — 시연 노드 Agent

시연 노드(Windows PC) 1대에서 다음 작품 파일을 준비하고, 이미 실행 중인 UE의 실제 적용 상태를 중앙에 보고하는 프로그램입니다.
기본 관측 모드는 Runtime 프로세스를 시작하거나 종료하지 않습니다.
선택적인 [프로세스 관리 모드](LIFECYCLE.md)는 명시한 Shipping 실행본 하나의 시작·종료·복구를 담당합니다.
PC2 HTTP 키트의 중앙 재생목록·120초 전환·미준비 시 유지와 오프라인 동작은 [실제 재생 연동](../../docs/SHOW_PLAYBACK.md)을 따릅니다. 설치형 `a504-show-agent`의 관찰 CLI와 HTTP 키트를 동시에 실행하지 않습니다.

- **실행 장비**: 시연 노드 1대. 신규 코드 빌드 · 에디터 조립 · 쿠킹은 하지 않습니다. UE Runtime은 하나만 띄웁니다.
- **목표 설계**: 준비 완료(Ready)와 실제 적용(Applied)을 나눠 보고하고, 중앙과 연결이 끊겨도 마지막 정상 작품을 유지합니다. 자세한 내용은 [분산형 설계](../../docs/architecture/distributed-system-design.md)의 「시연 노드와 실제 적용 확인」입니다.
- **가져다 쓸 것**: [contracts](../../packages/contracts/README.md)(게시 명령 · 상태 형식), [studio](../../packages/studio/README.md)의 `exhibition`(버전 폴더 + `release.json` 원자 교체). central 코드는 import하지 않습니다.

제작 API의 `prepared`는 파일 후보 준비이고 런타임 Ready가 아닙니다. Ready 보고는 요청 버전과 실행 세션을 확인하며, Applied는 추가로 활성 버전과 적용 플래그를 확인합니다. 모순되거나 다른 요청의 보고는 Unknown으로 남깁니다.

공유 저장소에서 검증한 파일을 로컬 버전 폴더에 준비합니다. 파일 복사 완료는 `Staged`이며
UE 상태 파일의 `Ready`·`Applied`와 구분합니다. 적용 기록은 실제 런타임 세션·프로세스·요청 버전을 확인합니다.
현재 UE release watcher에는 자동 commit 동작이 있으므로 독립적인 중앙 commit 프로토콜은 후속 작업입니다.
고정 PID 모드는 실행 중인 Runtime에 연결합니다. 관리 모드도 빌드·쿠킹은 하지 않으며 같은 실행본의 중복 실행을 막습니다.

실행 방법은 [infra 운영 준비](../../infra/OPERATIONS.md)를 참고합니다.
테스트용 상태 파일 결과는 실제 전시 적용으로 취급하지 않습니다.
