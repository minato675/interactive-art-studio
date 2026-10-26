# UE 실행 검증과 사람 검수 — 2812b85 시험 기준

현재 운영 작업 종류는 `ue_assemble → ue_verify → 승인 시 ue_publish`입니다.
중앙 카탈로그는 MR !33 `2812b856cceea7f85d50dedfed661ed2390314d5`의 템플릿 5개입니다.
새 3개 템플릿은 자동 선택·보고에 추가하지 않습니다.

조립 후 별도 검증 작업을 만드는 연결과 저장 위치는 [파이프라인](UE_RUNTIME_PIPELINE.md),
키트 설정·실행은 [설치 안내](UE_RUNTIME_SETUP.md)를 따릅니다.
조립은 `ue-exchange-1`, PC1 검증은 `ue-review-1`, PC2 승인을 받은 게시는 `ue-show-1`이 담당합니다.

## verdict와 사람 검수

- `pass`: 실행 검증 통과. 운영자의 명시적 게시 승인 전에는 PC2 작업을 만들지 않습니다.
- `review`: `review_state=awaiting_human_review`. 사람 검수 대기 목록에 표시합니다.
- `fail`: 검증 불통과. 게시 승인을 거부합니다.

작업 `succeeded`는 유효한 보고서를 받았다는 뜻이며, 작품 통과 여부는 반드시 `result.verdict`로 판단합니다.
실행·다운로드·통신 실패는 작업 실패 또는 임대 만료로 남습니다.
`passed/needs_review`와 `verdict`가 모순되는 보고서는 거부합니다.

사람 검수 API에는 운영자 토큰이 필요합니다. 실행기 토큰으로는 승인할 수 없습니다.

| API | 기능 |
|---|---|
| `GET /infra/runtime/reviews` | 아직 사람이 판정하지 않은 review 목록 |
| `POST /infra/runtime/jobs/{id}/review` | `decision` approve/reject, `reviewer`, `reason`, `expected_attempt`로 판정 |
| `POST /infra/jobs/{id}/approve-publication` | 통과 또는 사람 승인된 작품의 PC2 게시 작업 등록 |

같은 사람 판정 재전송은 멱등이며 다른 판정으로 변경할 수 없습니다.
반려된 작품의 게시 요청은 거부합니다. 검수 승인만으로는 게시 작업을 만들지 않습니다.
기존 게시 승인 API로 review 작품을 직접 승인한 경우에도 검수자·사유·현재 시도를 사람 승인 이력으로 남깁니다.

EC2에서 `infra/central_cli.py runtime-reviews`로 대기 목록을 보고,
`runtime-review <ID> --decision approve 또는 reject --reviewer <검수자> --reason <사유> --attempt <시도>`로 판정합니다.
공통 `--env` 옵션과 실행 방법은 CLI `--help`를 따릅니다. 실제 운영 환경 파일은 `/opt/ranel/.env.infra`입니다.

실제 GPU 화면·반응·성능 검증은 PC1 실행 이후 확인합니다. 중앙의 합성 보고서 테스트는 API·권한·상태 전이 검증입니다.
