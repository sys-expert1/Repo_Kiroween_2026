#!/usr/bin/env bash
# Verify the Docker sandbox against a REAL container runtime.
#
# Builds the sandbox image, then runs backend/tests/test_sandbox_real.py. A skipped test means the
# sandbox was NOT verified, so this script fails if anything was skipped.
#
#   scripts/verify_sandbox.sh                     # Docker
#   NECRO_DOCKER_BIN=podman scripts/verify_sandbox.sh   # Podman (needs a working install)
#
# Exit codes: 0 verified, 1 a test failed, 2 no usable runtime, 3 tests were skipped.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
bin="${NECRO_DOCKER_BIN:-docker}"
image="${NECRO_SANDBOX_IMAGE:-necromancer-sandbox:latest}"
python="$root/backend/.venv/bin/python"

if [ ! -x "$python" ]; then
  echo "Create the backend environment first: cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt" >&2
  exit 2
fi
if ! "$bin" info >/dev/null 2>&1; then
  echo "No working container runtime: '$bin info' failed. Nothing was verified." >&2
  exit 2
fi
echo "Runtime: $("$bin" --version)"
"$bin" build -f "$root/deployment/sandbox.Dockerfile" -t "$image" "$root/deployment"

log="$(mktemp)"
trap 'rm -f "$log"' EXIT
cd "$root/backend"
set +e
NECRO_DOCKER_BIN="$bin" NECRO_SANDBOX_IMAGE="$image" \
  "$python" -m pytest tests/test_sandbox_real.py -v -rs -p no:cacheprovider 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
set -e

if [ "$status" -ne 0 ]; then
  echo "A sandbox test FAILED. Read the output above: it may be a sandbox bug or a test bug." >&2
  exit 1
fi
if grep -qiE "skipped|no tests ran" "$log"; then
  echo "Some tests were SKIPPED, so the sandbox was NOT fully verified." >&2
  exit 3
fi
echo "Sandbox verified against $("$bin" --version)."
