"""실제 HTTPS 프록시·CA·운영자 인증을 확인한다. 인증서 검증을 끄지 않는다."""
import json
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from central_cli import config


def main():
    env = config(Path(".env.infra"))
    settings = config(Path(".env.https"))
    container = subprocess.run(
        ["docker", "compose", "--env-file", ".env.infra", "--env-file", ".env.https", "-f", "infra/central/compose.yaml",
         "-f", "infra/central/compose.https.yaml", "ps", "-q", "gateway"],
        check=True, capture_output=True, text=True).stdout.strip()
    ca = subprocess.run(["docker", "exec", container, "cat", "/data/caddy/pki/authorities/local/root.crt"],
                        check=True, capture_output=True, text=True).stdout
    base = f"https://{settings['CENTRAL_HOST']}:{int(settings.get('CENTRAL_HTTPS_PORT', '8443'))}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context(cadata=ca)))
    with opener.open(base + "/ready", timeout=10) as response:
        assert json.load(response)["ok"]
    request = urllib.request.Request(base + "/infra/jobs?limit=1", headers={"X-Operator-Token": env["OPERATOR_TOKEN"]})
    with opener.open(request, timeout=10) as response:
        assert isinstance(json.load(response), list)
    try:
        opener.open(base + "/infra/jobs?limit=1", timeout=10)
        raise AssertionError("Unauthenticated request accepted")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
    print(json.dumps({"https": base, "tls": "verified", "operator_auth": "verified", "scope": "server_self_check"}))


if __name__ == "__main__":
    main()
