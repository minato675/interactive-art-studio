# UE 개발자 전달 — 자동 Head 조립·검수·전시 연결

중앙 EC2: https://k15a504.p.ssafy.io (443). UE 템플릿 지문은 MR !33 2812b85의 기존 5개를 유지한다. 새 3개 템플릿은 기존 정책대로 대응표 연결 전까지 자동 선택하지 않는다.

## PC1에 달라지는 점

- 중앙이 에셋 완료 후 AssemblySpec(spec/assets/files/target_node)을 자동 작성한다. 생성 PC가 별도로 넘길 assets.json을 기다리지 않는다.
- 새 자동 조립은 호환 지문을 보고한 노드에 고정 배정한다. ue_worker.py는 실행 전 `_head.template`의 템플릿·지문과 설치 리소스를 다시 확인한다.
- 입력은 중앙 HTTP 저장소에서 다운로드한다. 생성 PC·PC1·PC2 간 SMB 직접 전송은 요구하지 않는다.
- 조립 완료 후 별도 ue_verify가 등록된다. 검수 release.json 게시·Shipping 실행·손 반응·캡처·측정·보고는 기존 검증 키트가 수행한다.
- Head가 runtime-report.json과 실제 캡처를 AI에 제공해 approve/rework_assets/rework_background/hold를 판단한다.
- 기술 pass + AI approve이면 새 자동 흐름은 PC2 게시로 진행한다. 기술 fail을 AI가 통과시킬 수 없으며 review·환경 의심·프로세스 오류는 사람 확인으로 보류한다. 기존 수동 조립 또는 auto_publish=false는 운영자 승인을 기다린다.

## PC1 설치

최신 PC1 ZIP의 ue-exchange-1·ue-review-1을 각각 별도 폴더에 둔다. 기존 토큰을 유지했다. 진행 중 작업과 완료 pending을 먼저 확인하고 이전 실행기를 종료한다. 기존 runtime.local.json의 로컬 경로는 새 폴더로 복사해 유지할 수 있다. assignment.secret.json을 임의로 새 키트로 복사하거나 삭제하지 않는다.

MR !33 호환 Python 저장소에서 조립 실행기를 실행한다.

```powershell
uv run python <키트>/ue-exchange-1/client.py --check
uv run python <키트>/ue-exchange-1/ue_worker.py --folder <키트>/ue-exchange-1 --report-capabilities
uv run python <키트>/ue-exchange-1/ue_worker.py --folder <키트>/ue-exchange-1 --cache C:/Ranel/assembly/cache
```

다른 터미널에서 ue-review-1의 runtime.local.json을 설정하고 Start-Runtime.ps1 --check, Start-Runtime.ps1 순서로 실행한다. 경로·Shipping PID·전용 검수 폴더·프로세스 중복 방지 등 상세 절차는 UE_RUNTIME_SETUP.md를 따른다. API 변경을 UE 브랜치에 복사할 필요는 없다. 키트가 중앙 계약을 해석하고 UE 브랜치의 assemble·Runner를 호출한다.

## PC2 설치와 접속

PC2의 신고 외부 출발 IP **211.192.210.113/32**를 EC2 호스트 HTTPS 허용 목록에 반영했다. 다른 기존 주소와 SSH 설정은 유지했다. 실제 PC2에서 다음을 확인한다.

```powershell
Test-NetConnection k15a504.p.ssafy.io -Port 443
python client.py --check
```

TCP와 TLS 검사 성공 후 기본 관측 모드에서는 호환 Shipping 실행본을 한 개 실행하고 runtime.local.json의 delivery와 실제 프로세스 PID를 맞춘다. ue-show-1의 Start-Runtime.ps1로 계속 대기한다. 실행기는 승인된 번들을 받아 포인터를 교체하고 상태 파일의 PID·생성 시각·세션·버전으로 실제 Applied를 확인한다. 파일 복사를 Ready/Applied로 보고하지 않는다. 자동 실행·정지·종료 복구를 사용하려면 별도의 [선택적 관리 모드](../apps/show_agent/LIFECYCLE.md)를 설정한다. Draft MR !49의 기능으로 이 문서의 과거 배포 기록과 구분한다.

## 검증·재작업 규약

runtime-report.json은 Report.summary()로 만들며 verdict와 checks·measurements·screenshots·bundle 해시를 유지한다. 캡처도 함께 업로드한다. succeeded는 보고서 접수 완료이며 작품 합격은 아니다. Head 판단 근거와 입력 해시는 중앙 workflow.decisions에 기록한다.

재작업은 새 작업 ID·조립 ID·release 버전을 사용한다. 기존 버전 폴더와 캡처를 수정하지 않는다. 배경 재작업은 배경부터, 에셋 재작업은 같은 배경을 참조해 에셋 전체를 다시 만든다. 기본 두 번의 재작업 이후에는 보류한다. 설치·GPU·프로세스 이상은 이미지 재생성으로 해결하지 않는다.

## 실제 확인이 필요한 것

PC1의 실제 Shipping 화면·합성 손 반응·캡처, GMS 운영 모델의 첫 다중 이미지 검수, PC2 TCP/TLS와 실제 화면 적용은 현장 통합 사이클로 확인한다. 중앙 자동 작업 흐름과 합성 런타임 통합 검사는 실제 Shipping 확인을 대신하지 않는다. UE 실행본은 ZIP에 포함하지 않는다.

원본 파일·보고서·캡처는 EC2 파일 볼륨, 상태·판정·승인은 PostgreSQL, 각 PC에는 실행용 사본을 보관한다.
