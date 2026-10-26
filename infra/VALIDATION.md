# 인프라 준비 검증 기록

## UE 제작 노드 주소 확정 — 2026-10-22

- 사용자에게 받은 UE 제작 노드 주소 `70.12.246.196`을 중앙 HTTPS 방화벽 허용 목록에 추가했다.
  기존 생성 노드 `70.12.245.47`은 유지하고, 두 주소가 나머지 원격 IP 차단 범위에서 제외된 것을 조회로 확인했다.
- 기존 규칙 갱신 시 `Set-NetFirewallRule -Name`과 선택 매개변수 `DisplayName`이 충돌하는 오류를 수정했다.
  갱신에 `NewDisplayName`을 사용해 관리자 실행이 성공했고, 실행 결과 `ok=true`를 확인했다.
- 서버 자체 HTTPS·인증 검사는 통과했다. UE PC에서 키트 실행과 실제 파일 왕복은 아직 확인하지 않았다.
  기존 UE 접속 키트의 URL·토큰·CA는 변경하지 않았다.

## 생성 서버 → 중앙 → UE 제작 서버 — 2026-10-22

- 코드 커밋: `558f786`(HTTP 파일 API·작업 계약), `b640282`(클라이언트·UE 실행기),
  `32c5848`(Head HTTP 작업·예제·왕복 검사). 배포 이미지의 소스는 `32c5848`이다.
- `uv run ruff check .`, 잠금 파일 검사, Compose 검사 통과.
- Linux Python 3.12 `uv run --no-sync pytest -q`: **260 passed**, 기존 httpx 경고 1개.
  실제 PostgreSQL 테스트 포함. Windows controller: 184개 중 181 통과·3 건너뜀.
- Windows `uv run pytest`: **255 passed, 5 skipped**. 건너뛴 PostgreSQL 테스트 5개는 Linux에서 통과했다.
- 별도 API·Worker·Dispatcher·RabbitMQ 통합 검사와 생성 작업 의존성·파일 복사 검사 통과.
- 배포 전 DB·파일·CA 백업 후 노트북 중앙 이미지 교체 완료. 서버 자체 HTTPS·운영자 인증 검사 통과.
- 중앙 주소 `https://70.12.246.252:8443`, 방화벽의 원격 허용 주소 `70.12.245.47` 확인.
  UE 제작 서버 IP는 미정이므로 원격 허용 주소에 추가하지 않았다.
- `infra/smoke_exchange.py`로 전용 `exchange-smoke-v1` 카탈로그의 생성·제작 노드를 사용했다.
  배경·에셋 PNG 테스트 파일을 HTTPS로 업로드하고 MR !33의 실제 Python 조립기를 별도 프로세스에서
  실행했다. `gpu_particles` 장면 조립 후 결과 파일 4개를 운영자 API에서 내려받아 SHA-256·크기를 확인했다.
  배경·에셋·조립 세 작업이 모두 성공했고 조립 대기는 선행 작업 완료 전 배정되지 않았다.
- 테스트 파일은 실제 이미지 생성 결과가 아니다. 실제 원격 PC 간 접속·생성 모델·Shipping·게시·화면 전환은 미검증이다.
  조립 결과는 `assembly_report_only`, `publish_approved=false`를 유지한다.
- 파일 왕복 뒤 `RanelMaster/backups/20261022-http-exchange`에 다시 백업했고,
  `uv run python -m infra.verify_backup --backup <백업폴더>`로 별도 PostgreSQL·임시 볼륨에 복원했다.
  작업 29개·업로드 기록 6개를 포함한 모든 테이블 행 수와 파일 해시·CA가 일치했다.

로그: `runtime/validation/laptop-ci/20261022T003141Z-6896d9/`,
`runtime/validation/server-exchange/dd3e892702bc/`. 토큰 키트는 Git 밖 개인 폴더에 보관한다.
현장 실행은 [오늘 연결 안내](TODAY_CONNECTION.md)를 따른다.

