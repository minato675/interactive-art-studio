# UE 개발자 전달 — PC1 검증·PC2 시연 연결

2026-10-22 중앙 EC2 적용 완료. 서버: `https://k15a504.p.ssafy.io` (443).
중앙 코드 revision: `cc5a9b162e62308e0f64fbd084add5f270d36189`.
기존 UE 템플릿 지문은 MR !33 `2812b85` 기준 5개를 유지한다. 이번 변경은 지문을 바꾸지 않는다.

## 이번에 연결한 범위

조립 완료 → 별도 ue_verify 등록 → PC1 검수 폴더 게시 → ue_runner → 보고서·캡처 중앙 저장 → 운영자 승인 → PC2 ue_publish → 실제 Applied 보고.

- 조립 실행기 `ue-exchange-1`의 기존 토큰·역할은 유지했다. 갱신본을 제공한다.
- PC1 검증 전용 `ue-review-1`을 새로 등록했다. 조립과 별도 폴더·터미널로 실행한다.
- PC2 시연 전용 `ue-show-1`을 새로 등록했다. 승인된 작품만 받으며 기본 관측 모드에서는 UE를 직접 실행하지 않는다.
- `review`는 사람 검수 후 승인, `fail`은 승인 불가다. 새 자동 Head 요청의 `pass`는 AI approve까지 충족하면 자동 게시하며 기존 수동 흐름은 명시적 승인을 유지한다.
- 실행 검증 작업의 succeeded는 보고서 수신 완료다. 실제 판정은 result.verdict를 읽는다.

## 전달 파일

| 파일 | 압축 내부 |
|---|---|
| Ranel-PC1-Assembly-Verification-20261022.zip | ue-exchange-1, ue-review-1, 안내 문서 |
| Ranel-PC2-Exhibition-20261022.zip | ue-show-1, 안내 문서 |

토큰이 들어 있으므로 비공개 전달한다. 예전 키트와 합쳐 덮어쓰기보다 새 폴더에 해제한다. 기존 실행기를 종료한 후 갱신본으로 재실행한다. assignment.secret.json이 있는 진행 중 작업은 먼저 완료하거나 중앙과 상태를 확인한다. 같은 노드 ID를 두 터미널에서 동시에 실행하지 않는다.

## PC1 담당자에게 필요한 작업

1. 기존 조립 실행기는 갱신 키트로 유지한다.
2. 검증 키트의 example을 runtime.local.json으로 복사한다.
3. UE Python 저장소 경로, Shipping 실행본·패키지 루트, 전용 delivery/cache/probe 절대 경로를 설정한다.
4. 수동 UE 검증 프로세스를 종료하고 `& .\Start-Runtime.ps1 --check`, 이후 `& .\Start-Runtime.ps1`을 실행한다.
5. 새 조립 한 건으로 Shipping 화면·합성 손 반응·캡처·중앙 runtime-report.json 업로드를 확인한다.

PC1 실행본은 제공되지 않는다. ue_runner가 있는 UE 브랜치와 호환 Shipping 빌드가 필요하다. 중앙 Python 패키지를 UE 저장소에 덮어쓰지 않는다.

## PC2 담당자에게 필요한 작업

1. 새 템플릿·Sprite와 호환되는 Shipping 빌드를 PC2에 배치한다.
2. **현재 신고 주소 211.192.210.113이 유지되는지 확인한다.** PowerShell에서 `curl.exe -4 https://checkip.amazonaws.com`을 실행한 결과를 중앙 담당자에게 보낸다. 확인 전까지 다른 PC의 주소를 PC2 주소로 사용하지 않는다.
3. EC2 허용 완료 후 `python client.py --check`로 TLS·등록을 확인한다.
4. runtime.local.json에 전용 delivery/cache와 실제 UE Shipping PID를 설정한다.
5. UE는 한 개만 실행하며 `-ArtworkRelease=<delivery 절대 경로>\release.json`을 지정한다. 기존 전시 프로세스를 무턱대고 추가 실행하지 않는다.
6. `& .\Start-Runtime.ps1 --check`, 이후 `& .\Start-Runtime.ps1`을 실행한다.
7. 중앙에서 PC1 판정·캡처 확인 후 승인하고 PC2 Staged→Applied를 확인한다.

위 설치 순서는 기존 실행본을 관찰하는 방식이다. 실행본 재시작 시 PID를 갱신한다. 자동 실행·정지·복구가 필요한 새 키트는 [선택적 관리 모드](../apps/show_agent/LIFECYCLE.md)를 적용한다. 이 기능은 Draft MR !49의 코드이며 아래 과거 배포 기록에 포함되지 않는다. PC2 외부 출구 IP 211.192.210.113/32는 EC2 호스트 허용 완료이며 실제 접속·화면 적용 확인은 남아 있다. 키트 구성·중앙 TLS 검사는 운영 노트북에서 통과했다.

## 검증 결과와 미검증 범위

- Windows: pytest 302 통과·PostgreSQL 전용 6 건너뜀, Ruff 통과.
- EC2 Linux: PostgreSQL 검사 포함 pytest 308 통과, Ruff 통과.
- controller: 184 실행, 181 통과·3 건너뜀.
- 실제 PostgreSQL: 서로 다른 대상의 실행 검증 작업 2개 동시 임대·대상 제한 확인.
- 키트 HTTP 연결 검사: 파일 다운로드·보고서/캡처 업로드·승인·시연 결과 보고 통과. 런타임은 합성 대역을 사용했다.
- 실제 Shipping/GPU·손 반응·PC2 전시 화면은 담당 PC에서 확인해야 한다.
- EC2 배포 전 DB·파일 백업 완료. SSH·방화벽 변경 없음. 유료 AI 호출 없음.

## 최종 보관 위치

| 데이터 | 원본 보관소 | 현장 사본 |
|---|---|---|
| 작품·작업·시도·판정·승인·노드 상태 | EC2 PostgreSQL, ranel_postgres-data | 키트의 최근 결과·상태 파일 |
| 생성 배경·에셋·scene.json·검증 보고서·캡처 | EC2 ranel_central-data 볼륨, /data/artifacts/objects | 생성 PC 작업 폴더, PC1 검수·PC2 전시 버전 폴더 |
| 실행 중 작품 포인터·UE 상태·마지막 정상 기록 | 각 PC의 delivery·키트 폴더 | 중앙에는 상태 보고·적용 결과 저장 |
| Shipping 실행본·UE 프로젝트 | PC1·PC2의 설치 경로와 개발 저장소 | 이번 작품 번들에는 포함하지 않음 |

RabbitMQ에는 작업 ID만 전달한다. EC2에 저장한 파일이 PC2 인터넷 단절 시에도 필요한 이유로, PC2에 받은 버전은 자동 삭제하지 않는다. 현재 보관 기간·자동 삭제·다른 장비의 독립 백업은 추가 정책이 필요하다. 기존 SMB 저장소 설계는 이 HTTP 경로의 필수 조건이 아니다.

상세 API와 설치 절차는 UE_RUNTIME_SETUP.md, 흐름·상태 의미는 UE_RUNTIME_PIPELINE.md를 따른다.


## 사람 검수 도구 유지

별도 사람 검수 승인·반려와 대기열은 UE_RUNTIME_VERIFY.md를 따른다. 검수 승인만으로 PC2 작업을 만들지 않으며, 반려된 작품은 게시 승인으로 우회할 수 없다. 중앙 CLI의 runtime-reviews·runtime-review 명령도 유지한다.
