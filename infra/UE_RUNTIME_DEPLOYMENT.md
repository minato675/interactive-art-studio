# UE 실행 검증·시연 연결 배포 기록

- 배포일: 2026-10-22
- 중앙 코드: cc5a9b162e62308e0f64fbd084add5f270d36189
- 이미지: ranel-central:cc5a9b162e62308e0f64fbd084add5f270d36189
- 운영 EC2 HTTPS 443·기존 CA·SSH·방화벽 유지. API·worker·dispatcher만 교체했다.
- 노드: ue-exchange-1(ue_assemble) 유지, ue-review-1(ue_verify)·ue-show-1(ue_publish) 등록.
- 백업: /opt/ranel/operations/runtime-cc5a9b162e62308e0f64fbd084add5f270d36189/before
- 로그·배포 결과: 같은 operations 폴더의 tests.log, ruff.log, runtime-postgres.log, deploy.log, deployment.json.

Windows pytest 302 통과·6 PostgreSQL 검사 건너뜀, Ruff 통과. EC2 Linux pytest 308 통과·Ruff 통과.
controller 184 실행·181 통과·3 건너뜀. 기존 Starlette 경고 1개가 있다.

실제 PostgreSQL의 독립 테스트 스키마에서 대상이 다른 ue_verify 2건을 동시에 임대해 중복·교차 배정이 없음을 확인했다.
키트 공통 클라이언트와 FastAPI를 연결한 검사는 실제 바이트 전송·해시·보고서·캡처·승인·Applied 보고를 확인했다.
런타임은 합성 대역이며 실제 UE 화면 검증으로 기록하지 않는다.

운영 노트북에서 세 키트 모두 HTTPS 인증서와 노드별 역할·허용 종류를 확인했다. 이 검사는 heartbeat를 변경하지 않으므로 현장 PC가 온라인이라는 뜻은 아니다.
배포 smoke의 diagnostic·draft는 실제 RabbitMQ·PostgreSQL·Celery 왕복으로 성공했다. 유료 AI를 호출하지 않았다.

PC1 Shipping/GPU 검증, PC2 호환 실행본 설치·실제 출구 IP 수신·허용·현장 연결·화면 적용은 남아 있다.
담당자 전달 문서는 [UE_RUNTIME_HANDOFF.md](UE_RUNTIME_HANDOFF.md)를 따른다.
