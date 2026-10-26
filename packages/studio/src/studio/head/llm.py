"""LLM 연결. 기본은 비움(none) → 규칙으로 계획한다.
LLM_PROVIDER=gms면 SSAFY GMS(OpenAI Responses API 중계), ollama면 로컬 Ollama를 쓴다.
모두 complete_json(system, user, schema) 하나로 같은 모양이라, 부르는 쪽(planning · spec_matching)은 어느 것인지 모른다.
LLM_FALLBACK=ollama면 FallbackLLM이 주 LLM에 닿지 못할 때(네트워크 끊김 포함) 이 PC의 Ollama(기본 모델 EXAONE 3.5)에 대신 묻는다."""
import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol


class LLMError(RuntimeError):
    """LLM에서 쓸 만한 답을 받지 못함. 부르는 쪽은 한 번 다시 묻고, 그래도 안 되면 규칙으로 대체한다."""


class LLMUnavailable(LLMError):
    """LLM 공급자에 닿지 못함: 네트워크 오류 · 시간 초과 · HTTP 오류(키 오류 401, 크레딧 소진 · 한도 초과 429 등).
    답 내용이 틀린 경우(LLMError)와 달리 공급자 자체가 안 되는 것이라, FallbackLLM은 대체 LLM으로 넘기고 잠시 건너뛴다."""


class LLM(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict) -> dict: ...


