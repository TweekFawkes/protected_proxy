"""User-friendly launcher for the web_proxy mitmproxy app."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


APP_NAME = "web_proxy"
DEFAULT_PORT = 8080
DEFAULT_MAX_BODY = 1_048_576
MITMPROXY_CA = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"


@dataclass(frozen=True)
class RuntimePaths:
    home: Path
    config: Path
    rules: Path
    logs: Path


@dataclass(frozen=True)
class ProxyConfig:
    port: int
    log_dir: Path
    rules_file: Path
    max_body: int
    block_global: bool


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = runtime_paths()

    if args.command == "init":
        config = ensure_runtime_files(paths, force=args.force)
        print_ready(paths, config)
        return 0

    if args.command == "paths":
        ensure_runtime_files(paths, force=False)
        print(paths.home)
        return 0

    if args.command == "env":
        config = load_config(paths)
        print_proxy_env(config.port)
        return 0

    if args.command == "doctor":
        config = ensure_runtime_files(paths, force=False)
        return doctor(paths, config, fix=args.fix)

    if args.command == "trust-ca":
        config = ensure_runtime_files(paths, force=False)
        ensure_ca_exists(fix=True)
        return trust_ca(MITMPROXY_CA, dry_run=args.dry_run)

    config = ensure_runtime_files(paths, force=False)
    config = apply_run_overrides(config, args)
    return run_proxy(paths, config, ensure_ca=not args.no_ca_check)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    commands = {"run", "init", "env", "doctor", "trust-ca", "paths"}
    if not raw or raw[0] not in commands:
        raw.insert(0, "run")

    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Logging HTTP/HTTPS proxy for debugging app web traffic.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="start the proxy (default)")
    run.add_argument("--port", type=int, help="proxy listen port")
    run.add_argument("--log-dir", type=Path, help="directory for flow logs")
    run.add_argument("--rules-file", type=Path, help="JSON response rewrite rules")
    run.add_argument("--max-body", type=int, help="maximum body bytes written per message")
    run.add_argument(
        "--block-global",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="whether mitmproxy should reject non-local clients",
    )
    run.add_argument(
        "--no-ca-check",
        action="store_true",
        help="skip first-run mitmproxy CA generation/check",
    )

    init = sub.add_parser("init", help="create editable config, rules, and log directories")
    init.add_argument("--force", action="store_true", help="rewrite config from defaults")

    sub.add_parser("env", help="print shell exports for routing an app through the proxy")

    doctor_parser = sub.add_parser("doctor", help="check runtime readiness")
    doctor_parser.add_argument("--fix", action="store_true", help="create missing runtime files/CA")

    trust = sub.add_parser("trust-ca", help="trust the mitmproxy CA system-wide on macOS")
    trust.add_argument("--dry-run", action="store_true", help="print the command without running it")

    sub.add_parser("paths", help="print the user config directory")

    return parser.parse_args(raw)


def runtime_paths() -> RuntimePaths:
    home = Path(os.environ.get("WEB_PROXY_HOME", Path.home() / ".web_proxy")).expanduser()
    return RuntimePaths(
        home=home,
        config=home / "config.json",
        rules=home / "rules.json",
        logs=home / "logs",
    )


def ensure_runtime_files(paths: RuntimePaths, *, force: bool) -> ProxyConfig:
    paths.home.mkdir(parents=True, exist_ok=True)
    paths.logs.mkdir(parents=True, exist_ok=True)

    if not paths.rules.exists():
        template = Path(__file__).with_name("rules.example.json")
        if template.exists():
            shutil.copyfile(template, paths.rules)
        else:
            paths.rules.write_text('{"rules": []}\n', encoding="utf-8")

    if force or not paths.config.exists():
        config = {
            "port": int(os.environ.get("PORT", DEFAULT_PORT)),
            "log_dir": str(paths.logs),
            "rules_file": str(paths.rules),
            "max_body": int(os.environ.get("WEB_PROXY_MAX_BODY", DEFAULT_MAX_BODY)),
            "block_global": False,
        }
        paths.config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    return load_config(paths)


def load_config(paths: RuntimePaths) -> ProxyConfig:
    if not paths.config.exists():
        return ensure_runtime_files(paths, force=False)

    raw = json.loads(paths.config.read_text(encoding="utf-8"))
    port = int(os.environ.get("PORT", raw.get("port", DEFAULT_PORT)))
    log_dir = Path(os.environ.get("WEB_PROXY_LOG_DIR", raw.get("log_dir", paths.logs))).expanduser()
    rules_file = Path(
        os.environ.get("WEB_PROXY_RULES_FILE", raw.get("rules_file", paths.rules))
    ).expanduser()
    max_body = int(os.environ.get("WEB_PROXY_MAX_BODY", raw.get("max_body", DEFAULT_MAX_BODY)))
    block_global = bool(raw.get("block_global", False))
    return ProxyConfig(
        port=port,
        log_dir=log_dir,
        rules_file=rules_file,
        max_body=max_body,
        block_global=block_global,
    )


def apply_run_overrides(config: ProxyConfig, args: argparse.Namespace) -> ProxyConfig:
    return ProxyConfig(
        port=args.port if args.port is not None else config.port,
        log_dir=args.log_dir.expanduser() if args.log_dir is not None else config.log_dir,
        rules_file=args.rules_file.expanduser() if args.rules_file is not None else config.rules_file,
        max_body=args.max_body if args.max_body is not None else config.max_body,
        block_global=args.block_global if args.block_global is not None else config.block_global,
    )


def run_proxy(paths: RuntimePaths, config: ProxyConfig, *, ensure_ca: bool) -> int:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.rules_file.parent.mkdir(parents=True, exist_ok=True)
    if not config.rules_file.exists():
        shutil.copyfile(paths.rules, config.rules_file)

    if ensure_ca:
        ensure_ca_exists(fix=True)

    os.environ["WEB_PROXY_LOG_DIR"] = str(config.log_dir.resolve())
    os.environ["WEB_PROXY_RULES_FILE"] = str(config.rules_file.resolve())
    os.environ["WEB_PROXY_MAX_BODY"] = str(config.max_body)

    print_startup(config)
    from mitmproxy.tools import main as mitm_main

    block_global = "true" if config.block_global else "false"
    addon = str(Path(__file__).with_name("logger.py").resolve())
    result = mitm_main.mitmdump(
        [
            "--listen-port",
            str(config.port),
            "--set",
            f"block_global={block_global}",
            "-s",
            addon,
        ]
    )
    return int(result or 0)


def print_startup(config: ProxyConfig) -> None:
    print(f"{APP_NAME}: starting on port {config.port}", flush=True)
    print(f"{APP_NAME}: writing logs to {config.log_dir}", flush=True)
    print(f"{APP_NAME}: reading rules from {config.rules_file}", flush=True)
    print("")
    print("Point your app at this proxy with:")
    print_proxy_env(config.port)
    print("")
    if MITMPROXY_CA.exists():
        print(f"mitmproxy CA: {MITMPROXY_CA}")
        print(f"Trust it system-wide with: {APP_NAME} trust-ca")
    else:
        print(f"mitmproxy CA will be created at: {MITMPROXY_CA}")
    print("")


def print_ready(paths: RuntimePaths, config: ProxyConfig) -> None:
    print(f"{APP_NAME}: ready")
    print(f"config : {paths.config}")
    print(f"rules  : {config.rules_file}")
    print(f"logs   : {config.log_dir}")
    print("")
    print(f"Start proxy: {APP_NAME}")
    print(f"Edit config: open {paths.home}")
    print(f"Check setup: {APP_NAME} doctor")


def print_proxy_env(port: int) -> None:
    print(f"export HTTP_PROXY=http://localhost:{port}")
    print(f"export HTTPS_PROXY=http://localhost:{port}")
    print(f"export http_proxy=http://localhost:{port}")
    print(f"export https_proxy=http://localhost:{port}")


def doctor(paths: RuntimePaths, config: ProxyConfig, *, fix: bool) -> int:
    failures: list[str] = []
    print(f"config directory: {paths.home}")
    print(f"config file     : {paths.config}")
    print(f"rules file      : {config.rules_file}")
    print(f"log directory   : {config.log_dir}")
    print(f"platform        : {platform.system()} {platform.machine()}")

    try:
        import mitmproxy  # noqa: F401

        print("mitmproxy       : available")
    except Exception as exc:
        failures.append(f"mitmproxy import failed: {exc!r}")
        print(f"mitmproxy       : missing ({exc!r})")

    ca_exists = ensure_ca_exists(fix=fix)
    print(f"mitmproxy CA    : {'present' if ca_exists else 'missing'} ({MITMPROXY_CA})")
    if not ca_exists:
        failures.append("mitmproxy CA is missing; run `web_proxy doctor --fix`")

    if platform.system() == "Darwin":
        trusted = ca_appears_in_system_keychain()
        print(f"macOS trust     : {'system keychain has mitmproxy cert' if trusted else 'not detected'}")
        if not trusted:
            print(f"                 optional: {APP_NAME} trust-ca")

    if failures:
        print("")
        print("Problems:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("")
    print("All required checks passed.")
    return 0


def ensure_ca_exists(*, fix: bool) -> bool:
    if MITMPROXY_CA.exists():
        return True
    if not fix:
        return False

    MITMPROXY_CA.parent.mkdir(parents=True, exist_ok=True)
    code = (
        "from mitmproxy.tools import main; "
        "main.mitmdump(['--listen-port', '0', '-q'])"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(80):
            if MITMPROXY_CA.exists():
                return True
            time.sleep(0.1)
        return MITMPROXY_CA.exists()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def ca_appears_in_system_keychain() -> bool:
    if platform.system() != "Darwin":
        return False
    result = subprocess.run(
        ["security", "find-certificate", "-c", "mitmproxy", "/Library/Keychains/System.keychain"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def trust_ca(ca: Path, *, dry_run: bool) -> int:
    if platform.system() != "Darwin":
        print("System CA trust automation is only supported on macOS.")
        return 1
    if not ca.exists():
        print(f"CA certificate does not exist: {ca}")
        return 1

    command = [
        "sudo",
        "security",
        "add-trusted-cert",
        "-d",
        "-r",
        "trustRoot",
        "-k",
        "/Library/Keychains/System.keychain",
        str(ca),
    ]
    print(" ".join(command))
    if dry_run:
        return 0
    result = subprocess.run(command, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
