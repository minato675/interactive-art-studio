#!/usr/bin/env bash
set -euo pipefail
# 읽기 전용. sudo 설치, 보안 그룹 수정, 디스크 삭제를 하지 않는다.
uname -m
cat /etc/os-release
docker version
docker compose version
df -h /
free -m
test -w /opt/ranel
test -r /opt/ranel/.env.infra
printf '%s\n' 'Preflight passed. Registry login, SSH host key and backup target must also be configured.'
