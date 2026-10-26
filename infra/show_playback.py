"""PC2 로컬 재생. 중앙 동기화·다운로드는 시간 관측과 분리하며 준비 실패는 포인터를 바꾸지 않는다."""
import copy
import json
import re
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from client import atomic_json, digest
from runtime_delivery import observe, prepare, publish
from show_supervisor import checked_manifest


def validated(value, node_id):
    value = copy.deepcopy(value)
    plan = value["plan"]
    if value["node_id"] != node_id or type(value["revision"]) is not int or value["revision"] < 0:
        raise ValueError("playlist_identity_mismatch")
    plan.setdefault("autoplay", False)
    if (type(plan["duration_seconds"]) is not int or not 5 <= plan["duration_seconds"] <= 3600
            or type(plan["autoplay"]) is not bool or type(plan["loop"]) is not bool
            or not isinstance(plan["items"], list) or len(plan["items"]) > 200):
        raise ValueError("invalid_playlist")
    ids = set()
    for item in plan["items"]:
        key = item["verification_job_id"]
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", key)
                or key in ids or type(item["repeat"]) is not int or not 1 <= item["repeat"] <= 100
                or type(item["enabled"]) is not bool):
            raise ValueError("invalid_playlist_item")
        ids.add(key)
    entries = value["entries"]
    if len(entries) != len(ids) or {item["verification_job_id"] for item in entries} != ids:
        raise ValueError("playlist_entries_mismatch")
    for item in entries:
        if item.get("error"):
            continue
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", item["source_job_id"])
                or item["runtime_version"] != "ue-" + item["source_job_id"]
                or not 1 <= len(item["files"]) <= 256):
            raise ValueError("invalid_playlist_source")
        names = set()
        for ref in item["files"]:
            name = ref["name"]
            if (not isinstance(name, str) or not name or PurePosixPath(name).is_absolute()
                    or any(part in ("", ".", "..") for part in name.split("/")) or "\\" in name or ":" in name
                    or name in names or not re.fullmatch(r"[a-f0-9]{64}", ref["sha256"])
                    or type(ref["size"]) is not int or ref["size"] < 0):
                raise ValueError("invalid_playlist_file")
            names.add(name)
        if "scene.json" not in names:
            raise ValueError("playlist_scene_missing")
    return value


