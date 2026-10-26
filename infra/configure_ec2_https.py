"""EC2 HTTPS 443 공개 또는 명시적 IPv4 허용 목록. 다른 서비스 포트는 바꾸지 않는다."""
import argparse
import ipaddress
import json
import os
import shutil
import subprocess
from pathlib import Path

CHAIN = "RANEL-HTTPS"
SERVICE = "ranel-https-ingress.service"
INSTALLED = Path("/usr/local/lib/ranel/configure_ec2_https.py")


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, check=True, **kwargs).stdout.strip()


def rules(clients=None, *, public=False):
    if public:
        if clients:
            raise ValueError("공개 모드와 클라이언트 목록을 함께 지정하지 않습니다")
        return f"*filter\n:{CHAIN} - [0:0]\n-F {CHAIN}\n-A {CHAIN} -j RETURN\nCOMMIT\n", []
    clients = clients or []
    addresses = sorted({str(ipaddress.IPv4Network(client, strict=True)) for client in clients})
    if not addresses or any(ipaddress.ip_network(a).prefixlen != 32 for a in addresses):
        raise ValueError("명시적인 IPv4 /32 출구 주소를 지정합니다")
    # --noflush와 자체 체인만의 -F를 사용해 다른 Docker/UFW 규칙을 보존한다.
    return ("*filter\n:" + CHAIN + " - [0:0]\n-F " + CHAIN + "\n"
            + "".join(f"-A {CHAIN} -s {a} -j RETURN\n" for a in addresses)
            + f"-A {CHAIN} -j DROP\nCOMMIT\n"), addresses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--client", action="append")
    mode.add_argument("--public", action="store_true", help="HTTPS 443만 모든 출구 IP에서 허용")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        parser.error("EC2에서 sudo python3로 실행합니다")
    if not 1 <= args.port <= 65535:
        parser.error("유효한 TCP 포트가 필요합니다")
    if args.public and args.port != 443:
        parser.error("공개 모드는 HTTPS 443에만 적용할 수 있습니다")
    table, clients = rules(args.client, public=args.public)
    routes = json.loads(run(["ip", "-j", "route", "show", "default"]))
    interface = routes[0]["dev"]
    run(["iptables", "-w", "-S", "DOCKER-USER"])
    run(["iptables-restore", "--noflush", "--wait", "5"], input=table)
    hook = ["-i", interface, "-p", "tcp", "-m", "conntrack", "--ctdir", "ORIGINAL",
            "--ctorigdstport", str(args.port), "-j", CHAIN]
    exists = subprocess.run(["iptables", "-w", "-C", "DOCKER-USER", *hook],
                             capture_output=True, check=False).returncode == 0
    if not exists:
        run(["iptables", "-w", "-I", "DOCKER-USER", "1", *hook])
    if args.public:
        run(["ufw", "allow", "443/tcp", "comment", "Ranel public HTTPS account login"])
    for client in clients:
        run(["ufw", "allow", "from", client, "to", "any", "port", str(args.port), "proto", "tcp",
             "comment", "Ranel HTTPS client"])
    if args.persist:
        INSTALLED.parent.mkdir(parents=True, exist_ok=True)
        if Path(__file__).resolve() != INSTALLED.resolve():
            shutil.copyfile(__file__, INSTALLED)
        INSTALLED.chmod(0o700)
        mode_args = " --public" if args.public else "".join(f" --client {a}" for a in clients)
        command = f"/usr/bin/python3 {INSTALLED} --port {args.port}" + mode_args
        Path("/etc/systemd/system/" + SERVICE).write_text(
            "[Unit]\nDescription=Ranel Docker HTTPS ingress\nAfter=docker.service network-online.target\n"
            "Requires=docker.service\nPartOf=docker.service\n[Service]\nType=oneshot\nRemainAfterExit=yes\n"
            f"ExecStart={command}\n[Install]\nWantedBy=docker.service\n", encoding="utf-8")
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", SERVICE])
        run(["systemctl", "restart", SERVICE])
    print(json.dumps({"port": args.port, "public": args.public, "clients": clients, "interface": interface,
                      "docker_chain": CHAIN, "persistent": args.persist}))


if __name__ == "__main__":
    main()
