"""계정 인증 경계와 기존 native PC2 실행기 사이의 고정 내부 HTTP 연결."""

import json
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Request

from contracts.native_artworks import NativeCommand, NativePlay

from .account_auth import identity
from .infra_api import operator

router = APIRouter(prefix="/infra/native-artworks", tags=["native-artworks"], dependencies=[Depends(operator)])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError("redirect_refused")


def backend(request: Request, path: str, body=None):
    token = request.app.state.settings.operator_token
    if not token:
        raise HTTPException(503, "Native PC2 연결 키가 설정되지 않았습니다")
    url = "http://earth-pc2-queue:8014/operator/" + path
    data = None if body is None else json.dumps(body).encode()
    message = urllib.request.Request(
        url, data=data, headers={"X-Operator-Token": token, "Content-Type": "application/json"}
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(message, timeout=5) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("response_too_large")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise TypeError("invalid_response")
        return result
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise HTTPException(409, "다른 요청으로 구성이 바뀌었습니다. 새로고침 후 다시 선택하세요") from None
        if exc.code == 400:
            raise HTTPException(400, "설치 카탈로그의 작품과 재생 설정을 확인하세요") from None
        raise HTTPException(503, "아카이브 PC2 서비스 연결을 확인하세요") from None
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        raise HTTPException(503, "아카이브 PC2 서비스에 연결하지 못했습니다") from None


@router.get("")
def snapshot(request: Request):
    return backend(request, "state")


@router.post("/play")
def play(body: NativePlay, request: Request):
    return backend(request, "play", {**body.model_dump(), "requested_by": identity(request)["username"]})


@router.post("/stop")
def stop(body: NativeCommand, request: Request):
    return backend(request, "stop", {**body.model_dump(), "requested_by": identity(request)["username"]})
