# 게시 범위와 원본 기준

- 정리일: 2026-10-26
- 게시 대상: `minato675/interactive-art-studio` · **Private**
- 원본: [S15P31A504](https://lab.ssafy.com/s15-final/S15P31A504/-/tree/4df1093955496adc1b5f63251384032cf298c828)
- 기준 브랜치: `develop`
- 기준 커밋: `4df1093955496adc1b5f63251384032cf298c828`
- 접근 방식: 로그인된 Chrome에서 GitLab을 열고 Code → Download source code → ZIP으로 내려받았습니다.
- ZIP의 Git 커밋 comment와 브라우저에서 확인한 기준 커밋이 일치하는지 확인했습니다.

## 선별 방식

소스·테스트·설계 문서를 검토용으로 옮겼습니다. 원본 README는 `docs/source/README.original.md`에 보관하고,
루트 README에 실제 개인 담당·팀 기능·실행 의존성을 구분했습니다. 원본 README에는 이전 단계의 설명이나 제외 경로를 가리키는 링크가 남아 있을 수 있습니다.
원본 코드의 커밋 이력 전체를 이관하는 작업이 아니라 선별한 현재 소스의 새 스냅샷입니다. 원래 GitLab 저장소와 이력은 유지됩니다.

실제 `.env`, 키 파일, 설치 실행 파일·모델 가중치·대용량 생성 자료와 원래 팀의 에이전트·GitLab 메타데이터 일부는 게시에서 제외했습니다.
기존의 테스트용 문자열, 로컬 DB 예시 설정, 실행 시 생성하는 키의 PEM 형식 문자열은 운영 비밀키와 구분해 검토했습니다.

| 제외 사유 코드 | 파일 수 |
| --- | ---: |
| `company-source-mixed-directory-or-source-snapshot` | 518 |
| `identical-blob-in-company-remote-history` | 4 |
| `original-team-agent-or-workflow-metadata` | 48 |
| `shared-source-block-with-company-runtime-fixture` | 1 |

선별한 원본 파일은 **412개**이며, 파일별 경로·원본 SHA256과 제외 사유는 [manifest](EXPORT_MANIFEST.json)에 있습니다.
문서 작성 후 원본 파일의 바이트 일치, Python 구문과 JSON 형식, GitHub에 게시한 파일과 공개 범위를 검증합니다.
전체 서비스 실행·유료 API·실기·외부 배포를 이번 정리에서 새로 검증했다고 주장하지 않습니다.

## 회사 원본 제외

회사 코드와 팀 확장이 섞인 `controller`, `ue`, `td`, 원문 보관 영역인 `archive`, `docs/provenance`를 제외했습니다.
그 밖의 선별 파일도 PC에 이미 있던 회사 저장소의 `origin` 원격 refs에서 도달 가능한 Git 객체와 대조했습니다.
본문 비교에서는 회사 저장소의 현재·과거 텍스트 blob 1,472개를 비교하고 겹친 UE fixture 변형도 제외했습니다.
비교는 코드 중복 선별을 위한 것이며 작성 주체나 저작권 소유를 판정하는 절차가 아닙니다.

| 비교 저장소 | 로컬 origin/main 기준 |
| --- | --- |
| `lanael-studio/interactive-controller` | `4fca9463000aa91660902c809faf8383de2a30c7` |
| `lanael-studio/interactive-runtime` | `67e871c4d2145b4dcf2ecb6be2c74e85cbbdffa3` |
| `lanael-studio/interactive-sensors` | `48d3cda7dc833872bc6f736be65281e692c8fbc3` |
| `lanael-studio/demo-repository` | `77f075b0c7d376b27c3cd757d1bb6b4162dead73` |

회사 원본 내용이 들어 있는 GitLab 과거 이력은 이 저장소에 가져오지 않았습니다.
제외한 외부 의존성을 사용하는 코드·테스트는 원본 프로젝트의 승인된 환경을 필요로 합니다.
