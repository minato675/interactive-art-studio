"""Authenticated native-artwork queue extension. Run behind the existing TLS gateway.

No remote command execution, no legacy verified-job fabrication, no operator key distribution.
One installer identity enrolls one device; a separate test identity never consumes it.
"""

import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("EARTH_DATA", "/earth/data"))
LOCK = threading.Lock()
HEX = re.compile(r"^[a-f0-9]{64}$")


def read(name):
    return json.loads((ROOT / name).read_text("utf-8"))


def save(name, data):
    p = ROOT / name
    t = p.with_suffix(".tmp")
    t.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    t.replace(p)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def equal(a, b):
    return isinstance(a, str) and isinstance(b, str) and hmac.compare_digest(a, b)


def operator_allowed(headers):
    if not headers.get("X-Operator-Token"):
        return False
    try:
        expected = (ROOT / "operator-token.sha256").read_text().strip()
    except OSError:
        return False
    return bool(HEX.fullmatch(expected) and equal(expected, digest(headers.get("X-Operator-Token", ""))))


def library():
    rows = read("catalog.json")
    return [{k: x[k] for k in ("id", "title", "sensor_status", "quality_status")} for x in rows]


def operator_state():
    rows = library()
    plan = read("plan.json")
    creds = read("credentials.json")
    identity = creds.get("pc2-installer", {})
    reports = read("reports.json") if (ROOT / "reports.json").exists() else {}
    report = reports.get("pc2-installer")
    bound = bool(identity.get("enabled") and identity.get("device_id"))
    online = bool(
        bound
        and report
        and report.get("device_id") == identity["device_id"]
        and 0 <= time.time() - report.get("received_at", 0) <= 15
    )
    active = {x["id"] for x in plan["items"] if x["enabled"]}
    confirmed = bool(
        online
        and plan["autoplay"]
        and report.get("state") == "playing"
        and report.get("ready") is True
        and type(report.get("queue_revision")) is int
        and report.get("queue_revision") == plan["revision"]
        and report.get("installation_version") == read("release.json").get("version")
        and report.get("artwork_id") in active
        and type(report.get("runtime_pid")) is int
        and report["runtime_pid"] > 0
        and re.fullmatch("[a-f0-9]{32}", str(report.get("session", "")))
    )
    return {
        "library": rows,
        "plan": plan,
        "device": report,
        "enrolled": bound,
        "online": online,
        "playing_confirmed": confirmed,
        "observed_at": time.time(),
        "physical_sensor_verified": False,
        "required_installation_version": read("release.json").get("version"),
    }


def operator_action(action, body):
    allowed = {"expected_revision", "request_id", "requested_by"}
    if action == "play":
        allowed |= {"artwork_id", "duration_seconds", "loop"}
    if set(body) - allowed or not re.fullmatch("[a-f0-9]{32}", str(body.get("request_id", ""))):
        return 400, {"error": "invalid_request"}
    if type(body.get("expected_revision")) is not int:
        return 400, {"error": "invalid_revision"}
    if not isinstance(body.get("requested_by"), str) or len(body["requested_by"]) > 80:
        return 400, {"error": "invalid_actor"}
    rows = {x["id"]: x for x in library()}
    if action == "play":
        if body.get("artwork_id") not in rows:
            return 400, {"error": "unknown_artwork"}
        if type(body.get("duration_seconds")) is not int or not 5 <= body["duration_seconds"] <= 3600:
            return 400, {"error": "invalid_duration"}
        if type(body.get("loop")) is not bool:
            return 400, {"error": "invalid_loop"}
    signature = {k: v for k, v in body.items() if k != "expected_revision"}
    with LOCK:
        plan = read("plan.json")
        last = plan.get("operator_request", {})
        if last.get("request_id") == body["request_id"]:
            if last.get("action") != action or last.get("signature") != signature:
                return 409, {"error": "request_id_reused"}
            return 200, operator_state()
        if plan["revision"] != body["expected_revision"]:
            return 409, {"error": "revision_conflict"}
        if action == "play":
            row = rows[body["artwork_id"]]
            plan.update(
                items=[{**row, "enabled": True}],
                duration_seconds=body["duration_seconds"],
                loop=body["loop"],
                autoplay=True,
            )
        else:
            plan["autoplay"] = False
        plan["revision"] += 1
        plan["play_request_id"] = body["request_id"]
        plan["operator_request"] = {
            "action": action,
            "request_id": body["request_id"],
            "signature": signature,
            "at": time.time(),
        }
        save("plan.json", plan)
        return 200, operator_state()