class OllamaLLM:
    """로컬 Ollama. GPU를 다른 작업과 나눠 쓰면 첫 응답이 2분 넘게 걸린 적이 있어 시간 제한을 넉넉히 둔다."""
    name = "ollama"

    def __init__(self, model: str, url: str, timeout: float = 180):
        self.model, self.url, self.timeout = model, url, timeout

    def complete_json(self, system, user, schema):
        payload = {"model": self.model, "stream": False, "format": schema, "think": False,
                   "options": {"temperature": 0.6},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        request = urllib.request.Request(self.url, json.dumps(payload).encode("utf-8"),
                                         {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, http.client.HTTPException) as error:   # Ollama가 꺼져 있음 · 시간 초과 등
            raise LLMUnavailable(f"Ollama 호출 실패: {error}") from None
        except json.JSONDecodeError as error:
            raise LLMError(f"Ollama 호출 실패: {error}") from None
        try:
            data = json.loads(body["message"]["content"])
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise LLMError(f"Ollama 호출 실패: {error}") from None
        if not isinstance(data, dict):
            raise LLMError("Ollama 응답이 JSON 객체가 아님")
        return data


class GMSLLM:
    """SSAFY GMS. OpenAI Responses API를 중계하므로 요청 · 응답 형식이 OpenAI와 같다(주소만 gms.ssafy.io/gmsapi/...).

    - 인증: Authorization: Bearer <GMS_KEY>. 키는 환경 변수로만 받고 오류 메시지에도 넣지 않는다.
    - 지시문(system)은 instructions, 질문(user)은 input에 넣는다.
    - 구조화 출력: text.format에 JSON 스키마를 넣는다. 우리 스키마는 null 허용 · 빠져도 되는 칸이 있어
      OpenAI strict 규칙(모든 칸 필수 등)에 맞지 않으므로 strict는 끄고, 답 검사는 부르는 쪽이 한다.
    - 사용한 토큰만큼 SSAFY 크레딧이 차감된다.
    """
    name = "gms"

    def __init__(self, model: str, url: str, key: str, timeout: float = 60):
        self.model, self.url, self.key, self.timeout = model, url, key, timeout

    def complete_json(self, system, user, schema):
        payload = {"model": self.model, "instructions": system, "input": user,
                   "text": {"format": {"type": "json_schema", "name": "answer", "schema": schema, "strict": False}}}
        request = urllib.request.Request(self.url, json.dumps(payload).encode("utf-8"),
                                         {"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:   # 401(키 오류) · 429(크레딧 · 한도) 등. 서버가 준 사유를 함께 남긴다
            raise LLMUnavailable(f"GMS 호출 실패: HTTP {error.code} {_error_message(error)}") from None
        except (OSError, http.client.HTTPException) as error:   # 네트워크 오류 · 시간 초과
            raise LLMUnavailable(f"GMS 호출 실패: {error}") from None
        except json.JSONDecodeError as error:
            raise LLMError(f"GMS 호출 실패: {error}") from None
        try:
            data = json.loads(_output_text(body))
        except json.JSONDecodeError:
            raise LLMError("GMS 응답이 JSON이 아님") from None
        if not isinstance(data, dict):
            raise LLMError("GMS 응답이 JSON 객체가 아님")
        return data


def _output_text(body) -> str:
    """Responses API 응답에서 답 글을 꺼낸다: output[] 중 type=message의 content[] 중 type=output_text의 text."""
    if not isinstance(body, dict):
        raise LLMError("GMS 응답 형식이 아님")
    if body.get("status") == "incomplete":   # 최대 출력 토큰 초과 등으로 답이 잘림
        raise LLMError(f"GMS 응답이 끝나지 않음: {body.get('incomplete_details')}")
    texts = []
    for item in body.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue   # reasoning 등 답이 아닌 항목
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "refusal":
                raise LLMError(f"GMS가 답을 거절함: {part.get('refusal')}")
            if isinstance(part, dict) and part.get("type") == "output_text":
                texts.append(part.get("text") or "")
    if not texts:
        raise LLMError("GMS 응답에 답 글(output_text)이 없음")
    return "".join(texts)


def _error_message(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read().decode("utf-8"))
        return str(body.get("error", {}).get("message") or body)[:300]
    except (OSError, ValueError, AttributeError):
        return error.reason or ""


class FallbackLLM:
    """주 LLM(예: GMS)에 먼저 묻고, 실패하면 대체 LLM(예: 이 PC의 Ollama로 실행하는 EXAONE)에 묻는다.

    - 주 LLM에 닿지 못하면(LLMUnavailable) cooldown초 동안 주 LLM을 건너뛰고 바로 대체 LLM에 묻는다.
      GMS가 꺼졌을 때 호출마다 시간 초과(60초)를 기다리지 않기 위해서다. 시간이 지나면 다시 주 LLM부터 묻는다.
    - 주 LLM의 답이 망가진 경우(LLMError: 잘린 답 · JSON 아님 등)는 이번 호출만 대체 LLM에 묻고 건너뛰지 않는다.
    - 둘 다 실패하면 LLMError를 낸다. 부르는 쪽(planning · spec_matching)은 그때 규칙으로 대체한다.
      주 LLM이 닿지 못했거나 건너뛰었고 대체 LLM도 닿지 못했으면 LLMUnavailable(LLMError의 하위)을 낸다.
      답이 망가진 쪽이 하나라도 있으면 LLMError다.
    - name · model은 마지막으로 답한 LLM의 것이다. 추출 근거(model_ref)에 실제로 답한 모델이 남는다.
    """

    def __init__(self, primary: LLM, backup: LLM, cooldown: float = 300,
                 clock: Callable[[], float] = time.monotonic):
        self.primary, self.backup, self.cooldown, self.clock = primary, backup, cooldown, clock
        self.skip_until = 0.0      # 이 시각(clock 기준)까지는 주 LLM을 건너뜀
        self.last = primary        # 마지막으로 답한(또는 물은) LLM

    @property
    def name(self) -> str:
        return self.last.name

    @property
    def model(self) -> str | None:
        return getattr(self.last, "model", None)

    def complete_json(self, system, user, schema):
        reason = "건너뜀(최근에 닿지 못함)"
        unreachable = True   # 주 LLM이 닿지 못했거나 건너뜀
        if self.clock() >= self.skip_until:
            try:
                data = self.primary.complete_json(system, user, schema)
            except LLMUnavailable as error:
                self.skip_until = self.clock() + self.cooldown
                reason = str(error)
            except LLMError as error:
                reason, unreachable = str(error), False
            else:
                self.last = self.primary
                return data
        self.last = self.backup
        try:
            return self.backup.complete_json(system, user, schema)
        except LLMError as error:
            kind = LLMUnavailable if unreachable and isinstance(error, LLMUnavailable) else LLMError
            raise kind(f"주 LLM({self.primary.name}): {reason} / 대체 LLM({self.backup.name}): {error}") from None


def make_llm(provider: str, model: str, url: str, key: str = "") -> LLM | None:
    """LLM_PROVIDER에 맞는 연결. model · url · key는 그 공급자의 값(central의 Settings.llm_args)."""
    if provider in ("", "none"):
        return None
    if provider == "gms":
        if not key:
            raise ValueError("LLM_PROVIDER=gms에는 GMS_KEY 환경 변수가 필요함")
        return GMSLLM(model, url, key)
    if provider == "ollama":
        return OllamaLLM(model, url)
    raise ValueError(f"알 수 없는 LLM_PROVIDER: {provider} (none / gms / ollama)")


def with_fallback(primary: LLM | None, backup: LLM | None) -> LLM | None:
    """LLM_FALLBACK이 있으면 주 LLM을 FallbackLLM으로 감싼다. 주 LLM이 없으면(none) 대체도 쓰지 않는다."""
    if primary is None or backup is None:
        return primary
    return FallbackLLM(primary, backup)
