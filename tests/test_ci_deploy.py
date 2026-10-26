"""CI가 기존 서버를 건드리거나 실패를 성공으로 보고하지 않도록 경계를 검사한다."""
import json
import xml.etree.ElementTree as ET

import pytest
from infra.ci_deploy_test import DeploymentCheck, require_disposable_engine


@pytest.mark.parametrize("info", [
    {"Labels": [], "SecurityOptions": ["name=rootless"], "Containers": 0},
    {"Labels": ["ranel.ci.disposable=true"], "SecurityOptions": [], "Containers": 0},
    {"Labels": ["ranel.ci.disposable=true"], "SecurityOptions": ["name=rootless"], "Containers": 1},
])
def test_existing_or_unisolated_engine_is_rejected(info):
    with pytest.raises(RuntimeError):
        require_disposable_engine(info)


def test_disposable_empty_rootless_engine_is_accepted():
    require_disposable_engine({"Labels": ["ranel.ci.disposable=true"],
                               "SecurityOptions": ["name=rootless"], "Containers": 0})


def test_failure_report_keeps_failed_step_even_after_cleanup(tmp_path):
    check = DeploymentCheck("ranel-ci:" + "a" * 40, tmp_path / "reports", tmp_path)

    def fail():
        raise RuntimeError("failure " + check.secrets[0])

    with pytest.raises(RuntimeError):
        check.step("rollback", fail)
    check.step("cleanup", lambda: None)
    check.report()
    suite = ET.parse(check.output / "junit.xml").getroot()
    assert suite.attrib["failures"] == "1"
    assert suite.find("testcase/failure") is not None
    report = (check.output / "result.json").read_text()
    assert check.secrets[0] not in report
    assert json.loads(report)["checks"][0]["status"] == "failed"


def test_failure_before_rollout_is_not_reported_as_rollback(tmp_path, monkeypatch):
    check = DeploymentCheck("ranel-ci:" + "a" * 40, tmp_path / "reports", tmp_path)
    monkeypatch.setattr(check, "run", lambda *a, **k: "could not connect to daemon")
    with pytest.raises(RuntimeError, match="rollback branch"):
        check.deploy(check.image, failure=True)
