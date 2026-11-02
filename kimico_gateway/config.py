"""Load and validate the gateway configuration.

This module owns the configuration schema. Configuration is read exactly once
at startup; later modules receive the frozen object produced here and never
parse TOML themselves.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import Any

DEFAULT_LOG_LEVEL = "INFO"
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(Exception):
    """Configuration could not be loaded or validated.

    The message lists every problem on its own line.
    """


@dataclass(frozen=True)
class ServerConfig:
    access_token: str


@dataclass(frozen=True)
class AuthConfig:
    main_qq: int


@dataclass(frozen=True)
class OnebotConfig:
    access_token: str


@dataclass(frozen=True)
class LoggingConfig:
    level: str


@dataclass(frozen=True)
class Config:
    server: ServerConfig
    auth: AuthConfig
    onebot: OnebotConfig
    logging: LoggingConfig


def load_config(path: str) -> Config:
    """Read and validate the TOML configuration at ``path``.

    Raises:
        ConfigError: the file cannot be read or parsed, or validation failed.
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

    server_token = _require_str(raw, "server", "access_token", errors)
    main_qq = _require_int(raw, "auth", "main_qq", errors)
    onebot_token = _require_str(raw, "onebot", "access_token", errors)
    log_level = _optional_str(
        raw, "logging", "level", DEFAULT_LOG_LEVEL, errors
    )
    if log_level is not None:
        log_level = log_level.upper()
        if log_level not in VALID_LOG_LEVELS:
            errors.append(
                "logging.level: must be one of " + ", ".join(VALID_LOG_LEVELS)
            )

    if errors:
        raise ConfigError("\n".join(errors))

    return Config(
        server=ServerConfig(access_token=server_token),
        auth=AuthConfig(main_qq=main_qq),
        onebot=OnebotConfig(access_token=onebot_token),
        logging=LoggingConfig(level=log_level),
    )


def _section(raw: dict[str, Any], name: str, errors: list[str]) -> Any:
    value = raw.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{name}: must be a table")
        return {}
    return value


def _require_str(
    raw: dict[str, Any], section: str, field: str, errors: list[str]
) -> str:
    value = _section(raw, section, errors).get(field)
    if value is None:
        errors.append(f"{section}.{field}: required")
        return ""
    if not isinstance(value, str):
        errors.append(f"{section}.{field}: must be str")
        return ""
    return value


def _require_int(
    raw: dict[str, Any], section: str, field: str, errors: list[str]
) -> int:
    value = _section(raw, section, errors).get(field)
    if value is None:
        errors.append(f"{section}.{field}: required")
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{section}.{field}: must be int")
        return 0
    return value


def _optional_str(
    raw: dict[str, Any],
    section: str,
    field: str,
    default: str,
    errors: list[str],
) -> str | None:
    value = _section(raw, section, errors).get(field, default)
    if not isinstance(value, str):
        errors.append(f"{section}.{field}: must be str")
        return None
    return value
