"""아카이브 선택 UI가 요청 저장과 실제 PC2 실행을 구분하는지 확인한다."""
import copy
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication

from operator_app.native_artworks import NativeArtworkPanel


def state():
    rows = [{"id": f"work-{i:02}", "title": f"작품 {i}", "sensor_status": "synthetic_only",
             "quality_status": "local_candidate"} for i in range(28)]
    return {"library": rows, "plan": {"revision": 1, "autoplay": False,
            "items": [{"id": rows[0]["id"], "enabled": True}]}, "online": False,
            "device": None, "playing_confirmed": False, "enrolled": False}


def wait(app, condition):
    end = time.monotonic() + 5
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.01)
    assert condition()


def test_all_28_visible_and_selection_saves_request_without_fake_playing():
    app = QApplication.instance() or QApplication([])
    class Client:
        def __init__(self):
            self.value, self.sent = state(), None
        def native_artworks(self):
            return copy.deepcopy(self.value)
        def native_play(self, body):
            self.sent = body
            self.value["plan"] = {"revision": 2, "autoplay": True,
                                   "items": [{"id": body["artwork_id"], "enabled": True}]}
            return copy.deepcopy(self.value)
        def native_stop(self, body):
            self.value["plan"].update(revision=3, autoplay=False)
            return copy.deepcopy(self.value)
    client = Client()
    panel = NativeArtworkPanel(client)
    panel.set_active(True)
    wait(app, lambda: panel.requests == 0)
    assert panel.library.rowCount() == 28
    assert "PC2 미연결" in panel.connection.text()
    panel.library.selectRow(7)
    panel.play()
    wait(app, lambda: panel.requests == 0)
    assert client.sent["artwork_id"] == "work-07"
    assert client.sent["expected_revision"] == 1
    assert len(client.sent["request_id"]) == 32
    assert "확인 대기" in panel.playback.text() and "실행 보고:" not in panel.playback.text()
    panel.stop()
    wait(app, lambda: panel.requests == 0)
    assert "정지 요청" in panel.playback.text()
    panel.shutdown()
    assert not panel.timer.isActive()
    panel.close()
