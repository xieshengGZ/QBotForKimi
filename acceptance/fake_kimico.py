#!/usr/bin/env python3
"""Deterministic fake kimico TUI used by the acceptance suite.

The gateway only needs a process in a tmux pane that reads submitted lines and
prints output. Modes mirror the scenarios in ``docs/prd/20-acceptance.md``:

* ``plain``   - echo one answered line per input.
* ``multi``   - echo plus two answer lines (echo-edge checks).
* ``long``    - ``gen`` prints 3000 numbered lines.
* ``stream``  - ``stream <id> <count> <delay_ms>`` prints slowly numbered lines.
* ``flood``   - ``flood <id>`` prints 3000 long lines (output-lane pressure).
* ``prefix``  - terminal echo is disabled and the script prints ``> <input>``
                so the configured prompt-prefix suppression is exercised.
* ``quick``   - print a banner and exit immediately (dead-pane fixture).
"""

from __future__ import annotations

import argparse
import sys
import termios
import time


def _emit(text: str) -> None:
    print(text, flush=True)


def _disable_echo() -> None:
    try:
        fd = sys.stdin.fileno()
        attrs = termios.tcgetattr(fd)
    except (OSError, termios.error):
        return
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def _handle(mode: str, line: str) -> None:
    stripped = line.strip()
    if mode == "plain":
        _emit(f"you said: {line}")
    elif mode == "multi":
        _emit(f"you said: {line}")
        _emit("answer line 1")
        _emit("answer line 2")
    elif mode == "long":
        if stripped == "gen":
            for number in range(1, 3001):
                _emit(f"line-{number:04d}")
        else:
            _emit(f"you said: {line}")
    elif mode == "stream":
        parts = stripped.split()
        if len(parts) == 4 and parts[0] == "stream":
            run_id, count_text, delay_text = parts[1:]
            count = int(count_text)
            delay = max(0, int(delay_text)) / 1000.0
            for number in range(1, count + 1):
                _emit(f"stream-{run_id}-{number:04d}")
                time.sleep(delay)
        else:
            _emit(f"you said: {line}")
    elif mode == "flood":
        parts = stripped.split()
        if len(parts) == 2 and parts[0] == "flood":
            run_id = parts[1]
            payload = "x" * 270
            for number in range(1, 3001):
                _emit(f"flood-{run_id}-{number:04d} {payload}")
        else:
            _emit(f"you said: {line}")
    elif mode == "prefix":
        _emit(f"> {line}")
        _emit("answer-prefix")
    else:
        raise SystemExit(f"unknown mode: {mode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        nargs="?",
        default="plain",
        choices=("plain", "multi", "long", "stream", "flood", "prefix", "quick"),
    )
    args = parser.parse_args(argv)

    if args.mode == "quick":
        _emit("fake-kimico-quick ready")
        return 0

    _emit(f"fake-kimico-{args.mode} ready")
    if args.mode == "prefix":
        _disable_echo()

    for raw_line in sys.stdin:
        _handle(args.mode, raw_line.rstrip("\n"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
