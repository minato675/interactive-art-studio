# UE 지문 기준 갱신

## MR !57 잔상 기준 갱신 — 2026-10-23 (로컬 반영, 배포·노드 재보고 전)

중앙 목록은 MR !57 `8d2b03b740172307c4cf556f8e1116b8f08beac2`(날갯짓 나비 · 캔버스 원화 · 템플릿 값 변경)의 설치 리소스 기준으로 다시 계산했습니다. 이 템플릿은 라넬 작업 폴더 9957d16 카탈로그로 만든 새 UE 실행본에서만 적용되므로 중앙 배포 · 노드 재보고 · 실행본 교체를 같은 시점에 합니다. `source`에는 MR 번호와 전체 revision을 기록합니다. Head 검수에는 기존 화면과 함께 보고서에 존재하는 `afterimage`·`settled` 화면을 전달합니다.

`ae903ee`는 검증마다 `Trace.jsonl`을 교체하고 새로 추가된 완성 줄만 이어 읽어, 계측 누적으로 검증이 느려지는 문제를 수정합니다. `ue_runner.py`는 현재 조립 지문의 계산 대상이 아니므로 5종 지문 값은 `548676de` 때와 같고 출처 revision만 최신화됩니다. 배포할 실행 검증 러너는 `ae903ee`와 이 연동 MR의 교체 거부 시 읽기 위치 보완을 함께 포함해야 합니다. 새 Runner의 최초 교체가 거부되더라도 기존 대용량 계측은 건너뜁니다. PC1 화면 보정으로 scene·slots·조립기·계약 검사기가 바뀌면 지문을 다시 내보내고 노드도 재보고해야 합니다.

아래 순서로 중앙과 실제 제작·검증 PC를 함께 갱신합니다. 이 문서의 파일 수정은 서버 배포나 PC1 설치·보고 완료를 뜻하지 않습니다.

1. MR !57과 이 연동 변경을 포함한 통합 버전에서 테스트합니다. 중앙 목록의 지문과 실제 설치 리소스의 지문 5개가 같은지 확인합니다.
2. 노드의 새 작업 수신을 중지하고 진행 중 작업을 마무리한 뒤, 중앙과 제작·검증 PC에 같은 통합 버전을 설치합니다. 기존 흐름에 고정된 지문·조건은 덮어쓰지 않습니다.
3. UE 제작 노드가 `ue_worker.py --report-capabilities`로 설치 리소스를 다시 보고하고, 실행기를 재개해 heartbeat를 유지합니다. 토큰·CA·DB는 유지합니다.
4. 운영자 인증으로 `GET /infra/head/templates`의 일치·온라인 상태와 `head-preflight` 성공을 확인한 뒤 새 제작 요청을 재개합니다.
5. PC1에서 손 뗀 뒤 잔상·가라앉음 화면과 Head 검수 입력을 확인합니다. 물결·입자의 `review` 판정은 PC1 기준 보정 전까지 유지합니다.

카탈로그를 다시 내보낼 때는 **검토한 버전을 실제로 설치한 프로젝트 루트**에서 실행합니다. revision 인자는 설치 코드를 바꾸지 않으므로 실제 설치 버전과 일치하는 전체 SHA를 넣습니다.

```powershell
uv run python infra/ue_capabilities.py --export-catalog packages/studio/src/studio/head/ue_templates.json --source-revision 8d2b03b740172307c4cf556f8e1116b8f08beac2 --source-merge-request 57
```

내보내기는 로컬 파일 저장입니다. 중앙 배포와 노드 보고는 별도로 수행합니다. `--source-merge-request`를 생략하면 기존 MR !33 내보내기와 호환되도록 33을 기록합니다.

## 이전 합동 시험 기준 — 2026-10-22

이번 합동 시험의 기준은 MR !33 `2812b856cceea7f85d50dedfed661ed2390314d5`입니다.
중앙 `ue_templates.json`과 실제 UE 노드의 보고가 같은 지문이어야 생성 사전 확인을 통과합니다.
중앙 기준을 갱신해도 노드 설치·보고·Shipping 실행 검증이 자동 완료되지는 않습니다.

## UE 담당자 실행 순서

기존 EC2 443 키트를 그대로 사용합니다. 토큰 교체나 새 ZIP 설치는 필요 없습니다.
UE PC에 실제 설치한 MR !33 프로젝트 루트에서 실행합니다.

