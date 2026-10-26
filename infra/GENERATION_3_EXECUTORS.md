# 생성 PC 실행기 3개 연결 안내 — 2026-10-22

생성 PC는 한 대이며 별도의 키트·작업 폴더·Codex 세션 세 개를 사용합니다.
중앙은 EC2의 `https://k15a504.p.ssafy.io`입니다. 기본 HTTPS 443을 사용하며 8443을 붙이지 않습니다.

## 키트 배치

| ZIP 이름 | 압축을 풀 권장 폴더 | 맡는 작업 | 결과 폴더 예시 |
|---|---|---|---|
| generation-background-1.zip | C:\RanelKit\background | background_generate | C:\RanelOutput\background |
| generation-assets-1.zip | C:\RanelKit\assets1 | assets_generate | C:\RanelOutput\assets1 |
| generation-assets-2.zip | C:\RanelKit\assets2 | assets_generate | C:\RanelOutput\assets2 |

키트 원본은 마스터 노트북의 `C:\Users\SSAFY\RanelMaster\kits\generation-dedicated-20261022`에 있습니다.
최초 전달은 `Ranel-Generation-3-Executors-EC2-443-20261022.zip` 하나를 사용합니다.
통합 ZIP에는 background·assets1·assets2 폴더와 배치·실행 안내가 있으며 기존 키트 없이 설치할 수 있습니다.
각 ZIP을 해당 실행기 폴더에 풉니다. 기존 generation-1 키트는 비활성화됐으므로 사용하지 않습니다.
ZIP에는 해당 실행기의 토큰이 들어 있으므로 생성 PC에만 전달하고 Git·공개 채팅에 올리지 않습니다.
공유 파일 저장소 `C:\RanelAssets`는 공용으로 사용할 수 있지만 상태 파일과 결과 폴더는 섞지 않습니다.

## 실행

Python 3.10 이상이 필요합니다. 세 키트 폴더에서 각각 아래 명령을 실행합니다.

```powershell
python client.py --check
python client.py --watch
```

`--check`의 node_id와 job_kinds를 위 표와 비교합니다. `tls=verified`가 나와야 합니다.
`--watch` 창은 유지하고 각 키트 폴더의 별도 창에서 작업을 받습니다.

```powershell
python client.py --claim
```

작업이 없으면 `no_ready_job`입니다. 배경이 성공하기 전 에셋 실행기에서 작업이 없는 것은 정상입니다.
각 실행기의 `assignment.json`을 해당 Codex 세션에 전달합니다.
`assignment.secret.json`은 임대 토큰을 포함하므로 프롬프트에 붙이지 않습니다.
기존 client.py 수동 방식은 생성 프로그램을 실행하지 않습니다. 새 generation_worker.py 자동 대기·로컬 프로그램 연결은 GENERATION_HANDLER.md를 따릅니다.

HTTP 전송 모드에서는 키트의 `WORKFLOW.md`·`SERVER_EXCHANGE.md`에 따라 입력을 받고
결과를 업로드합니다. 작업의 `file_transport`에 맞는 방법을 사용하며, 공유 저장소를 통한 모드와 혼동하지 않습니다.
작업 임대 중에는 heartbeat를 유지하고 오래된 시도의 결과를 새 작업에 등록하지 않습니다.

## 첫 통합 사이클

1. 생성 전에 중앙과 `spec.json`·`choice.json`을 공유하고 image_stir 지원을 확인합니다.
2. 에셋 수는 지정하거나 생략합니다. 생략하면 Head가 max_asset_count(기본 12) 안에서 정합니다.
3. 배경 전담이 작업을 받아 제작·검사 후 결과를 등록합니다.
4. 새 자동 흐름은 중앙 저장 배경을 Head가 직접 읽습니다. background-input 호출·운영자 토큰 전달은 필요 없습니다.
5. Head가 만든 N개 에셋 작업을 에셋 전담 둘이 하나씩 나눠 받습니다.
6. 결과가 모두 등록되면 중앙이 assets.json 메타데이터와 UE 조립 작업을 자동으로 만듭니다.

현재 연결 설정은 생성 담당자가 정정한 주소 `70.12.245.47`과 운영 노트북 출구 IP `211.192.210.218`을 허용합니다.
이전에 전달된 `211.192.252.228`은 잘못된 값으로 제거했습니다.
생성 PC에서 실제 EC2 접속이 성공하는지는 아직 확인하지 않았습니다. NAT 등으로 실제 출발 주소가 다르면
그 주소를 확인해 중앙 담당자에게 알려 접근 규칙을 갱신합니다.

## 이번에 확인한 것

- EC2 새 이미지 적용, 기존 PostgreSQL 컨테이너와 볼륨 유지.
- EC2 Linux 전체 테스트 301개 통과, 실제 PostgreSQL 동시 임대 포함, Ruff 통과.
- 운영 노트북에서 세 키트 모두 외부 HTTPS 443·CA 인증서·전담 종류 검사 통과.
- EC2 실제 API에서 배경 성공 전 에셋 임대 보류, 배경/에셋 교차 배정 방지,
  에셋 두 개 동시 임대, 노드당 슬롯 하나, 세 번째 에셋 후속 임대 확인.
- 합성 PNG의 HTTP 업로드·완료 등록 성공, 기존 generation-1 인증 401 확인.
- 테스트는 합성 제작 결과를 사용했으며 유료 AI 호출은 하지 않았습니다.

생성 PC에서의 실제 접속, Codex 세 세션의 제작, UE 화면 검증은 아직 별도 확인이 필요합니다.

## 배포 기록

- 작업 브랜치: `S15P31A504-20-generation-routing`.
- 배정·발급 기능: `cfa8010`.
- HTTPS 접근 제어: `c18d259`.
- 실행 소스: `c18d2591dacb63a4965e06e76d65a9aa8eabeccd`.
- 서버 소스: `/opt/ranel/releases/c18d2591dacb63a4965e06e76d65a9aa8eabeccd`.
- 서버 검사·백업: `/opt/ranel/operations/generation-c18d259/`.
- `before/`에는 DB 덤프와 서버 비밀 설정이 있으므로 서버 담당자만 접근합니다.
- AWS 보안 그룹은 변경하지 않았습니다. 서버의 UFW와 Docker DOCKER-USER 체인에 명시적인
  출구 IP 제한을 적용하고 전용 systemd 서비스로 재적용하도록 설정했습니다. 실제 재부팅 검증은 미실시입니다.
- 기존 노드 ID·토큰·DB 스키마는 보존하며 기존 범용 generation-1의 인증만 비활성화했습니다.
- 이번 기능 브랜치는 운영 코드보다 오래된 develop 코드로 덮어쓰지 않도록 MR 통합 후 유지해야 합니다.
