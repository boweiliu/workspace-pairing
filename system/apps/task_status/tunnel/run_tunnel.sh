#!/usr/bin/env bash
# Runs the ngrok agent that publishes the status gate at STATUS_TUNNEL_DOMAIN.
# The agent binary is machine state: fetched into data/.state/status_tunnel/ on first
# start and reused after. The tunnel key arrives as NGROK_AUTHTOKEN from the
# supervisord program's with_secrets.py wrapper.
set -euo pipefail

STATE_DIR="data/.state/status_tunnel"
NGROK="$STATE_DIR/bin/ngrok"
NGROK_DOWNLOAD_URL="https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz"
GATE_PORT="${STATUS_GATE_PORT:-8096}"

: "${STATUS_TUNNEL_DOMAIN:?STATUS_TUNNEL_DOMAIN must be set}"
: "${NGROK_AUTHTOKEN:?NGROK_AUTHTOKEN must be set (run under with_secrets.py)}"

if [ ! -x "$NGROK" ]; then
    mkdir -p "$STATE_DIR/bin"
    download_dir="$(mktemp -d)"
    curl -sSfL -o "$download_dir/ngrok.tgz" "$NGROK_DOWNLOAD_URL"
    tar -xzf "$download_dir/ngrok.tgz" -C "$STATE_DIR/bin" ngrok
    rm -rf "$download_dir"
fi

# A minimal config file of our own keeps the agent from reading or writing one under HOME.
printf 'version: "3"\nagent:\n  web_addr: false\n' > "$STATE_DIR/ngrok.yml"

exec "$NGROK" http "http://127.0.0.1:$GATE_PORT" \
    --url "https://$STATUS_TUNNEL_DOMAIN" \
    --config "$STATE_DIR/ngrok.yml" \
    --inspect=false \
    --log stdout --log-format logfmt
