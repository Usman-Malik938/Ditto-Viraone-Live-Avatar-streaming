#!/bin/bash
# Starts (or restarts) the whole pipeline on the pod and prints the test link:
#   bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/runpod/start.sh
# 1. Ditto avatar service (repo root) on 127.0.0.1:8090 (pod only; approved look + tested real-time speed)
# 2. LiveKit server: signalling on 7880 (the pod's HTTPS link), call media over ONE TCP port with the same number
#    inside and outside the pod (RunPod passes no UDP; expose TCP port 70000 when creating the pod)
# 3. the chatbot (chatbot/) on 8080: test console (/), voice page (/voice), knowledge base, LLM, ElevenLabs, voice agent
# Logs: /workspace/logs/{ditto,chatbot,livekit}.log
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO="$ROOT/chatbot"
VENV="${CHATBOT_VENV:-/workspace/venv-chatbot}"
LOGS=/workspace/logs
export HF_HOME=/workspace/hf_cache     # always this folder: RunPod images set their own HF_HOME, which setup didn't download to
mkdir -p "$LOGS"
# SSH sessions don't get RunPod's variables; read them from the container's first process
for v in RUNPOD_POD_ID RUNPOD_PUBLIC_IP RUNPOD_TCP_PORT_70000; do
  [ -n "${!v:-}" ] || export "$v=$(tr '\0' '\n' < /proc/1/environ 2>/dev/null | grep "^$v=" | cut -d= -f2- || true)"
done
cd "$REPO"

env_value() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true; }
[ -f .env ] || { echo "no .env: run deploy/runpod/setup.sh first"; exit 1; }
[ -f "$(env_value AVATAR_PORTRAIT_PATH)" ] || echo "WARNING: no portrait at $(env_value AVATAR_PORTRAIT_PATH): calls will be voice only."
[ -n "$(env_value OPENAI_API_KEY)$(env_value GROQ_API_KEY)" ] || echo "WARNING: no OPENAI_API_KEY yet: bash $ROOT/deploy/runpod/set_key.sh OPENAI_API_KEY"
if [ -z "$(env_value WIDGET_ACCESS_KEY)" ]; then
  # The widget's own key, separate from the console password (see WIDGET_ACCESS_KEY in the .env template).
  WIDGET_KEY_NEW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  if grep -qE "^WIDGET_ACCESS_KEY=" .env; then
    sed -i "s|^WIDGET_ACCESS_KEY=.*|WIDGET_ACCESS_KEY=$WIDGET_KEY_NEW|" .env
  else
    printf '\nWIDGET_ACCESS_KEY=%s\n' "$WIDGET_KEY_NEW" >> .env
  fi
  echo "generated a WIDGET_ACCESS_KEY for the SCARD widget"
fi
[ -n "$(env_value ELEVENLABS_API_KEY)" ] || echo "WARNING: no ELEVENLABS_API_KEY yet (no voice, no avatar): bash $ROOT/deploy/runpod/set_key.sh ELEVENLABS_API_KEY"

echo "== stopping a previous run"
pkill -f "main.py web" 2>/dev/null || true
pkill -f "livekit-server --config" 2>/dev/null || true
pkill -f "$ROOT/server/server.py" 2>/dev/null || true
# the voice agent can take a while to shut down; wait for its ports, then force it
for i in $(seq 30); do
  ss -ltn | grep -qE ":(8080|7880|8090) " || break
  if [ "$i" = 15 ]; then
    pkill -9 -f "main.py web" 2>/dev/null || true
    pkill -9 -f "livekit-server --config" 2>/dev/null || true
    pkill -9 -f "$ROOT/server/server.py" 2>/dev/null || true
  fi
  sleep 1
done

echo "== pod network"
POD_ID="${RUNPOD_POD_ID:?not on a RunPod pod (RUNPOD_POD_ID is unset)}"
MEDIA_PORT="${RUNPOD_TCP_PORT_70000:-}"
if [ -z "$MEDIA_PORT" ]; then
  echo "This pod has no symmetric TCP port: recreate it with TCP port 70000 exposed (calls need it)."; exit 1
