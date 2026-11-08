#!/bin/bash
# One-time setup on a RunPod GPU pod (RTX 4090 / Ampere or newer), run from a clone of this repo:
#   cd /workspace && git clone https://github.com/Usman-Malik938/Ditto-Viraone-Live-Avatar-streaming.git
#   bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/setup_pod.sh
# Python 3.10 venv (Ditto's tested version, needed by TensorRT 8.6.1), Ditto's pinned packages,
# the server packages, and the cfg + Ampere_Plus TensorRT engines into checkpoints/.
# Keep the clone in /workspace so it survives a pod stop/start.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-/workspace/venv310}"
echo "== repo $REPO, venv $VENV"; date

# Newer RunPod images (Ubuntu 24.04) refuse a plain system pip install
command -v uv > /dev/null || pip install -q uv 2> /dev/null || pip install -q --break-system-packages uv
[ -d "$VENV" ] || uv venv "$VENV" --python 3.10 --seed -q
source "$VENV/bin/activate"

echo "== packages (Ditto's environment.yaml pins + server)"
uv pip install -q "torch==2.5.1" --index-url https://download.pytorch.org/whl/cu121
uv pip install -q --extra-index-url https://pypi.nvidia.com --index-strategy unsafe-best-match \
  -r "$REPO/deploy/requirements-server.txt"
# the `tensorrt` module itself: NVIDIA ships 8.6.1 only as a source package; its deps are installed above
uv pip install -q --no-deps --no-build-isolation --extra-index-url https://pypi.nvidia.com "tensorrt==8.6.1"

echo "== cuDNN 8 for TensorRT 8.6's plugins (torch keeps its own cuDNN 9)"
CUDNN8="${CUDNN8:-/workspace/cudnn8}"
if [ ! -f "$CUDNN8/lib/libcudnn.so.8" ]; then
  tmp="$(mktemp -d)"
  pip download -q --no-deps "nvidia-cudnn-cu12==8.9.7.29" -d "$tmp"
  python -m zipfile -e "$tmp"/nvidia_cudnn_cu12-*.whl "$tmp/x"
  mkdir -p "$CUDNN8" && cp -r "$tmp/x/nvidia/cudnn/lib" "$CUDNN8/"
  rm -rf "$tmp"
fi
export LD_LIBRARY_PATH="$CUDNN8/lib:${LD_LIBRARY_PATH:-}"

echo "== weights: cfg + Ampere_Plus TensorRT engines"
python - "$REPO/checkpoints" <<'PY'
import sys
from huggingface_hub import snapshot_download
snapshot_download("digital-avatar/ditto-talkinghead", local_dir=sys.argv[1],
                  allow_patterns=["ditto_cfg/*", "ditto_trt_Ampere_Plus/*"])
PY
du -sh "$REPO"/checkpoints/* || true
cd "$REPO" && python -c "from core.utils.tensorrt_utils import trt; import torch; print('tensorrt', trt.__version__, '| torch', torch.__version__, '| cuda', torch.cuda.is_available())"
echo "SETUP DONE"; date
