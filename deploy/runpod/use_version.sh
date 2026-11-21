#!/bin/bash
# Run ON THE POD to switch the avatar to another version of the code for an A/B comparison, then restart it:
#   bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/runpod/use_version.sh 57c8692   # Thursday's version
#   bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/runpod/use_version.sh main      # the current version
# chatbot/.env (keys, limits, SPEAKERPHONE_SAFE_MODE) is not in git, so it stays. The knowledge base is re-indexed,
# because versions differ in it. Calls in progress end when start.sh restarts everything (about 3 minutes).
set -euo pipefail
VERSION="${1:?usage: use_version.sh <git commit or branch, e.g. 57c8692 or main>}"
REPO=/workspace/Ditto-Viraone-Live-Avatar-streaming
cd "$REPO"
git fetch -q origin
if [ "$VERSION" = "main" ]; then
  git checkout -q main && git pull -q origin main
else
  git checkout -q "$VERSION"
fi
echo "now on: $(git log --oneline -1)"
cd chatbot
/workspace/venv-chatbot/bin/python main.py index-knowledge 2>&1 | grep -i indexed
bash "$REPO/deploy/runpod/start.sh"
