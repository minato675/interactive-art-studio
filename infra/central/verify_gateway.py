"""서버 내부에서 실제 HTTPS 인증서·운영자 인증을 검증한다. CA 검증을 끄지 않는다."""
import argparse
import hashlib
import http.client
import json
import re
import socket
import ssl
import subprocess
from contextlib import closing
from pathlib import Path


def env_file(path):
    return dict(line.split("=", 1) for line in path.read_text("utf-8-sig").splitlines()
                if line and not line.lstrip().startswith("#"))


def verify(root, project):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise ValueError("Invalid Compose project")
    settings, secrets = env_file(root / ".env.https"), env_file(root / ".env.infra")
    host, port = settings["CENTRAL_HOST"], int(settings.get("CENTRAL_HTTPS_PORT", "8443"))
    ca = subprocess.check_output(["docker", "exec", project + "-gateway-1", "cat",
                                  "/data/caddy/pki/authorities/local/root.crt"], text=True)
    context = ssl.create_default_context(cadata=ca)

    def call(path, authenticated):
        with socket.create_connection(("127.0.0.1", port), timeout=10) as raw, \
                closing(http.client.HTTPConnection(host, port, timeout=10)) as connection:
            connection.sock = context.wrap_socket(raw, server_hostname=host)
            headers = {"X-Operator-Token": secrets["OPERATOR_TOKEN"]} if authenticated else {}
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())

    code, ready = call("/ready", False)
    if code != 200 or ready.get("database") != "postgresql":
        raise RuntimeError("HTTPS readiness failed")
    code, jobs = call("/infra/jobs?limit=1", True)
    if code != 200 or not isinstance(jobs, list):
        raise RuntimeError("HTTPS operator authentication failed")
    if call("/infra/jobs?limit=1", False)[0] != 401:
        raise RuntimeError("Unauthenticated operator request accepted")
    return {"tls": "verified", "operator_auth": "verified", "unauthenticated": 401,
            "project": project, "ca_der_sha256": hashlib.sha256(ssl.PEM_cert_to_DER_cert(ca)).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.root, args.project)))


if __name__ == "__main__":
    main()
