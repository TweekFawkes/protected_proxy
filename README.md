# web_proxy

Logging HTTP/HTTPS proxy for debugging app web traffic. Built on
[mitmproxy](https://mitmproxy.org). Each request/response pair is written to
its own file under `logs/` so you can grep, diff, and share individual flows.

## Quick start from a single macOS binary

Build or download a `web_proxy` binary, then run it:

```sh
./web_proxy
```

This repo also includes a prebuilt Apple Silicon macOS binary:

```sh
./bin/protected_proxy-macos-arm64
```

On first run it creates editable user files under `~/.web_proxy/`:

| File | Purpose |
|---|---|
| `~/.web_proxy/config.json` | Default port, log directory, rules file, and body limit |
| `~/.web_proxy/rules.json` | Response-rewriting rules |
| `~/.web_proxy/logs/` | One log file per captured flow |

Useful commands:

```sh
./web_proxy init          # create config/rules/log folders without starting
./web_proxy doctor        # check config, mitmproxy import, CA, and macOS trust
./web_proxy doctor --fix  # also generate the mitmproxy CA if missing
./web_proxy env           # print HTTP_PROXY/HTTPS_PROXY exports
./web_proxy trust-ca      # opt-in: trust mitmproxy CA system-wide on macOS
./web_proxy --port 9090   # start on a different port
```

`trust-ca` uses `sudo security add-trusted-cert`, so macOS may ask for your
password or Touch ID. You can skip system-wide trust and use per-app trust
instead; see "HTTPS body capture" below.

To build the binary locally with PyCrucible:

```sh
uvx pycrucible -e . -o ./dist/web_proxy
```

PyCrucible embeds this project plus `uv`. On a fresh machine, the first launch
may download the pinned Python dependencies, including mitmproxy; later launches
reuse the resolved environment.

## Quick start from a repo checkout (macOS)

Prereq: [`uv`](https://github.com/astral-sh/uv) (`brew install uv`).

```sh
./setup.sh    # installs deps, scaffolds rules.json, generates the mitm CA
./run.sh      # starts the proxy on :8080
```

Then in another shell, point your app at the proxy:

```sh
export HTTP_PROXY=http://localhost:8080
export HTTPS_PROXY=http://localhost:8080
export http_proxy=http://localhost:8080
export https_proxy=http://localhost:8080
```

Run your app. Each web request will land in `logs/` as a single file:

```
logs/20260502T031245_123_api.example.com_POST_a3f81b9c.log
```

`setup.sh` is idempotent — safe to re-run.

## Git worktrees

This repo is set up so each git worktree can have its own local proxy
defaults. The local file is `.web-proxy.env`; it is ignored by git and read by
both `setup.sh` and `run.sh` before environment defaults are chosen.

After the repo has an initial commit, create a sibling worktree like this:

```sh
./worktree.sh feature/rewrite-rules
cd ../web_proxy-feature-rewrite-rules
./setup.sh
./run.sh
```

The helper creates the branch/worktree and writes a `.web-proxy.env` with:

```sh
PORT=18xxx
WEB_PROXY_LOG_DIR=logs
WEB_PROXY_RULES_FILE=rules.json
```

That keeps parallel worktrees from fighting over port `8080` while preserving
the usual env-var overrides:

```sh
PORT=9090 ./run.sh
WEB_PROXY_RULES_FILE=/tmp/rules.json ./run.sh
```

If you make a worktree manually with `git worktree add`, either copy
`.web-proxy.env` from another worktree and change the port, or create it with
the three variables above. Without this file, `./run.sh` still works with the
standard defaults: port `8080`, `./logs`, and `./rules.json`.

## HTTPS body capture (one-time CA trust)

mitmproxy generates its own CA at `~/.mitmproxy/mitmproxy-ca-cert.pem`
(`setup.sh` does this for you on first run). To see decrypted HTTPS bodies,
your app has to trust it. `setup.sh` deliberately does **not** auto-trust the
CA system-wide — you should make that decision yourself. Pick one:

**System-wide (most apps respect this; requires sudo + Touch ID):**

```sh
sudo security add-trusted-cert -d -r trustRoot \
    -k /Library/Keychains/System.keychain \
    ~/.mitmproxy/mitmproxy-ca-cert.pem
```

To remove later: `sudo security delete-certificate -c mitmproxy /Library/Keychains/System.keychain`.

**Per-app (no system change; safer if you only need one tool):**
- `curl`: `curl --proxy http://localhost:8080 --cacert ~/.mitmproxy/mitmproxy-ca-cert.pem https://example.com`
- Python `requests`: `export REQUESTS_CA_BUNDLE=~/.mitmproxy/mitmproxy-ca-cert.pem`
- Node (incl. Claude Code): `export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem`
- Go: `export SSL_CERT_FILE=~/.mitmproxy/mitmproxy-ca-cert.pem`

Apps that pin certificates (some mobile SDKs, some banking apps) cannot be
intercepted no matter what — that's the pinning working as designed.

## Configuration

Environment variables:

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | Proxy listen port |
| `WEB_PROXY_LOG_DIR` | `./logs` | Where log files go |
| `WEB_PROXY_MAX_BODY` | `1048576` | Per-message body cap in bytes (truncated above this) |
| `WEB_PROXY_RULES_FILE` | `./rules.json` | Response-rewriting rules (see below) |

## Rewriting responses (rules)

You can rewrite values in JSON response bodies before they reach the client —
useful for testing how an app behaves with stubbed account state, feature
flags, error fields, etc., without modifying the API.

Copy the example to start:

```sh
cp rules.example.json rules.json
```

`rules.json` is a list of rules. Each rule has an optional `match` filter and
a `json_replace_keys` map. Every occurrence of a matching key anywhere in the
response JSON gets its value replaced (any depth, including inside lists).

```jsonc
{
  "rules": [
    {
      "name": "stub email in claude bootstrap",
      "match": {
        "host": "api.example.com",          // optional: exact host match
        "url_pattern": "/bootstrap"         // optional: regex against full URL
      },
      "json_replace_keys": {
        "account_email": "user@example.com"
      }
    }
  ]
}
```

Match fields are AND-ed; an empty `"match": {}` matches every request. The
replacement value can be any JSON-serializable thing — string, number, bool,
null, an object, or a list.

When a rule fires, the saved log file gains a `--- RULES APPLIED ---` section
showing the rule name and the JSON path that was rewritten:

```
--- RULES APPLIED ---
  - stub email in claude bootstrap: $.oauth_account.account_email
```

The proxy currently rewrites only `application/json` responses. Compressed
bodies (gzip/br/deflate/zstd) are decoded, modified, and re-encoded so the
client never knows the difference.

Rules are loaded once at proxy startup. Edit `rules.json` and restart `run.sh`
to pick up changes.

## Log file format

Plain text, one flow per file:

```
==============================================================================
flow_id : <uuid>
client  : ('127.0.0.1', 54321)
server  : example.com:443
tls     : TLSv1.3
started : 2026-05-02T03:12:45.123+00:00

--- REQUEST ---
GET https://example.com/ HTTP/2
host: example.com
user-agent: curl/8.4.0
...

(empty body)

--- RESPONSE ---
HTTP/2 200 OK
content-type: text/html; charset=UTF-8
...

<!doctype html>...

--- TIMING ---
request_started : 2026-05-02T03:12:45.123+00:00
response_ended  : 2026-05-02T03:12:45.456+00:00
total_ms        : 332.4
```

Binary bodies are noted with their length but not dumped.

## How it works

`run.sh` invokes `mitmdump` with `logger.py` as an addon. The addon hooks
`response` and `error` events from mitmproxy and writes one self-contained
text file per HTTP flow. mitmproxy handles all the hard parts (HTTP/1.1, HTTP/2,
TLS interception, CONNECT tunnels, websockets).
