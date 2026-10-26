# PC1 검증·PC2 시연 키트 설치 안내

## 키트 구분

- 기존 `ue-exchange-1`: 조립만 수행한다. 기존 실행기를 유지한다. 수정된 공통 client.py도 함께 제공한다.
- 새 `ue-review-1`: PC1에서 실행 검증만 수행한다. 중앙이 조립 완료 후 자동 배정한다.
- 새 `ue-show-1`: PC2에서 승인된 작품의 다운로드·게시·실제 적용 보고를 수행한다.

각 키트는 서로 다른 토큰·폴더를 쓴다. PC1에서는 조립과 검증을 별도 터미널로 실행할 수 있다. 검증기는 UE 프로세스 하나를 직접 관리하므로 수동 검증기·다른 UE 검증 프로세스와 동시에 실행하지 않는다. PC2 키트를 PC1에서 실행하지 않는다. 토큰 ZIP은 담당자에게 비공개 전달한다.

## PC1 담당자

1. uv와 호환 Shipping 패키지, MR !33 `2812b85`의 `studio.verify.ue_runner` 및 `studio.assembly.ue_assemble`을 준비한다. 더 최신 revision은 중앙 지문 등록 여부를 확인한다. 중앙 저장소를 UE 저장소 위에 덮어쓰지 않는다.
2. 조립 키트는 기존대로 실행한다. 새 검증 키트는 예: `C:\Ranel\ue-review-kit`에 압축 해제한다.
3. `runtime.local.example.json`을 `runtime.local.json`으로 복사한다. repository는 UE Python 저장소, executable은 실제 Shipping 실행 경로, working_directory는 패키지 루트다. delivery·cache·probe는 서로 겹치지 않는 전용 절대 경로를 쓴다. 전시 PC 폴더를 지정하지 않는다.
4. PowerShell에서 `& .\Start-Runtime.ps1 --check`로 설정과 TLS를 확인한다. `--check`는 UE를 실행하거나 작업을 임대하지 않는다. 이어서 `& .\Start-Runtime.ps1`로 대기한다.
5. 조립 완료 후 검증 작업이 자동 배정된다. 다운로드→해시 검증→불변 버전 준비→release.json 교체→Shipping 실행·합성 손 입력·캡처→`Report.summary()` 업로드 순서다. Ctrl+C 시 이 검증기가 시작한 UE만 종료한다.

기존 API·계약을 UE 브랜치에 이식할 필요는 없다. 키트는 표준 HTTP 클라이언트이며 UE 브랜치의 검증기만 import한다. API 토큰이나 GMS 키를 UE 프로그램에 넣지 않는다.

## PC2 담당자

아래는 기존 고정 PID 관측 모드다. 새 PC2의 `auto_launch=true` 키트는 [시연 자동 복구](UE_SHOW_RECOVERY.md)를,
명시적 시작·정지와 제한된 재시도를 사용하는 `managed_runtime` 모드는 [Show Agent 관리 설정](../apps/show_agent/LIFECYCLE.md)을 따른다.
두 자동 관리 설정은 함께 지정할 수 없다. 세 방식 중 하나만 실행하며, 자동 관리 방식은 같은 Windows 호스트 잠금을 공유한다.

1. uv와 호환 Shipping 패키지를 설치한다. 실행본은 키트에 포함하지 않는다. 새 Sprite·템플릿을 쓰는 작품은 해당 기능을 포함해 빌드해야 한다.
2. `ue-show-1` 키트를 예: `C:\Ranel\ue-show-kit`에 압축 해제한다. example을 runtime.local.json으로 복사하고 delivery·cache를 설정한다.
3. Shipping을 **한 개만** 실행한다. 기존 실행 명령에 `-ArtworkRelease=C:\Ranel\show\delivery\release.json`을 지정한다. 경로는 local 설정과 같아야 한다. 이 폴더는 미리 생성한다. 기존 전시와 전환하려면 기존 정상 버전 폴더와 포인터를 함께 보존한다.
4. 실제 Shipping 프로세스 PID를 확인한다. `Get-CimInstance Win32_Process | Where-Object Name -Match 'Art.*exe' | Select-Object ProcessId,Name`으로 확인하고 runtime_pid에 넣는다. 런처 Art.exe가 아니라 상태 파일 ProcessId와 같은 프로세스를 선택한다.
5. `& .\Start-Runtime.ps1 --check`, 이후 `& .\Start-Runtime.ps1`을 실행한다. psutil 의존성은 uv가 설치한다. 처음 설치에는 인터넷이 필요하다.
6. 중앙이 승인한 번들만 내려받는다. 파일 준비는 Staged이며 Ready·Applied는 UE 상태를 관찰해 보고한다. 타임아웃은 작업 실패로 기록하며 자동으로 다른 작품을 승인하지 않는다. 런타임이 거부한 경우 이전 작품 유지 동작은 UE watcher가 담당한다.
7. UE 재시작 시 PID가 바뀌므로 설정을 갱신하고 키트를 재시작한다. 마지막 정상 작품과 버전 폴더는 자동 삭제하지 않는다. 장기 인터넷 단절 시 현재 로컬 작품은 유지되며 새 작업은 진행되지 않는다.

