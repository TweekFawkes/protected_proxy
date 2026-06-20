#!/usr/bin/env bash
# One-shot setup for the logging proxy. Idempotent — safe to re-run.
#
#   1. Verify uv is installed
#   2. Sync python deps (mitmproxy)
#   3. Scaffold rules.json from rules.example.json if missing
#   4. Generate the mitmproxy CA cert (so step 4 below has something to trust)
#   5. Print next steps the user has to do themselves (CA trust, env vars)
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

step() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

if [[ -f .web-proxy.env ]]; then
    PORT_OVERRIDE="${PORT-}"
    RULES_FILE_OVERRIDE="${WEB_PROXY_RULES_FILE-}"
    set -a
    # shellcheck disable=SC1091
    source .web-proxy.env
    set +a
    [[ -n "${PORT_OVERRIDE:-}" ]] && PORT="$PORT_OVERRIDE"
    [[ -n "${RULES_FILE_OVERRIDE:-}" ]] && WEB_PROXY_RULES_FILE="$RULES_FILE_OVERRIDE"
fi

abs_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$ROOT" "$1" ;;
    esac
}

RULES_FILE="$(abs_path "${WEB_PROXY_RULES_FILE:-rules.json}")"
PORT="${PORT:-8080}"

# ---------- 1. uv ----------
step "checking uv"
if ! command -v uv >/dev/null 2>&1; then
    fail "uv is not installed. Install it with one of:
        brew install uv
        curl -LsSf https://astral.sh/uv/install.sh | sh"
fi
note "uv: $(uv --version)"

# ---------- 2. deps ----------
step "installing python deps (uv sync)"
uv sync --quiet
note "deps installed in .venv/"

# ---------- 3. rules file ----------
step "scaffolding rules file"
if [[ -f "$RULES_FILE" ]]; then
    note "$RULES_FILE already exists, leaving it alone"
else
    mkdir -p "$(dirname "$RULES_FILE")"
    cp rules.example.json "$RULES_FILE"
    note "created $RULES_FILE from rules.example.json"
fi

# ---------- 4. mitmproxy CA ----------
CA="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
step "ensuring mitmproxy CA cert exists"
if [[ -f "$CA" ]]; then
    note "found $CA"
else
    note "first-run cert generation (briefly starting mitmdump)..."
    uv run mitmdump --listen-port 0 -q >/dev/null 2>&1 &
    PID=$!
    for _ in $(seq 1 50); do
        [[ -f "$CA" ]] && break
        sleep 0.1
    done
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
    if [[ ! -f "$CA" ]]; then
        fail "could not generate CA cert. Try running ./run.sh once and rerun setup."
    fi
    note "generated $CA"
fi

# ---------- 5. next steps ----------
cat <<EOF

==> setup complete. Two manual steps remain:

(a) Trust the mitmproxy CA so HTTPS response bodies decrypt. Pick ONE:

    System-wide (most apps work; requires sudo + Touch ID):
      sudo security add-trusted-cert -d -r trustRoot \\
          -k /Library/Keychains/System.keychain \\
          "$CA"

    Per-app (no system change; safer if you only need one tool):
      Node:    export NODE_EXTRA_CA_CERTS="$CA"
      Python:  export REQUESTS_CA_BUNDLE="$CA"
               export SSL_CERT_FILE="$CA"
      curl:    curl --cacert "$CA" ...

(b) Point your app at the proxy and run it:

      ./run.sh                              # in this terminal

      # in another terminal, where your app runs:
      export HTTPS_PROXY=http://localhost:$PORT
      export HTTP_PROXY=http://localhost:$PORT
      <your-app>

Logs land in ./logs/ unless WEB_PROXY_LOG_DIR overrides it. See README.md for the rules.json rewriting feature.
EOF
