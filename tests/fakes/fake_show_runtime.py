"""자동 복구 시험 자식 프로세스. UE/GPU 없이 런타임 파일 규약만 재현한다."""
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

args = dict(item.split("=", 1) for item in sys.argv[1:] if item.startswith("-") and "=" in item)
release = Path(args["-ArtworkRelease"])
session, version, request = uuid.uuid4().hex, None, 0
while True:
    try:
        desired = json.loads(release.read_text("utf-8"))["Version"]
        if version != desired:
            scene = json.loads((release.parent / "versions" / desired / "scene.json").read_text("utf-8"))
            version, request = desired, request + 1
            if scene.get("crash"):
                raise SystemExit(42)
            failed, hang = scene.get("fail", False), scene.get("hang", False)
            status = {"RuntimeSessionId": session, "RequestId": request, "ProcessId": os.getpid(),
                      "Phase": "Failed" if failed else "Reading" if hang else "Applied",
                      "Applied": not failed and not hang, "RequestedVersion": version,
                      "ActiveReleaseVersion": "" if failed or hang else version,
                      "Error": "fake_failure" if failed else "", "UpdatedAtUtc": datetime.now(UTC).isoformat()}
            temporary = release.with_suffix(".fake.tmp")
            temporary.write_text(json.dumps(status), encoding="utf-8")
            temporary.replace(release.with_suffix(".json.status.json"))
    except (OSError, ValueError, KeyError):
        pass
    time.sleep(0.03)