fi
PUBLIC_IP="${RUNPOD_PUBLIC_IP:-$(curl -s --max-time 5 https://api.ipify.org || true)}"
[ -n "$PUBLIC_IP" ] || { echo "can't find the pod's public IP"; exit 1; }
echo "public IP $PUBLIC_IP, call media on TCP $MEDIA_PORT"

# LiveKit: own keys, TCP media on MEDIA_PORT at the public IP; the agent on the same pod joins over loopback.
(umask 077; cat > tools/livekit/livekit.yaml <<EOF
port: 7880
rtc:
  tcp_port: $MEDIA_PORT
  port_range_start: 50000
  port_range_end: 50100
  use_external_ip: false
  node_ip: $PUBLIC_IP
  advertise_internal_ip: true
  enable_loopback_candidate: true
keys:
  $(env_value LIVEKIT_API_KEY): $(env_value LIVEKIT_API_SECRET)
logging:
  level: info
EOF
)

echo "== 1/2 Ditto avatar service (127.0.0.1:8090)"
HOST=127.0.0.1 PORT=8090 VIRAONE_API_KEY="$(env_value AVATAR_SERVICE_API_KEY)" AVATAR_DIR=/workspace/avatars \
  setsid nohup bash "$ROOT/deploy/start_server.sh" > "$LOGS/ditto.log" 2>&1 < /dev/null &
for _ in $(seq 120); do
  curl -s http://127.0.0.1:8090/health | grep -q '"ok":true' && break
  sleep 2
done
curl -s http://127.0.0.1:8090/health | grep -q '"ok":true' || { echo "Ditto didn't start; see $LOGS/ditto.log"; tail -20 "$LOGS/ditto.log"; exit 1; }
echo "Ditto ready: $(curl -s http://127.0.0.1:8090/health)"

echo "== 2/2 LiveKit server + chatbot + voice agent (port 8080)"
source "$VENV/bin/activate"
export LIVEKIT_URL=ws://localhost:7880
export LIVEKIT_PUBLIC_URL="wss://$POD_ID-7880.proxy.runpod.net"
export LIVEKIT_SERVER_CONFIG_PATH="$REPO/tools/livekit/livekit.yaml"
export AVATAR_SERVICE_URL=ws://127.0.0.1:8090
export WEB_SERVER_HOST=0.0.0.0 WEB_SERVER_PORT=8080
setsid nohup python main.py web > "$LOGS/chatbot.log" 2>&1 < /dev/null &
for _ in $(seq 150); do
  grep -q "=== Ready" "$LOGS/chatbot.log" && break
  if ! pgrep -f "main.py web" > /dev/null; then echo "the chatbot stopped; see $LOGS/chatbot.log"; tail -30 "$LOGS/chatbot.log"; exit 1; fi
  sleep 2
done
grep -q "=== Ready" "$LOGS/chatbot.log" || { echo "the chatbot isn't ready yet; follow: tail -f $LOGS/chatbot.log"; exit 1; }
cp tools/livekit/livekit-server.log "$LOGS/livekit.log" 2>/dev/null || true

echo
echo "READY"
echo "  YOUR test console (private, don't share):"
echo "    https://$POD_ID-8080.proxy.runpod.net/?access=$(env_value WEB_ACCESS_PASSWORD)"
echo "  SCARD widget demo (this one you can share with the client):"
echo "    https://$POD_ID-8080.proxy.runpod.net/widget?access=$(env_value WIDGET_ACCESS_KEY)"
echo "    add &viraone-timing for the response-time panel"
echo "  widget snippet settings: data-server=\"https://$POD_ID-8080.proxy.runpod.net\" data-key=\"$(env_value WIDGET_ACCESS_KEY)\""
echo "  logs         : tail -f $LOGS/chatbot.log   ($LOGS/ditto.log, $REPO/tools/livekit/livekit-server.log)"
