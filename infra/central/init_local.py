"""비밀번호를 출력하거나 덮어쓰지 않고 로컬 환경을 만든다."""
import json
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / ".env.infra"
agents = {
    name: {"token": secrets.token_hex(32), "role": role, "catalog_version": "v1"}
    for name, role in [("production-1", "production"), ("production-2", "production"), ("show-1", "show")]
}
values = {
    "POSTGRES_PASSWORD": secrets.token_hex(24), "RABBITMQ_PASSWORD": secrets.token_hex(24),
    "OPERATOR_TOKEN": secrets.token_hex(32), "AGENT_KEYS_JSON": json.dumps(agents, separators=(",", ":")),
    "CENTRAL_PORT": "18080", "LEASE_SECONDS": "120", "JOB_MAX_ATTEMPTS": "3",
}
with path.open("x", encoding="utf-8", newline="\n") as output:
    output.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print("Created .env.infra (secret values omitted)")
