# SSAFY EC2 중앙 서버 이전

팀 A504의 `k15a504.p.ssafy.io`에 노트북 중앙 서비스를 이전합니다. PostgreSQL의 작업 기록,
노드 등록·토큰과 중앙 파일을 유지합니다. HTTP 파일 교환으로 중앙에 올라온 파일도 포함합니다.
현장 PC 디스크 전체를 옮긴 것은 아니며, 향후 대용량 공유 디스크는 현장에 둡니다.
이 문서는 실제 서버 준비·전환 기록입니다. CI의 main 수동 배포 완료와 구분합니다.
팀원 PC의 접속·테스트는 [테스트·운영 접속 안내](../docs/ENVIRONMENT_ACCESS.md)를 따릅니다.

## 접속과 서버 규칙

- SSH 사용자: `ubuntu`. 제공된 PEM 또는 별도로 등록한 SSH 키로 인증합니다.
- UFW를 활성 상태로 유지하고 SSH 22번 포트·기존 공개키를 보존합니다.
- `/home`·시스템 상위 디렉터리의 권한을 변경하지 않습니다. 새 앱 전용 경로만 준비합니다.
- PEM·서버 환경 변수·CA 개인키·백업은 Git에 넣지 않습니다.
- 노드 API·DB·RabbitMQ의 인터넷 공개를 SSH 접속 성공과 혼동하지 않습니다.
- 제공 안내상 서버는 프로젝트 종료 후 7일 이내 삭제될 수 있습니다. 종료 전에 필요한 데이터를 서버 밖에 보관합니다.

2026-10-22 최초 확인: Ubuntu 24.04.4 LTS, x86_64, vCPU 4개, 메모리 약 16GB,
루트 디스크 약 309GB(여유 약 307GB). UFW는 활성 상태이고 IPv4·IPv6의 TCP 22만 허용되어 있었습니다.
최초에는 Docker가 설치되지 않았습니다.

## 이전 순서