PC2에서는 실행기를 종료해도 UE를 종료하지 않는다. 기본 관측 모드는 UE를 추가 실행하지 않으며, 관리 모드는 명시적 stop 명령으로 정지한다. 어느 모드도 빌드·쿠킹을 하지 않는다. PC2의 실제 공인 출구 IP가 EC2 HTTPS 허용 목록에 있어야 한다. 기존 PC1 허용 IP를 PC2 주소라고 가정하지 않는다.

## 중앙 운영자

새 Head 요청은 HEAD_AUTOMATION.md의 자동 사이클을 사용한다. 기술 pass와 AI approve가 모두 있으면 자동 게시한다. 아래 승인 API는 기존 수동 흐름·auto_publish=false·사람 검수가 필요한 review에 사용한다.

review 대기 목록 조회·별도 승인/반려·재전송 규칙은 [사람 검수 안내](UE_RUNTIME_VERIFY.md)를 따른다.
검수만 승인해도 PC2 작업은 생성되지 않는다. 반려한 작품은 아래 게시 승인 API로도 게시할 수 없다.

`GET /infra/jobs`와 `GET /infra/jobs/{id}`로 ue_verify 결과를 확인한다. result.verdict가 pass/review이면 보고서와 캡처를 읽고 운영자 인증으로 다음 API를 호출한다.

```http
POST /infra/jobs/{ue_verify 작업 ID}/approve-publication
X-Operator-Token: <운영자 전용 토큰>
Content-Type: application/json

{"request_id":"show-art001-v001-1","target_node":"ue-show-1","reviewer":"검수자 이름","reason":"캡처와 반응 검토 완료"}
```

같은 request_id·같은 내용은 중복 등록하지 않는다. 다른 작품 게시에는 새 request_id를 쓴다. fail은 승인 불가다. review는 사람이 판정한 사유를 적는다. 운영자 토큰을 PC1·PC2 개발자 키트에 넣지 않는다. Applied 확인은 게시 작업 result.phase와 `/infra/nodes`를 함께 확인한다. 오래된 heartbeat의 Applied는 현재 온라인 상태를 보증하지 않는다.

## 데이터·오류 대응

원본 파일·보고서·캡처는 중앙 EC2 파일 볼륨, 메타데이터·승인은 PostgreSQL에 남는다. PC1·PC2 버전 사본은 자동 정리하지 않는다. 자세한 기준은 UE_RUNTIME_PIPELINE.md다. 로컬 delivery의 포인터가 `ue-<조립 작업 ID>`를 사용하므로 작품 버전 이름이 같아도 서로 덮어쓰지 않는다.

연결 실패 시 `python client.py --check`로 TLS·노드 등록부터 확인한다. 검증 실패는 runtime-report.json의 verdict·checks를 확인한다. 보고서 파일은 중앙의 작업별 파일 다운로드 API로 받을 수 있다. 임대 소유권이 유실되면 새 게시와 완료 확정을 중단한다. 완료 응답 유실은 저장된 pending을 재전송한다. 이미 성공한 조립을 변경하지 말고 수정 작품은 새 버전·새 조립 작업으로 만든다.

실제 Shipping/GPU 실행 검증과 PC2 화면 적용 확인은 해당 PC에서 이 절차를 실행한 뒤에만 완료로 기록한다.

PC2 신고 외부 출발 IP 211.192.210.113/32는 2026-10-22 EC2 호스트의 HTTPS 허용 목록에 추가했다. 실제 PC2에서 TCP와 client.py --check 재검사가 필요하다.
