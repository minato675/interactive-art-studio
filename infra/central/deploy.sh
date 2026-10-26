#!/usr/bin/env bash
set -euo pipefail
umask 077
image=${1:?Usage: deploy.sh image:commit-sha}
[[ "$image" =~ ^[a-zA-Z0-9./:_-]+:[a-f0-9]{40}$ ]] || { printf '%s\n' 'Use an immutable commit SHA tag'; exit 2; }
root=${RANEL_DEPLOY_ROOT:-/opt/ranel}
[[ ! -e "$root/RETIRED" ]] || { printf '%s\n' 'Deployment environment is retired'; exit 2; }
project=${RANEL_COMPOSE_PROJECT:-ranel}
wait_timeout=${RANEL_DEPLOY_WAIT_TIMEOUT:-180}
smoke_timeout=${RANEL_DEPLOY_SMOKE_TIMEOUT:-90}
pull_policy=${RANEL_DEPLOY_PULL_POLICY:-always}
build_source=${RANEL_DEPLOY_BUILD:-0}
[[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || exit 2
[[ "$wait_timeout" =~ ^[1-9][0-9]*$ && "$smoke_timeout" =~ ^[1-9][0-9]*$ ]] || exit 2
[[ "$pull_policy" = always || "$pull_policy" = never ]] || exit 2
[[ "$build_source" = 0 || "$build_source" = 1 ]] || exit 2
up_policy=missing
if [[ "$pull_policy" = never ]]; then up_policy=never; fi
release_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_dir=$(cd -- "$release_dir/../.." && pwd)
test -f "$root/.env.infra"
exec 9>"$root/deploy.lock"
flock -n 9 || { printf '%s\n' 'Deployment already running'; exit 3; }
set_compose() {
    compose=(docker compose --project-name "$project" --env-file "$root/.env.infra")
    if [[ -f "$root/.env.https" ]]; then compose+=(--env-file "$root/.env.https"); fi
    compose+=(-f "$1/compose.yaml")
    if [[ -f "$root/.env.https" ]]; then compose+=(-f "$1/compose.https.yaml"); fi
    if [[ -f "$root/compose.host.yaml" ]]; then compose+=(-f "$root/compose.host.yaml"); fi
    if [[ -n "${RANEL_COMPOSE_OVERRIDE:-}" ]]; then compose+=(-f "$RANEL_COMPOSE_OVERRIDE"); fi
}
export APP_IMAGE="$image"
set_compose "$release_dir"
previous=""
previous_release="$release_dir"
if [[ -f "$root/current-image" ]]; then previous=$(cat "$root/current-image"); fi
if [[ -f "$root/current-release" ]]; then previous_release=$(cat "$root/current-release"); fi
test -f "$previous_release/compose.yaml"
if [[ -n "$previous" ]]; then
    [[ "$previous" =~ ^[a-zA-Z0-9./:_-]+:[a-f0-9]{40}$ ]] || { printf '%s\n' 'Invalid previous image'; exit 2; }
fi

smoke() {
    "${compose[@]}" exec -T central python - --env /dev/null --url http://127.0.0.1:8000 \
        --timeout "$smoke_timeout" < "$1/smoke.py"
}

gateway() {
    if [[ -f "$root/.env.https" ]]; then
        python3 "$1/verify_gateway.py" --root "$root" --project "$project"
    fi
}

"${compose[@]}" config --quiet
if [[ "$build_source" = 1 ]]; then
    docker build --target runtime --build-arg "VCS_REF=${image##*:}" \
        -f "$release_dir/Dockerfile" -t "$image" "$source_dir"
    docker run --rm -i --network none --workdir /tmp --entrypoint python "$image" -I - \
        < "$source_dir/infra/check_packaged_scene.py"
    # 서버 비밀 파일은 빌드 문맥에 복사하지 않는다. Compose만 서버의 설정을 읽는다.
    if [[ -f "$root/.env.head" && "$source_dir" != "$root" ]]; then
        if [[ -e "$source_dir/.env.head" || -L "$source_dir/.env.head" ]]; then
            [[ "$(readlink -f "$source_dir/.env.head")" = "$(readlink -f "$root/.env.head")" ]] || exit 2
        else
            ln -s -- "$root/.env.head" "$source_dir/.env.head"
        fi
    fi
fi
if [[ "$pull_policy" = always ]]; then "${compose[@]}" pull central worker dispatcher; fi
# 운영/테스트 접수의 원자적 차단은 아니다. 점검 시간에 실행하며 실행 중 작업은 먼저 마친다.
if [[ "$build_source" = 1 && -n "$previous" ]]; then
    active=$("${compose[@]}" exec -T postgres psql -U ranel -d ranel -Atc \
        "SELECT count(*) FROM infra_job WHERE status='running';")
    [[ "$active" = 0 ]] || { printf '%s\n' 'Active jobs exist; deployment postponed.'; exit 4; }
fi
if "${compose[@]}" up -d --no-build --pull "$up_policy" --wait --wait-timeout "$wait_timeout" \
    && smoke "$release_dir" && gateway "$release_dir"; then
    printf '%s\n' "$image" > "$root/current-image.new"
    printf '%s\n' "$release_dir" > "$root/current-release.new"
    mv -- "$root/current-image.new" "$root/current-image"
    mv -- "$root/current-release.new" "$root/current-release"
    ln -s -- "$source_dir" "$root/current.$$.new"
    mv -Tf -- "$root/current.$$.new" "$root/current"
    printf '%s\n' 'Deployment and asynchronous smoke test passed.'
else
    printf '%s\n' 'Deployment failed; retaining data volumes.'
    if [[ -n "$previous" ]]; then
        export APP_IMAGE="$previous"
        set_compose "$previous_release"
        "${compose[@]}" up -d --no-build --pull "$up_policy" --wait --wait-timeout "$wait_timeout"
        smoke "$previous_release"
        # 최초 수동 설치에는 새 검증 도구가 없을 수 있다. 검증 로직은 후보의 도구를 사용한다.
        gateway "$release_dir"
        printf '%s\n' 'Previous image restored. Review compatibility before retrying.'
    fi
    exit 1
fi