1. Docker 공식 Ubuntu 저장소에서 Engine·Compose를 설치하고 SSH·UFW 상태를 다시 확인합니다.
2. 실행 중인 노트북의 이미지 revision과 작업 경로를 확인합니다. 코드 변경과 데이터 이전을 한 번에 섞지 않습니다.
3. 서버에서 같은 코드의 실행 준비를 마친 뒤 노트북의 실행 중 작업이 없는지 확인합니다.
4. [백업 절차](./MASTER_NODE.md#백업과-격리-복원-확인)로 최종 백업을 만들고 기존 중앙 쓰기 서비스를 정지합니다.
5. [복원 절차](./RESTORE.txt)에 따라 빈 EC2 DB·볼륨에 복원합니다. 체크섬·테이블별 행 수·파일 해시를 비교합니다.
6. 준비 상태·인증·실제 비동기 작업 완료를 확인합니다. 기존 노트북의 자동 시작·예약 백업이 중앙을 다시 켜지 않도록 정리합니다.
7. 노드 접속 주소를 EC2로 바꿉니다. 노드별 토큰과 기존 공개 CA는 유지할 수 있지만 서버 인증서 이름은 새 도메인과 일치해야 합니다.

초기 서버 검증은 loopback API와 SSH 터널로 수행합니다. 이 단계에서는 UFW에 새 포트를 열 필요가 없습니다.
최초 이관 때 TLS·접근 제어를 확인한 뒤 TCP 8443을 추가 허용했습니다. 이후 운영 진입점은
TCP 443으로 변경됐으며 develop 테스트 환경은 8444를 사용합니다. 실제 현장 PC의 접속 확인과 구분해 기록합니다.

Docker의 공개 포트는 UFW 규칙을 우회할 수 있으므로 `ufw status`만으로 비공개 상태를 판정하지 않습니다.
기본 API는 `127.0.0.1:18080`에 바인딩하고 PostgreSQL·RabbitMQ는 호스트 포트를 발행하지 않습니다.
근거: [Docker Ubuntu 설치](https://docs.docker.com/engine/install/ubuntu/),
[Docker와 UFW](https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw).

## 현재 실행과 접속

- 운영 주소: `https://k15a504.p.ssafy.io`(443). 기존 내부 CA로 인증서를 검증합니다. 인증서 검증을 끄지 않습니다.
- 테스트 주소: `https://k15a504.p.ssafy.io:8444`. `/opt/ranel-staging`·`ranel-staging`의 독립 DB·큐·파일·토큰·CA를 사용합니다.
- 서버 경로: `/opt/ranel`. 비밀 설정은 `.env.infra`·`.env.https`·선택 `.env.head`이며 600 권한입니다.
- 초기 이전 이미지는 노트북에서 실행하던 `3b02e887b7e84debf5583ef27177f160548d4ada` 소스로 EC2에서 빌드했습니다.
- API·Worker·Dispatcher·PostgreSQL·RabbitMQ·Caddy 6개 컨테이너가 상주합니다. Windows·UE 실행기는 포함하지 않습니다.
- Docker는 부팅 시 자동 시작하고 컨테이너는 `unless-stopped`로 재시작합니다. 실제 EC2 재부팅 검증은 수행하지 않았습니다.

서버에서 실행하는 예시입니다. `ubuntu`를 Docker 그룹에 추가하지 않았으므로 Docker 작업에는 `sudo`를 사용합니다.

```bash
sudo docker ps --filter label=com.docker.compose.project=ranel
sudo docker logs --tail 100 ranel-central-1
sudo docker logs --tail 100 ranel-worker-1
sudo docker logs --tail 100 ranel-dispatcher-1
```

새 테스트 작업을 등록하는 `smoke.py` 실행과 적용 버전 확인은 [접속·테스트 안내](../docs/ENVIRONMENT_ACCESS.md#53-실제-비동기-작업-완료--서버-담당자)를 따릅니다.
CI 배포 뒤에는 `current`·`current-release`와 실제 컨테이너 이미지가 현재 버전의 기준입니다.
초기 복원 폴더의 Compose로 `up`을 실행해 예전 이미지를 다시 띄우지 않습니다.
로그를 공유할 때 실제 작품 데이터나 비밀 값이 포함되지 않았는지 확인합니다. 데이터가 필요하므로 `down -v`를 사용하지 않습니다.

기존 키트는 `server.json`의 `url`만 새 주소로 바꿉니다. 노드 ID·토큰·공개 CA는 유지했습니다.
이 PC에는 비밀 파일을 보존한 별도 EC2용 생성·UE 키트도 만들었습니다. 각 노드 전용 키트만 해당 PC에 전달합니다.

## 검증 기록 — 2026-10-22

- SSH 접속 성공. Windows 다운로드 폴더의 키 권한이 넓어 거부되던 문제는 현재 사용자만 접근하는 `.ssh` 전용 사본으로 해결했습니다.
- DNS·TCP 22 접속과 서버 사양·UFW 상태 확인 완료.
- Docker Engine 29.9.2·Compose 5.6.1 설치, `hello-world` 실행 통과.
- 최종 백업 9개 파일의 체크섬 확인. 복원 직후 PostgreSQL 9개 테이블의 행 수가 모두 원본과 일치했습니다.
- 복원 직후 중앙 파일 5개·CA/인증서 파일 12개의 SHA-256이 원본과 일치했습니다.
- API → RabbitMQ → Celery → PostgreSQL의 실제 진단·초안 작업 완료 확인.
- 서버 자체와 노트북 → EC2의 TLS·기존 노드 인증 통과. 운영자 토큰 없는 요청은 401로 거부됩니다.
- 기존 운영 앱의 API 클라이언트로 표현 방식 8개·노드 4개 조회를 확인했습니다. GUI 조작 검증은 포함하지 않습니다.
- EC2의 배포 소스 테스트 이미지에서 `uv run pytest` 287개 통과(건너뜀 없음), `uv run ruff check .` 통과.
  실제 PostgreSQL 전용 테스트도 분리된 테스트 DB에서 실행했습니다. 기존 Starlette 사용 중단 예정 경고 1개가 남습니다.
- 최초 이전 당시 노트북에서 22·8443 접속 성공, 18080·5432·5672·15672 접속 불가 확인. Docker 바인딩도 함께 확인했습니다.
- 최초 이전 당시 UFW 활성·SSH 22와 HTTPS 8443을 확인했습니다. 현재 운영 바인딩은 443, 테스트는 8444입니다.
  접근 허용 IP는 서버 담당자가 관리하며 DB·큐·관리 포트는 호스트에 발행하지 않습니다.
- 노트북의 기존 중앙 6개 컨테이너는 정지하고 재시작 정책을 `no`로 변경했습니다. 데이터 볼륨·이미지·최종 백업은 보존했습니다.
- 노트북 중앙의 Startup 바로가기는 개인 보관 폴더로 이동하고 `Ranel-Master-Backup` 예약 작업은 비활성화했습니다.
- 노트북의 GitLab Runner·별도 SQLite 개발 서버는 기존 컨테이너 ID와 실행 상태를 유지했습니다.

운영 앱에는 `CENTRAL_URL`을 새 주소로 지정하고 `SSL_CERT_FILE`을 기존 공개 CA 경로로 지정합니다.
운영자 토큰은 이전 값을 유지합니다. 이 PC의 개인 `RanelMaster` 폴더에 EC2용 환경 파일과
`Start-Operator-EC2.ps1` 실행기도 준비했으며, 토큰·설정 값은 저장소에 넣지 않았습니다.

복원 과정에서 Caddy의 새 볼륨에 이미지가 만든 빈 `caddy/` 디렉터리 때문에 기존의 완전 빈 폴더 검사가 중단됐습니다.
기존 파일·링크가 없는 것을 확인하고 복원했으며 [복원 안내](./RESTORE.txt)에도 이 조건을 반영했습니다.

## 남은 작업

- 생성 PC·UE 제작 PC·시연 PC에서 새 주소의 실제 접속, 파일 교환·작업 실행 확인.
- 현장 공유 디스크·대용량 전송과 실제 UE 조립·전시 검증. 이번 이전으로 완료됐다고 간주하지 않습니다.
- develop 테스트 자동 배포는 [MR !37](https://lab.ssafy.com/s15-final/S15P31A504/-/merge_requests/37)과
  [파이프라인 #236465](https://lab.ssafy.com/s15-final/S15P31A504/-/pipelines/236465)에서 연결·검증했습니다.
  main 운영 릴리스는 기능 통합 후 수동 배포와 배포 후 검증이 남아 있습니다. [CI 운영 절차](central/CI_EC2.md)를 따릅니다.
- EC2의 정기 백업·서버 외부 복사·보관 기간, 장애 시 복구 훈련. 최초 이전 백업은 노트북 개인 폴더와 EC2의 제한된 경로에 보관했습니다.

GitLab Runner는 노트북의 별도 서비스입니다. 중앙 서버 이전만으로 Runner를 EC2로 이전하거나 Jenkins로 교체하지 않습니다.
