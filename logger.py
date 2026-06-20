"""mitmproxy addon: write one log file per request/response flow.

Each flow becomes a single human-readable file under ./logs/, named
{timestamp}_{host}_{method}_{flow_id_prefix}.log so files sort
chronologically and are easy to grep by host or method.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mitmproxy import ctx, http

from rules import Rule, apply_rules, load_rules

LOG_DIR = Path(os.environ.get("WEB_PROXY_LOG_DIR", "logs")).resolve()
MAX_BODY_PREVIEW = int(os.environ.get("WEB_PROXY_MAX_BODY", "1048576"))  # 1 MiB
RULES_FILE = Path(os.environ.get("WEB_PROXY_RULES_FILE", "rules.json")).resolve()
CHANGES_KEY = "web_proxy_changes"  # flow.metadata key shared between addons

TEXT_CONTENT_HINTS = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-www-form-urlencoded",
    "application/graphql",
)


def _is_textual(headers: http.Headers) -> bool:
    ctype = headers.get("content-type", "").lower()
    return any(hint in ctype for hint in TEXT_CONTENT_HINTS)


def _format_body(message: http.Message) -> str:
    # Use .content (decompressed) not .raw_content (still gzip/br/deflate/zstd).
    try:
        body = message.content
    except ValueError as exc:
        raw_len = len(message.raw_content or b"")
        return f"(failed to decode body: {exc}; {raw_len} compressed bytes on the wire)"

    if body is None or len(body) == 0:
        return "(empty body)"
    if len(body) > MAX_BODY_PREVIEW:
        return (
            f"(body truncated: {len(body)} decoded bytes, showing first {MAX_BODY_PREVIEW})\n"
            + _decode(body[:MAX_BODY_PREVIEW], message.headers)
        )
    return _decode(body, message.headers)


def _decode(raw: bytes, headers: http.Headers) -> str:
    if _is_textual(headers):
        try:
            return raw.decode(message_charset(headers), errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")
    return f"(binary, {len(raw)} bytes)"


def message_charset(headers: http.Headers) -> str:
    ctype = headers.get("content-type", "")
    for part in ctype.split(";"):
        part = part.strip().lower()
        if part.startswith("charset="):
            return part.split("=", 1)[1].strip().strip('"').strip("'") or "utf-8"
    return "utf-8"


def _format_headers(headers: http.Headers) -> str:
    if not headers:
        return "(none)"
    return "\n".join(f"{k}: {v}" for k, v in headers.items())


def _safe_segment(value: str, fallback: str = "unknown") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)
    return cleaned[:64] or fallback


class Rewriter:
    """Apply rules.json replacements to JSON response bodies before logging."""

    def __init__(self) -> None:
        self.rules: list[Rule] = []

    def load(self, loader):  # noqa: ARG002 - mitmproxy hook signature
        try:
            self.rules = load_rules(RULES_FILE)
        except Exception as exc:  # malformed rules file shouldn't kill the proxy
            ctx.log.warn(f"[web_proxy] failed to load rules from {RULES_FILE}: {exc!r}")
            self.rules = []
        if self.rules:
            ctx.log.info(f"[web_proxy] loaded {len(self.rules)} rule(s) from {RULES_FILE}")
        else:
            ctx.log.info(f"[web_proxy] no rules loaded (looked at {RULES_FILE})")

    def response(self, flow: http.HTTPFlow) -> None:
        if not self.rules or flow.response is None:
            return
        ctype = flow.response.headers.get("content-type", "").lower()
        if "application/json" not in ctype:
            return  # MVP: only rewrite JSON bodies

        try:
            body_bytes = flow.response.content
            if not body_bytes:
                return
            body = json.loads(body_bytes)
        except (ValueError, json.JSONDecodeError) as exc:
            ctx.log.debug(f"[web_proxy] could not parse JSON for {flow.id}: {exc!r}")
            return

        _, changes = apply_rules(
            body,
            self.rules,
            host=flow.request.pretty_host,
            url=flow.request.pretty_url,
        )
        if not changes:
            return

        # Re-serialize and write back. Setting .content re-applies content-encoding
        # (gzip/br/etc) and updates content-length automatically.
        flow.response.content = json.dumps(body).encode("utf-8")
        flow.metadata[CHANGES_KEY] = changes


class FlowLogger:
    def load(self, loader):  # noqa: ARG002 - mitmproxy hook signature
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ctx.log.info(f"[web_proxy] writing logs to {LOG_DIR}")

    def response(self, flow: http.HTTPFlow) -> None:
        self._write(flow, error=None)

    def error(self, flow: http.HTTPFlow) -> None:
        self._write(flow, error=flow.error.msg if flow.error else "unknown error")

    def _write(self, flow: http.HTTPFlow, error: str | None) -> None:
        try:
            path = self._build_path(flow)
            path.write_text(self._render(flow, error), encoding="utf-8")
        except Exception as exc:  # last-resort: don't break the proxy on a logging bug
            ctx.log.warn(f"[web_proxy] failed to log flow {flow.id}: {exc!r}")

    def _build_path(self, flow: http.HTTPFlow) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")[:-3]
        host = _safe_segment(flow.request.pretty_host)
        method = _safe_segment(flow.request.method)
        short_id = flow.id.split("-")[0]
        return LOG_DIR / f"{ts}_{host}_{method}_{short_id}.log"

    def _render(self, flow: http.HTTPFlow, error: str | None) -> str:
        req = flow.request
        res = flow.response
        lines: list[str] = []

        lines.append("=" * 78)
        lines.append(f"flow_id : {flow.id}")
        lines.append(f"client  : {flow.client_conn.peername}")
        lines.append(f"server  : {req.pretty_host}:{req.port}")
        if flow.server_conn and flow.server_conn.tls_version:
            lines.append(f"tls     : {flow.server_conn.tls_version}")
        lines.append(f"started : {_iso(flow.timestamp_created)}")
        if error:
            lines.append(f"error   : {error}")

        lines.append("\n--- REQUEST ---")
        lines.append(f"{req.method} {req.pretty_url} HTTP/{req.http_version.split('/')[-1]}")
        lines.append(_format_headers(req.headers))
        lines.append("")
        lines.append(_format_body(req))

        if res is not None:
            lines.append("\n--- RESPONSE ---")
            lines.append(f"HTTP/{res.http_version.split('/')[-1]} {res.status_code} {res.reason}")
            lines.append(_format_headers(res.headers))
            lines.append("")
            lines.append(_format_body(res))

            changes = flow.metadata.get(CHANGES_KEY)
            if changes:
                lines.append("\n--- RULES APPLIED ---")
                lines.extend(f"  - {entry}" for entry in changes)

            lines.append("\n--- TIMING ---")
            lines.append(_render_timing(flow))
        else:
            lines.append("\n--- RESPONSE ---")
            lines.append("(no response)")

        lines.append("")
        return "\n".join(lines)


def _iso(epoch: float | None) -> str:
    if not epoch:
        return "-"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="milliseconds")


def _render_timing(flow: http.HTTPFlow) -> str:
    req = flow.request
    res = flow.response
    parts = [f"request_started : {_iso(req.timestamp_start)}"]
    if req.timestamp_end:
        parts.append(f"request_ended   : {_iso(req.timestamp_end)}")
    if res and res.timestamp_start:
        parts.append(f"response_started: {_iso(res.timestamp_start)}")
    if res and res.timestamp_end:
        parts.append(f"response_ended  : {_iso(res.timestamp_end)}")
    if res and res.timestamp_end and req.timestamp_start:
        total_ms = (res.timestamp_end - req.timestamp_start) * 1000
        parts.append(f"total_ms        : {total_ms:.1f}")
    return "\n".join(parts)


addons = [Rewriter(), FlowLogger()]
