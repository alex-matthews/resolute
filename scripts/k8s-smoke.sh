#!/usr/bin/env bash
# Kubernetes-constraint smoke: run the image exactly the way the cluster
# runs stateful apps (home-ops live manifests + deploy/kubernetes here):
#   - arbitrary non-image uid:gid via --user 1032:100 (the image must not
#     bake storage identity; fsGroup makes /data group-writable in-cluster)
#   - read-only root filesystem, all capabilities dropped
#   - no usable HOME
#   - only the mounted /data writable
#   - policy mounted read-only (the image ships none)
# Asserts /healthz and /readyz answer 200, the DB lands in /data, and the
# logs are free of errors.
set -euo pipefail

IMAGE="${IMAGE:-resolute:smoke}"
NAME="resolute-k8s-smoke-$$"
PORT="${PORT:-18130}"

if [ -z "${IMAGE_PREBUILT:-}" ]; then
  docker build -t "$IMAGE" .
fi

tmp="$(mktemp -d)"
cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

mkdir -p "$tmp/data"
# Emulate fsGroup: in-cluster the PVC arrives writable for gid 100
# (fsGroup: 100, fsGroupChangePolicy: OnRootMismatch).
chmod 0777 "$tmp/data"

docker run -d --name "$NAME" \
  --user 1032:100 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  -e HOME=/nonexistent \
  -v "$tmp/data:/data" \
  -v "$PWD/config/policy.example.yaml:/config/policy.yaml:ro" \
  -p "127.0.0.1:$PORT:8130" \
  "$IMAGE" >/dev/null

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

fail=0
check() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "  PASS  $label"
  else
    echo "  FAIL  $label"
    fail=1
  fi
}

check "runs as uid 1032 (not the image default)" \
  sh -c "[ \"\$(docker exec '$NAME' id -u)\" = '1032' ]"
check "/healthz 200" curl -fsS "http://127.0.0.1:$PORT/healthz"
check "/readyz 200" curl -fsS "http://127.0.0.1:$PORT/readyz"
check "sqlite db created in mounted /data" \
  docker exec "$NAME" test -f /data/resolute.db
check "clean logs (no errors/tracebacks)" \
  sh -c "! docker logs '$NAME' 2>&1 | grep -Ei 'traceback|^ERROR| ERROR '"

if [ "$fail" -ne 0 ]; then
  echo "--- container logs ---"
  docker logs "$NAME" 2>&1 | tail -50
  echo "k8s-smoke: FAIL"
  exit 1
fi
echo "k8s-smoke: PASS"
