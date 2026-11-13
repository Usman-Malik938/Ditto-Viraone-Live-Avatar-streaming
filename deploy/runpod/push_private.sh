#!/bin/bash
# Run on YOUR computer (not on the pod), once setup.sh has finished on the pod:
#   bash deploy/runpod/push_private.sh <pod-id>
# Copies the private things git doesn't carry to the pod, so you never type them on the pod:
#   - your keys (OPENAI_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_IDS) into the pod's chatbot/.env;
#   - the approved portrait, chatbot/assets/avatar_portrait.jpg.
# The keys are kept on this computer in ~/.viraone/pod-keys.env (outside the repo, readable only by you). The first run
# asks for any key that file doesn't have yet (hidden input); later runs, for any new pod, ask nothing.
# Nothing is printed except each key's last 4 characters. Needs runpodctl with its SSH key (runpodctl doctor).
set -euo pipefail
POD_ID="${1:?usage: push_private.sh <pod-id>   (the id from the RunPod console or runpodctl pod list)}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KEY_STORE="$HOME/.viraone/pod-keys.env"
KEY_NAMES=(OPENAI_API_KEY ELEVENLABS_API_KEY ELEVENLABS_VOICE_IDS)
POD_REPO=/workspace/Ditto-Viraone-Live-Avatar-streaming
PORTRAIT="$ROOT/chatbot/assets/avatar_portrait.jpg"
SSH_KEY="$HOME/.runpod/ssh/runpodctl-ssh-key"
RUNPODCTL="$(command -v runpodctl || echo "$HOME/.local/bin/runpodctl")"

mkdir -p "$(dirname "$KEY_STORE")"
chmod 700 "$(dirname "$KEY_STORE")"
touch "$KEY_STORE"
chmod 600 "$KEY_STORE"

stored_value() {
  grep -E "^$1=" "$KEY_STORE" | head -1 | cut -d= -f2- || true
}

store_value() {
  KEY_NAME="$1" KEY_VALUE="$2" python3 - "$KEY_STORE" <<'PY'
import os, re, sys
path, name = sys.argv[1], os.environ["KEY_NAME"]
# hidden input: stray arrow keys or control codes would otherwise end up inside the key
value = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|[\x00-\x1f\x7f]", "", os.environ["KEY_VALUE"]).strip()
text = open(path).read()
line = f"{name}={value}"
if re.search(rf"^{name}=.*$", text, flags=re.M):
    text = re.sub(rf"^{name}=.*$", lambda m: line, text, count=1, flags=re.M)
else:
    text = (text.rstrip("\n") + "\n" if text.strip() else "") + line + "\n"
open(path, "w").write(text)
PY
}

LOCAL_ENV="$ROOT/chatbot/.env"
for name in "${KEY_NAMES[@]}"; do
  # A key already in this computer's chatbot/.env is reused, so it isn't asked for again.
  if [ -z "$(stored_value "$name")" ] && [ -f "$LOCAL_ENV" ]; then
    local_value="$(grep -E "^$name=" "$LOCAL_ENV" | head -1 | cut -d= -f2- || true)"
    [ -z "$local_value" ] || store_value "$name" "$local_value"
  fi
  if [ -z "$(stored_value "$name")" ]; then
    read -rsp "Paste $name (hidden, asked only once), then Enter: " value; echo
    [ -n "$value" ] || { echo "$name is empty: nothing copied. Run again and paste it."; exit 1; }
    store_value "$name" "$value"
  fi
done

echo "Finding the pod's SSH address..."
read -r SSH_HOST SSH_PORT < <("$RUNPODCTL" pod get "$POD_ID" | python3 -c '
import json, re, sys
ssh = json.load(sys.stdin).get("ssh") or {}
host, port = ssh.get("ip") or ssh.get("host") or "", ssh.get("port") or ""
match = re.search(r"@([0-9.]+).*-p\s*([0-9]+)", ssh.get("command") or ssh.get("sshCommand") or "")
if (not host or not port) and match:
    host, port = match.group(1), match.group(2)
if not host or not port:
    sys.exit("The pod has no SSH address yet (still starting?): " + str(ssh.get("error") or ssh))
print(host, port)
')
SSH=(ssh -i "$SSH_KEY" -p "$SSH_PORT" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "root@$SSH_HOST")

echo "Copying the keys into the pod's chatbot/.env..."
# The values travel over SSH's standard input, never as command arguments (which other processes could see).
# The small Python program that writes them goes as base64, so no quoting can break it.
REMOTE_PROGRAM=$(base64 <<'PY' | tr -d '\n'
import os, re, sys
path = sys.argv[1]
if not os.path.exists(path):
    sys.exit("The pod has no chatbot/.env yet: let deploy/runpod/setup.sh finish on the pod first.")
text = open(path).read()
for entry in sys.stdin.read().splitlines():
    name, _, value = entry.partition("=")
    if not name or not value:
        continue
    line = f"{name}={value}"
    if re.search(rf"^{name}=.*$", text, flags=re.M):
        text = re.sub(rf"^{name}=.*$", lambda match: line, text, count=1, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    print(f"  {name} saved (ends in ...{value[-4:]})")
open(path, "w").write(text)
os.chmod(path, 0o600)
PY
)
for name in "${KEY_NAMES[@]}"; do
  printf '%s=%s\n' "$name" "$(stored_value "$name")"
done | "${SSH[@]}" "python3 -c \"import base64; exec(base64.b64decode('$REMOTE_PROGRAM'))\" $POD_REPO/chatbot/.env"

echo "Copying the portrait..."
[ -f "$PORTRAIT" ] || { echo "No portrait at $PORTRAIT: copy the approved selfie there and run again."; exit 1; }
"${SSH[@]}" "mkdir -p $POD_REPO/chatbot/assets"
scp -q -i "$SSH_KEY" -P "$SSH_PORT" -o StrictHostKeyChecking=accept-new "$PORTRAIT" "root@$SSH_HOST:$POD_REPO/chatbot/assets/avatar_portrait.jpg"
echo "  portrait copied"
echo
echo "Done. On the pod, start (or restart) everything:  bash $POD_REPO/deploy/runpod/start.sh"
