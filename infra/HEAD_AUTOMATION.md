# Head 자동 제작 사이클

새 Head 요청은 배경 생성 → 중앙 저장 이미지 분석 → 에셋 N개 → UE 조립 → PC1 실행 검증 → AI 보고서·캡처 검수 → 승인된 작품의 PC2 게시를 자동 연결한다. 기존 요청과 수동 조립의 승인 정책은 유지한다. 기존 흐름은 자동 게시로 소급 전환하지 않는다.

전시 중 이 사이클에 요청을 계속 넣는 쪽(AI 디렉터 → 작품 명세 → 사전 확인 → 접수)은 [전시 자동 순환](HEAD_EXHIBITION_FEED.md)이다. 자동 순환은 `exhibit-` 요청을 `automation=true` · `auto_publish=true`로 넣을 뿐 이 사이클의 정책을 바꾸지 않는다. GMS 장애(디렉터 · Input Agent · 최근 Head 판단 2개의 `LLMUnavailable`)나 필요한 노드가 꺼졌을 때는 새 요청 넣기만 `EXHIBITION_FEED_RETRY_SECONDS`(60초) 쉬고, 이미 접수된 흐름은 이 사이클대로 진행한다.

asset_count를 생략하거나 null로 보내면 신규 감독 Head가 0~max_asset_count(기본 12)의 개수와 목록을 판단한다. 수량 미지정 시 대표 생성 조건 한 개를 전달하며 이는 생성 수량이 아니다. 지정하면 그 개수를 지킨다. 에셋 실행기는 두 개여도 에셋 수는 두 개로 제한하지 않는다. 배경 참조 이미지는 작업의 inputs로 전달하며 중앙은 업로드된 원본의 SHA-256을 확인한 뒤 AI 이미지 입력으로 사용한다. 별도의 background-input 호출은 필요 없다. 5MiB를 넘는 판단 이미지는 보류하며 원본을 임의로 축소·교체하지 않는다.

에셋 계획에는 객체 배치와 크기를 포함한다. 서버가 UE assets.json v1과 파일 대응표를 만들며 허용 템플릿·파일 이름·행동은 서버 정책으로 고정한다. 템플릿 지문을 확인한 제작 노드만 조립 작업을 임대한다.

AI 검수는 원래 요청·배경·PC1 runtime-report.json·측정값·캡처를 입력으로 받아 approve/rework_assets/rework_background/hold를 반환한다. 기술 판정 pass와 AI approve가 모두 있어야 자동 게시한다. 기술 fail은 AI가 무시할 수 없고 review·환경 의심은 사람 확인으로 보류한다. auto_publish=false는 AI 검수 완료 후 운영자 승인을 기다린다. PC2의 실제 Applied 보고 이후에만 흐름을 완료한다.

재작업은 max_reworks(기본 2, 최대 3) 안에서 새 작업 ID로 수행한다. 기존 파일·조립·검증 결과는 변경하지 않고 이력을 남긴다. 에셋 재작업은 배경을 재사용하고 전체 에셋 계획·생성을 새로 수행한다. 배경 재작업은 배경과 에셋을 새로 만든다. 실행기 설치·네트워크·GPU 환경 문제는 이미지 재생성을 반복하지 않고 보류한다. AI 호출 오류도 게시하지 않는다.

DB 열은 추가하지 않는다. Head payload._flow에 라운드·조립·검증·게시 작업 ID와 이력을 저장하고 decisions에 AI 근거·모델·입력 파일 해시를 남긴다. 네트워크 호출 중 DB 잠금을 유지하지 않으며 저장 시 임대와 현재 라운드를 재검사한다. 단계별 request_id를 고정해 dispatcher 재시작과 중복 호출이 자식 작업을 늘리지 않게 한다.

현재 파일 원본은 EC2 /data/artifacts/objects, 메타데이터는 PostgreSQL에 둔다. 생성 PC→EC2→PC1→EC2→PC2 경로다. 키트의 생성 프로그램은 배정받은 프롬프트와 inputs를 실제 이미지 생성기로 전달하고 결과만 업로드한다. 중앙 변경만으로 미구현된 현장 이미지 생성기를 대신하지 않는다.

API 형식 근거: [OpenAI 이미지 입력](https://developers.openai.com/api/docs/guides/images-vision), [구조화 출력](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses). GMS 게이트웨이의 다중 이미지 지원 여부는 현장 공급자 설정으로 별도 확인한다.

개발자별 전달: [생성 개발자](HEAD_GENERATION_HANDOFF.md), [UE 개발자](HEAD_UE_HANDOFF.md), [생성 프로그램 어댑터](GENERATION_HANDLER.md). PC2 외부 IP 211.192.210.113/32는 호스트 허용 완료이며 PC2의 TCP/TLS 재검사는 필요하다.

배포 코드·검사 결과·현장 미검증 범위는 [자동 사이클 검증 기록](HEAD_AUTOMATION_VALIDATION.md)에 정리했다.

에셋이 불필요하면 assets와 director.instances를 모두 빈 배열로 반환한다. 보류·생성 금지 원본을 작업 목록에 넣지 않는다. asset_count=0 명시도 허용하며, 객체가 필요한 follow/glow는 사전 확인에서 거부한다. 자동 흐름은 에셋 실행기를 호출하지 않고 배경 한 장으로 조립·실행 검수·AI 검수를 이어간다. 지침이 없는 기존 흐름의 계획 계약은 최소 한 개를 유지한다.
