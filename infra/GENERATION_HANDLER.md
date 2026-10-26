# 생성 프로그램 연결 규약

생성 실행기는 generation_worker.py를 사용하면 자동 임대→배경 참조 다운로드→로컬 이미지 프로그램 실행→결과 업로드를 반복한다. 세 실행기에 각각 다른 키트·cache·작업 디렉터리·생성 세션을 설정한다. 기존 수동 client.py 방식도 유지하지만 자동 실행기와 같은 노드 ID로 동시에 켜지 않는다.

## 로컬 설정

키트의 generation.local.example.json을 generation.local.json으로 복사한다. handler는 로컬 프로그램의 argv 배열이다. 중앙 또는 AI가 handler를 지정할 수 없다. Python API 프로그램이면 Python 실행 경로와 스크립트 경로를 배열에 넣고, 자체 실행 파일이면 실행 파일 경로를 넣는다. 셸 문자열은 지원하지 않는다.

```json
{"cache":"C:/Ranel/generation/background/cache","working_directory":"C:/Ranel/GenerationRepo","handler":["C:/Path/To/python.exe","C:/Ranel/GenerationRepo/handler.py"],"timeout_seconds":1800}
```

```powershell
python client.py --check
python generation_worker.py --check
python generation_worker.py
```

handler.py는 생성 개발자가 사용하는 실제 이미지 생성 API/프로그램을 연결하는 어댑터다. 키트가 이미지 모델·Codex 세션·API 키를 제공하지는 않는다. 인터페이스는 다음과 같다.

```text
handler.py --request <task.json 절대 경로> --output <빈 결과 폴더 절대 경로>
```

## task.json

job_id, kind, generation(artwork_id/version/prompt/conditions), inputs(선행 작업의 결과 목록), input_root, output_dir를 제공한다. API·임대 토큰은 넣지 않는다. 배경 참조 파일의 실제 위치는 `<input_root>/<선행 작업 ID>/<files.name>`이다. 에셋 생성 시 프롬프트만 읽지 말고 이 배경 이미지를 생성 API의 참조 이미지에 전달한다.

배경은 conditions.output_name=background.png, 에셋은 asset-001.png 등의 지정 이름으로 저장한다. conditions의 템플릿·규격·알파·역할·허용 차원을 지킨다. 각 작업은 해당 에셋 한 개를 만든다. 에셋 개수가 지정되지 않으면 중앙 Head가 개수와 작업을 결정하므로 생성 쪽에서 임의로 추가·축소하지 않는다.

handler는 0으로 종료하면 성공이다. 실패 시 0이 아닌 코드로 종료한다. output에는 최종 전달할 파일만 저장한다. 디버그·프리뷰·로그·키·UE 프로젝트를 넣지 않는다. 생성 실행기는 지정 파일을 확인하고 SHA-256과 크기로 업로드한다. 로컬 last-handler.log는 생성 프로그램의 출력을 포함하므로 공개 전달하지 않는다.

중앙 업로드가 완료되면 추가 background-input 호출이나 assets.json 수동 조립이 필요 없다. 중앙이 배경을 읽어 에셋을 계획하고, 에셋 결과를 모아 UE 조립 요청을 만든다. 실패 작업을 성공한 것으로 보고하지 말고 오류 상태로 남긴다. 중복 완료 응답은 같은 시도의 저장된 완료 내용을 재전송한다.

재작업 시 새로운 job_id가 온다. 같은 파일 이름이어도 이전 작업 결과를 재사용했다고 임의 보고하지 않는다. 현재 작업의 프롬프트·조건과 input_root를 읽고 새 output에 결과를 만든다. 작업 종료 후 임시 폴더는 삭제되므로 생성 도구의 장기 작업 기록이 필요하면 별도 로컬 디스크에 보관한다.
