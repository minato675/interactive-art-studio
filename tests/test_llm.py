import io
import json
import urllib.error

import pytest
from test_spec import WHALE
from test_spec_matching import WHALE_ANSWER

from central.config import Settings
from central.main import create_app
from studio.head.llm import GMSLLM, FallbackLLM, LLMError, LLMUnavailable, make_llm, with_fallback
from studio.head.spec_matching import LLMSpecMatcher

URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1/responses"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def fake_urlopen(monkeypatch, body=None, error=None):
    """실제 GMS를 부르지 않고, 보낸 요청을 sent에 남기고 정해 둔 응답을 돌려준다."""
    sent = []

    def urlopen(request, timeout):
        sent.append(request)
        if error:
            raise error
        return FakeResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr("studio.head.llm.urllib.request.urlopen", urlopen)
    return sent


def answer(data, **extra):
    """OpenAI Responses API 응답 모양. 추론(reasoning) 항목 뒤에 답 글이 온다."""
    return {"status": "completed", **extra, "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(data)}]}]}


def test_gms_sends_responses_api_request(monkeypatch):
    sent = fake_urlopen(monkeypatch, answer({"ok": True}))
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    assert GMSLLM("gpt-6-luna", URL, "test-key").complete_json("지시", "질문", schema) == {"ok": True}
    request = sent[0]
    assert request.full_url == URL and request.get_header("Authorization") == "Bearer test-key"
    payload = json.loads(request.data)
    assert payload["model"] == "gpt-6-luna" and payload["instructions"] == "지시" and payload["input"] == "질문"
    assert payload["text"]["format"] == {"type": "json_schema", "name": "answer", "schema": schema, "strict": False}


@pytest.mark.parametrize("body, message", [
    ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []}, "끝나지 않음"),
    ({"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "안 됨"}]}]}, "거절"),
    ({"output": [{"type": "reasoning"}]}, "output_text"),
    ({"output": [{"type": "message", "content": [{"type": "output_text", "text": "그냥 글"}]}]}, "JSON이 아님"),
    ({"output": [{"type": "message", "content": [{"type": "output_text", "text": "[1]"}]}]}, "JSON 객체가 아님"),
])
def test_gms_bad_answers_raise_llm_error(monkeypatch, body, message):
    fake_urlopen(monkeypatch, body)
    with pytest.raises(LLMError, match=message):
        GMSLLM("gpt-6-luna", URL, "test-key").complete_json("지시", "질문", {})


def test_gms_http_error_keeps_reason_without_key(monkeypatch):
    body = io.BytesIO(json.dumps({"error": {"message": "Invalid API key"}}).encode("utf-8"))
    fake_urlopen(monkeypatch, error=urllib.error.HTTPError(URL, 401, "Unauthorized", {}, body))
    with pytest.raises(LLMUnavailable, match="HTTP 401 Invalid API key") as caught:
        GMSLLM("gpt-6-luna", URL, "secret-key").complete_json("지시", "질문", {})
    assert "secret-key" not in str(caught.value)


def test_gms_network_error_is_unavailable(monkeypatch):
    fake_urlopen(monkeypatch, error=TimeoutError("timed out"))
    with pytest.raises(LLMUnavailable, match="timed out"):
        GMSLLM("gpt-6-luna", URL, "test-key").complete_json("지시", "질문", {})


def test_make_llm_picks_gms_from_settings(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gms")
    monkeypatch.setenv("GMS_KEY", "test-key")
    settings = Settings()
    assert settings.llm_args("gms") == ("gpt-6.1-sol", URL, "test-key")
    llm = make_llm(settings.llm_provider, *settings.llm_args(settings.llm_provider))
    assert isinstance(llm, GMSLLM) and llm.model == "gpt-6.1-sol"
    with pytest.raises(ValueError, match="GMS_KEY"):
        make_llm("gms", "gpt-6-luna", URL, "")


def test_spec_matching_through_gms(monkeypatch):
    fake_urlopen(monkeypatch, answer(WHALE_ANSWER))
    result = LLMSpecMatcher(GMSLLM("gpt-6-luna", URL, "test-key")).match(WHALE["raw_prompt"], "scene_family")
    assert result.sources == {"명세": "llm"}
    assert {p.model_ref for p in result.spec.provenance if p.origin.value == "extracted"} == {"gms:gpt-6-luna"}


# --- GMS가 안 될 때 Ollama(EXAONE)로 넘기기 ---------------------------------------------------------------

class Stub:
    """정해 둔 답이나 오류를 차례로 내는 가짜 LLM."""

    def __init__(self, name, model, *answers):
        self.name, self.model, self.answers, self.calls = name, model, list(answers), 0

    def complete_json(self, system, user, schema):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_fallback_skips_unreachable_primary_for_a_while():
    gms = Stub("gms", "gpt-6-luna", LLMUnavailable("HTTP 429 크레딧 부족"), {"from": "gms"})
    ollama = Stub("ollama", "exaone3.5:7.8b", {"from": "ollama"}, {"from": "ollama"})
    clock = Clock()
    llm = FallbackLLM(gms, ollama, cooldown=300, clock=clock)
    assert llm.complete_json("s", "u", {}) == {"from": "ollama"} and llm.name == "ollama"
    clock.now = 100   # 5분 안: GMS를 건너뛰고 바로 Ollama
    assert llm.complete_json("s", "u", {}) == {"from": "ollama"} and gms.calls == 1
    clock.now = 301   # 5분 뒤: 다시 GMS부터
    assert llm.complete_json("s", "u", {}) == {"from": "gms"} and llm.model == "gpt-6-luna" and gms.calls == 2


def test_fallback_on_broken_answer_does_not_skip_primary():
    gms = Stub("gms", "gpt-6-luna", LLMError("GMS 응답이 JSON이 아님"), {"from": "gms"})
    ollama = Stub("ollama", "exaone3.5:7.8b", {"from": "ollama"})
    llm = FallbackLLM(gms, ollama)
    assert llm.complete_json("s", "u", {}) == {"from": "ollama"}
    assert llm.complete_json("s", "u", {}) == {"from": "gms"}


def test_fallback_reports_both_failures():
    llm = FallbackLLM(Stub("gms", "m", LLMUnavailable("HTTP 429")), Stub("ollama", "m", LLMUnavailable("꺼져 있음")))
    with pytest.raises(LLMError, match="HTTP 429.*꺼져 있음"):
        llm.complete_json("s", "u", {})


def test_fallback_raises_unavailable_when_both_unreachable_or_skipped():
    clock = Clock()
    llm = FallbackLLM(Stub("gms", "m", LLMUnavailable("HTTP 429")), Stub("ollama", "m", LLMUnavailable("꺼져 있음"),
                      LLMUnavailable("꺼져 있음")), clock=clock)
    with pytest.raises(LLMUnavailable, match="HTTP 429.*꺼져 있음"):
        llm.complete_json("s", "u", {})
    clock.now = 10   # 주 LLM 건너뜀 + 대체도 닿지 못함
    with pytest.raises(LLMUnavailable, match="건너뜀.*꺼져 있음"):
        llm.complete_json("s", "u", {})


def test_fallback_keeps_plain_llm_error_when_an_answer_was_broken():
    llm = FallbackLLM(Stub("gms", "m", LLMUnavailable("HTTP 429")), Stub("ollama", "m", LLMError("JSON 아님")))
    with pytest.raises(LLMError) as raised:
        llm.complete_json("s", "u", {})
    assert not isinstance(raised.value, LLMUnavailable)


def test_spec_records_the_model_that_actually_answered():
    gms = Stub("gms", "gpt-6-luna", LLMUnavailable("네트워크 오류"))
    ollama = Stub("ollama", "exaone3.5:7.8b", WHALE_ANSWER)
    result = LLMSpecMatcher(FallbackLLM(gms, ollama)).match(WHALE["raw_prompt"], "scene_family")
    assert result.sources == {"명세": "llm"}
    assert {p.model_ref for p in result.spec.provenance if p.origin.value == "extracted"} == {"ollama:exaone3.5:7.8b"}


def test_app_wraps_gms_with_ollama_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gms")
    monkeypatch.setenv("GMS_KEY", "test-key")
    monkeypatch.setenv("LLM_FALLBACK", "ollama")
    llm = create_app(Settings(data_dir=tmp_path)).state.planner.llm
    assert isinstance(llm, FallbackLLM) and (llm.primary.name, llm.backup.name) == ("gms", "ollama")
    assert llm.backup.model == "exaone3.5:7.8b"   # 우회 기본 모델은 EXAONE (상업 배포 시 OLLAMA_MODEL로 교체)
    assert with_fallback(None, llm.backup) is None   # 주 LLM이 none이면 대체도 쓰지 않음


def test_director_uses_its_own_gms_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gms")
    monkeypatch.setenv("GMS_KEY", "test-key")
    monkeypatch.setenv("GMS_MODEL", "gpt-6-luna")   # 기본값은 둘 다 gpt-6.1-sol이라 기획 모델을 따로 지정
    settings = Settings()
    assert settings.llm_args("gms", director=True) == ("gpt-6.1-sol", URL, "test-key")
    assert settings.llm_args("ollama", director=True) == settings.llm_args("ollama")   # 디렉터 모델은 GMS에만
    app = create_app(settings)
    assert app.state.director.model_ref == "gms:gpt-6.1-sol" and app.state.planner.llm.model == "gpt-6-luna"
    monkeypatch.setenv("DIRECTOR_MODEL", "gpt-6-luna")
    assert create_app(Settings()).state.director.model_ref == "gms:gpt-6-luna"
