# 노트북 GitLab Runner

노트북의 Docker Desktop Linux 엔진에서 이 프로젝트의 CI 작업을 실행합니다.
중앙 서버 Compose와 별도 프로젝트이며, EC2를 제공받아도 중앙 서버 이전과 Runner 이전은 각각 결정합니다.
노트북이나 Docker Desktop이 꺼져 있거나 절전 상태이면 CI 작업은 대기합니다.

## 실행 구성

- 공식 GitLab Runner 19.5.0 이미지를 digest로 고정합니다. 현재 등록된 프로젝트 Runner는 [#2191](https://lab.ssafy.com/s15-final/S15P31A504/-/runners/2191)입니다.
- GitLab 프로젝트 `S15P31A504`에 잠그고 `linux-docker` 태그 작업만 받습니다. MR 검사를 위해 작업 브랜치도 허용합니다.
- 동시에 CI 작업을 최대 4개 실행합니다. 작업 컨테이너는 CPU 2개·메모리 2 GiB, 각 서비스는 CPU 2개·메모리 2 GiB로 제한합니다. lint 후 네 테스트가 병렬 실행되며 이미지 검사는 프로젝트당 하나씩 실행합니다.
- 작업별 Docker 네트워크를 만들며, 작업 컨테이너에는 Docker socket·호스트 폴더·중앙 서버 데이터 볼륨을 연결하지 않습니다.
- Runner 관리자만 작업 컨테이너 생성용 Docker socket을 사용합니다. 이 프로젝트에서 실행하는 CI 코드는 팀이 검토해야 합니다.
- 일반 job에는 기존 `seccomp-buildkit.json`을 적용하며 privileged 모드는 사용하지 않습니다.
- 실제 컨테이너 기동 검사는 digest가 고정된 공식 `docker:29.9.1-dind-rootless` 서비스 하나에만 privileged 실행을 허용합니다. Docker 데몬은 UID 1000의 rootless 모드이며 일회성 데이터만 보관합니다. 이 서비스 권한은 컨테이너 내부 Docker 실행을 위한 예외이며 완전한 호스트 격리를 보증하지는 않습니다.
- CI job은 임시 데몬에만 TLS로 연결합니다. `/certs/client`는 Runner가 작업 슬롯별로 관리하는 인증서 볼륨이며 호스트 경로가 아닙니다. 중앙 서버의 Docker socket·볼륨은 공유하지 않습니다. 서비스 이미지 allowlist를 임의로 넓히지 않습니다.
- 토큰이 들어 있는 실제 `config.toml`은 전용 Docker volume에 저장합니다. 토큰·실제 설정은 Git이나 MR에 올리지 않습니다.

## 시작과 중지

저장소 루트에서 실행합니다. 새 PC는 GitLab에서 Runner 생성·등록을 먼저 해야 합니다.

```powershell
docker compose -f infra/runner/compose.yaml up -d
docker compose -f infra/runner/compose.yaml ps
docker compose -f infra/runner/compose.yaml stop
```

실행 중인 CI를 끝낸 뒤 중지하려면 GitLab의 Runner 화면에서 먼저 **Pause**하고 현재 job 완료를 기다린 후
위 `stop` 명령을 사용합니다. 다시 시작할 때 GitLab에서도 **Resume**합니다.
`config.template.toml`은 비밀 값이 없는 등록 템플릿이며 토큰은 별도로 전달합니다.
중앙 서버의 `.env.infra`, 생성 PC 토큰, 내부 CA 개인키는 Runner에 복사하지 않습니다.

### BuildKit 보안 프로필

Docker 기본 프로필에서는 rootless BuildKit이 `fork/exec /proc/self/exe: operation not permitted`로
시작하지 못했습니다. [GitLab 안내](https://docs.gitlab.com/ci/docker/using_buildkit/#rootless-build-fails-with-permission-errors)에
따라 `clone`, `unshare`, `setns`, `mount`, `umount2`, `pivot_root`, `sethostname`을 추가 허용한 프로필을 사용합니다.
커널 capability 검사와 다른 seccomp 규칙은 유지합니다. 노트북 Docker 엔진에는 AppArmor가 활성화되어 있지 않으며,
다른 Linux 호스트로 옮길 때는 해당 호스트의 보안 설정에서 다시 검증해야 합니다.

원본은 [moby/profiles의 고정 커밋](https://github.com/moby/profiles/blob/4b8214fc4971e06172938a1f1554ccff6647ee80/seccomp/default.json)이며,
원본 SHA-256은 `6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6`입니다.
추가로 `keyctl`의 세션 생성 요청은 계속 차단하되 오류를 `ENOSYS`로 반환합니다.
[runc의 미지원 환경 처리](https://github.com/opencontainers/runc/blob/main/libcontainer/standard_init_linux.go)를
사용하며, 키 조회·추가·검색을 허용하지 않습니다. 변경은 JSON 마지막 항목 2개이며,
Apache-2.0 원본 고지는 `LICENSE.moby-profiles`에 보존합니다.
이 프로필은 프로젝트 Runner의 작업 컨테이너에 적용되며, 중앙 서비스의 보안 설정은 변경하지 않습니다.

등록 전에 `docker volume create ranel-gitlab-runner-config`로 전용 볼륨을 만듭니다.
등록 시 `--template-config`로 이 템플릿을 전달한 뒤, 실제 `/etc/gitlab-runner/config.toml`의
전역 `concurrent`를 **4**로 설정합니다. 등록 템플릿의 `limit = 4`만으로는 전역 기본값 1을
늘릴 수 없습니다. 현재 노트북에는 전역 `concurrent = 4`, Runner의 `limit = 4`와
`request_concurrency = 4`를 모두 적용했습니다. 이 중 `request_concurrency`는 작업 요청 수이고
실제 동시 실행 상한은 `concurrent`와 `limit`입니다.

컨테이너는 Docker 엔진이 다시 시작되면 자동 재시작합니다. Docker Desktop이 실행되어야 하며,
`stop`으로 직접 중지했다면 위 `up -d`로 다시 시작합니다. 노트북 방화벽에 Runner용 인바운드 포트를
추가하지 않습니다. Runner가 GitLab에 HTTPS로 작업을 요청합니다.

MR의 lint·네 테스트·build-smoke 검증과 main의 수동 EC2 배포는 [CI 설정](../../.gitlab-ci.yml)을 따릅니다.
EC2 배포는 서버 제공 후 SSH 호스트 키·배포 변수·Registry·TLS를 확인한 뒤 활성화합니다.

근거: [Runner 컨테이너 실행](https://docs.gitlab.com/runner/install/docker/),
[Docker executor](https://docs.gitlab.com/runner/executors/docker/),
[Runner 설정](https://docs.gitlab.com/runner/configuration/advanced-configuration/).

일회성 이미지 검증은 [GitLab의 제한된 rootless DinD 서비스 설정](https://docs.gitlab.com/runner/executors/docker/#use-rootless-docker-in-docker-with-restricted-privileged-mode)을 따릅니다.
일반 job의 `privileged=false`를 유지하고 `services_privileged`와 `allowed_privileged_services`로 고정 이미지 하나만 허용합니다.
Runner 실제 설정은 등록 후 템플릿과 맞춰야 하며, 템플릿 파일 변경만으로 실행 중인 Runner가 바뀌지는 않습니다.
