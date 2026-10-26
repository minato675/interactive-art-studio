"""EC2-driven native UE playback. Only local allowlisted runtime/artwork arguments execute."""

import argparse
import json
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__:
    from .common import Client, read, write
else:
    from common import Client, read, write


def validate_plan(plan, catalog):
    if plan.get("schema") != 1 or plan.get("kind") != "earth-native-v1":
        raise ValueError("unsupported_queue")
    if type(plan.get("revision")) is not int or plan["revision"] < 1:
        raise ValueError("invalid_revision")
    if type(plan.get("duration_seconds")) is not int or not 5 <= plan["duration_seconds"] <= 3600:
        raise ValueError("invalid_duration")
    if type(plan.get("autoplay")) is not bool or type(plan.get("loop")) is not bool:
        raise ValueError("invalid_autoplay")
    ids = [i["id"] for i in plan["items"]]
    if not ids or len(ids) > 200 or len(set(ids)) != len(ids) or any(i not in catalog for i in ids):
        raise ValueError("unknown_artwork")
    if any(type(i.get("enabled")) is not bool for i in plan["items"]):
        raise ValueError("invalid_enabled")
    return plan


class Player:
    def __init__(self, root, release):
        self.root = Path(root)
        self.release = Path(release)
        self.catalog = {e["id"]: e for e in read(self.release / "catalog.json")}
        self.config = read(self.root / "device.json")
        self.settings = read(self.root / "settings.json", {})
        self.client = Client(self.config, self.root / "server-ca.crt")
        self.child = None
        self.bridge = None
        self.output = None
        self.log = None
        self.session = ""
        self.current = None
        self.started = None
        self.launched = 0
        self.index = 0
        self.last_sync = -100
        self.last_report = -100
        self.last_ready = 0
        self.plan = None
        self.stop_event = threading.Event()
        self.skip = False
        self.status = {"state": "starting", "ready": False}
        self.sync_error = None

    def sync(self):
        try:
            plan = validate_plan(self.client.call("/plan"), self.catalog)
            if self.plan and plan["revision"] < self.plan["revision"]:
                raise ValueError("stale_queue_revision")
            if self.plan and plan.get("play_request_id") != self.plan.get("play_request_id"):
                self.stop_child()
                self.index = 0
                self.status = {"state": "starting", "ready": False}
            if not self.plan or plan["revision"] >= self.plan["revision"]:
                write(self.root / "plan.json", plan)
                self.plan = plan
            self.sync_error = None
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            self.sync_error = type(exc).__name__
            if self.plan is None:
                cached = read(self.root / "plan.json")
                if cached:
                    self.plan = validate_plan(cached, self.catalog)
        self.last_sync = time.monotonic()

    def stop_child(self):
        for child in (self.bridge, self.child):
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(30)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(15)
        self.child = self.bridge = None
        if self.log:
            self.log.close()
            self.log = None
        self.started = None

    def launch(self, ident):
        self.settings = read(self.root / "settings.json", self.settings)
        if not self.settings.get("skip_legacy_handoff"):
            from handoff import handoff

            try:
                handoff(self.root)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                handoff(self.root, restore=True)
                raise
        self.stop_child()
        self.current = self.catalog[ident]
        self.session = uuid.uuid4().hex
        self.output = self.root / "sessions" / self.session
        self.output.mkdir(parents=True)
        self.log = (self.output / "process.log").open("wb")
        e = self.current
        exe = self.release / "ue/MossForest/Binaries/Win64/MossForest.exe"
        args = [
            str(exe),
            "/Game/Earth/L_Earth",
            "-dx12",
            "-unattended",
            "-nosound",
            "-nosplash",
            "-windowed",
            f"-ResX={int(self.settings.get('width', 1920))}",
            f"-ResY={int(self.settings.get('height', 480))}",
            "-ForceRes",
            f"-EarthArtwork={ident}",
            f"-EarthSession={self.session}",
            f"-EarthOutput={self.output}",
        ]
        if self.settings.get("fullscreen"):
            args.remove("-windowed")
            args.append("-fullscreen")
        if e["prefix"]:
            args.append(f"-{e['prefix']}Output={self.output}")
        if self.settings.get("offscreen"):
            args.append("-RenderOffscreen")
        self.child = subprocess.Popen(args, cwd=self.release / "ue", stdout=self.log, stderr=subprocess.STDOUT)
        self.launched = time.monotonic()
        self.last_ready = self.launched
        self.status = {"state": "loading", "ready": False, "artwork_id": ident}

    def poll(self):
        if self.child.poll() is not None:
            raise RuntimeError("ue_exited_before_segment_end")
        state = read(self.output / "earth-status.json", {})
        valid = (
            state.get("ready") is True
            and state.get("session") == self.session
            and state.get("artwork_id") == self.current["id"]
            and state.get("pid") == self.child.pid
            and abs(time.time() - state.get("wall_time", 0)) < 5
        )
        if not valid:
            if (self.started is not None and time.monotonic() - self.last_ready > 8) or (
                self.started is None and time.monotonic() - self.launched > 150
            ):
                raise RuntimeError("runtime_readiness_timeout")
            return
        self.last_ready = time.monotonic()
        if self.started is None:
            self.started = time.monotonic()
            if self.settings.get("camera", True):
                self.bridge = subprocess.Popen(
                    [
                        str(self.release / "python/python.exe"),
                        str(self.release / "agent/hand_bridge.py"),
                        "--port",
                        str(self.current["port"]),
                        "--model",
                        str(self.release / "model/hand_landmarker.task"),
                        "--parent-pid",
                        str(self.child.pid),
                        "--status",
                        str(self.output / "hand-status.json"),
                        "--camera",
                        str(int(self.settings.get("camera_index", 0))),
                    ],
                    cwd=self.release,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    stdout=self.log,
                    stderr=subprocess.STDOUT,
                )
        camera = read(self.output / "hand-status.json", {})
        self.status.update(
            state="playing",
            ready=True,
            elapsed_seconds=round(time.monotonic() - self.started, 1),
            runtime_pid=self.child.pid,
            session=self.session,
            camera_state=camera.get("state", "disabled" if not self.settings.get("camera", True) else "starting"),
            camera_frames_with_hands=camera.get("frames_with_hands", 0),
            camera_error=camera.get("error"),
        )
        if self.skip or time.monotonic() - self.started >= self.plan["duration_seconds"]:
            self.skip = False
            self.stop_child()
            self.index += 1

    def report(self):
        report = {
            k: v
            for k, v in self.status.items()
            if k
            in {
                "state",
                "ready",
                "artwork_id",
                "session",
                "runtime_pid",
                "elapsed_seconds",
                "camera_state",
                "camera_frames_with_hands",
                "camera_error",
                "error",
            }
        }
        report.update(
            device_id=self.config["device_id"],
            queue_revision=self.plan["revision"] if self.plan else 0,
            installation_version=read(self.release / "release-info.json")["version"],
        )
        write(
            self.root / "status.json",
            {
                **report,
                "updated_at": time.time(),
                "sync_error": self.sync_error,
                "plan": self.plan,
                "local_dashboard": "http://127.0.0.1:48926",
            },
        )
        try:
            self.client.call("/report", report)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            self.sync_error = "connection_unavailable"
        self.last_report = time.monotonic()

    def run(self, max_seconds=0):
        import msvcrt

        lock = (self.root / "agent.lock").open("a+b")
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock.close()
            return
        begin = time.monotonic()
        failures = 0
        retry_after = 0
        server = start_dashboard(self)
        self.sync()
        self.report()
        try:
            while not self.stop_event.wait(0.25):
                if max_seconds and time.monotonic() - begin >= max_seconds:
                    break
                control = read(self.root / "control.json", {"running": True})
                if time.monotonic() - self.last_sync > 3:
                    self.sync()
                if not self.plan or not control.get("running", True) or not self.plan["autoplay"]:
                    if self.child:
                        self.stop_child()
                    self.status = {"state": "paused" if self.plan else "waiting_for_queue", "ready": False}
                elif time.monotonic() >= retry_after:
                    ids = [x["id"] for x in self.plan["items"] if x["enabled"]]
                    if not ids:
                        self.status = {"state": "empty_queue", "ready": False}
                    elif self.index >= len(ids) and not self.plan["loop"]:
                        self.status = {"state": "completed", "ready": False}
                    else:
                        try:
                            if not self.child:
                                self.launch(ids[self.index % len(ids)])
                            self.poll()
                            if self.started and time.monotonic() - self.started > 10:
                                failures = 0
                        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
                            self.stop_child()
                            failures += 1
                            self.status = {
                                "state": "error",
                                "ready": False,
                                "error": str(exc)[:120],
                                "artwork_id": self.current["id"] if self.current else None,
                            }
                            retry_after = time.monotonic() + min(60, 5 * failures)
                            if failures >= 3:
                                self.index += 1
                                failures = 0
                if time.monotonic() - self.last_report > 3:
                    self.report()
        finally:
            self.stop_child()
            self.status = {"state": "stopped", "ready": False}
            self.report()
            server.shutdown()
            lock.close()


