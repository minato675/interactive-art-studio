# native PC2 연결

기존 28개용 원클릭 설치기의 HTTP 계약을 유지하며 계정 인증된 중앙 API의 선택 재생을 연결한다.
[전체 연결과 상태 의미](../../docs/NATIVE_ARCHIVE_PLAYBACK.md)를 따른다.

`server.py`는 별도 Docker 서비스에서 실행한다. 데이터·기기 키는 기존 `/earth/data`에 두며,
운영자 키의 SHA256만 `operator-token.sha256` 파일로 제공한다. 실제 키는 코드에 넣지 않는다.
`agent.py`는 설치 패키지에 들어가며 같은 패키지의 common.py·handoff.py를 사용한다.
UE 실행 파일과 허용 작품 ID는 고정 카탈로그에서만 선택한다.
