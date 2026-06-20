#!/usr/bin/env bash
# Start the logging proxy on port 8080.
# Usage: ./run.sh                  # listens on 8080, logs to ./logs
#        PORT=9090 ./run.sh        # custom port
#        WEB_PROXY_LOG_DIR=/tmp/foo ./run.sh
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

if [[ -f .web-proxy.env ]]; then
    PORT_OVERRIDE="${PORT-}"
    LOG_DIR_OVERRIDE="${WEB_PROXY_LOG_DIR-}"
    RULES_FILE_OVERRIDE="${WEB_PROXY_RULES_FILE-}"
    set -a
    # shellcheck disable=SC1091
    source .web-proxy.env
    set +a
    [[ -n "${PORT_OVERRIDE:-}" ]] && PORT="$PORT_OVERRIDE"
    [[ -n "${LOG_DIR_OVERRIDE:-}" ]] && WEB_PROXY_LOG_DIR="$LOG_DIR_OVERRIDE"
    [[ -n "${RULES_FILE_OVERRIDE:-}" ]] && WEB_PROXY_RULES_FILE="$RULES_FILE_OVERRIDE"
fi

abs_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$ROOT" "$1" ;;
    esac
}

PORT="${PORT:-8080}"
LOG_DIR="$(abs_path "${WEB_PROXY_LOG_DIR:-logs}")"
RULES_FILE="$(abs_path "${WEB_PROXY_RULES_FILE:-rules.json}")"
export WEB_PROXY_LOG_DIR="$LOG_DIR"
export WEB_PROXY_RULES_FILE="$RULES_FILE"

mkdir -p "$LOG_DIR"

cat <<EOF
web_proxy: starting on port $PORT
web_proxy: writing logs to $LOG_DIR
web_proxy: reading rules from $RULES_FILE

Point your app at this proxy by exporting:

  export HTTP_PROXY=http://localhost:$PORT
  export HTTPS_PROXY=http://localhost:$PORT
  export http_proxy=http://localhost:$PORT
  export https_proxy=http://localhost:$PORT

For HTTPS body capture, install the mitmproxy CA cert (one-time):
  1. With this proxy running and your shell using it, open http://mitm.it
  2. Download the macOS .pem and add it to the System keychain as "Always Trust"
  3. Or for a single tool: pass the cert via its own trust store
     (curl: --cacert ~/.mitmproxy/mitmproxy-ca-cert.pem)

EOF

exec uv run mitmdump \
    --listen-port "$PORT" \
    --set block_global=false \
    -s logger.py
