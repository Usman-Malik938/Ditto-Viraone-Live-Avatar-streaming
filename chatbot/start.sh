#!/usr/bin/env bash
# Builds and starts the chatbot and its Cloudflare tunnel, then prints the public link.
# The quick-tunnel link changes whenever the tunnel container is recreated.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo ".env is missing: copy .env.example to .env and fill in GROQ_API_KEY."
    exit 1
fi

echo "Building and starting. The tunnel waits until the chatbot has loaded its models (the first start downloads about 5 GB)..."
docker compose up -d --build

while true; do
    chatbot_state=$(docker compose ps -a --format '{{.State}}' chatbot)
    if [ "$chatbot_state" = "exited" ]; then
        echo "The chatbot stopped. Last log lines:"
        docker compose logs --tail 30 chatbot
        exit 1
    fi
    public_url=$(docker compose logs tunnel 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -n 1 || true)
    if [ -n "$public_url" ]; then
        break
    fi
    sleep 5
done

echo
echo "Chatbot is live at: $public_url"
echo "Logs: docker compose logs -f    Stop: docker compose down"