```powershell
cd C:\Ranel\ue-kit
Test-NetConnection k15a504.p.ssafy.io -Port 443
python client.py --check
cd C:\Ranel\project
git rev-parse HEAD
uv run python C:\Ranel\ue-kit\ue_capabilities.py --export-catalog C:\Ranel\ue-kit\ue-catalog-2812b85.json --source-revision 2812b85
uv run python C:\Ranel\ue-kit\ue_worker.py --report-capabilities
uv run python C:\Ranel\ue-kit\ue_worker.py
```

`--export-catalog`에는 출력 파일 경로를 지정합니다. 이는 로컬 파일 저장이며 중앙 보고가 아닙니다.
`--report-capabilities`가 노드 보고입니다. 실제로 불러온 코드와 지정한 revision이 같은지 확인합니다.
연속 실행 중에는 heartbeat를 유지하며 중앙은 90초 이내 온라인 상태를 확인합니다.
현재 보고 계약은 기존 5개만 지원하며 새 3개는 대응표·계약 확장까지 보고 대상에서 제외합니다.

## 네트워크

UE PC가 확인한 실제 출발 IP는 `211.192.210.162/32`입니다. EC2 서버의
UFW·Docker DOCKER-USER·재적용 서비스에 추가했습니다. 기존 생성 PC 신고 주소
`70.12.245.47/32`와 운영 노트북 `211.192.210.218/32`도 유지합니다.
AWS 보안 그룹은 이번 서버 작업에서 변경하지 않았습니다.
현재 인스턴스 IAM 자격으로 DescribeSecurityGroups 호출 시 403 UnauthorizedOperation이 발생해
AWS 측 실제 규칙을 API로 확인·변경할 수 없습니다. 서버 허용 후에도 TCP 접속이 실패하면
AWS 관리 권한이 있는 담당자가 TCP443·원본211.192.210.162/32 허용 여부를 확인해야 합니다.
SSH22·DB·다른 서비스의 포트는 변경하지 않습니다.
허용 후 UE 담당자가 TCP443과 `client.py --check` 모두 성공했다고 회신했습니다.
이 확인은 UE 노드 보고나 실제 조립·화면 검증 완료를 의미하지 않습니다.

서버 점검 결과 UFW와 SSH 서비스는 활성, SSH 포트는22·공개키 인증은 활성·암호 인증은 비활성입니다.
`/home`·`/etc`·`/usr`·`/var`는 root 소유755, 기존 공개키 파일과 서버 환경 파일은600입니다.
이번 작업에서 기존 시스템 디렉터리 권한이나 공개키는 변경하지 않았습니다.
PostgreSQL·RabbitMQ는 호스트에 포트를 발행하지 않고 API는127.0.0.1:18080에 바인딩합니다.
기존8443·8444의 UFW 허용 규칙은 남아 있으며 이번 작업에서 다른 테스트 환경의 접근 정책은 변경하지 않았습니다.

## 운영 기준

평소에는 검증한 develop 통합·배포 버전을 기준으로 갱신합니다. 이번에는 합의한 시험 커밋을 고정합니다.
대상 코드·계약·템플릿이 바뀌어 지문이 변경되면 중앙과 UE 양쪽을 갱신합니다.
문서만 바뀌어 계산 대상이 같으면 재등록이 필요 없습니다. 기존 제작 중 작품의 조건은 덮어쓰지 않습니다.
PC2 Shipping 배포·Report.verdict 기반 실행 검증·최종 게시 승인은 별도 확인 사항입니다.

## 적용 결과

- 기능 커밋·실행 이미지 기준: `edc6493b26517219013ca1c52f8e87be64421534`.
- EC2 Linux 전체 테스트302개 통과, 실제 PostgreSQL 검사 포함, Ruff 통과.
- Windows 전체 테스트296개 통과, PostgreSQL 전용6개 건너뜀, Ruff 통과.
- UE 노드의 보고5개와 중앙 기준2812b85의 지문이 모두 일치.
- 실제 `/infra/head/preflight`에서 image_stir·ue-exchange-1 호환 판정 통과.
- 사전 확인은 유료AI 호출0회·작업 등록0개·runtime_verified=false.
- DB컨테이너·볼륨·노드 토큰·기존 작품 조건을 유지하고 API·Worker·Dispatcher만 교체.
- 배포 전 DB·환경 파일 백업은 `/opt/ranel/operations/ue-catalog-edc6493/before/`에 제한된 권한으로 보관.
- 이번 배포에서 SSH·방화벽·시스템 디렉터리 권한을 추가 변경하지 않음.
