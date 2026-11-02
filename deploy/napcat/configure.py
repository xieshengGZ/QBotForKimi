#!/usr/bin/env python3
"""Align the gateway config and a NapCat OneBot config.

The script:

* reads ``/etc/kimico-gateway/config.toml``;
* keeps a free OneBot HTTP port (or picks 13000+ when the configured one is
  already in use);
* rotates tokens that are still placeholders (or when ``--rotate-tokens``);
* writes the matching ``onebot11_<gateway-qq>.json`` for NapCat.

Run it directly, or let ``deploy/napcat/start.sh`` call it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_CONFIG = "/etc/kimico-gateway/config.toml"
DEFAULT_NAPCAT_DIR = "/opt/napcat/config"
PLACEHOLDER_PREFIXES = ("REPLACE_WITH_", "verify-")


def is_placeholder(token: str) -> bool:
    if not token or token == "change-me":
        return True
    return token.startswith(PLACEHOLDER_PREFIXES)


def port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False


def choose_port(host: str, preferred: int) -> int:
    if preferred and port_available(host, preferred):
        return preferred
    for candidate in range(13000, 13100):
        if port_available(host, candidate):
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def api_base_port(api_base: str) -> int:
    try:
        return urlsplit(api_base).port or 3000
    except ValueError:
        return 3000


def _replace_key(
    lines: list[str], section: str, key: str, value: str
) -> list[str]:
    pattern = re.compile(
        rf"^(\s*{re.escape(key)}\s*=\s*)(.*?)(\r?\n)?$"
    )
    current: str | None = None
    replaced = False
    out: list[str] = []
    for line in lines:
        header = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if header:
            current = header.group(1).strip()
        match = pattern.match(line) if current == section else None
        if match:
            ending = match.group(3) or "\n"
            out.append(f'{match.group(1)}"{value}"{ending}')
            replaced = True
        else:
            out.append(line)
    if not replaced:
        raise ValueError(f"missing {section}.{key} in config")
    return out


def update_gateway_config(
    config_path: Path,
    *,
    api_base: str,
    server_token: str,
    onebot_token: str,
    kimico: str | None = None,
) -> None:
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines = _replace_key(lines, "onebot", "api_base", api_base)
    lines = _replace_key(lines, "server", "access_token", server_token)
    lines = _replace_key(lines, "onebot", "access_token", onebot_token)
    if kimico is not None:
        lines = _replace_key(lines, "tmux", "kimico_command", kimico)
    config_path.write_text("".join(lines), encoding="utf-8")
    os.chmod(config_path, 0o600)


def render_onebot_config(
    template: Path,
    output: Path,
    *,
    napcat_port: int,
    server_token: str,
    onebot_token: str,
    gateway_host: str,
    gateway_port: int,
    gateway_path: str,
) -> None:
    data = json.loads(template.read_text(encoding="utf-8"))
    network = data["network"]
    server = network["httpServers"][0]
    server["host"] = "127.0.0.1"
    server["port"] = napcat_port
    server["secret"] = onebot_token
    client = network["httpClients"][0]
    client["url"] = (
        f"http://{gateway_host}:{gateway_port}{gateway_path}"
    )
    client["secret"] = server_token
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(output, 0o600)


def _loopback_host(value: str) -> str:
    return "127.0.0.1" if value in ("0.0.0.0", "::", "") else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--gateway-qq", default=None)
    parser.add_argument("--napcat-dir", default=DEFAULT_NAPCAT_DIR)
    parser.add_argument(
        "--template",
        default=str(Path(__file__).with_name("onebot11.template.json")),
    )
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--rotate-tokens", action="store_true")
    parser.add_argument("--kimico", default=None)
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"cannot read {config_path}: {exc}", file=sys.stderr)
        return 2

    server = raw.get("server", {})
    onebot = raw.get("onebot", {})
    host = _loopback_host(str(server.get("host", "127.0.0.1")))
    gateway_port = int(server.get("port", 8080))
    gateway_path = str(server.get("path", "/cq"))
    current_port = api_base_port(str(onebot.get("api_base", "")))

    if args.port is not None:
        if not port_available(host, args.port):
            print(f"port {args.port} is already in use", file=sys.stderr)
            return 2
        napcat_port = args.port
    else:
        napcat_port = choose_port(host, current_port)

    server_token = str(server.get("access_token", ""))
    onebot_token = str(onebot.get("access_token", ""))
    rotated = False
    if args.rotate_tokens or is_placeholder(server_token):
        server_token = secrets.token_hex(32)
        rotated = True
    if args.rotate_tokens or is_placeholder(onebot_token):
        onebot_token = secrets.token_hex(32)
        rotated = True

    api_base = f"http://{host}:{napcat_port}"
    update_gateway_config(
        config_path,
        api_base=api_base,
        server_token=server_token,
        onebot_token=onebot_token,
        kimico=args.kimico,
    )

    print(f"gateway config: {config_path}")
    print(f"napcat api:     {api_base}")
    print(f"gateway http:   http://{host}:{gateway_port}{gateway_path}")
    print(f"tokens:         {'rotated' if rotated else 'kept'}")

    if args.gateway_qq:
        if not args.gateway_qq.isdigit():
            print("--gateway-qq must be a numeric QQ number", file=sys.stderr)
            return 2
        output = Path(args.napcat_dir) / f"onebot11_{args.gateway_qq}.json"
        render_onebot_config(
            Path(args.template),
            output,
            napcat_port=napcat_port,
            server_token=server_token,
            onebot_token=onebot_token,
            gateway_host=host,
            gateway_port=gateway_port,
            gateway_path=gateway_path,
        )
        print(f"napcat config:  {output}")
    else:
        print(
            "napcat config:  not written (pass --gateway-qq when starting "
            "the container)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
