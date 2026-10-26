"""노트북 중앙 작업 등록·조회. 운영자 토큰은 환경 파일에서 읽고 출력하지 않는다."""
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def config(path):
    return dict(line.split("=", 1) for line in path.read_text("utf-8-sig").splitlines()
                if line.strip() and not line.lstrip().startswith("#"))


class Operator:
    def __init__(self, env_file=Path(".env.infra")):
        env = config(env_file)
        self.url = f"http://127.0.0.1:{int(env.get('CENTRAL_PORT', '18080'))}"
        self.token = env["OPERATOR_TOKEN"]
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, body=None, post=False):
        request = urllib.request.Request(
            self.url + path, data=json.dumps(body).encode() if post else None,
            headers={"X-Operator-Token": self.token, "Content-Type": "application/json"})
        with self.opener.open(request, timeout=20) as response:
            return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=Path(".env.infra"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("runtime-reviews")
    runtime_review = sub.add_parser("runtime-review")
    runtime_review.add_argument("job_id")
    runtime_review.add_argument("--decision", choices=["approve", "reject"], required=True)
    runtime_review.add_argument("--reviewer", required=True)
    runtime_review.add_argument("--reason", required=True)
    runtime_review.add_argument("--attempt", type=int, required=True)
    for command in ("submit", "head-preflight", "head-submit"):
        child = sub.add_parser(command)
        child.add_argument("--request", type=Path, required=True)
    for command in ("job", "retry"):
        child = sub.add_parser(command)
        child.add_argument("job_id")
    args = parser.parse_args()
    operator = Operator(args.env)
    try:
        if args.command == "status":
            result = {"ready": operator.call("/ready"), "nodes": operator.call("/infra/nodes"),
                      "recent_jobs": operator.call("/infra/jobs?limit=50")}
        elif args.command == "runtime-reviews":
            result = operator.call("/infra/runtime/reviews")
        elif args.command == "runtime-review":
            result = operator.call("/infra/runtime/jobs/" + urllib.parse.quote(args.job_id, safe="") + "/review",
                {"decision": args.decision, "reviewer": args.reviewer, "reason": args.reason,
                 "expected_attempt": args.attempt}, post=True)
        elif args.command in ("submit", "head-preflight", "head-submit"):
            path = {"submit": "/infra/jobs", "head-preflight": "/infra/head/preflight",
                    "head-submit": "/infra/head/workflows"}[args.command]
            result = operator.call(path, json.loads(args.request.read_text("utf-8-sig")), post=True)
        else:
            path = "/infra/jobs/" + urllib.parse.quote(args.job_id, safe="")
            result = operator.call(path + ("/retry" if args.command == "retry" else ""), post=args.command == "retry")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"중앙 요청 거부: HTTP {exc.code}. 작업 ID·현재 상태·요청 형식을 확인하세요.") from None


if __name__ == "__main__":
    main()