## 노트북 마스터 추가 검증 — 2026-10-21

작업 브랜치 `S15P31A504-6-laptop-master`에서 노트북 단독 운영 기반을 검증했다.

- Windows `uv run pytest`: 248 passed, 5 skipped. 실제 PostgreSQL 테스트는 Linux 검사에서 별도 실행한다.
- `uv run ruff check .`: 통과.
- AC 절전·최대절전 대기 0, 이전 설정 기록. 현재 사용자 로그인 시작 바로가기 설치.
- 작업 스케줄러에 현재 사용자 대화형 세션의 매일 19시 백업 등록.
- 개인 운영 경로는 `C:/Users/SSAFY/RanelMaster`. OneDrive·앱 AppData 가상화 경로와 분리했다.
- 실제 백업을 새 PostgreSQL 16 컨테이너와 빈 볼륨에 격리 복원했다. 6개 테이블의 행 수,
  중앙 파일 0개와 CA 파일 9개의 해시가 일치했다. 운영 볼륨은 복원 대상으로 사용하지 않았다.

위 항목은 Windows 실재부팅, 다른 PC의 HTTPS·SMB 접속, 실제 생성·UE 실행을 검증했다는 뜻이 아니다.
PC 간 연결은 사용자 요청에 따라 다음 날 진행한다. 새 이미지의 Linux 검사·실제 배포 결과는
개인 로그와 `runtime/validation/laptop-ci/`의 해당 실행 결과를 확인한다.

## Docker 구성 폴더 분리 — 2026-10-21

현재 경로는 로컬 개발용 `infra/local-dev/`, 중앙 서버용 `infra/central/`, CI Runner `infra/runner/`입니다.
아래의 이전 검증 기록은 당시 경로를 그대로 보존합니다. 실행할 때는 [현재 infra 안내](./README.md)를 따릅니다.

| 확인 | 결과 |
|---|---|
| 로컬·중앙 runtime Docker 이미지 빌드 | 두 이미지 모두 새 Dockerfile 경로에서 통과 |
| 로컬 실제 실행 | 인증된 작품 생성·TD/UE 후보 파일 준비, 재생성한 컨테이너에서 DB/파일 유지 확인 |
| 중앙 실제 실행 | 격리된 Compose 프로젝트에서 API→RabbitMQ→Celery→PostgreSQL 진단·초안 완료 |
| 중앙 설치 리소스 검사 | 저장소 밖·네트워크 없는 이미지에서 네 테마의 장면 조립·검사 통과 |
| Compose 이전/이후 비교 | 로컬·중앙·중앙+HTTPS 3종의 런타임 설정 일치. 빌드/마운트 경로만 새 위치로 변경 |
| 비밀 설정 생성 | 임시 가상 저장소에서 루트 `.env.infra` 생성 및 기존 파일 덮어쓰기 거부 확인 |
| pytest | 241 passed, 5 skipped, 기존 Starlette 경고 1건. PostgreSQL 전용 5건은 호스트 테스트 URL 미설정으로 생략 |
| Ruff·uv lock·배포 Bash 구문·diff | 통과 |
| 문서 상대 링크 | 현재 변경 문서의 142개 경로 확인 |
| 기존 노트북 서버 | 기존 컨테이너 8개의 ID 유지. 검증용 별도 프로젝트·볼륨은 정리 완료 |

실제 EC2 배포·SSH 파일 전달·다른 PC 접속·UE 화면은 이번 폴더 정리 검증에 포함하지 않습니다.
GitLab의 기존 CI 결과와 위 로컬 변경 후 검증은 구분하며, 이번 변경의 새 MR CI는 아직 실행하지 않았습니다.
OneDrive의 이동 파일 reparse point로 첫 빌드가 `invalid file request`로 실패했으나,
동일 바이트를 일반 파일로 다시 기록한 뒤 두 이미지 빌드가 통과했습니다. 내용 해시는 유지했습니다.

## CI 테스트 분리·실제 이미지 배포 복구 — 2026-10-21

