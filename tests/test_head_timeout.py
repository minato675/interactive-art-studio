import io
import json
import urllib.request
from types import SimpleNamespace

import pytest

from central.config import Settings
from studio.head.llm import LLMUnavailable
from studio.head.responses import ResponsesHead


@pytest.mark.parametrize('configured, expected', [(None, 600), ('720', 720)])
@pytest.mark.parametrize('profile', ['production', 'test'])
def test_head_timeout_reaches_transport(monkeypatch, configured, expected, profile):
    monkeypatch.delenv('HEAD_TIMEOUT_SECONDS', raising=False)
    if configured is not None:
        monkeypatch.setenv('HEAD_TIMEOUT_SECONDS', configured)
    settings = Settings(gms_key='test-gms-key', openai_api_key='test-openai-key')
    timeouts = []

    def send(request, timeout):
        timeouts.append(timeout)
        return io.BytesIO(json.dumps({'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': '{"ok": true}'}]}]}).encode())

    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=send))
    assert settings.head_connection(profile).complete('system', 'text', {'type': 'object'}) == {'ok': True}
    assert timeouts == [expected]


@pytest.mark.parametrize('configured', ['0', '-1', 'invalid'])
def test_invalid_timeout_rejected_before_network(monkeypatch, configured):
    monkeypatch.setenv('HEAD_TIMEOUT_SECONDS', configured)
    with pytest.raises(ValueError):
        Settings()


def test_head_transport_timeout_still_fails_without_exposing_key(monkeypatch):
    def send(request, timeout):
        assert timeout == 600
        raise TimeoutError('private transport details')

    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=send))
    head = ResponsesHead('gms', 'test-model', 'https://gms.example/responses', 'private-key')
    with pytest.raises(LLMUnavailable, match='Head API 연결 실패') as error:
        head.complete('system', 'text', {'type': 'object'})
    assert 'private' not in str(error.value)
