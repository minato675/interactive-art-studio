"""Small dependency-free installer/client primitives with pinned TLS and safe extraction."""

import hashlib
import json
import ssl
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text("utf-8-sig"))
    except (OSError, ValueError):
        return default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for i in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)


def sha256(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Client:
    def __init__(self, config, ca):
        if not config["url"].startswith("https://") or not config["url"].endswith("/infra/earth-pc2"):
            raise ValueError("invalid_endpoint")
        self.url = config["url"]
        self.token = config["token"]
        self.context = ssl.create_default_context(cafile=str(ca))

    def request(self, path, body=None, headers=None, timeout=30):
        req = urllib.request.Request(
            self.url + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json", **(headers or {})},
        )

        # Redirects must not send the scoped credential to another origin.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError("redirect_refused")

        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=self.context), NoRedirect())
        return opener.open(req, timeout=timeout)

    def call(self, path, body=None):
        with self.request(path, body, timeout=5) as response:
            return json.load(response)

    def download(self, package, destination, progress=print):
        destination = Path(destination)
        if destination.exists() and sha256(destination) == package["sha256"]:
            return
        part = destination.with_suffix(".partial")
        part.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(5):
            start = part.stat().st_size if part.exists() else 0
            if start >= package["size"]:
                part.unlink()
                start = 0
            try:
                with self.request(
                    "/files/" + package["sha256"], headers={"Range": f"bytes={start}-"} if start else {}
                ) as response:
                    if start and response.status != 206:
                        raise ValueError("resume_not_supported")
                    with part.open("ab" if start else "wb") as f:
                        last = time.monotonic()
                        while raw := response.read(1024 * 1024):
                            f.write(raw)
                            if f.tell() > package["size"]:
                                raise ValueError("download_size_overflow")
                            if time.monotonic() - last > 5:
                                progress(f"{package['name']}: {f.tell() * 100 // package['size']}%")
                                last = time.monotonic()
                if part.stat().st_size != package["size"] or sha256(part) != package["sha256"]:
                    part.unlink()
                    raise ValueError("download_integrity_failed")
                part.replace(destination)
                return
            except (OSError, urllib.error.URLError):
                if attempt == 4:
                    raise
                time.sleep(min(2**attempt, 8))
        raise RuntimeError("download_failed")


def safe_extract(archive, root, max_bytes=20 * 1024**3):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        seen = set()
        total = 0
        for f in z.infolist():
            p = PurePosixPath(f.filename)
            if p.is_absolute() or ".." in p.parts or ":" in f.filename or "\\" in f.filename:
                raise ValueError("unsafe_archive_path")
            key = f.filename.casefold().rstrip("/")
            if key in seen:
                raise ValueError("duplicate_archive_path")
            seen.add(key)
            total += f.file_size
            if total > max_bytes or (f.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("unsafe_archive")
            target = (root / Path(*p.parts)).resolve()
            if not target.is_relative_to(root):
                raise ValueError("archive_escape")
        z.extractall(root)
