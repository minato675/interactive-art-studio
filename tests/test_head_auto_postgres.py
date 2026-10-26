"""자동 Head 사이클을 실제 PostgreSQL의 독립 테스트 스키마에서 확인한다."""
import pytest
from test_head_automation import automatic
from test_head_automation import test_auto_publish_off_waits_for_operator as check_manual
from test_head_automation import test_automatic_optional_count_to_ai_approval_and_applied as check_automatic
from test_infra_postgres import pg_engine  # noqa: F401 — fixture


@pytest.mark.parametrize("mode", ["automatic", "manual"])
def test_head_workflow_with_real_postgres(pg_engine, tmp_path, monkeypatch, mode):  # noqa: F811
    monkeypatch.setattr("central.main.make_engine", lambda url: pg_engine)
    fixture = automatic.__wrapped__(tmp_path, monkeypatch)
    try:
        context = next(fixture)
        (check_automatic if mode == "automatic" else check_manual)(context)
    finally:
        fixture.close()
