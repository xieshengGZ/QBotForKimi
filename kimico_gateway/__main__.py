"""Process entry point: load configuration once, log, wait for a stop signal."""

from __future__ import annotations

import logging
import signal
import sys
import threading

from . import __version__
from .config import ConfigError, load_config

DEFAULT_CONFIG_PATH = "/etc/kimico-gateway/config.toml"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        print("usage: python3 -m kimico_gateway [config_path]", file=sys.stderr)
        return 2
    config_path = args[0] if args else DEFAULT_CONFIG_PATH

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    logging.basicConfig(
        stream=sys.stdout,
        format=LOG_FORMAT,
        level=config.logging.level,
        force=True,
    )
    logging.raiseExceptions = False
    log = logging.getLogger("kimico_gateway.__main__")

    stop_event = threading.Event()

    def handle_signal(signum: int, frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("starting kimico-gateway %s (config: %s)", __version__, config_path)
    stop_event.wait()
    log.info("shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
