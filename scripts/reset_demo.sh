#!/usr/bin/env bash
# Forget all runs and imported repositories. Demo fixtures and your repositories are never touched.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
data="${NECRO_DATA_DIR:-$root/backend/.data}"
case "$data" in
  */.data|*/necromancer-data) rm -rf -- "$data"; echo "Removed $data" ;;
  *) echo "Refusing to delete '$data': set NECRO_DATA_DIR to a directory named .data" >&2; exit 1 ;;
esac
