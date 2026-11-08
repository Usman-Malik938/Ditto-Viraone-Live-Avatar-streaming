#!/bin/bash
# Saves one key into .env without showing it or keeping it in the shell history:
#   bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/runpod/set_key.sh OPENAI_API_KEY
#   bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/runpod/set_key.sh ELEVENLABS_API_KEY
# The key goes into chatbot/.env. Then restart: bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/runpod/start.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO="$ROOT/chatbot"
NAME="${1:?usage: set_key.sh KEY_NAME   (e.g. OPENAI_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID_PL)}"
[[ "$NAME" =~ ^[A-Z][A-Z0-9_]*$ ]] || { echo "key names are UPPER_CASE"; exit 1; }
[ -f "$REPO/.env" ] || { echo "no .env yet: run deploy/runpod/setup.sh first"; exit 1; }
read -rsp "Paste $NAME (hidden), then Enter: " VALUE; echo
[ -n "$VALUE" ] || { echo "empty, nothing saved"; exit 1; }
KEY_NAME="$NAME" KEY_VALUE="$VALUE" python3 - "$REPO/.env" <<'PY'
import os, re, sys
path, name = sys.argv[1], os.environ["KEY_NAME"]
# the prompt hides typing, so stray keys (arrows, Ctrl codes) would otherwise be saved inside the key
value = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|[\x00-\x1f\x7f]", "", os.environ["KEY_VALUE"]).strip()
text = open(path).read()
line = f"{name}={value}"
if re.search(rf"^{name}=.*$", text, flags=re.M):
    text = re.sub(rf"^{name}=.*$", lambda m: line, text, count=1, flags=re.M)
else:
    text = text.rstrip("\n") + "\n" + line + "\n"
open(path, "w").write(text)
PY
chmod 600 "$REPO/.env"
echo "$NAME saved (ends in ...${VALUE: -4}). Restart to use it: bash $ROOT/deploy/runpod/start.sh"
