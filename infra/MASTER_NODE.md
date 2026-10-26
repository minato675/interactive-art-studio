# 노트북 마스터 운영

노트북의 Linux Docker에서 중앙 FastAPI·PostgreSQL·RabbitMQ·Celery·Dispatcher·HTTPS를 실행합니다.
현재 역할은 중앙 운영 기반이며, 생성 PC와 UE 제작·시연 PC의 실접속은 별도 확인합니다.

## 배포 버전

`local_pipeline.py --deploy`는 Git 추적·미추적 변경이 없는 작업 브랜치에서 실행합니다.
이미지 태그·OCI revision 라벨·`runtime/validation/laptop-ci/*/result.json`에 커밋을 기록합니다.
배포 실패 시 이전 이미지로 복구하며, 기존 DB·데이터·CA 볼륨은 보존합니다.
검사만 수행하는 실행은 작업 중 소스에서도 가능하며 기록에 dirty 여부를 남깁니다.

## 노트북 설치와 시작

저장소 루트에서 `pwsh -File infra/Install-Master.ps1`를 실행합니다. 현재 사용자 Startup 폴더에
숨김 시작 바로가기를 설치하고, AC 전원에서 절전·최대절전 대기를 해제합니다. 기존 전원 값은
사용자 프로필의 `RanelMaster/power-before.json`에 저장합니다.
배터리 전원 정책은 OS 설정을 따릅니다. 덮개 동작·자동 로그인·Windows 업데이트 정책은 별도 운영 사항입니다.

로그인하면 Docker Desktop 시작을 기다린 뒤 기존 이미지로 중앙을 시작합니다. Docker Desktop을 사용하는
구조이므로 로그인 전의 Windows 서비스 운영을 보장하지 않습니다. 실제 재부팅 검증은 점검 시간에 수행합니다.

```powershell
pwsh -File infra/Start-Master.ps1 -Action Start
uv run python -m infra.master_node status
uv run python -m infra.master_node backup
```

마스터용 `infra/central/compose.laptop.yaml`에 CPU·메모리·PID 상한을 둡니다. 중앙 서비스 합계 메모리 상한은
약 5.1 GiB이며 예약량은 아닙니다. Runner와 개발용 SQLite 서버는 별도 Compose 프로젝트입니다.
실제 사용량을 보고 한도를 조정합니다. 중앙 시작 명령은 이미지 빌드를 수행하지 않습니다.

설치기는 현재 사용자에게 매일 19시 백업 작업 등록을 시도합니다. 작업 스케줄러 권한이 없으면 설치 보고에
그 사실을 기록하며 자동 시작은 Startup 바로가기로 유지합니다. 백업은 `backup` 명령으로 실행할 수 있습니다.
예약 실행은 로그인 상태와 Docker 실행이 필요하고, 진행 중 제작 작업이 있으면 백업을 건너뛰고 로그에 남깁니다.

실행 결과·설치 보고·백업은 위 개인 경로(이 PC는 `C:/Users/SSAFY/RanelMaster`)에 저장하며
해당 사용자·SYSTEM·Administrators만 접근하도록 설정합니다. 앱의 AppData 가상화와 OneDrive의 영향을 피합니다.

## 구성 원칙

운영 상태는 PostgreSQL로 관리합니다. 별도의 SQLite 개발 서버 8001과 중앙 API 18080을 구분합니다.
노드 토큰과 DB 비밀번호는 기존 값을 사용하고 Git에 넣지 않습니다.
이미지·에셋의 실제 공유 저장소는 생성 PC에 두며 내일 해당 PC에서 공유·계정·속도를 검증합니다.
노트북에는 중앙 데이터와 서버 전용 백업을 보관합니다.

## 백업과 격리 복원 확인

`uv run python -m infra.master_node backup`은 작업이 없을 때 중앙 쓰기 서비스를 잠시 멈추고
PostgreSQL·중앙 파일·CA·운영 설정·추적 소스를 저장한 뒤 서비스를 재개합니다. 백업에는
원본 테이블별 행 수, 파일별 해시, 실행 이미지·커밋 정보가 포함됩니다. 비밀 값은 개인 폴더에만 저장합니다.

```powershell
uv run python -m infra.verify_backup --backup '<개인 백업 폴더>'
```

위 검사는 체크섬을 먼저 확인하고 고유 이름의 격리 PostgreSQL 컨테이너·빈 볼륨을 생성합니다.
운영 포트를 열지 않으며 복원한 행 수와 중앙·CA 파일 해시를 비교하고 자신이 만든 자원만 제거합니다.
검증 결과는 개인 로그 폴더에 남깁니다. 생성 PC의 실제 에셋 공유 폴더는 별도 백업 대상으로,
이 서버 백업의 central-data에 현장 파일 전체가 포함된다고 가정하지 않습니다.

## PC 연결 확인 — 다음 작업

API 기반 Head 운영 절차는 [Head 문서](central/HEAD_NODE.md)를 따릅니다. 백업에는 선택 설정
`.env.head`도 포함하므로 키가 들어 있는 백업은 기존과 동일하게 개인 폴더에서 관리합니다.
현재 외부 PC 검증 전에는 HTTPS를 loopback 주소로 제한할 수 있습니다. 다음 연결 시 실제 LAN 주소로
`.env.https`를 설정하고 방화벽·인증서 이름을 다시 확인합니다.

1. 실제 노트북 주소·HTTPS 인증서 이름·방화벽 허용 IP를 확인합니다.
2. 생성 PC에 해당 노드 전용 설정과 공개 CA를 전달합니다.
3. 생성 PC에서 인증·heartbeat·작업 임대·입력 다운로드·결과 등록을 확인합니다.
4. 생성 PC의 전용 저장 폴더를 공유하고 실행 계정의 읽기·쓰기 권한과 해시 전송을 확인합니다.
5. UE 제작·시연 노드 연결은 각 역할의 토큰·카탈로그·실행기를 준비한 후 검증합니다.

노트북 내부 진단 성공을 다른 PC의 접속 성공으로 기록하지 않습니다.
