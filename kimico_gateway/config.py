"""Load and validate the gateway configuration.

This module owns the configuration schema. Configuration is read exactly once
at startup; later modules receive the frozen object produced here and never
parse TOML themselves.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import tomllib
from dataclasses import dataclass, fields
from typing import Any

logger = logging.getLogger("kimico_gateway.config")

DEFAULT_LOG_LEVEL = "INFO"
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
PLACEHOLDER_TOKEN = "change-me"

_MISSING = object()

_SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "server": ("host", "port", "path", "access_token", "max_body_bytes"),
    "auth": ("main_qq",),
    "onebot": ("api_base", "access_token"),
    "tmux": (
        "session",
        "kimico_command",
        "history_limit",
        "capture_lines",
        "poll_ms",
        "stable_rounds",
        "volatile_tail_lines",
        "k0_rebuild_threshold",
    ),
    "mirror": (
        "chunk_chars",
        "send_interval_ms",
        "outbound_queue_max",
        "batch_quiet_ms",
        "batch_max_chars",
        "ignore_patterns",
        "echo_prefix_patterns",
    ),
    "recovery": (
        "backoff_seconds",
        "max_attempts",
        "window_seconds",
        "success_reset_seconds",
    ),
    "state": ("path",),
    "logging": ("level",),
}


class ConfigError(Exception):
    """Configuration could not be loaded or validated.

    The message lists every problem on its own line.
    """


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    path: str
    access_token: str
    max_body_bytes: int


@dataclass(frozen=True)
class AuthConfig:
    main_qq: int


@dataclass(frozen=True)
class OnebotConfig:
    api_base: str
    access_token: str


@dataclass(frozen=True)
class TmuxConfig:
    session: str
    kimico_command: str
    history_limit: int
    capture_lines: int
    poll_ms: int
    stable_rounds: int
    volatile_tail_lines: int
    k0_rebuild_threshold: int


@dataclass(frozen=True)
class MirrorConfig:
    chunk_chars: int
    send_interval_ms: int
    outbound_queue_max: int
    batch_quiet_ms: int
    batch_max_chars: int
    ignore_patterns: tuple[str, ...]
    echo_prefix_patterns: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryConfig:
    backoff_seconds: tuple[int, ...]
    max_attempts: int
    window_seconds: int
    success_reset_seconds: int


@dataclass(frozen=True)
class StateConfig:
    path: str


@dataclass(frozen=True)
class LoggingConfig:
    level: str


@dataclass(frozen=True)
class Config:
    server: ServerConfig
    auth: AuthConfig
    onebot: OnebotConfig
    tmux: TmuxConfig
    mirror: MirrorConfig
    recovery: RecoveryConfig
    state: StateConfig
    logging: LoggingConfig


def load_config(path: str) -> Config:
    """Read and validate the TOML configuration at ``path``.

    Raises:
        ConfigError: the file cannot be read or parsed, or validation failed.
            Every problem is reported on its own line.
    """
    try:
        with open(path, "rb") as config_file:
            raw = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"failed to parse {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"failed to parse {path}: invalid UTF-8: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc

    errors: list[str] = []
    _warn_unknown_keys(raw)

    server_raw = _section(raw, "server", errors)
    auth_raw = _section(raw, "auth", errors)
    onebot_raw = _section(raw, "onebot", errors)
    tmux_raw = _section(raw, "tmux", errors)
    mirror_raw = _section(raw, "mirror", errors)
    recovery_raw = _section(raw, "recovery", errors)
    state_raw = _section(raw, "state", errors)
    logging_raw = _section(raw, "logging", errors)

    host = _str_field(
        server_raw, "server", "host", "127.0.0.1", errors, non_empty=True
    )
    port = _int_field(
        server_raw, "server", "port", 8080, errors, minimum=1, maximum=65535
    )
    server_path = _str_field(
        server_raw, "server", "path", "/cq", errors, non_empty=True
    )
    if server_path is not None and not server_path.startswith("/"):
        errors.append("server.path: must start with '/'")
    server_token = _str_field(
        server_raw, "server", "access_token", _MISSING, errors, non_empty=True
    )
    _reject_placeholder(server_token, "server.access_token", errors)
    max_body_bytes = _int_field(
        server_raw, "server", "max_body_bytes", 1048576, errors, minimum=1
    )

    if host is not None and not _is_loopback(host):
        logger.warning("server.host is not loopback: %s", host)

    main_qq = _int_field(auth_raw, "auth", "main_qq", _MISSING, errors, minimum=1)

    api_base = _str_field(
        onebot_raw,
        "onebot",
        "api_base",
        "http://127.0.0.1:3000",
        errors,
        non_empty=True,
    )
    if api_base is not None:
        if not api_base.startswith(("http://", "https://")):
            errors.append("onebot.api_base: must start with http:// or https://")
            api_base = None
        else:
            api_base = api_base.rstrip("/")
            if api_base in ("http:", "https:"):
                errors.append("onebot.api_base: must include a host")
                api_base = None
    onebot_token = _str_field(
        onebot_raw, "onebot", "access_token", _MISSING, errors, non_empty=True
    )
    _reject_placeholder(onebot_token, "onebot.access_token", errors)

    tmux_session = _str_field(
        tmux_raw, "tmux", "session", "kimico", errors, non_empty=True
    )
    kimico_command = _str_field(
        tmux_raw, "tmux", "kimico_command", "kimico", errors, non_empty=True
    )
    history_limit = _int_field(
        tmux_raw, "tmux", "history_limit", 50000, errors, minimum=1000
    )
    capture_lines = _int_field(
        tmux_raw, "tmux", "capture_lines", 5000, errors, minimum=100
    )
    poll_ms = _int_field(tmux_raw, "tmux", "poll_ms", 300, errors, minimum=50)
    stable_rounds = _int_field(
        tmux_raw, "tmux", "stable_rounds", 3, errors, minimum=1
    )
    volatile_tail_lines = _int_field(
        tmux_raw, "tmux", "volatile_tail_lines", 0, errors, minimum=0
    )
    k0_rebuild_threshold = _int_field(
        tmux_raw, "tmux", "k0_rebuild_threshold", 3, errors, minimum=1
    )
    if (
        volatile_tail_lines is not None
        and capture_lines is not None
        and volatile_tail_lines >= capture_lines
    ):
        errors.append(
            "tmux.volatile_tail_lines: must be < tmux.capture_lines"
        )

    chunk_chars = _int_field(
        mirror_raw, "mirror", "chunk_chars", 1500, errors, minimum=100
    )
    send_interval_ms = _int_field(
        mirror_raw, "mirror", "send_interval_ms", 1000, errors, minimum=0
    )
    outbound_queue_max = _int_field(
        mirror_raw, "mirror", "outbound_queue_max", 500, errors, minimum=10
    )
    batch_quiet_ms = _int_field(
        mirror_raw, "mirror", "batch_quiet_ms", 300, errors, minimum=0
    )
    batch_max_chars = _int_field(
        mirror_raw, "mirror", "batch_max_chars", 3000, errors, minimum=100
    )
    ignore_patterns = _regex_list_field(
        mirror_raw,
        "mirror",
        "ignore_patterns",
        (r"^\s*$",),
        errors,
    )
    echo_prefix_patterns = _regex_list_field(
        mirror_raw,
        "mirror",
        "echo_prefix_patterns",
        (r"^\s*[>›]\s*",),
        errors,
    )
    if (
        batch_max_chars is not None
        and chunk_chars is not None
        and batch_max_chars < chunk_chars
    ):
        errors.append("mirror.batch_max_chars: must be >= mirror.chunk_chars")

    backoff_seconds = _int_list_field(
        recovery_raw, "recovery", "backoff_seconds", (5, 15, 60), errors
    )
    max_attempts = _int_field(
        recovery_raw, "recovery", "max_attempts", 3, errors, minimum=1
    )
    window_seconds = _int_field(
        recovery_raw, "recovery", "window_seconds", 600, errors, minimum=1
    )
    success_reset_seconds = _int_field(
        recovery_raw, "recovery", "success_reset_seconds", 60, errors, minimum=1
    )

    state_path = _str_field(
        state_raw,
        "state",
        "path",
        "/var/lib/kimico-gateway/state.json",
        errors,
        non_empty=True,
    )
    if state_path is not None and not os.path.isabs(state_path):
        errors.append("state.path: must be an absolute path")

    log_level = _str_field(
        logging_raw, "logging", "level", DEFAULT_LOG_LEVEL, errors, non_empty=True
    )
    if log_level is not None:
        log_level = log_level.upper()
        if log_level not in VALID_LOG_LEVELS:
            errors.append(
                "logging.level: must be one of " + ", ".join(VALID_LOG_LEVELS)
            )
            log_level = None

    if errors:
        raise ConfigError("\n".join(errors))

    config = Config(
        server=ServerConfig(
            host=host,
            port=port,
            path=server_path,
            access_token=server_token,
            max_body_bytes=max_body_bytes,
        ),
        auth=AuthConfig(main_qq=main_qq),
        onebot=OnebotConfig(api_base=api_base, access_token=onebot_token),
        tmux=TmuxConfig(
            session=tmux_session,
            kimico_command=kimico_command,
            history_limit=history_limit,
            capture_lines=capture_lines,
            poll_ms=poll_ms,
            stable_rounds=stable_rounds,
            volatile_tail_lines=volatile_tail_lines,
            k0_rebuild_threshold=k0_rebuild_threshold,
        ),
        mirror=MirrorConfig(
            chunk_chars=chunk_chars,
            send_interval_ms=send_interval_ms,
            outbound_queue_max=outbound_queue_max,
            batch_quiet_ms=batch_quiet_ms,
            batch_max_chars=batch_max_chars,
            ignore_patterns=ignore_patterns,
            echo_prefix_patterns=echo_prefix_patterns,
        ),
        recovery=RecoveryConfig(
            backoff_seconds=backoff_seconds,
            max_attempts=max_attempts,
            window_seconds=window_seconds,
            success_reset_seconds=success_reset_seconds,
        ),
        state=StateConfig(path=state_path),
        logging=LoggingConfig(level=log_level),
    )
    logger.debug("configuration loaded: %s", summarize_config(config))
    return config


def summarize_config(config: Config) -> str:
    """Return a one-line configuration summary with access tokens masked."""
    parts: list[str] = []
    for section_name in _SECTION_FIELDS:
        section = getattr(config, section_name)
        for field in fields(section):
            value = getattr(section, field.name)
            if field.name.endswith("token"):
                value = "***"
            parts.append(f"{section_name}.{field.name}={value}")
    return " ".join(parts)


def _section(
    raw: dict[str, Any], name: str, errors: list[str]
) -> dict[str, Any]:
    value = raw.get(name, _MISSING)
    if value is _MISSING:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{name}: must be a table")
        return {}
    return value


def _str_field(
    section: dict[str, Any],
    section_name: str,
    field: str,
    default: Any,
    errors: list[str],
    *,
    non_empty: bool = False,
) -> str | None:
    path = f"{section_name}.{field}"
    value = section.get(field, default)
    if value is _MISSING:
        errors.append(f"{path}: required")
        return None
    if not isinstance(value, str):
        errors.append(f"{path}: must be str")
        return None
    if non_empty and not value.strip():
        errors.append(f"{path}: must not be empty")
        return None
    return value


def _int_field(
    section: dict[str, Any],
    section_name: str,
    field: str,
    default: Any,
    errors: list[str],
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    path = f"{section_name}.{field}"
    value = section.get(field, default)
    if value is _MISSING:
        errors.append(f"{path}: required")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{path}: must be int")
        return None
    if minimum is not None and value < minimum:
        errors.append(f"{path}: must be >= {minimum}")
    if maximum is not None and value > maximum:
        errors.append(f"{path}: must be <= {maximum}")
    return value


def _regex_list_field(
    section: dict[str, Any],
    section_name: str,
    field: str,
    default: tuple[str, ...],
    errors: list[str],
) -> tuple[str, ...] | None:
    path = f"{section_name}.{field}"
    value = section.get(field, default)
    if not isinstance(value, (list, tuple)):
        errors.append(f"{path}: must be a list of strings")
        return None
    result: list[str] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str):
            errors.append(f"{item_path}: must be str")
            continue
        try:
            re.compile(item)
        except re.error as exc:
            errors.append(f"{item_path}: invalid regular expression: {exc}")
            continue
        result.append(item)
    return tuple(result)


def _int_list_field(
    section: dict[str, Any],
    section_name: str,
    field: str,
    default: tuple[int, ...],
    errors: list[str],
) -> tuple[int, ...] | None:
    path = f"{section_name}.{field}"
    value = section.get(field, default)
    if not isinstance(value, (list, tuple)):
        errors.append(f"{path}: must be a list of integers")
        return None
    if not value:
        errors.append(f"{path}: must not be empty")
        return None
    result: list[int] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if isinstance(item, bool) or not isinstance(item, int):
            errors.append(f"{item_path}: must be int")
            continue
        if item < 0:
            errors.append(f"{item_path}: must be >= 0")
            continue
        result.append(item)
    return tuple(result)


def _reject_placeholder(
    token: str | None, path: str, errors: list[str]
) -> None:
    if token is not None and token.strip().lower() == PLACEHOLDER_TOKEN:
        errors.append(f"{path}: must not be the placeholder value 'change-me'")


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _warn_unknown_keys(raw: dict[str, Any]) -> None:
    for key in raw:
        if key not in _SECTION_FIELDS:
            logger.warning("unknown config key: %s", key)
    for section_name, known_fields in _SECTION_FIELDS.items():
        section = raw.get(section_name)
        if not isinstance(section, dict):
            continue
        for key in section:
            if key not in known_fields:
                logger.warning("unknown config key: %s.%s", section_name, key)
