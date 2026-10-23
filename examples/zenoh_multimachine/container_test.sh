#!/usr/bin/env bash
# Fork-local PoC: verify zenoh communication between two Docker containers.
# See docs/zenoh-multimachine-feasibility.md.
#
# The containers get the host's repo (with .venv) and uv-managed Python
# mounted read-only at the same absolute paths, so no image build is needed.
#
# Usage:
#   ./container_test.sh            # scouting (multicast) mode
#   ./container_test.sh router     # zenoh router mode (runbook option B)

set -euo pipefail

MODE="${1:-scouting}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
UV_PY="$HOME/.local/share/uv/python"
POC="$REPO/examples/zenoh_multimachine"
PY="$REPO/.venv/bin/python"
NET=zenohpoc
IMG=zenohpoc:latest
docker image inspect $IMG >/dev/null 2>&1 || docker build -t $IMG "$POC"

MOUNTS=(-v "$REPO:$REPO:ro" -v "$UV_PY:$UV_PY:ro" --tmpfs "$REPO/logs:rw")
# Read the repo .env from a scratch overlay instead? No: env vars below override
# what matters (transport, scouting); the repo .env may set ROBOT_IP which only
# causes a harmless 1s connect-timeout warning.
# GlobalConfig env names are the bare field names (ROBOT_IP, ZENOH_SCOUTING...);
# only DIMOS_TRANSPORT carries a prefix, via an explicit alias.
COMMON_ENV=(
  -e DIMOS_TRANSPORT=zenoh
  -e DIMOS_RUN_LOG_DIR=/tmp/dimos-logs  # repo mount is read-only
)

cleanup() {
  docker rm -f zenoh-pong zenoh-ping zenoh-router >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network inspect $NET >/dev/null 2>&1 || docker network create $NET >/dev/null

cleanup

case "$MODE" in
  scouting)
    SIDE_ENV=(-e ZENOH_SCOUTING=true)
    ;;
  router)
    # zenoh peers cannot relay DATA through a router (verified with zenoh
    # 1.9); the router's role here is gossip: it tells the peers each
    # other's locators and they connect directly over TCP. That is why
    # ZENOH_SCOUTING=true (which keeps gossip on) is required as well.
    docker run -d --name zenoh-router --network $NET eclipse/zenoh:1.9.0 >/dev/null
    sleep 3
    ROUTER_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' zenoh-router)
    echo "router at $ROUTER_IP"
    SIDE_ENV=(-e ROBOT_IP="$ROUTER_IP" -e ZENOH_SCOUTING=true)
    ;;
  *)
    echo "unknown mode: $MODE (use: scouting | router)" >&2
    exit 2
    ;;
esac

echo "== starting pong container =="
docker run -d --name zenoh-pong --network $NET "${MOUNTS[@]}" \
  "${COMMON_ENV[@]}" "${SIDE_ENV[@]}" -w "$POC" $IMG "$PY" run_pong.py >/dev/null

sleep 15

echo "== running ping container =="
set +e
docker run --name zenoh-ping --network $NET "${MOUNTS[@]}" \
  "${COMMON_ENV[@]}" "${SIDE_ENV[@]}" -w "$POC" $IMG \
  "$PY" run_ping.py --count 200 --rate 20 --image --json /tmp/result.json
RC=$?
set -e

echo "== pong container log (tail) =="
docker logs zenoh-pong 2>&1 | tail -5

echo "== ping exit code: $RC =="
exit $RC
