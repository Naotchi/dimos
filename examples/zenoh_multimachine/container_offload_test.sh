#!/usr/bin/env bash
# Fork-local PoC: mapping-offload demo with STOCK dimos modules across two
# Docker containers over zenoh. See docs/zenoh-multimachine-feasibility.md.
#
#   container A (edge/robot): dimos --replay run unitree-go2-basic
#   container B (cloud):      VoxelGridMapper -> CostMapper (run_mapping_offload.py)
#   boundary streams:         lidar (A->B), global_costmap produced on B
#
# Ordering matters: the go2_short replay is ~60s and does NOT loop, so B is
# started first and A only once B is probing.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
UV_PY="$HOME/.local/share/uv/python"
POC="$REPO/examples/zenoh_multimachine"
PY="$REPO/.venv/bin/python"
DIMOS="$REPO/.venv/bin/dimos"
NET=zenohpoc
IMG=zenohpoc:latest
docker image inspect $IMG >/dev/null 2>&1 || docker build -t $IMG "$POC"

MOUNTS=(-v "$REPO:$REPO:ro" -v "$UV_PY:$UV_PY:ro" --tmpfs "$REPO/logs:rw")
ENV=(
  -e DIMOS_TRANSPORT=zenoh
  -e ZENOH_SCOUTING=true
  -e DIMOS_RUN_LOG_DIR=/tmp/dimos-logs
)

cleanup() {
  docker rm -f offload-robot offload-mapper >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network inspect $NET >/dev/null 2>&1 || docker network create $NET >/dev/null
cleanup

echo "== starting mapper (cloud side) container =="
docker run -d --name offload-mapper --network $NET "${MOUNTS[@]}" "${ENV[@]}" \
  -w "$POC" $IMG "$PY" run_mapping_offload.py --duration 300 >/dev/null

echo "== waiting for mapper to start probing =="
until docker logs offload-mapper 2>&1 | grep -q "counts="; do sleep 2; done

echo "== starting robot (edge side) container =="
# The replay sqlite DB cannot be opened on the read-only repo mount, so it is
# copied into the container first and passed by absolute path.
docker run -d --name offload-robot --network $NET "${MOUNTS[@]}" "${ENV[@]}" \
  -w "$REPO" $IMG bash -c \
  "cp $REPO/data/go2_short.db /tmp/ && exec $DIMOS --transport zenoh --replay --replay-db /tmp/go2_short.db --viewer none run unitree-go2-basic" >/dev/null

echo "== waiting for mapper verdict =="
RC=$(docker wait offload-mapper)

echo "== mapper log (tail) =="
docker logs offload-mapper 2>&1 | grep -E "counts=|PASS|FAIL" | tail -6
echo "== robot log (tail) =="
docker logs offload-robot 2>&1 | tail -3

echo "== mapper exit code: $RC =="
exit "$RC"
