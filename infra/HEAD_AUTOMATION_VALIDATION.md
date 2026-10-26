# Head 자동 사이클 검증·배포 기록 — 2026-10-22

## 적용 결과

- 운영 이미지: ranel-central:1525e2f6c96d810cafa5976cac63999a0131784a
- EC2 HTTPS: https://k15a504.p.ssafy.io (443), PostgreSQL ready 정상.
- 운영 중이던 8f4df78의 사람 검수 승인·반려 API와 CI·배포 변경을 병합해 보존했다.
- 기존 흐름은 자동 게시로 소급 전환하지 않는다. 새 Head 요청이 기본 자동 사이클을 사용한다.
- 여섯 실행기의 기존 토큰 유지, 배포 후 HTTPS 인증·노드 ID·역할·허용 작업 검사 통과.
- PC2 외부 출발 IP 211.192.210.113/32의 호스트 HTTPS 허용 규칙을 확인했다. 실제 PC2 접속·화면은 현장 확인이 필요하다.
- SSH 서비스 정상·기존 접속 설정 유지·환경 파일 권한 600.

## 확인한 검사

| 검사 | 결과 |
|---|---|
| 최종 EC2 Linux 전체 pytest | 334 통과, 건너뜀 없음 |
| 최종 EC2 Ruff | 통과 |
| Windows 통합 pytest | 326 통과, PostgreSQL 전용 8 건너뜀 |
| 최신 배경 계획 사용 회귀 검사 | 10 통과 |
| controller | 184 실행, 181 통과·3 건너뜀 |
| 실제 PostgreSQL 자동/사람 승인 사이클 | 최종 전체 검사에 포함, 모두 통과 |
| OpenAI gpt-6-luna 실제 API | 2회: 이미지 기반 에셋 계획·다중 이미지 검수 형식 통과 |
| MR !33 2812b85 Python 조립 | 신규 assets 메타데이터를 gpu_particles로 오류 없이 조립, 파일 4개 |
| 전달 ZIP | CRC·파일명 중복·Python 문법·파일 manifest 확인 |

기존 Starlette 경고 1개가 있다. API 검수 시험은 합성 이미지로 hold를 확인했으며 실제 작품 승인·이미지 생성·Shipping 화면 검증으로 기록하지 않았다. 실제 API 시험은 테스트 키·6루나를 사용했고 운영 GMS 제작 요청을 생성하지 않았다. 배포 smoke의 diagnostic·draft는 실제 PostgreSQL·RabbitMQ·Celery 왕복으로 확인했다.

## 원본·백업·로그

파일 원본은 EC2 ranel_central-data의 /data/artifacts/objects, 메타데이터·검수·승인은 PostgreSQL이다. 배포 전에 API·worker·dispatcher를 정지하고 활성 작업 없음 확인 후 DB·파일을 함께 백업했다. PostgreSQL·RabbitMQ·gateway 볼륨은 유지했다.

운영 증거 경로:
`/opt/ranel/operations/head-auto-1525e2f6c96d810cafa5976cac63999a0131784a/`

before/에는 DB dump·파일 볼륨 스냅샷·이전 설정·이미지 포인터가 있다. tests.log·ruff.log·validation.json·deploy.log·deployment.json·post-deployment.json에 검사와 적용 결과가 있다. before/의 비밀 설정·DB는 개발자에게 전달하지 않는다. 이 백업은 같은 EC2에 있으므로 별도 장기 보관 백업과 구분한다.

## 개발자 전달물

- 생성: HEAD_GENERATION_HANDOFF.md + GENERATION_HANDLER.md + 생성 실행기 3개 ZIP.
- UE PC1: HEAD_UE_HANDOFF.md + 조립·검증 실행기 ZIP.
- UE PC2: 같은 UE 안내 + 시연 실행기 ZIP.
- 각 ZIP에 README·설정 example·파일 manifest를 넣었다. 노드 토큰이 포함되므로 비공개 전달한다.

## 현장에 남은 일

1. 생성 개발자의 실제 이미지 프로그램을 handler 인터페이스에 연결하고 세 실행기를 시작한다. 키트는 모델·API 키를 제공하지 않는다.
2. PC1에서 호환 Shipping 실행본 경로를 설정하고 조립·검증 실행기를 시작한다.
3. PC2에서 TCP/TLS를 확인하고 호환 실행본 한 개와 시연 실행기를 시작한다.
4. 운영 GMS 모델의 첫 실제 배경→에셋→조립→AI 검수→PC2 Applied 사이클을 확인한다. 현장 화면·GPU·손 반응 검증은 합성 통합 검사로 대신하지 않는다.
