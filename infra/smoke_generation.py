"""CI 전용 생성 작업 왕복 검사. 이미지 생성이나 실제 생성 PC 실행을 가장하지 않는다."""
import json
import os
import tempfile
import urllib.request
import uuid
from pathlib import Path

from client import Client, digest

from contracts.infra import FileRef
from studio.infra.storage import FileStore, verify


def main():
    base = "http://127.0.0.1:18780"
    token = os.environ["CI_GENERATION_TOKEN"]
    node = os.environ["CI_GENERATION_NODE"]
    operator = os.environ["OPERATOR_TOKEN"]

    def call(path, body=None, post=False, agent=False):
        request = urllib.request.Request(base + path, data=json.dumps(body).encode() if post else None,
                                         headers={"X-Agent-Token" if agent else "X-Operator-Token": token if agent else operator,
                                                  "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        client = Client.__new__(Client)  # 테스트의 로컬 서버 전용. 배포 키트의 HTTPS 정책은 바꾸지 않는다.
        client.folder, client.node = folder, {"node_id": node, "role": "generation"}
        client.call = lambda path, body=None, heartbeat=False, post=False: call(
            path, body, post or heartbeat, agent=True)
        spec = {"artwork_id": "ci-art-" + uuid.uuid4().hex[:10], "version": "v1", "catalog_version": node,
                "prompt": "transport fixture only"}
        background = call("/infra/jobs", {"request_id": uuid.uuid4().hex, "kind": "background_generate",
                                          "generation": spec}, post=True)
        assets = call("/infra/jobs", {"request_id": uuid.uuid4().hex, "kind": "assets_generate",
                                      "generation": spec, "depends_on": [background["id"]]}, post=True)
        assert client.claim()["job_id"] == background["id"]
        source = folder / "generated-fixture"
        source.mkdir()
        (source / "background.bin").write_bytes(b"CI file transfer fixture\0" * 1024)
        client.renew(40)
        client.complete(source, folder / "store")
        assert client.claim()["job_id"] == assets["id"]
        client.inputs(folder / "inputs", folder / "store")
        downloaded = folder / "inputs" / background["id"] / "background.bin"
        assert digest(downloaded) == digest(source / "background.bin")
        ref = FileRef(name="background.bin", sha256=digest(downloaded)[0], size=downloaded.stat().st_size)
        verify(FileStore(folder / "store").path(ref), ref)
        client.complete(source, folder / "store")
        for job in (background, assets):
            status = call("/infra/jobs/" + job["id"])
            assert status["status"] == "succeeded" and not status["result"]["publish_approved"]
        print(json.dumps({"check": "generation_dependency_file_transfer", "status": "passed", "fixture_only": True}))


if __name__ == "__main__":
    main()