def start_dashboard(player):
    token = uuid.uuid4().hex

    class Local(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get("Host") not in ("127.0.0.1:48926", "localhost:48926"):
                self.send_error(403)
                return
            if self.path == "/status":
                raw = json.dumps(read(player.root / "status.json", {}), ensure_ascii=False).encode()
                kind = "application/json"
            elif self.path == "/":
                raw = (player.release / "agent/dashboard.html").read_text("utf-8").replace("__TOKEN__", token).encode()
                kind = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if (
                self.headers.get("Host") not in ("127.0.0.1:48926", "localhost:48926")
                or self.headers.get("X-Earth-Token") != token
            ):
                self.send_error(403)
                return
            if self.path == "/pause":
                write(player.root / "control.json", {"running": False})
            elif self.path == "/resume":
                write(player.root / "control.json", {"running": True})
            elif self.path == "/next":
                player.skip = True
            else:
                self.send_error(404)
                return
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 48926), Local)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=0)
    args = parser.parse_args()
    release = Path(__file__).resolve().parents[1]
    try:
        Player(args.root, release).run(args.max_seconds)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        write(
            args.root / "status.json",
            {
                "state": "failed",
                "ready": False,
                "error": type(exc).__name__ + ": " + str(exc)[:160],
                "updated_at": time.time(),
            },
        )
        raise
