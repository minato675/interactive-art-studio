"""Windows/Linux 공통 API 클라이언트. PC별 작업 ID와 비밀 토큰은 환경에서 받는다."""
import json
import os
import ssl
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx


class AgentClient:
    def __init__(self, base_url: str, token: str, ca_file: str | None = None):
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
        ):
            raise ValueError("원격 Agent 통신에는 HTTPS 또는 localhost SSH 터널을 사용합니다")
        if not token:
            raise ValueError("AGENT_TOKEN이 필요합니다")
        self.http = httpx.Client(base_url=base_url.rstrip("/"), headers={"X-Agent-Token": token},
                                 timeout=httpx.Timeout(60, connect=5), trust_env=False,
                                 verify=ssl.create_default_context(cafile=ca_file) if ca_file else True)

    def post(self, path: str, body=None):
        response = self.http.post("/infra/agent/" + path, json=body)
        response.raise_for_status()
        return response.json()

    def close(self):
        self.http.close()


@contextmanager
def singleton(path: Path):
    """프로세스가 종료되면 OS가 잠금을 해제한다. 같은 노드는 같은 로컬 경로를 사용해야 한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def log(event: str, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)
