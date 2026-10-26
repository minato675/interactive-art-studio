#!/usr/bin/env sh
set -eu
umask 077
case "$CI_COMMIT_SHA" in *[!a-f0-9]*|'') exit 2;; esac
test "${#CI_COMMIT_SHA}" = 40
case "$EC2_USER@$EC2_HOST" in *[!a-zA-Z0-9_.@-]*|'') exit 2;; esac
case "$CI_COMMIT_BRANCH:$DEPLOY_ENVIRONMENT" in
  develop:production) :;;
  *) printf '%s\n' 'Branch and deployment environment do not match'; exit 2;;
esac
case "$DEPLOY_ENVIRONMENT" in
  production) root=/opt/ranel; project=ranel;;
  *) exit 2;;
esac
test "$(git rev-parse HEAD)" = "$CI_COMMIT_SHA"
mkdir -p runtime/source-deploy
archive="runtime/source-deploy/$CI_COMMIT_SHA.tar.gz"
git archive --format=tar.gz --output="$archive" "$CI_COMMIT_SHA"
checksum=$(sha256sum "$archive" | cut -d ' ' -f 1)
target="$EC2_USER@$EC2_HOST"
remote_archive="$root/incoming/$CI_COMMIT_SHA.tar.gz"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$target" "mkdir -p '$root/incoming'"
scp -o BatchMode=yes -o StrictHostKeyChecking=yes "$archive" "$target:$remote_archive"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$target" \
  "python3 '/opt/ranel/ci/receive_source.py' '$CI_COMMIT_SHA' '$remote_archive' '$checksum' --root '$root'"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$target" \
  "sudo -n env RANEL_DEPLOY_ROOT='$root' RANEL_COMPOSE_PROJECT='$project' RANEL_DEPLOY_BUILD=1 RANEL_DEPLOY_PULL_POLICY=never bash '$root/releases/$CI_COMMIT_SHA/infra/central/deploy.sh' 'ranel-central:$CI_COMMIT_SHA'"
