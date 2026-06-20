#!/usr/bin/env bash
# Create a sibling git worktree with per-worktree proxy defaults.
#
# Usage:
#   ./worktree.sh feature/my-branch
#   ./worktree.sh feature/my-branch ../web_proxy-my-branch
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
BASE_NAME="$(basename "$ROOT")"

usage() {
    cat <<EOF
Usage: ./worktree.sh <branch> [path]

Creates a git worktree for <branch> and writes a local .web-proxy.env in it.
The env file keeps worktrees from fighting over port 8080 or shared logs.

Examples:
  ./worktree.sh feature/rewrite-rules
  ./worktree.sh bugfix/proxy-timeout ../web_proxy-timeout
EOF
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

BRANCH="${1:-}"
[[ -n "$BRANCH" ]] || {
    usage >&2
    exit 2
}

if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
    fail "git worktrees need at least one commit. Create the initial commit, then rerun this script."
fi

safe_name() {
    printf '%s' "$1" | sed 's#[/[:space:]]#-#g; s#[^A-Za-z0-9._-]##g; s#--*#-#g; s#^-##; s#-$##'
}

SAFE_BRANCH="$(safe_name "$BRANCH")"
WORKTREE_PATH="${2:-../${BASE_NAME}-${SAFE_BRANCH}}"

if [[ -e "$WORKTREE_PATH" ]]; then
    fail "$WORKTREE_PATH already exists"
fi

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git worktree add "$WORKTREE_PATH" "$BRANCH"
else
    git worktree add -b "$BRANCH" "$WORKTREE_PATH"
fi

WORKTREE_ABS="$(cd "$WORKTREE_PATH" && pwd)"
PORT_OFFSET="$(printf '%s' "$BRANCH" | cksum | awk '{print $1 % 1000}')"
PORT="$((18080 + PORT_OFFSET))"

cat >"$WORKTREE_ABS/.web-proxy.env" <<EOF
# Local defaults for this git worktree. This file is intentionally ignored.
PORT=$PORT
WEB_PROXY_LOG_DIR=logs
WEB_PROXY_RULES_FILE=rules.json
EOF

cat <<EOF

Created worktree:
  $WORKTREE_ABS

Local defaults:
  port:  $PORT
  env:   $WORKTREE_ABS/.web-proxy.env
  logs:  $WORKTREE_ABS/logs
  rules: $WORKTREE_ABS/rules.json

Next:
  cd "$WORKTREE_ABS"
  ./setup.sh
  ./run.sh
EOF
