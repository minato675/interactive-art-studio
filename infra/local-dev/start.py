"""Start central only with an explicitly configured local operator token."""
import os
import re
import sys


def main() -> None:
    token = os.environ.get("OPERATOR_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", token):
        sys.exit("OPERATOR_TOKEN must contain at least 32 ASCII letters, digits, underscores or hyphens.")
    os.execv("/app/.venv/bin/uvicorn", [
        "uvicorn", "central.main:app", "--host", "0.0.0.0", "--port", "8000",
    ])


if __name__ == "__main__":
    main()
