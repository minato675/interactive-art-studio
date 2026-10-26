"""중앙 상태에서 누락·비정상 서비스를 성공으로 보고하지 않는다."""
import json

import pytest
from infra import master_node


@pytest.mark.parametrize("bad_health", ["unhealthy", "starting"])
def test_database_ready_is_insufficient_when_worker_is_unhealthy(tmp_path, monkeypatch, bad_health):
    (tmp_path / ".env.infra").write_text("CENTRAL_PORT=18080\nOPERATOR_TOKEN=never-print-this\n")
    monkeypatch.setattr(master_node, "ROOT", tmp_path)
    rows = [{"Service": name, "State": "running", "Health": "", "Image": "candidate"}
            for name in ("central", "worker", "dispatcher", "postgres", "rabbitmq", "gateway")]
    rows[1]["Health"] = bad_health
    monkeypatch.setattr(master_node, "run", lambda command: json.dumps(rows) if command[-1] == "json" else "revision")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok":true,"database":"postgresql"}'

    monkeypatch.setattr(master_node.urllib.request, "urlopen", lambda *a, **k: Response())
    report = master_node.status()
    assert report["ok"] is False
    assert "never-print-this" not in json.dumps(report)
