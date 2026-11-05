#!/bin/bash
# Start the streaming server on port 8080 (the pod's HTTP port). Needs VIRAONE_API_KEY in the environment.
#   VIRAONE_API_KEY=<long-random-key> bash deploy/start_server.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${VIRAONE_API_KEY:?set VIRAONE_API_KEY first}"
source "${VENV:-/workspace/venv310}/bin/activate"
export AVATAR_DIR="${AVATAR_DIR:-/workspace/avatars}"
exec python "$REPO/server/server.py"
