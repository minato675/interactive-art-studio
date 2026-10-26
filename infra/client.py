"""생성 PC 작업 임대·파일 전달 도구. Codex/UE 자체는 실행하지 않는다."""
import argparse
import hashlib
import json
import os
import re
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def digest(path, tick=lambda: None):
    value, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
            size += len(block)
            tick()
    return value.hexdigest(), size


def object_path(store, ref):
    if not re.fullmatch(r"[a-f0-9]{64}", ref["sha256"]):
        raise ValueError("잘못된 파일 해시")
    path = (store / "objects" / ref["sha256"][:2] / ref["sha256"]).resolve()
    if not path.is_relative_to(store.resolve()):
        raise ValueError("저장소 밖 경로")
    return path


def copy_verified(source, destination, expected=None, tick=lambda: None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".transfer-")
    try:
        value, size = hashlib.sha256(), 0
        with os.fdopen(fd, "wb") as out, source.open("rb") as src:
            while block := src.read(1024 * 1024):
                out.write(block)
                value.update(block)
                size += len(block)
                tick()
            out.flush()
            os.fsync(out.fileno())
        actual = (value.hexdigest(), size)
        if expected is not None and actual != expected:
            raise ValueError("파일 무결성 불일치")
        os.replace(temporary, destination)
        return actual
    finally:
        Path(temporary).unlink(missing_ok=True)


def ingest_folder(source, store, tick=lambda: None):
    source, store = source.resolve(strict=True), store.resolve()
    if not source.is_dir() or store.is_relative_to(source):
        raise ValueError("전용 결과 폴더와 별도의 저장소를 지정하세요")
    paths = sorted(p for p in source.rglob("*") if p.is_file())
    if not 1 <= len(paths) <= 256:
        raise ValueError("결과 파일은 1~256개여야 합니다")
    refs = []
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError("결과 폴더 밖 링크는 허용하지 않습니다")
        sha256, size = digest(path, tick)
        ref = {"name": path.relative_to(source).as_posix(), "sha256": sha256, "size": size}
        destination = object_path(store, ref)
        if destination.exists():
            if digest(destination, tick) != (sha256, size):
                raise ValueError("기존 저장소 파일 손상")
        else:
            copy_verified(path, destination, (sha256, size), tick)
        refs.append(ref)
        tick()
    return refs


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("노드 토큰을 다른 주소로 전달하지 않습니다")


