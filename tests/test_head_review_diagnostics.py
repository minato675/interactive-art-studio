import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from studio.head.responses import HeadAPIUnavailable, ResponsesHead
from studio.verify.ue_runner import interaction_isolation


@pytest.mark.parametrize('status,code,category', [(413, 'request_too_large', 'payload_too_large'),
    (429, 'insufficient_quota', 'quota'), (401, 'invalid_api_key', 'authentication'),
    (502, 'bad_gateway', 'upstream_error')])
def test_safe_http_details(monkeypatch, status, code, category):
    def send(request, timeout):
        body = {'error': {'message': 'Bearer private-key secret-prompt', 'code': code,
                          'type': 'request_error', 'param': 'input'}}
        raise urllib.error.HTTPError('https://private.example', status, 'private-key',
            {'x-request-id': 'req-example-1'}, io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=send))
    with pytest.raises(HeadAPIUnavailable) as caught:
        ResponsesHead('gms', 'model', 'https://gms.example/responses', 'private-key').complete(
            'secret-system', 'secret-prompt', {'type': 'object'}, ['data:image/png;base64,AA=='])
    detail = caught.value.diagnostic
    assert detail['http_status'] == status and detail['category'] == category
    assert detail['provider_code'] == code and detail['request_id'] == 'req-example-1'
    assert detail['image_count'] == 1 and detail['request_bytes'] > 0
    serialized = json.dumps(detail) + str(caught.value)
    assert all(x not in serialized for x in ('private-key', 'secret-prompt', 'secret-system', 'private.example'))


def frame(local, remote):
    return bytes([local] * 3 * 64 + [remote] * 3 * 64)


TARGET = list(range(0, 64 * 3, 3))
CONTROL = list(range(64 * 3, 128 * 3, 3))


def test_global_particle_motion_is_not_local_reaction():
    quiet = [frame(0, 0), frame(30, 30), frame(60, 60), frame(90, 90)]
    found = interaction_isolation(quiet, frame(120, 120), TARGET, CONTROL)
    assert found['status'] == 'inconclusive' and found['local_excess'] == 0


@pytest.mark.parametrize('local,direction', [(160, 'increase'), (90, 'decrease')])
def test_local_change_can_increase_or_decrease_motion(local, direction):
    quiet = [frame(0, 0), frame(30, 30), frame(60, 60), frame(90, 90)]
    found = interaction_isolation(quiet, frame(local, 120), TARGET, CONTROL)
    assert found['status'] == 'localized_change_observed' and found['direction'] == direction
    assert found['causal_verification'] is False


def test_remote_motion_shift_and_missing_control_are_inconclusive():
    quiet = [frame(0, 0), frame(30, 30), frame(60, 60), frame(90, 90)]
    assert interaction_isolation(quiet, frame(200, 170), TARGET, CONTROL)['reason'] == 'control_motion_shift'
    assert interaction_isolation(quiet, frame(200, 120), TARGET, [])['reason'] == 'insufficient_samples'

def test_gms_nonstandard_credit_reason_without_raw_message(monkeypatch):
    def send(request, timeout):
        raise urllib.error.HTTPError('https://gms.example', 429, '', {},
            io.BytesIO(json.dumps({'detail': '크레딧이 부족합니다 secret-prompt'}).encode()))
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=send))
    with pytest.raises(HeadAPIUnavailable) as caught:
        ResponsesHead('gms', 'model', 'https://gms.example/responses', 'private-key').complete(
            '', 'secret-prompt', {'type': 'object'})
    assert caught.value.diagnostic['category'] == 'quota'
    assert caught.value.diagnostic['provider_reason'] == 'insufficient_credit'
    assert 'secret-prompt' not in json.dumps(caught.value.diagnostic)

def test_review_preview_preserves_source_and_records_derivative():
    import base64

    from test_ue_runner import encode

    from studio.head.review_preview import preview
    from studio.verify.png import decode_png

    raw = encode(16, 8, bytes([120, 40, 10] * 128), [0])
    url = 'data:image/png;base64,' + base64.b64encode(raw).decode()
    result, metadata = preview(url, maximum=8)
    assert decode_png(base64.b64decode(result.split(',')[1])) == (8, 4, bytes([120, 40, 10] * 32))
    assert base64.b64decode(url.split(',')[1]) == raw
    assert metadata['original_preserved'] and not metadata['fine_detail_verifiable']
    assert preview(url, maximum=32)[0] == url
