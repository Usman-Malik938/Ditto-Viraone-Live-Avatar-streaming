#!/bin/bash
# One-time setup of the whole pipeline on one RunPod RTX 4090 pod: the Ditto avatar service (repo root) and the
# chatbot (chatbot/: knowledge base, LLM, ElevenLabs, LiveKit voice agent, LiveKit server). From the pod's terminal:
#   cd /workspace && git clone https://github.com/Usman-Malik938/Ditto-Viraone-Live-Avatar-streaming.git
#   bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/runpod/setup.sh      # ~20 min the first time
# Everything goes to /workspace, so it survives a pod stop/start. Re-running it is safe: done steps are skipped.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHATBOT="$ROOT/chatbot"
VENV="${CHATBOT_VENV:-/workspace/venv-chatbot}"
LIVEKIT_VERSION=1.13.11
export HF_HOME=/workspace/hf_cache     # always this folder: RunPod images set their own HF_HOME, which setup didn't download to
echo "== repo $ROOT, chatbot venv $VENV"; date

# uv installs both venvs. Newer RunPod images (Ubuntu 24.04) refuse a plain system pip install.
command -v uv > /dev/null || pip install -q uv 2> /dev/null || pip install -q --break-system-packages uv

echo "== 1/6 Ditto avatar service (its own Python 3.10 venv, TensorRT engines)"
if [ -f "$ROOT/checkpoints/ditto_trt_Ampere_Plus/.done" ]; then
  echo "already set up"
else
  bash "$ROOT/deploy/setup_pod.sh"
  touch "$ROOT/checkpoints/ditto_trt_Ampere_Plus/.done"
fi

echo "== 2/6 chatbot packages (Python 3.14 like its Docker image; CUDA torch so knowledge search runs on the GPU)"
[ -d "$VENV" ] || uv venv "$VENV" --python 3.14 --seed -q
source "$VENV/bin/activate"
# cu126 runs on every current RunPod driver; the default cu130 build needs driver 580+
uv pip install -q "torch==2.14.1" --index-url https://download.pytorch.org/whl/cu126
uv pip install -q -r "$CHATBOT/requirements.txt"

echo "== 3/6 LiveKit server $LIVEKIT_VERSION (Linux)"
mkdir -p "$CHATBOT/tools/livekit"
if [ ! -x "$CHATBOT/tools/livekit/livekit-server" ]; then
  curl -sL "https://github.com/livekit/livekit/releases/download/v$LIVEKIT_VERSION/livekit_${LIVEKIT_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C "$CHATBOT/tools/livekit" livekit-server
fi
"$CHATBOT/tools/livekit/livekit-server" --version

echo "== 4/6 chatbot/.env with generated passwords (your OpenAI and ElevenLabs keys are added with set_key.sh)"
if [ ! -f "$CHATBOT/.env" ]; then
  (umask 077; cp "$ROOT/deploy/runpod/env.runpod.example" "$CHATBOT/.env")
  python - "$CHATBOT/.env" <<'PY'
import secrets, sys
path = sys.argv[1]
text = open(path).read()
for name, value in {"LIVEKIT_API_KEY": "viraone" + secrets.token_hex(4), "LIVEKIT_API_SECRET": secrets.token_urlsafe(32),
                    "AVATAR_SERVICE_API_KEY": secrets.token_urlsafe(24), "WEB_ACCESS_PASSWORD": secrets.token_urlsafe(9)}.items():
    text = text.replace(f"{name}=\n", f"{name}={value}\n", 1)
open(path, "w").write(text)
PY
  echo "created $CHATBOT/.env"
else
  echo "$CHATBOT/.env exists, kept"
fi
chmod 600 "$CHATBOT/.env"

echo "== 5/6 voice models (Silero VAD, turn detector)"
cd "$CHATBOT" && python main.py agent download-files

echo "== 6/6 knowledge base index (downloads BGE-M3 the first time)"
cd "$CHATBOT" && python main.py index-knowledge
python -c "import torch; print('chatbot torch', torch.__version__, '| cuda', torch.cuda.is_available())"

echo
echo "SETUP DONE. Next:"
echo "  1. add your keys:     bash $ROOT/deploy/runpod/set_key.sh OPENAI_API_KEY"
echo "                        bash $ROOT/deploy/runpod/set_key.sh ELEVENLABS_API_KEY"
echo "  2. add the portrait:  $CHATBOT/assets/avatar_portrait.jpg  (the approved selfie)"
echo "  3. start everything:  bash $ROOT/deploy/runpod/start.sh"
date