class PlaylistPlayer:
    def __init__(self, client, config, supervisor=None, activation_lock=None, clock=time.monotonic):
        self.client, self.config, self.supervisor = client, config, supervisor
        self.delivery = Path(config["delivery"])
        self.cache = Path(config["cache"])
        self.cache.mkdir(parents=True, exist_ok=True)
        self.path = client.folder / "show-playlist.json"
        self.guard = threading.RLock()
        self.activation_lock = activation_lock or threading.RLock()
        self.stop_event, self.threads = threading.Event(), []
        self.clock = clock
        self.value = {"node_id": client.node["node_id"], "revision": 0,
                      "plan": {"autoplay": False, "duration_seconds": 120, "loop": True, "items": []}, "entries": []}
        self.prepared, self.retry = {}, {}
        self.cache_errors = {}
        self.last_sync, self.error = None, ""
        self.state, self.current, self.next_id = "Disabled", None, None
        self.key, self.started, self.duration, self.repetition = None, None, 120, 1
        self.enabled, self.finished = False, False
        if self.path.exists():
            try:
                self.accept(json.loads(self.path.read_text("utf-8")), persist=False)
            except (OSError, ValueError, KeyError, TypeError):
                self.error = "cached_playlist_invalid"

    def accept(self, value, persist=True):
        value = validated(value, self.client.node["node_id"])
        with self.guard:
            if value["revision"] < self.value["revision"]:
                raise ValueError("old_playlist_revision")
            changed = value != self.value
            self.value = value
            entries = {entry["verification_job_id"]: entry for entry in value["entries"]}
            self.prepared = {key: record for key, record in self.prepared.items()
                             if key in entries and entries[key].get("files") == record["files"]}
            self.cache_errors = {key: error for key, error in self.cache_errors.items() if key in entries}
            if changed:
                self.finished = False
                self.retry.clear()
            if persist:
                atomic_json(self.path, value)
                self.last_sync, self.error = time.time(), ""

    def sync(self):
        value = self.client.call("/infra/agent/show-playlist")
        if not self.stop_event.is_set():
            self.accept(value)

    def prepare_entry(self, entry):
        if entry.get("error"):
            raise ValueError("artwork_not_approved")
        source = {"id": entry["source_job_id"], "kind": "ue_assemble", "status": "succeeded",
                  "result": {"files": entry["files"]}}
        job = {"payload": {"runtime": {"source_job_id": source["id"]}}, "inputs": [source]}
        with tempfile.TemporaryDirectory(dir=self.cache, prefix=".playlist-") as folder:
            root = Path(folder)
            target = self.delivery / "versions" / entry["runtime_version"]
            if not target.exists():
                for ref in entry["files"]:
                    if self.stop_event.is_set():
                        raise RuntimeError("playlist_stopped")
                    path = root / source["id"] / ref["name"]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    url = ("/infra/agent/show-playlist/" + quote(entry["verification_job_id"], safe="")
                           + "/files/" + ref["sha256"])
                    import urllib.request

                    request = urllib.request.Request(self.client.url + url,
                                                    headers={"X-Agent-Token": self.client.node["token"]})
                    with self.client.opener.open(request, timeout=10) as response, path.open("wb") as output:
                        size = 0
                        while block := response.read(1024 * 1024):
                            size += len(block)
                            if size > ref["size"] or self.stop_event.is_set():
                                raise ValueError("playlist_transfer_aborted")
                            output.write(block)
                    if digest(path) != (ref["sha256"], ref["size"]):
                        raise ValueError("playlist_integrity_failed")
            version, sha = prepare(job, root, self.delivery)
        record = {"version": version, "files": entry["files"], "source_job_id": source["id"],
                  "source_bundle_sha256": sha}
        checked_manifest(self.delivery, record)
        with self.guard:
            if entry in self.value["entries"]:
                self.prepared[entry["verification_job_id"]] = record
                self.cache_errors.pop(entry["verification_job_id"], None)

    def observed(self):
        if self.supervisor and self.config.get("auto_launch") is True:
            return self.supervisor.snapshot()
        pid = self.supervisor.pid if self.supervisor else self.config["runtime_pid"]
        return observe(self.delivery, pid)

    def switch(self, entry, record):
        checked_manifest(self.delivery, record)  # 캐시 준비 이후의 손상도 게시 전에 잡는다.
        lost = self.stop_event
        if self.supervisor and self.config.get("auto_launch") is True:
            ticket = self.supervisor.activate(record["version"], record, lost)
            deadline = time.monotonic() + self.config.get("apply_timeout_seconds", 120) + 2
            try:
                while not ticket["event"].wait(.1):
                    if lost.is_set():
                        raise RuntimeError("playlist_stopped")
                    if time.monotonic() >= deadline or not self.supervisor.thread.is_alive():
                        raise TimeoutError("playlist_supervisor_unconfirmed")
                if ticket["error"]:
                    raise RuntimeError("playlist_apply_failed")
                return ticket["status"]
            finally:
                self.supervisor.abort(ticket)
        previous = self.observed().get("active_version")
        try:
            if self.supervisor:
                self.supervisor.activate(lambda: publish(self.delivery, record["version"], lost))
            else:
                publish(self.delivery, record["version"], lost)
            deadline = time.monotonic() + self.config.get("apply_timeout_seconds", 120)
            while time.monotonic() < deadline and not lost.wait(.1):
                if self.supervisor and not self.supervisor.running_requested:
                    raise RuntimeError("runtime_explicitly_stopped")
                status = self.observed()
                if status["phase"] == "Applied" and status["active_version"] == record["version"]:
                    return status
                if status["phase"] == "Failed":
                    break
            raise TimeoutError("playlist_apply_unconfirmed")
        except Exception:
            if previous:
                publish(self.delivery, previous, threading.Event())
            raise

    def _begin(self, status, entry_id, repetition=1):
        self.key = (status["runtime_pid"], status["runtime_session"], status["active_version"])
        self.current, self.started, self.repetition = entry_id, self.clock(), repetition
        self.duration = self.value["plan"]["duration_seconds"]
        self.state, self.error = "Playing", ""

    def tick(self):
        if not self.activation_lock.acquire(blocking=False):
            return
        try:
            with self.guard:
                value = copy.deepcopy(self.value)
            plan = value["plan"]
            items = [item for item in plan["items"] if item["enabled"]]
            if not plan["autoplay"]:
                self.state, self.enabled, self.next_id = "Disabled", False, None
                return
            if not items:
                self.state, self.enabled, self.next_id = "Empty", False, None
                return
            if self.supervisor and self.config.get("managed_runtime") and not self.supervisor.running_requested:
                self.state, self.started = "WaitingForRuntime", None
                return
            status = self.observed()
            if status["phase"] != "Applied":
                self.state, self.started = "WaitingForRuntime", None
                return
            entries = {entry["verification_job_id"]: entry for entry in value["entries"]}
            key = (status["runtime_pid"], status["runtime_session"], status["active_version"])
            if key != self.key or self.started is None or not self.enabled:
                current = next((item["verification_job_id"] for item in items
                                if entries[item["verification_job_id"]].get("runtime_version") == key[2]), None)
                self._begin(status, current)
                self.finished = False
            self.enabled = True
            if self.clock() - self.started < self.duration:
                self.state = "Playing"
                return
            if self.finished:
                self.state = "Completed"
                return
            index = next((i for i, item in enumerate(items) if item["verification_job_id"] == self.current), -1)
            repeat = index >= 0 and self.repetition < items[index]["repeat"]
            following = index if repeat else index + 1
            if following >= len(items):
                if not plan["loop"]:
                    self.state, self.finished, self.next_id = "Completed", True, None
                    return
                following = 0
            item = items[following]
            self.next_id = item["verification_job_id"]
            entry = entries[self.next_id]
            record = self.prepared.get(self.next_id)
            if entry.get("error") or not record or self.retry.get(self.next_id, 0) > self.clock():
                self.state = "WaitingForNext"
                return
            with self.guard:
                if self.value["revision"] != value["revision"] or not self.value["plan"]["autoplay"]:
                    return
            self.state = "Applying"
            if record["version"] != status["active_version"]:
                status = self.switch(entry, record)
            self._begin(status, self.next_id, self.repetition + 1 if repeat else 1)
        except Exception as exc:  # noqa: BLE001 — 원문·키·경로를 상태에 남기지 않는다.
            self.state, self.error = "WaitingForNext", type(exc).__name__
            if self.next_id:
                self.prepared.pop(self.next_id, None)
                self.retry[self.next_id] = self.clock() + 5
        finally:
            self.activation_lock.release()

    def report(self):
        with self.guard:
            return {"revision": self.value["revision"], "state": self.state,
                    "cached_verification_job_ids": sorted(self.prepared),
                    "cache_errors": dict(self.cache_errors), "current_repeat": self.repetition,
                    "current_verification_job_id": self.current, "next_verification_job_id": self.next_id,
                    "duration_seconds": self.duration if self.started is not None else self.value["plan"]["duration_seconds"],
                    "elapsed_seconds": max(0, self.clock() - self.started)
                    if self.started is not None else 0, "cached_count": len(self.prepared),
                    "last_sync_at": self.last_sync, "error": self.error}

    def start(self):
        def poll():
            while not self.stop_event.is_set():
                try:
                    self.sync()
                except Exception as exc:  # noqa: BLE001 — 마지막 구성과 로컬 실행은 유지
                    self.error = "sync_" + type(exc).__name__
                self.stop_event.wait(3)

        def stage():
            while not self.stop_event.is_set():
                with self.guard:
                    entries = copy.deepcopy(self.value["entries"])
                    enabled = {item["verification_job_id"] for item in self.value["plan"]["items"] if item["enabled"]}
                for entry in entries:
                    key = entry["verification_job_id"]
                    if key not in enabled or key in self.prepared or self.retry.get(key, 0) > self.clock():
                        continue
                    try:
                        self.prepare_entry(entry)
                    except Exception as exc:  # noqa: BLE001
                        self.error = "prepare_" + type(exc).__name__
                        with self.guard:
                            self.cache_errors[key] = self.error
                        self.retry[key] = self.clock() + 5
                self.stop_event.wait(.5)

        def play():
            while not self.stop_event.is_set():
                self.tick()
                self.stop_event.wait(.25)

        for target in (poll, stage, play):
            thread = threading.Thread(target=target, daemon=True)
            self.threads.append(thread)
            thread.start()
        return self

    def close(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=12)
