"""Process entry point: load configuration once, log, wait for a stop signal."""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time

from . import __version__
from .capture import CaptureError, LiveTailTracker, capture_pane
from .config import Config, ConfigError, load_config, summarize_config
from .mirror import MirrorPipeline, OutputEpoch
from .session import PaneState, RecoveryManager, get_pane_state

DEFAULT_CONFIG_PATH = "/etc/kimico-gateway/config.toml"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

logger = logging.getLogger("kimico_gateway.__main__")


class RuntimeState:
    """Thread-safe state shared by the monitor, status, and HTTP threads."""

    def __init__(self) -> None:
        self.started_monotonic = time.monotonic()
        self._last_output: float | None = None
        self._lock = threading.Lock()

    def set_last_output(self, ts: float) -> None:
        with self._lock:
            self._last_output = ts

    def last_output(self) -> float | None:
        with self._lock:
            return self._last_output


def run_monitor_loop(
    cfg: Config,
    pipeline: MirrorPipeline,
    reset_baseline: threading.Event,
    runtime: RuntimeState,
    recovery: RecoveryManager,
    epoch: OutputEpoch,
    stop_event: threading.Event,
) -> None:
    """Run one monitor round per poll until ``stop_event`` is set.

    The monitor thread is the only writer of the output baseline, stability
    tracker, and batch buffer. Rounds run in the documented order: reset
    request, pane state/recovery, capture/diff/stability, then the batch
    flush. A failure in one round is logged and does not end the thread.
    """
    tracker = LiveTailTracker(
        cfg.tmux.stable_rounds,
        volatile_tail_lines=cfg.tmux.volatile_tail_lines,
        k0_rebuild_threshold=cfg.tmux.k0_rebuild_threshold,
    )
    poll = cfg.tmux.poll_ms / 1000.0

    while not stop_event.is_set():
        try:
            if reset_baseline.is_set():
                reset_baseline.clear()
                pipeline.clear()
                tracker.reset()
                logger.debug("monitor: output baseline reset requested")

            state = get_pane_state(cfg.tmux.session)
            if state is PaneState.ALIVE:
                recovery.note_alive()
                released = tracker.update(
                    capture_pane(cfg.tmux.session, cfg.tmux.capture_lines)
                )
                if released:
                    runtime.set_last_output(time.time())
                    pipeline.handle_stable_lines(released, epoch.current())
            elif state in (PaneState.DEAD, PaneState.MISSING):
                if recovery.note_dead_and_recover(state):
                    tracker.reset()
                    pipeline.clear()
                    logger.info(
                        "monitor: pane recovered; output baseline reset"
                    )
            elif state is PaneState.UNKNOWN:
                logger.warning(
                    "monitor: pane state UNKNOWN for session %r; "
                    "skipping capture and recovery",
                    cfg.tmux.session,
                )
            else:
                logger.warning(
                    "monitor: unexpected pane state %r; "
                    "skipping capture and recovery",
                    state,
                )
        except CaptureError as exc:
            logger.warning("monitor: capture failed; skipping round: %s", exc)
        except Exception:
            logger.exception("monitor round failed")

        try:
            pipeline.flush_if_due()
        except Exception:
            logger.exception("monitor: batch flush failed")

        stop_event.wait(poll)


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
    log.debug("configuration: %s", summarize_config(config))

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