class Client:
    def __init__(self, folder: Path):
        self.folder = folder
        server = json.loads((folder / "server.json").read_text("utf-8-sig"))
        self.node = json.loads((folder / "node.secret.json").read_text("utf-8-sig"))
        self.url = server["url"].rstrip("/")
        parsed = urllib.parse.urlsplit(self.url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("server.json에는 경로·자격 증명 없는 HTTPS 서버 주소를 지정합니다")
        if self.node["role"] not in ("generation", "production", "show"):
            raise ValueError("이 도구는 생성·제작 노드 전용입니다")
        ca = (folder / server["ca_file"]).resolve(strict=True)
        # 연결 키트에 포함된 CA만 신뢰. 기관/사용자 프록시로 노드 토큰을 전달하지 않는다.
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca))),
        )

    def call(self, path: str, body=None, *, heartbeat: bool = False, post: bool = False):
        request = urllib.request.Request(
            self.url + path, data=json.dumps(body).encode() if heartbeat or post else None,
            headers={"X-Agent-Token": self.node["token"], "Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=10) as response:
            return json.load(response)

    def check(self):
        ready = self.call("/ready")
        identity = self.call("/infra/agent/identity")
        if not ready.get("ok") or identity["node_id"] != self.node["node_id"]:
            raise ValueError("서버 준비 상태 또는 등록된 노드 ID가 다릅니다")
        if identity["role"] != self.node["role"]:
            raise ValueError("서버에 등록된 노드 역할이 다릅니다")
        if "job_kinds" in self.node and set(identity.get("job_kinds", [])) != set(self.node["job_kinds"]):
            raise ValueError("서버와 키트의 허용 작업 종류가 다릅니다")
        return {"server": self.url, "node_id": identity["node_id"], "role": identity["role"], "tls": "verified",
                "job_kinds": identity.get("job_kinds")}

    @property
    def assignment_path(self):
        return self.folder / "assignment.secret.json"

    def current(self):
        return json.loads(self.assignment_path.read_text("utf-8")) if self.assignment_path.exists() else None

    def claim(self):
        self.call("/infra/agent/heartbeat", heartbeat=True)
        job = self.call("/infra/agent/current") or self.call("/infra/agent/claim", post=True)
        if job is None:
            self.assignment_path.unlink(missing_ok=True)
            return {"event": "no_ready_job"}
        # 작업에 필요한 입력은 공개 파일로, 소유권 토큰은 별도 파일로 보관한다.
        atomic_json(self.assignment_path, job)
        atomic_json(self.folder / "assignment.json", {k: v for k, v in job.items() if k != "token"})
        atomic_json(self.folder / "progress.json", {"job_id": job["id"], "progress": job["progress"]})
        return {"event": "job_claimed", "job_id": job["id"], "kind": job["kind"], "attempt_id": job["attempt_id"]}

    def renew(self, progress=None):
        self.call("/infra/agent/heartbeat", heartbeat=True)
        job = self.current()
        if job is None:
            return {"event": "node_heartbeat", "node_id": self.node["node_id"]}
        saved = self.folder / "progress.json"
        state = json.loads(saved.read_text("utf-8")) if saved.exists() else {}
        value = progress if progress is not None else (state.get("progress", 0) if state.get("job_id") == job["id"] else 0)
        self.call(f"/infra/agent/jobs/{job['id']}/heartbeat", {"token": job["token"], "progress": value}, post=True)
        if progress is not None:
            atomic_json(saved, {"job_id": job["id"], "progress": value})
        return {"event": "job_heartbeat", "job_id": job["id"], "progress": value}

    def transfer_tick(self):
        last = 0

        def tick():
            nonlocal last
            if time.monotonic() - last >= 10:
                self.renew()
                last = time.monotonic()
        return tick

    def upload_folder(self, source):
        job, tick = self.current(), self.transfer_tick()
        source = source.resolve(strict=True)
        if not source.is_dir():
            raise ValueError("전용 결과 폴더가 필요합니다")
        paths = sorted(p for p in source.rglob("*") if p.is_file())
        if not 1 <= len(paths) <= 256:
            raise ValueError("결과 파일은 1~256개여야 합니다")
        refs = []
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError("결과 폴더 밖 링크는 허용하지 않습니다")
            sha256, size = digest(path, tick)
            ref = {"name": path.relative_to(source).as_posix(), "sha256": sha256, "size": size}

            def blocks(source_path=path):
                with source_path.open("rb") as src:
                    while block := src.read(1024 * 1024):
                        tick()
                        yield block

            request = urllib.request.Request(self.url + f"/infra/agent/jobs/{job['id']}/files?" +
                urllib.parse.urlencode(ref), data=blocks(), method="PUT", headers={
                    "X-Agent-Token": self.node["token"], "X-Lease-Token": job["token"],
                    "Content-Type": "application/octet-stream", "Content-Length": str(size)})
            with self.opener.open(request, timeout=60) as response:
                if json.load(response) != ref:
                    raise ValueError("중앙 파일 영수증 불일치")
            refs.append(ref)
        return refs

    def download_file(self, job, ref, target, tick):
        if not re.fullmatch(r"[a-f0-9]{64}", ref["sha256"]):
            raise ValueError("잘못된 파일 해시")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".download-")
        try:
            request = urllib.request.Request(self.url + f"/infra/agent/jobs/{job['id']}/files/{ref['sha256']}",
                                             headers={"X-Agent-Token": self.node["token"],
                                                      "X-Lease-Token": job["token"]})
            with os.fdopen(fd, "wb") as output, self.opener.open(request, timeout=60) as response:
                size = 0
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > ref["size"]:
                        raise ValueError("다운로드 크기 초과")
                    output.write(block)
                    tick()
                output.flush()
                os.fsync(output.fileno())
            if digest(Path(temporary), tick) != (ref["sha256"], ref["size"]):
                raise ValueError("다운로드 무결성 불일치")
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def complete(self, source=None, store=None, error=""):
        job = self.current()
        if job is None:
            raise ValueError("임대한 작업이 없습니다")
        # 응답이 유실된 완료 요청은 같은 결과와 토큰으로 재전송한다.
        pending = self.folder / "completion.secret.json"
        body = json.loads(pending.read_text("utf-8")) if pending.exists() else None
        if body is None or body.get("job_id") != job["id"] or body.get("attempt_id") != job["attempt_id"]:
            result = None
            if not error:
                self.renew()
                if job["payload"].get("file_transport") == "http":
                    refs = self.upload_folder(source)
                else:
                    if store is None:
                        raise ValueError("공유 저장소 작업에는 --store가 필요합니다")
                    refs = ingest_folder(source, store, self.transfer_tick())
                spec = job["payload"].get("generation") or job["payload"].get("assembly") or job["payload"]["runtime"]
                result = {"artwork_id": spec["artwork_id"], "version": spec["version"], "files": refs}
            body = {"job_id": job["id"], "attempt_id": job["attempt_id"], "token": job["token"],
                    "result": result, "error": error}
            atomic_json(pending, body)
        payload = {k: v for k, v in body.items() if k not in ("job_id", "attempt_id")}
        response = self.call(f"/infra/agent/jobs/{job['id']}/complete", payload, post=True)
        atomic_json(self.folder / "last-result.json", {"job_id": job["id"], "result": body["result"], **response})
        self.assignment_path.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)
        return {"event": "result_registered", "job_id": job["id"], **response}

    def inputs(self, destination, store=None, source_ids=None):
        job = self.current()
        if job is None:
            raise ValueError("임대한 작업이 없습니다")
        tick = self.transfer_tick()
        count = 0
        for parent in job.get("inputs", []):
            if source_ids is not None and parent["id"] not in source_ids:
                continue
            root = (destination / parent["id"]).resolve()
            for ref in parent["result"].get("files", []):
                target = (root / ref["name"]).resolve()
                if not target.is_relative_to(root):
                    raise ValueError("입력 경로 이탈")
                expected = (ref["sha256"], ref["size"])
                if target.exists() and digest(target, tick) == expected:
                    continue
                if job["payload"].get("file_transport") == "http":
                    self.download_file(job, ref, target, tick)
                else:
                    if store is None:
                        raise ValueError("공유 저장소 작업에는 --store가 필요합니다")
                    copy_verified(object_path(store, ref), target, expected, tick)
                count += 1
        return {"event": "inputs_verified", "copied_files": count}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path(__file__).resolve().parent)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--watch", action="store_true")
    mode.add_argument("--claim", action="store_true")
    mode.add_argument("--complete", action="store_true")
    mode.add_argument("--fail", metavar="ERROR_CODE")
    mode.add_argument("--inputs", type=Path)
    mode.add_argument("--progress", type=int, choices=range(100), metavar="0..99")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--store", type=Path)
    args = parser.parse_args()
    if args.complete and not args.source:
        parser.error("--complete에는 --source가 필요합니다")
    try:
        client = Client(args.folder)
        print(json.dumps({"event": "connection_verified", **client.check()}, ensure_ascii=False), flush=True)
        if args.check:
            return
        if args.claim:
            result = client.claim()
        elif args.complete or args.fail:
            result = client.complete(args.source, args.store, args.fail or "")
        elif args.inputs:
            result = client.inputs(args.inputs, args.store)
        elif args.progress is not None:
            result = client.renew(args.progress)
        else:
            result = None
        if result:
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return
        while True:
            try:
                print(json.dumps(client.renew(), ensure_ascii=False), flush=True)
            except (OSError, ValueError) as exc:
                # URL 오류나 응답을 그대로 출력해 토큰/작업 내용을 로그에 남기지 않는다.
                print(json.dumps({"event": "heartbeat_retry", "error": type(exc).__name__}), flush=True)
            job = client.current()
            time.sleep(min(30, job["heartbeat_seconds"]) if job else 30)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, KeyError) as exc:
        raise SystemExit(f"연결 실패: {type(exc).__name__}. 서버 주소·CA 파일·노드 설정을 확인하세요.") from None


if __name__ == "__main__":
    main()
