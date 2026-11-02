"""Process entry point: load configuration once and orchestrate the gateway."""

from __future__ import annotations

import logging
import shlex
import shutil
import signal
import sys
import threading
import time
from typing import Callable

from . import __version__
from .capture import CaptureError, LiveTailTracker, capture_pane
from .config import Config, ConfigError, load_config, summarize_config
from .input import InboundQueue, run_input_worker
from .mirror import (
    EchoSuppressor,
    MirrorPipeline,
    MirrorSwitch,
    OutboundBus,
    OutputEpoch,
    compile_patterns,
    run_sender_loop,
)
from .onebot import DedupTable, OnebotServer
from .router import BUSY_REPLY, OVERFLOW_NOTICE, StatusSnapshot
from .session import (
    PaneState,
    RecoveryManager,
    TmuxError,
    ensure_session,
    get_pane_state,
    respawn_pane,
)
from .state import StateStore

DEFAULT_CONFIG_PATH = "/etc/kimico-gateway/config.toml"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
INBOUND_CAPACITY = 50
DEDUP_CAPACITY = 200
CONTROL_SOFT_MAX = 100
ECHO_WINDOW_SECONDS = 10.0
ECHO_MAX_ENTRIES = 50
HEALTH_CHECK_SECONDS = 1.0
SHUTDOWN_JOIN_SECONDS = 5.0

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
    *,
    capture_fn: Callable[[str, int], list[str]] | None = None,
    pane_state_fn: Callable[[str], PaneState] | None = None,
) -> None:
    """Run one monitor round per poll until ``stop_event`` is set.

    The monitor thread is the only writer of the output baseline, stability
    tracker, and batch buffer. Rounds run in the documented order: reset
    request, pane state/recovery, capture/diff/stability, then the batch
    flush. A failure in one round is logged and does not end the thread.
    """
    capture = capture_pane if capture_fn is None else capture_fn
    read_pane_state = get_pane_state if pane_state_fn is None else pane_state_fn
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

            state = read_pane_state(cfg.tmux.session)
            if state is PaneState.ALIVE:
                recovery.note_alive()
                released = tracker.update(
                    capture(cfg.tmux.session, cfg.tmux.capture_lines)
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


def make_status_provider(
    config: Config,
    switch: MirrorSwitch,
    inbound: InboundQueue,
    bus: OutboundBus,
    runtime: RuntimeState,
    *,
    get_pane_state_fn: Callable[[str], PaneState] = get_pane_state,
) -> Callable[[], StatusSnapshot]:
    """Return the live eight-field ``#status`` snapshot provider.

    Pane state is queried on every call (bounded by ``get_pane_state``'s own
    timeout). A query failure degrades to UNKNOWN instead of reaching the
    input worker.
    """

    def status_provider() -> StatusSnapshot:
        try:
            state = get_pane_state_fn(config.tmux.session)
        except Exception:
            logger.exception("status: pane state query failed; degrading")
            state = PaneState.UNKNOWN
        return StatusSnapshot(
            tmux_alive=state in (PaneState.ALIVE, PaneState.DEAD),
            kimico_alive=state is PaneState.ALIVE,
            mirror_on=switch.is_on(),
            inbound_len=inbound.qsize(),
            inbound_max=INBOUND_CAPACITY,
            control_len=bus.qsize_control(),
            control_max=CONTROL_SOFT_MAX,
            bulk_len=bus.qsize_bulk(),
            bulk_max=config.mirror.outbound_queue_max,
            last_output_ts=runtime.last_output(),
            started_monotonic=runtime.started_monotonic,
        )

    return status_provider


class Gateway:
    """Assemble the shared components and start the four worker threads.

    Construction binds the HTTP server, so a port failure surfaces as an
    ``OSError`` before any thread starts. ``start`` launches the HTTP, input,
    monitor, and sender daemons; ``supervise`` watches their liveness every
    second and performs the graceful shutdown sequence when a stop is
    requested or a thread dies.
    """

    def __init__(
        self,
        config: Config,
        stop_event: threading.Event,
        mirror_initial: bool,
        *,
        state_store: StateStore,
        ensure_session_fn: Callable[[str, str, int], None] = ensure_session,
        respawn_pane_fn: Callable[[str, str], None] = respawn_pane,
        get_pane_state_fn: Callable[[str], PaneState] = get_pane_state,
        capture_pane_fn: Callable[[str, int], list[str]] = capture_pane,
        server_factory: Callable[..., OnebotServer] = OnebotServer,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        self.config = config
        self.stop_event = stop_event
        self.state_store = state_store
        self.ensure_session_fn = ensure_session_fn
        self.respawn_pane_fn = respawn_pane_fn
        self.get_pane_state_fn = get_pane_state_fn
        self.capture_pane_fn = capture_pane_fn
        self.thread_factory = thread_factory
        self.threads: dict[str, threading.Thread] = {}

        self.runtime = RuntimeState()
        self.switch = MirrorSwitch(mirror_initial, on_change=state_store.save)
        self.epoch = OutputEpoch()
        self.echo = EchoSuppressor(
            window_seconds=ECHO_WINDOW_SECONDS,
            max_entries=ECHO_MAX_ENTRIES,
            prefix_patterns=compile_patterns(config.mirror.echo_prefix_patterns),
        )
        self.bus = OutboundBus(
            config.mirror.outbound_queue_max,
            OVERFLOW_NOTICE,
            control_soft_max=CONTROL_SOFT_MAX,
        )
        self.inbound = InboundQueue(INBOUND_CAPACITY)
        self.dedup = DedupTable(DEDUP_CAPACITY)
        self.pipeline = MirrorPipeline(
            self.switch,
            self.epoch,
            self.echo,
            self.bus,
            compile_patterns(config.mirror.ignore_patterns),
            config.mirror.chunk_chars,
            config.mirror.batch_quiet_ms,
            config.mirror.batch_max_chars,
        )
        self.recovery = RecoveryManager(
            config.recovery,
            notify=self.bus.put_control,
            respawn=lambda: respawn_pane_fn(
                config.tmux.session, config.tmux.kimico_command
            ),
            recreate=lambda: ensure_session_fn(
                config.tmux.session,
                config.tmux.kimico_command,
                config.tmux.history_limit,
            ),
            pane_state=lambda: get_pane_state_fn(config.tmux.session),
            sleep=stop_event.wait,
        )
        self.reset_baseline = threading.Event()
        self.status_provider = make_status_provider(
            config,
            self.switch,
            self.inbound,
            self.bus,
            self.runtime,
            get_pane_state_fn=get_pane_state_fn,
        )
        self.server = server_factory(
            host=config.server.host,
            port=config.server.port,
            path=config.server.path,
            access_token=config.server.access_token,
            max_body_bytes=config.server.max_body_bytes,
            main_qq=config.auth.main_qq,
            dedup=self.dedup,
            on_event=self.event_intake,
        )

    def event_intake(self, event) -> None:
        """Queue one accepted event, replying BUSY when the queue is full."""
        if not self.inbound.try_put(event):
            self.bus.put_control(BUSY_REPLY)

    def start(self) -> None:
        self.threads = {
            "http": self._start_thread(
                "gateway-http", self.server.serve_forever
            ),
            "input": self._start_thread("gateway-input", self._run_input),
            "monitor": self._start_thread(
                "gateway-monitor", self._run_monitor
            ),
            "sender": self._start_thread("gateway-sender", self._run_sender),
        }

    def supervise(self) -> int:
        """Watch thread liveness, then shut down; return the process code."""
        failed = self._wait_for_thread_death()
        self._shutdown()
        return 5 if failed else 0

    def wait(self) -> int:
        """Backward-compatible alias for ``supervise``."""
        return self.supervise()

    def _wait_for_thread_death(self) -> bool:
        while not self.stop_event.is_set():
            for name, thread in self.threads.items():
                if not thread.is_alive():
                    logger.critical("thread died: %s", name)
                    self.stop_event.set()
                    return True
            self.stop_event.wait(HEALTH_CHECK_SECONDS)
        return False

    def _shutdown(self) -> None:
        """Stop intake, persist state, and join workers without touching tmux."""
        self.stop_event.set()
        try:
            self.server.shutdown()
        except Exception:
            logger.exception("HTTP server shutdown failed")

        input_thread = self.threads.get("input")
        if input_thread is not None:
            self._join_thread("input", input_thread)

        try:
            self.state_store.save(self.switch.is_on())
        except Exception:
            logger.exception("final state save failed")

        for name in ("http", "monitor", "sender"):
            thread = self.threads.get(name)
            if thread is not None:
                self._join_thread(name, thread)

    def _join_thread(self, name: str, thread: threading.Thread) -> None:
        logger.info("gateway shutdown: joining %s thread", name)
        try:
            thread.join(timeout=SHUTDOWN_JOIN_SECONDS)
        except Exception:
            logger.exception("failed to join %s thread", name)
            return
        if thread.is_alive():
            logger.warning(
                "%s thread did not stop within %gs",
                name,
                SHUTDOWN_JOIN_SECONDS,
            )

    def _start_thread(self, name: str, target: Callable[[], None]):
        thread = self.thread_factory(target=target, name=name, daemon=True)
        thread.start()
        return thread

    def _run_input(self) -> None:
        run_input_worker(
            self.inbound,
            self.bus,
            self.switch,
            self.epoch,
            self.echo,
            self.reset_baseline,
            self.status_provider,
            self.config.tmux.session,
            self.stop_event,
        )

    def _run_monitor(self) -> None:
        run_monitor_loop(
            self.config,
            self.pipeline,
            self.reset_baseline,
            self.runtime,
            self.recovery,
            self.epoch,
            self.stop_event,
            capture_fn=self.capture_pane_fn,
            pane_state_fn=self.get_pane_state_fn,
        )

    def _run_sender(self) -> None:
        run_sender_loop(
            self.bus,
            self.epoch,
            self.config.onebot.api_base,
            self.config.onebot.access_token,
            self.config.auth.main_qq,
            self.config.mirror.send_interval_ms / 1000.0,
            0.1,
            self.stop_event,
        )


def run_gateway(
    config: Config,
    stop_event: threading.Event,
    *,
    state_store: StateStore | None = None,
    which: Callable[[str], str | None] = shutil.which,
    ensure_session_fn: Callable[[str, str, int], None] = ensure_session,
    respawn_pane_fn: Callable[[str, str], None] = respawn_pane,
    get_pane_state_fn: Callable[[str], PaneState] = get_pane_state,
    capture_pane_fn: Callable[[str, int], list[str]] = capture_pane,
    server_factory: Callable[..., OnebotServer] = OnebotServer,
    thread_factory: Callable[..., threading.Thread] = threading.Thread,
    install_signal_handlers: bool = True,
    on_ready: Callable[[Gateway], None] | None = None,
) -> int:
    """Run startup, supervise the threads, and return the process exit code.

    Returns 0 after a normal stop request, 3 for a kimico preflight or tmux
    failure, 4 for an HTTP bind failure, and 5 after a worker thread dies.
    """
    if state_store is None:
        state_store = StateStore(config.state.path)
    mirror_initial = state_store.load()

    try:
        kimico_argv = shlex.split(config.tmux.kimico_command)
    except ValueError as exc:
        print(
            f"invalid kimico command {config.tmux.kimico_command!r}: {exc}",
            file=sys.stderr,
        )
        return 3
    if not kimico_argv:
        print(
            f"kimico command is empty: {config.tmux.kimico_command!r}",
            file=sys.stderr,
        )
        return 3
    if which(kimico_argv[0]) is None:
        print(
            f"kimico command not found: {kimico_argv[0]!r}; "
            "check tmux.kimico_command and PATH",
            file=sys.stderr,
        )
        return 3

    try:
        ensure_session_fn(
            config.tmux.session,
            config.tmux.kimico_command,
            config.tmux.history_limit,
        )
    except TmuxError as exc:
        print(f"tmux self-check failed: {exc}", file=sys.stderr)
        return 3

    try:
        gateway = Gateway(
            config,
            stop_event,
            mirror_initial,
            state_store=state_store,
            ensure_session_fn=ensure_session_fn,
            respawn_pane_fn=respawn_pane_fn,
            get_pane_state_fn=get_pane_state_fn,
            capture_pane_fn=capture_pane_fn,
            server_factory=server_factory,
            thread_factory=thread_factory,
        )
    except OSError as exc:
        print(
            "failed to bind HTTP server on "
            f"{config.server.host}:{config.server.port}: {exc}",
            file=sys.stderr,
        )
        return 4

    gateway.start()
    if install_signal_handlers:
        _install_signal_handlers(stop_event)
    logger.info("gateway ready: %d threads started", len(gateway.threads))
    if on_ready is not None:
        on_ready(gateway)
    return gateway.supervise()


def _install_signal_handlers(stop_event: threading.Event) -> None:
    if threading.current_thread() is not threading.main_thread():
        logger.warning(
            "not registering signal handlers outside the main thread"
        )
        return

    def handle_signal(signum: int, frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


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

    log.info("starting kimico-gateway %s (config: %s)", __version__, config_path)
    return run_gateway(config, stop_event)


if __name__ == "__main__":
    sys.exit(main())
