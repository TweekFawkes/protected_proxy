"""Response-rewriting rules.

A rules file is JSON of the form:

  {
    "rules": [
      {
        "name": "stub email in bootstrap",
        "match": {
          "host": "api.example.com",
          "url_pattern": "/bootstrap"
        },
        "json_replace_keys": {
          "account_email": "user@example.com"
        }
      }
    ]
  }

Match fields are all optional and AND-ed together. An empty match {} matches
every request. `host` is an exact string compared to the request host;
`url_pattern` is a Python regex `search()`'d against the full URL.

`json_replace_keys` is a flat dict of key name -> replacement value. Every
occurrence of that key anywhere in the response JSON (any nesting depth,
including inside lists of dicts) gets its value replaced. The replacement
value is used verbatim, so it can be any JSON-serializable thing (string,
number, bool, null, dict, list).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Rule:
    name: str
    host: str | None = None
    url_pattern: re.Pattern[str] | None = None
    json_replace_keys: dict[str, Any] = field(default_factory=dict)

    def matches(self, host: str, url: str) -> bool:
        if self.host is not None and self.host != host:
            return False
        if self.url_pattern is not None and not self.url_pattern.search(url):
            return False
        return True


def load_rules(path: Path) -> list[Rule]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules: list[Rule] = []
    for i, entry in enumerate(raw.get("rules", [])):
        name = entry.get("name") or f"rule[{i}]"
        match = entry.get("match") or {}
        host = match.get("host")
        url_pattern_str = match.get("url_pattern")
        url_pattern = re.compile(url_pattern_str) if url_pattern_str else None
        json_replace_keys = entry.get("json_replace_keys") or {}
        if not isinstance(json_replace_keys, dict):
            raise ValueError(f"rule {name!r}: json_replace_keys must be an object")
        rules.append(
            Rule(
                name=name,
                host=host,
                url_pattern=url_pattern,
                json_replace_keys=json_replace_keys,
            )
        )
    return rules


def apply_rules(
    body: Any,
    rules: list[Rule],
    *,
    host: str,
    url: str,
) -> tuple[Any, list[str]]:
    """Apply every matching rule to `body` (a parsed JSON value).

    Returns (new_body, change_log) where change_log is a list of
    "<rule name>: <json path>" strings, one per replacement performed.
    """
    change_log: list[str] = []
    for rule in rules:
        if not rule.matches(host, url):
            continue
        if not rule.json_replace_keys:
            continue
        paths = _walk_replace(body, rule.json_replace_keys)
        for path in paths:
            change_log.append(f"{rule.name}: {path}")
    return body, change_log


def _walk_replace(node: Any, replacements: dict[str, Any], path: str = "$") -> list[str]:
    """Recursively replace values for matching keys; returns list of changed paths.

    Mutates `node` in place. Replacement happens at every depth — a key called
    "id" inside a deeply nested list-of-objects is still rewritten. Once a key
    is replaced, we do NOT recurse into the replacement value (you wanted it
    used verbatim).
    """
    changed: list[str] = []
    if isinstance(node, dict):
        for key in list(node.keys()):
            current = f"{path}.{key}"
            if key in replacements:
                node[key] = replacements[key]
                changed.append(current)
            else:
                changed.extend(_walk_replace(node[key], replacements, current))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            changed.extend(_walk_replace(item, replacements, f"{path}[{i}]"))
    return changed