class Handler(BaseHTTPRequestHandler):
    server_version = "EARTHQueue/1"

    def log_message(self, *args):
        pass  # Do not log Authorization headers or enrollment bodies.

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def out(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if not 0 < n <= 16384:
            raise ValueError("invalid_length")
        value = json.loads(self.rfile.read(n))
        if not isinstance(value, dict):
            raise TypeError("invalid_body")
        return value

    def auth(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        token = header[7:]
        for name, entry in read("credentials.json").items():
            if entry.get("enabled") and equal(entry["token_sha256"], digest(token)):
                return name, entry
        return None

    def do_GET(self):
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/health":
                return self.out(200, {"service": "earth-native-playback", "ok": True})
            if path == "/operator/state":
                if not operator_allowed(self.headers):
                    return self.out(401, {"error": "unauthorized"})
                return self.out(200, operator_state())
            auth = self.auth()
            if not auth:
                return self.out(401, {"error": "unauthorized"})
            name, entry = auth
            if path == "/plan":
                return self.out(200, read("plan.json"))
            if path == "/release":
                return self.out(200, read("release.json"))
            if path == "/status":
                reports = read("reports.json") if (ROOT / "reports.json").exists() else {}
                return self.out(
                    200,
                    {
                        "queue": read("plan.json"),
                        "device": reports.get(name),
                        "enrolled": bool(entry.get("device_id")),
                        "legacy_playlist_modified": False,
                    },
                )
            if path.startswith("/files/"):
                sha = path.removeprefix("/files/")
                release = read("release.json")
                allowed = {v["sha256"]: v for v in release["packages"]}
                if not HEX.fullmatch(sha) or sha not in allowed:
                    return self.out(404, {"error": "not_found"})
                file = ROOT / "files" / sha
                size = file.stat().st_size
                start = 0
                rng = self.headers.get("Range")
                if rng:
                    m = re.fullmatch(r"bytes=(\d+)-", rng)
                    if not m or int(m[1]) >= size:
                        return self.out(416, {"error": "invalid_range"})
                    start = int(m[1])
                self.send_response(206 if start else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size - start))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("ETag", '"' + sha + '"')
                if start:
                    self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
                self.end_headers()
                with file.open("rb") as f:
                    f.seek(start)
                    while data := f.read(1024 * 1024):
                        self.wfile.write(data)
                return
            return self.out(404, {"error": "not_found"})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except (OSError, ValueError, KeyError, TypeError):
            return self.out(500, {"error": "server_error"})

    def do_POST(self):
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path in ("/operator/play", "/operator/stop"):
                if not operator_allowed(self.headers):
                    return self.out(401, {"error": "unauthorized"})
                code, result = operator_action(path.rsplit("/", 1)[-1], self.body())
                return self.out(code, result)
            auth = self.auth()
            if not auth:
                return self.out(401, {"error": "unauthorized"})
            name, entry = auth
            body = self.body()
            path = urllib.parse.urlsplit(self.path).path
            if path == "/enroll":
                device = body.get("device_id", "")
                if not re.fullmatch("[a-f0-9]{32}", device):
                    return self.out(400, {"error": "invalid_device"})
                with LOCK:
                    creds = read("credentials.json")
                    current = creds[name]
                    if current.get("device_id") not in (None, device):
                        return self.out(409, {"error": "already_enrolled_other_device"})
                    current["device_id"] = device
                    current["enrolled_at"] = current.get("enrolled_at", time.time())
                    save("credentials.json", creds)
                return self.out(
                    200, {"enrolled": True, "device_id": device, "target": "ue-show-1", "queue_kind": "earth-native-v1"}
                )
            if path == "/report":
                if not entry.get("device_id") or not equal(entry["device_id"], body.get("device_id")):
                    return self.out(403, {"error": "device_mismatch"})
                # Deliberately excludes camera frames, landmarks and arbitrary filesystem paths.
                allowed = {
                    "device_id",
                    "state",
                    "artwork_id",
                    "session",
                    "runtime_pid",
                    "ready",
                    "queue_revision",
                    "elapsed_seconds",
                    "camera_state",
                    "camera_frames_with_hands",
                    "camera_error",
                    "error",
                    "installation_version",
                }
                if set(body) - allowed:
                    return self.out(400, {"error": "unknown_report_field"})
                if len(json.dumps(body)) > 5000:
                    return self.out(400, {"error": "report_too_large"})
                report = {**body, "received_at": time.time(), "physical_sensor_approval": False}
                with LOCK:
                    reports = read("reports.json") if (ROOT / "reports.json").exists() else {}
                    reports[name] = report
                    save("reports.json", reports)
                return self.out(200, {"accepted": True})
            return self.out(404, {"error": "not_found"})
        except (ValueError, TypeError, KeyError):
            return self.out(400, {"error": "invalid_body"})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except OSError:
            return self.out(500, {"error": "server_error"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8014"))), Handler).serve_forever()