`develop`의 `e82897a`를 통합한 커밋 `cd2a4b66`의
[파이프라인 #235516](https://lab.ssafy.com/s15-final/S15P31A504/-/pipelines/235516)이 **Passed**다.
lint 이후 네 테스트가 실제로 동시에 실행됐다. 전체 JUnit은 **368 passed, 3 skipped, 실패 0개**다.

| 작업 | 실제 결과 |
|---|---|
| lint #695560 | Ruff·uv lock·배포 Bash 구문 통과 |
| unit-tests #695561 | 163 passed |
| operator-tests #695562 | Qt offscreen 13 passed |
| controller-tests #695563 | 181 passed, 3 skipped. `ART_RUNTIME_ROOT`가 필요한 UE 자료 검사 생략 |
| integration-tests #695564 | PostgreSQL 동시성 5 passed, 실제 API/Worker/Dispatcher·RabbitMQ·PostgreSQL 통신과 Worker 재시작·생성 파일 전달 통과 |
| build-smoke #695586 | 같은 job에서 runtime 이미지를 빌드하고 아래 6개 단계 모두 통과 |
| Windows 전체 pytest | 176 passed, PostgreSQL 전용 5 skipped, 기존 Starlette 경고 1개 |

이미지 검사는 준비→정상 기준 이미지 기동→다른 이미지 ID의 후보로 교체→기동 실패 시 복구→
API가 정상인 상태의 Worker 처리 시간 초과 후 복구→정리의 6단계다. 실제 `deploy.sh`를 호출했으며
실패 배포가 1을 반환하고 이전 정상 이미지로 세 프로세스가 실행되는지 확인했다. 복구 후 비동기
작업 완료, `current-image`·인증 설정 유지, PostgreSQL 표식·`/data` 파일 보존도 통과했다.
임시 컨테이너와 볼륨이 남지 않았는지 검사했다. 로그·`result.json`·JUnit은 해당 job artifact에 7일간 보관한다.

첫 build-smoke #695565는 Alpine 패키지 저장소 DNS 오류로 검사 시작 전에 실패했다.
호스트와 새 컨테이너의 DNS 응답을 확인하고 같은 커밋의 해당 작업만 재실행해 #695586이 통과했다.
#235511은 Worker 장애 검사의 조건을 보강한 새 커밋으로 대체되어 취소됐다.

Runner의 일반 job은 비특권이며, digest로 고정한 공식 rootless DinD 서비스 한 종류에만
privileged 실행을 허용한다. 임시 Docker API는 작업별 TLS로 연결하고 호스트 Docker socket과
운영 데이터는 공유하지 않는다. service 상한은 CPU 2개·2 GiB이며 내부 테스트 스택이 이 상한을 공유한다.
이는 테스트 4개 병렬 실행 확인이며 장시간 부하나 완전한 호스트 격리를 보증하지 않는다.

노트북 중앙 서버는 교체하지 않았다. EC2·Registry 실배포, 실제 Windows 설치본·UE/TD 실행은 후속이다.
기준 이미지는 후보와 같은 앱 코드에 다른 표식을 넣은 이미지이므로 과거 버전과의 DB 스키마 호환성은
검증하지 않았다. DB 스키마 롤백과 무중단 배포도 보장하지 않는다.
현재 동작은 [CI/CD 문서](../docs/CI_CD.md), 추가 문서 커밋의 최종 CI와 병합 결과는
[MR !22](https://lab.ssafy.com/s15-final/S15P31A504/-/merge_requests/22)에서 확인한다.

## 노트북 GitLab Runner 실제 실행 — 2026-10-21

사용자 요청으로 프로젝트 전용 [Runner #2191](https://lab.ssafy.com/s15-final/S15P31A504/-/runners/2191)을
등록했다. Docker executor가 온라인이며 전역 `concurrent`, Runner `limit`, `request_concurrency`를
모두 **4**로 적용했다. 노트북 중앙 서비스는 교체하지 않았다.

커밋 `42729ee8`의 [파이프라인 #235463](https://lab.ssafy.com/s15-final/S15P31A504/-/pipelines/235463)이
실제 GitLab에서 **Passed** 상태다.

| 항목 | 결과 |
|---|---|
| lint, job #695352 | Ruff·uv lock·Bash 구문 통과 |
| test, job #695353 | pytest **155 passed**, 기존 경고 1개. PostgreSQL·Qt 검사 포함 |
| 같은 test job의 controller | **184개 실행, 181개 통과, 3 skipped** |
| 같은 test job의 실제 통신 | API→RabbitMQ→Celery→PostgreSQL 진단·초안 성공, Worker 재시작 후 작업 완료, 배경→에셋·해시 파일 전달 통과 |
| build, job #695398 | rootless BuildKit으로 중앙 runtime 이미지와 OCI 파일 생성 성공 |
| Runner 설정 검증 | 동시 실행 상한 4, job CPU 2개/2 GiB, service CPU 0.5개/512 MiB, job에 Docker socket·호스트 폴더 미연결 |
| 문서·설정 검사 | CI/CD 문서 상대 링크, Compose 설정, TOML·JSON 구문, 비밀 값 미포함 확인 |

최초 build #695354는 Docker 기본 seccomp에서 namespace 생성이 차단되어 실패했다.
중간 재시도 #695389에서 runc keyring 오류를 확인했고, 필요한 namespace·mount·hostname 호출 7개와
keyring 미지원 반환 처리를 적용했다. 키 접근은 계속 차단하며 privileged·seccomp 전체 해제를 사용하지 않았다.
최종 재시도 #695398에서 Dockerfile의 RUN 명령과 OCI export가 통과했다.
프로필의 출처·변경 내용·운영 절차는 [Runner 안내](./runner/README.md), 전체 파이프라인은
[CI/CD 문서](../docs/CI_CD.md)에 있다.

이 결과는 CI job 최대 4개 설정과 개별 job의 실제 실행 확인이다. 네 개의 무거운 job을 동시에 실행한
성능·장시간 부하 검증은 하지 않았다. EC2 실배포와 Registry 게시, 생성 PC 실접속·SMB·실제 AI·UE는 후속이다.
아래 기록의 Runner 미등록 설명은 등록 전 시점의 이력이다.

## GitLab MR 준비 및 최신 develop 통합 — 2026-10-21

`develop`의 `993a38e` 위로 rebase한 코드로 다시 검증했다. 기존 `infra/docker/` 구성과 기획 기능을
보존하고, 비동기 초안 Worker도 API와 같은 GMS·Ollama·대체 LLM 설정을 사용하도록 맞췄다.

| 항목 | 결과 |
|---|---|
| Windows `uv run pytest -q` | **150 passed, 5 skipped**, 기존 경고 1개. PostgreSQL 전용 5개는 Linux에서 실행 |
| Linux Compose pytest, Python 3.12 | **155 passed**, 생략 없음, 기존 경고 1개 |
| controller unittest | **184개 실행, 181개 통과, 3 skipped**. `ART_RUNTIME_ROOT`가 필요한 실제 UE 자료 검사 생략 |
| Ruff, uv lock, Compose 설정, runtime 이미지 빌드 | 통과 |
| 실제 API·RabbitMQ·Celery·PostgreSQL | 진단·초안 성공, Worker 중단 중 접수한 작업의 재시작 후 완료 확인 |
| 생성 작업·파일 전달 | 배경→에셋 의존성과 해시 파일 전달 통과. 테스트 파일만 사용 |
| GitLab Runner 조회 | 프로젝트 할당·추가 할당 가능·그룹·인스턴스 Runner 모두 없음. 자동 CI 실행 성공을 뜻하지 않음 |

이번 검증 실행은 `local_pipeline.py`의 검사·빌드 모드이며 노트북의 실행 중인 서비스를 새 이미지로 교체하지 않았다.
기존 노트북 배포 성공 기록은 다음 절에 별도로 보존한다. EC2는 아직 제공되지 않아 **추후 중앙 서비스를 EC2로
이전할 예정**이며, SSH·실배포·복원·TLS·현장 접속은 그때 검증한다.

Windows는 OneDrive의 기존 `.venv` 접근·하드링크 문제를 피하기 위해 별도 검증 가상환경에
`UV_LINK_MODE=copy`로 동일 `uv.lock`의 전체 workspace를 설치했다. 기존 경고는 Starlette TestClient의
httpx 사용 중단 예정 경고다. 실제 GMS·Ollama·Codex 생성·UE 화면은 이번 테스트에서 실행하지 않았다.

로그는 Git 제외 경로 `runtime/validation/laptop-ci/20261021T011458Z-363155/`,
Windows JUnit과 controller 로그는 `runtime/validation/gitlab-publish/`에 있다.
Runner 설정·EC2 배포 변수는 변경하지 않았다. 등록 요구 사항은 [운영 준비](./OPERATIONS.md#gitlab-runner-확인--2026-10-21)에 있다.

## 생성 작업과 노트북 배포 확장 — 2026-10-21

실행 기록: `runtime/validation/laptop-ci/20261021T010036Z-be26c7/result.json`.
배포 이미지: `ranel-central:laptop-20261021t010036z-be26c7`.

| 항목 | 결과 |
|---|---|
| Windows `uv run pytest -q` | **117 passed, 5 skipped**, 기존 deprecation warning 1개. PostgreSQL 전용 5개는 Linux에서 실행 |
| Linux Compose pytest | **122 passed**, 생략 없음. PostgreSQL 실제 동시 임대·세션에 남은 오래된 소유권 거부 포함 |
| `uv run ruff check .`, `uv lock --check` | 통과 |
| 실제 API → RabbitMQ → Celery → PostgreSQL | 진단·초안 작업 성공 |
| Worker 종료·재시작 | 테스트 Worker가 내려간 동안 접수한 작업이 대기 상태를 유지하고 재시작 후 성공 |
| 생성 작업 | 배경 성공 후 에셋 배정, 다른 작품 병행, 역할 분리, 실패 재시도·오래된 결과 거부, 완료 응답 유실 후 재전송 검사 통과 |
| 파일 전달 | 테스트 파일을 해시 저장소에 보관하고 후속 작업의 입력으로 복사·해시 확인. 실제 이미지 생성은 수행하지 않음 |
| 시연 안전 동작 | 테스트용 상태 자료로 준비 실패·중앙 통신 장애 시 기존 release 보존 확인. 실제 UE Applied나 화면 출력 검증은 아님 |
| 로컬 배포 | 검사 → 후보 이미지 빌드 → 기존 DB/CA/파일 백업 → 서비스 교체 → 실제 비동기 smoke와 HTTPS 검사 통과 |
| 배포 실패 분기 | 단위 테스트에서 실패 시 이전 이미지 복원·환경 파일 보존 확인. 운영 서버에 고의 장애를 내는 실험은 하지 않음 |

검증된 것은 노트북과 Linux 테스트 컨테이너의 동작이다. 생성 PC 실접속·SMB 공유·대용량 전송 성능,
Codex 자동 생성, UE 실행·실제 화면 전환, GitLab Runner의 자동 실행, EC2 배포는 후속이다.
기존 `controller`/UE/TD 사본과 이를 소비하는 조립·검수·전달 코드는 변경하지 않아 controller 전용 테스트는 이번에 다시 실행하지 않았다.
검증 로그와 JUnit은 위 실행 폴더에 보관한다. 이 실행 시점에는 commit·push·MR을 수행하지 않았으며,
이후 GitLab 게시를 위한 재검증은 맨 위 절에 기록했다.

## 노트북 임시 중앙 서버 — 2026-10-21

- 사용자가 지정한 `70.12.245.47`은 **Codex 기반 배경·에셋 생성 PC**다. UE 제작·시연 연결은 후속이다.
- 노트북 `https://70.12.246.252:8443`에서 Caddy → FastAPI 연결을 확인했다.
  내부 CA 검증을 켠 클라이언트로 준비 상태·노드 ID·역할을 조회했다. 잘못된 CA는 거부됐다.
- 인증 없는 `/api/artworks`와 잘못된 Agent 토큰은 401, 운영자 인증 조회는 200,
  외부 `/docs`는 404였다. 서버 자체 점검은 원격 PC 접속 검증을 대신하지 않는다.
- Windows 방화벽의 지정 PC 허용 규칙과 다른 원격 IP 차단 규칙을 실제 적용·조회했다.
  대상은 노트북의 TCP 8443에 한정한다. 기존 Docker 허용 규칙과 다른 포트는 보존했다.
- `generation-1` 접속 키트를 발급했다. 접속 검사만으로 heartbeat를 생성하지 않았고,
  생성 PC 실접속 검사는 사용자가 나중에 수행하기로 했다.
- `uv run pytest -q`: **108 passed, 2 skipped, 1 warning**. PostgreSQL 주소 미지정 테스트 2개 생략,
  기존 TestClient deprecation warning 1개다. `uv run ruff check .`도 통과했다.
- 백업의 SHA-256, 한글 파일명이 포함된 코드 스냅샷 606개, 공개 CA 일치를 확인했다.
  PostgreSQL dump를 별도 검증 DB에 복원하고 5개 테이블의 행 수가 원본과 일치함을 확인한 뒤 검증 DB를 제거했다.
  당시 중앙 `/data`에는 파일이 없었다. 현장 대용량 파일의 전송·복원 성능을 검증한 것은 아니다.
- 백업 후 중앙 서비스를 재개했고 실제 RabbitMQ/Celery 진단·초안 작업이 각각 성공했다.
- EC2 실서버 복원, 실제 생성 PC 연결, Codex 자동 호출·결과 등록, SMB 공유·UE 연결은 아직 수행하지 않았다.

설정·생성 PC 접속·EC2 이관은 [임시 중앙 서버](./LAPTOP_SERVER.md)를 따른다.
아래는 이전 기초 인프라 검증 기록이며 당시의 범위를 보존한다.

## 기초 인프라 — 2026-10-20

작업 위치: `_workspace/ranel-infra`, 브랜치 `S15P31A504-6-infra-foundation`.
기존 원본 저장소와 아키텍처 문서 작업 폴더는 수정하지 않았다. commit·push·MR 생성은 하지 않았다.

## 결과

| 확인 | 실제 결과 |
|---|---|
| Windows, `uv run --all-packages pytest -q` | **104 passed, 2 skipped**. PostgreSQL 주소를 지정하지 않은 환경의 동시성 테스트 2개만 생략 |
| Linux Compose 테스트 이미지, Python 3.12 | **106 passed**. 실제 PostgreSQL 동시 임대 2개와 PySide6 offscreen 테스트 포함 |
| controller unittest | **184개 실행, 3 skipped, 나머지 통과**. `ART_RUNTIME_ROOT` 미지정으로 UE 예제·프리셋 검사 3개 생략 |
| `uv run ruff check .` | 통과 |
| `uv lock --check` | 통과 |
| `bash -n infra/preflight.sh infra/deploy.sh` | 통과 |
| GitLab CI YAML 파싱·수동 배포 규칙 확인 | 통과. 실제 GitLab Runner 실행과는 별개 |
| Compose 설정 검증 | 통과. 호스트 공개 포트는 `127.0.0.1:18080`만 |
| 중앙 실행 이미지 빌드 | 통과. 실제 uv 이미지의 digest로 고정 |
| API·DB·브로커 기동 | FastAPI/DB readiness 정상, RabbitMQ 정상 |
| 실제 비동기 smoke | HTTP 202 → PostgreSQL 작업 등록 → Dispatcher → RabbitMQ → Celery → 진단·초안 완료 |
| CI용 별도 프로세스 통합 검사 | `check_pipeline.py`에서 API/Worker/Dispatcher를 별도 실행하고 진단·초안 완료, 정상 종료 |
| 두 제작 Agent 통신 | Windows 한 PC에서 Agent 2개가 다른 작업을 임대·파일 캐시 검사·완료 보고 |

기존 FastAPI TestClient의 httpx 사용에 대한 deprecation warning 1개가 있다.
검사 실패는 없으며 이 작업에서 HTTP 테스트 라이브러리를 별도로 교체하지 않았다.

추가 테스트는 중복 요청·다른 내용의 같은 ID 거부, 노드당 작업 1개, 임대 만료 재배정,
오래된 토큰의 완료 거부, 최대 시도 이후 종료, 브로커 오류 후 작업 보존, 캐시 손상·경로 이탈 거부,
공유 파일이 사라진 뒤 로컬 캐시 재사용, 다음 작품 실패 시 현재 release 보존,
UE 세션·PID·활성 버전 검사, 파일 준비·실패 중 현재 활성 버전 보존을 포함한다.

## Docker Desktop 오류 복구

프로젝트 실행 전 Docker Desktop 4.82.0이 오래된 Windows Unix socket 경로에서 시작 실패했다.
로그와 사용자 화면에서 `dockerInference`, 이어서 `docker-secrets-engine/engine.sock`의
`The file cannot be accessed by the system` 오류를 확인했다.
[Docker 공식 저장소의 동일 증상 보고](https://github.com/docker/desktop-feedback/issues/460)와 일치했다.

각 폴더에 0바이트 소켓만 있음을 확인하고 Docker 프로세스를 중지한 뒤 임시 소켓 폴더를 백업 이름으로 변경했다.
이미지·볼륨·WSL 디스크·Docker 설정은 초기화하지 않았다. Factory reset은 실행하지 않았다.

이 PC의 보존 경로:

- `C:\Users\SSAFY\AppData\Local\Docker\run.infra-backup-20261020-161503`
- `C:\Users\SSAFY\AppData\Local\Docker\run.infra-backup-20261020-161548`
- `C:\Users\SSAFY\AppData\Local\docker-secrets-engine.infra-backup-20261020-161548`

복구 뒤 `docker info`에서 Linux 엔진 `29.6.1`을 확인했고 이미지 빌드·컨테이너 실행·통합 검증을 수행했다.
모든 Docker 시작 문제를 같은 방법으로 처리하라는 지침이 아니라 이 PC에서 확인한 복구 기록이다.

## 미검증·미구현 범위

- EC2 실제 SSH 접속, OS/CPU/권한, Registry 인증, Runner 등록, GitLab 실제 파이프라인, 실서버 배포·복구.
- 서로 다른 물리 PC의 SMB 공유 설정·속도·동시 대용량 전송·장애 복구. 현재는 로컬 폴더로 저장 계약을 검증했다.
- UE 실행 파일에서 조립·프리뷰·쿠킹·성능 검사와 실제 화면 전환. Python 테스트는 UE 상태 계약을 검사했다.
- UE 자동 재기동, 별도 Ready→commit 명령, 게시 큐와 Show Agent 연결. 기존 watcher 전달은 명시적 CLI 요청이다.
- 전체 작품 DAG, 외부 AI 호출, 자동 검수·게시 승인, 운영 앱의 비동기 진행률 UI.
- 파일 부분 이어받기, 자동 보관 만료·캐시 정리, 실제 백업과 복구, 운영 TLS·토큰 관리 UI.

파일 무결성 검사 성공을 작품 검수 승인으로 표시하지 않는다.
Runtime의 Applied를 실제 화면 출력이나 무중단 성능 검증으로 표시하지 않는다.

로컬 빌드·Linux 테스트·controller 원본 로그는 Git 제외 경로 `runtime/validation/`에 보관한다.
실행 방법은 [운영 준비](./OPERATIONS.md), 공유 저장소 설계는 [저장소 문서](./STORAGE.md)에 있다.
