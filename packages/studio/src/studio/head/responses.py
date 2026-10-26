"""Head 전용 Responses API 연결. 기존 동기 기획의 fallback 정책과 분리한다."""
import copy
import http.client
import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .llm import LLMError, LLMUnavailable, _output_text


class HeadAPIUnavailable(LLMUnavailable):
    """원문 오류를 노출하지 않는 Responses 진단."""

    def __init__(self, message, diagnostic):
        super().__init__(message)
        self.diagnostic = diagnostic


def _identifier(value, key):
    value = str(value or "")
    return value if key not in value and re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,96}", value) else None


def _http_diagnostic(exc, key):
    body = {}
    try:
        body = json.loads(exc.read(32768))
        error = body.get("error", {}) if isinstance(body, dict) else {}
        error = error if isinstance(error, dict) else {}
    except (ValueError, OSError):
        error = {}
    code = _identifier(error.get("code"), key)
    kind = _identifier(error.get("type"), key)
    category = {401: "authentication", 403: "permission", 413: "payload_too_large",
                429: "rate_limit_or_quota", 400: "invalid_request", 404: "endpoint_or_model",
                408: "timeout"}.get(exc.code, "upstream_error" if exc.code >= 500 else "unknown")
    if code in ("insufficient_quota", "billing_hard_limit_reached"):
        category = "quota"
    # GMS의 비표준 detail/message도 원문을 저장하지 않고 고정된 원인 분류만 남긴다.
    text = json.dumps(body, ensure_ascii=False).lower()
    credit = any(word in text for word in ("credit", "balance", "크레딧", "잔액"))
    lacking = any(word in text for word in ("insufficient", "not enough", "exceed", "부족", "소진"))
    reason = "insufficient_credit" if credit and lacking else None
    if reason:
        category = "quota"
    headers = exc.headers or {}
    return {"http_status": exc.code, "category": category, "provider_code": code, "provider_type": kind,
            "parameter": _identifier(error.get("param"), key),
            "request_id": _identifier(headers.get("x-request-id") or headers.get("request-id"), key),
            "retry_after": _identifier(headers.get("retry-after"), key), "provider_reason": reason}


class ResponsesHead:
    def __init__(self, provider: str, model: str, url: str, key: str, timeout_seconds: int = 600):
        if provider not in ("gms", "openai") or not key:
            raise ValueError("Head 공급자와 API 키를 확인하세요")
        if urlparse(url).scheme != "https" or urlparse(url).username or urlparse(url).password:
            raise ValueError("Head API는 인증 정보가 없는 HTTPS 주소가 필요합니다")
        if timeout_seconds < 1:
            raise ValueError("Head API 응답 대기 시간은 양수여야 합니다")
        self.provider, self.model, self.url, self.key = provider, model, url, key
        self.timeout_seconds = timeout_seconds

    def complete(self, system: str, text: str, schema: dict, image: str | list[str] | None = None) -> dict:
        content = [{"type": "input_text", "text": text}]
        if image:
            for item in ([image] if isinstance(image, str) else image):
                content.append({"type": "input_image", "image_url": item})
        schema = copy.deepcopy(schema)

        def strict_objects(value):
            if isinstance(value, dict):
                value.pop("default", None)
                if value.get("type") == "object":
                    value["additionalProperties"] = False
                    value["required"] = list(value.get("properties", {}))
                for item in value.values():
                    strict_objects(item)
            elif isinstance(value, list):
                for item in value:
                    strict_objects(item)

        strict_objects(schema)
        payload = {"model": self.model, "instructions": system, "store": False,
                   "input": [{"role": "user", "content": content}],
                   "text": {"format": {"type": "json_schema", "name": "head_plan",
                                       "strict": True, "schema": schema}}}
        encoded = json.dumps(payload).encode()
        diagnostic = {"provider": self.provider, "model": self.model, "request_bytes": len(encoded),
                      "image_count": sum(c["type"] == "input_image" for c in content)}
        request = urllib.request.Request(self.url, encoded,
                                         {"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        # 다른 호스트로 인증 헤더가 전달되지 않도록 API 리다이렉트를 거부한다.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        began = time.monotonic()
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read(4_000_000).decode())
            result = json.loads(_output_text(body))
        except urllib.error.HTTPError as exc:
            diagnostic.update(_http_diagnostic(exc, self.key), elapsed_seconds=round(time.monotonic() - began, 3))
            raise HeadAPIUnavailable(f"Head API HTTP {exc.code}", diagnostic) from None
        except (OSError, http.client.HTTPException) as exc:
            diagnostic.update(category="timeout" if isinstance(exc, TimeoutError) else "connection",
                              elapsed_seconds=round(time.monotonic() - began, 3))
            raise HeadAPIUnavailable("Head API 연결 실패", diagnostic) from None
        except (ValueError, LLMError, TypeError, KeyError):
            raise LLMError("Head API 응답 형식 오류") from None
        if not isinstance(result, dict):
            raise LLMError("Head 판단은 JSON 객체여야 합니다")
        return result
