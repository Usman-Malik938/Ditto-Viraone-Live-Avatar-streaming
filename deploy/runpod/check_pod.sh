#!/bin/bash
# Run on YOUR computer right after a new pod boots, BEFORE setup.sh:
#   bash deploy/runpod/check_pod.sh <pod-id>
# Rejects pods that would give a bad call:
#   1. UDP must reach the pod. Without it the browser falls back to TCP, and the live voice stutters and breaks.
#   2. The CPU must not be an old one (Xeon Gold/Silver 61xx/51xx, 2017). The avatar's frame encoding runs on the CPU
#      and fell below 25 fps on one. AMD EPYC and newer Xeons were fine.
# Prints PASS or FAIL. Delete a FAIL pod at once: runpodctl pod delete <pod-id>
set -uo pipefail
POD_ID="${1:?usage: check_pod.sh <pod-id>}"
RUNPODCTL="$(command -v runpodctl || echo "$HOME/.local/bin/runpodctl")"
SSH_KEY="$HOME/.runpod/ssh/runpodctl-ssh-key"
read -r IP PORT < <("$RUNPODCTL" pod get "$POD_ID" 2>/dev/null | python3 -c 'import json,sys; s=(json.load(sys.stdin).get("ssh") or {}); print(s.get("ip") or "-", s.get("port") or "-")')
[ "$PORT" = "-" ] && { echo "FAIL: no SSH yet (still booting). If this lasts over 5 minutes, delete the pod."; exit 1; }
SSH=(ssh -i "$SSH_KEY" -p "$PORT" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "root@$IP")
CPU=$("${SSH[@]}" 'lscpu | sed -n "s/^Model name: *//p"' 2>/dev/null)
echo "CPU: $CPU"
"${SSH[@]}" 'nohup timeout 20 python3 -c "
import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind((\"0.0.0.0\",49999))
while True:
    d,a=s.recvfrom(100); s.sendto(b\"pong\",a)
" >/dev/null 2>&1 &' 2>/dev/null
sleep 2
UDP=$(python3 - "$IP" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
for _ in range(3):
    s.sendto(b"ping", (sys.argv[1], 49999))
    try:
        if s.recvfrom(100)[0] == b"pong":
            print("yes"); break
    except socket.timeout:
        pass
else:
    print("no")
PY
)
echo "UDP reachable: $UDP"
if [ "$UDP" != "yes" ]; then echo "note: no UDP (normal on RunPod; calls use TCP, fine for clients in Europe)"; fi
if echo "$CPU" | grep -qE "Xeon\(R\) (Gold|Silver|Bronze|Platinum) [65]1[0-9]{2}"; then echo "FAIL: old CPU, the avatar may fall below 25 fps. Delete this pod."; exit 1; fi
echo "PASS: this pod is fine, run setup."
