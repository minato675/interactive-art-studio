# Head 노드 구축·검증 기록 — 2026-10-21

## 완료한 구성

노트북 Docker의 FastAPI·PostgreSQL·RabbitMQ·Celery Worker·Dispatcher에 Head를 연결했습니다.
기능 커밋은 `3f27772`, 설정·검증 도구 커밋은 `e1c4917`입니다.
실행 이미지는 `e1c49175b77a` 소스를 기준으로 빌드했습니다. 이후 문서 변경은 실행 코드에 영향을 주지 않습니다.
키는 Git과 Docker 빌드에서 제외한 `.env.head`에 보관하며 Windows 파일 권한을 제한했습니다.

## 실제 확인한 결과

| 검사 | 결과 |
|---|---|
| Windows 전체 테스트 | 251 통과, PostgreSQL 전용 5개 건너뜀 |
| Linux 전체 테스트·실제 PostgreSQL 포함 | 256 통과, 건너뜀 없음 |
| Ruff | 통과 |
| 실제 RabbitMQ·Worker·PostgreSQL 통합 | 통과 |
| 로컬 HTTPS·CA 인증서·운영자 인증 | 통과 |
| 직접 OpenAI `gpt-6-luna` 배경 기획 | 실제 API 호출 성공 |
| 직접 OpenAI `gpt-6-luna` 이미지 기반 에셋 기획 | 실제 API 호출 성공 |
| 배경 1개 → 에셋 3개 → 결과 집계 | `generation_complete`, 게시 승인 false |

실제 호출 흐름 ID는 `2f6acb77df654f88981bae7ad05b3903`입니다.
API는 두 번 호출했고, 생성 PC의 이미지 제작 결과만 합성했습니다. 64×64 PNG 이미지를 실제로
모델에 전달해 경로를 검증했습니다. 이미지 생성 품질·UE 표현 품질·생성 PC 접속을 검증한 것은 아닙니다.
증빙은 무시되는 `runtime/validation/head-smoke.json`과
`runtime/validation/laptop-ci/20261021T113333Z-24e9f3/`에 있습니다. 기존 라이브러리의
Starlette TestClient 사용 중단 예정 경고 1개는 남아 있으며 테스트 실패는 없습니다.

## GMS 전환 — 2026-10-22

- 제공된 GMS 사용 예제의 `https://gms.ssafy.io/gmsapi/api.openai.com/v1/responses`,
  Bearer 인증, 모델 `gpt-6.1-sol`로 실제 연결 검사 1회를 수행했습니다. 구조화 JSON `{"ok":true}` 응답을 확인했습니다.
- 토큰은 Windows에서 숨겨진 입력으로 받아 사용자별 DPAPI 암호화 파일에 저장하고,
  SSH 표준 입력으로 전달했습니다. 키 본문을 명령 인자·로그·Git에 넣지 않았습니다.
- 운영 `/opt/ranel/.env.head`에 GMS 설정을 반영하고 `OPENAI_API_KEY`를 제거했습니다.
  실행 중 작업 0건과 배포 잠금을 확인한 뒤 같은 이미지 `c18d2591`로 API·Worker·Dispatcher만 재생성했습니다.
- 세 앱 모두 GMS 키가 적용되고 직접 OpenAI 키가 없는 것을 확인했습니다. 비밀 설정 파일 권한은 600입니다.
- DB·RabbitMQ·gateway의 컨테이너 ID, 모든 데이터 볼륨, 기존 `.env.infra`·`.env.https`를 유지했습니다.
  develop 테스트 환경의 6개 컨테이너도 그대로 유지했습니다.
- 전환 후 운영 HTTPS 인증서·운영자 인증·무인증 401과 진단/수동 초안의 실제 비동기 완료를 확인했습니다.
- 실제 GMS 배경·이미지 기반 에셋 워크플로 전체, 생성 PC·UE 품질은 이번 연결 검사에 포함하지 않았습니다.
  OpenAI 발급처의 키 삭제는 서버 설정 제거와 별개이며 아직 수행하지 않았습니다.

## 남은 장비·GMS 워크플로 검증

1. GMS의 실제 이미지 입력·배경 기획→이미지 기반 에셋 기획·결과 집계를 확인합니다.
   현재 운영은 `profile: production`의 GMS를 사용하며 직접 OpenAI `profile: test`는 키 제거로 차단됩니다.
2. 운영 HTTPS는 EC2 443, develop 테스트는 8444입니다. 팀원 PC의 인증서·토큰·접속 검사는
   [접속·테스트 안내](../../docs/ENVIRONMENT_ACCESS.md)를 따릅니다. 외부 `/docs`는 현재 gateway가 제공하지 않습니다.
3. 생성 PC와 작업 임대·배경 결과 보고·배경 이미지 전송을 연결합니다. 현재 이미지 전송 API는
   운영자 인증을 사용하므로 생성 PC에 운영자 토큰을 배포하지 않고 중앙의 제한된 중계 경로를 설계합니다.
4. 제작 실행기 2개로 공통 에셋 큐에서 N개를 나눠 처리합니다. 전역 동시 실행 상한 2개와
   노드별 배경/에셋 전담 설정이 필요하면 작업 임대 정책을 추가합니다.
5. 실제 배경·에셋 생성기, UE 조립·검수·시연·게시 경로를 연결합니다. 현재 Head는 생성 작업 등록과
   결과 집계까지 담당합니다. 에셋 생성 성공을 작품 품질 승인으로 취급하지 않습니다.

비용 예산·호출량 제한·실패 알림·이미지 보관 기간 정책은 운영 투입 전 후속 작업입니다.
운영 절차와 API는 [Head 노드 문서](HEAD_NODE.md)를 참고합니다.
