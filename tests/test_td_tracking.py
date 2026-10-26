"""TD 관람객 ID 부여(D1) 테스트를 루트 pytest에서도 돌린다.

테스트 내용은 td/project/tests에만 둔다. 여기서는 그 파일을 경로로 불러와 TestCase 클래스를 노출한다.
td/project는 TouchDesigner가 import 경로로 쓰는 폴더라, 그 안의 모듈(interaction_bridge)을 같은 이름으로 불러온다.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

TD_PROJECT = Path(__file__).resolve().parents[1] / "td" / "project"
TD_TESTS = ("test_tracking", "test_multi_hand_pipeline")

if str(TD_PROJECT) not in sys.path:
    sys.path.insert(0, str(TD_PROJECT))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"td_project_{name}", TD_PROJECT / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _test_cases():
    for name in TD_TESTS:
        for attribute, value in vars(_load(name)).items():
            if isinstance(value, type) and issubclass(value, unittest.TestCase) and value is not unittest.TestCase:
                yield attribute, value


# 루프 변수가 모듈에 남으면 같은 클래스가 다른 이름으로 한 번 더 수집되므로 함수 안에서 모은다.
globals().update(dict(_test_cases()))
