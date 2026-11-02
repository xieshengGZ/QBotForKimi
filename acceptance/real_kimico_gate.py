#!/usr/bin/env python3
"""Real kimi 5-10 minute acceptance gate (PRD acceptance 18).

Runs the real ``kimi`` TUI inside the real gateway capture/mirror pipeline for
at least five minutes, drives long answers with unique labels through both
phone-style HTTP injection and local tmux input, and records:

* ``capture_k0_total`` and ``recover_new_lines`` success/failure counts,
* tag loss and duplicate counts at the fake-NapCat receiver,
* per-tag mirror latency p50/p95/max.

The gateway is run in-process so the capture fallbacks can be instrumented;
its threads, tmux session, HTTP ingress, mirror pipeline, and sender are the
production code paths. Use ``--min-duration 300`` (default) or more.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acceptance.harness import AcceptanceEnv, percentile, wait_until  # noqa: E402
from kimico_gateway import __main__ as main_module  # noqa: E402
from kimico_gateway import capture as capture_module  # noqa: E402
from kimico_gateway.capture import capture_pane  # noqa: E402
from kimico_gateway.config import load_config  # noqa: E402
from kimico_gateway.state import StateStore  # noqa: E402

KIMI = "/home/ubuntu/.kimi-code/bin/kimi"
TAG_RE = re.compile(r"RK-[A-Za-z0-9]+-[A-Z]-\d{3}")
BOX_RE = re.compile(r"^\s*│\s*>\s?(.*?)\s*│?\s*$")


@dataclass
class Metrics:
    capture_rounds: int = 0
    capture_errors: int = 0
    k_rounds: int = 0
    k0_total: int = 0
    recover_calls: int = 0
    recover_success: int = 0
    recover_failure: int = 0
    tag_first_seen: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "capture_rounds": self.capture_rounds,
            "capture_errors": self.capture_errors,
            "k_rounds": self.k_rounds,
            "capture_k0_total": self.k0_total,
            "k0_ratio": round(self.k0_total / self.k_rounds, 4)
            if self.k_rounds
            else None,
            "recover_new_lines": {
                "calls": self.recover_calls,
                "success": self.recover_success,
                "failure": self.recover_failure,
            },
        }


def _capture_text(env: AcceptanceEnv) -> str:
    return "\n".join(env.capture(5000))


def _tags(text: str) -> set[str]:
    return set(TAG_RE.findall(text))


def _input_box_text(env: AcceptanceEnv) -> str:
    for line in _capture_text(env).splitlines():
        match = BOX_RE.match(line)
        if match:
            return match.group(1).strip()
    return ""


def _submit_local(env: AcceptanceEnv, prompt: str) -> None:
    env.local_input(prompt, settle=1.0)
    for _ in range(4):
        time.sleep(1.0)
        if not _input_box_text(env):
            return
        env.tmux("send-keys", "-t", f"={env.session}:", "C-m")
    raise RuntimeError("local prompt remained in the kimi input box")


def _wait_for_tags(
    env: AcceptanceEnv, expected: set[str], timeout: float
) -> None:
    wait_until(
        lambda: expected <= _tags(_capture_text(env)),
        timeout,
        description=f"{len(expected)} answer tags",
    )


def _answer_prompt(run: str, label: str, lines: int, kind: str) -> str:
    if kind == "long":
        return (
            f"只输出{lines}行，每行格式为 RK-{run}-{label}-XXX {label.lower()}，"
            f"XXX 从 001 递增到 {lines:03d}，不要输出其他内容。"
        )
    return (
        f"只回复 RK-{run}-{label}-XXX OK，XXX 是 001，不要输出其他内容。"
    )


def _expected_tags(run: str, label: str, lines: int, kind: str) -> set[str]:
    if kind == "long":
        return {f"RK-{run}-{label}-{index:03d}" for index in range(1, lines + 1)}
    return {f"RK-{run}-{label}-001"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-duration", type=float, default=300.0)
    parser.add_argument("--tag-timeout", type=float, default=180.0)
    parser.add_argument("--out", default="")
    parser.add_argument("--dump-messages", default="")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--kimi", default=KIMI)
    args = parser.parse_args(argv)

    run = "R" + str(int(time.time()) % 1_000_000)
    metrics = Metrics()
    original_find = capture_module.find_overlap
    original_recover = capture_module.recover_new_lines

    def counting_find(prev, cur):
        k = original_find(prev, cur)
        metrics.k_rounds += 1
        if k == 0:
            metrics.k0_total += 1
        return k

    def counting_recover(prev, cur):
        recovered = original_recover(prev, cur)
        metrics.recover_calls += 1
        if recovered:
            metrics.recover_success += 1
        else:
            metrics.recover_failure += 1
        return recovered

    def recording_capture(session: str, lines: int):
        metrics.capture_rounds += 1
        try:
            captured = capture_pane(session, lines)
        except Exception:
            metrics.capture_errors += 1
            raise
        now = time.monotonic()
        for line in captured:
            for tag in TAG_RE.findall(line):
                metrics.tag_first_seen.setdefault(tag, now)
        return captured

    capture_module.find_overlap = counting_find
    capture_module.recover_new_lines = counting_recover
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    started = time.monotonic()
    stop_event = threading.Event()
    ready = threading.Event()
    state: dict[str, Any] = {}
    gateway_box: dict[str, Any] = {}

    with AcceptanceEnv(mode="plain", keep=args.keep) as env:
        env.configure(kimico_command=args.kimi)
        old_env = os.environ.copy()
        old_cwd = os.getcwd()
        os.environ.pop("TMUX", None)
        os.environ.pop("TMUX_PANE", None)
        os.environ.update(env.env)
        os.chdir(env.workdir)

        def run_gateway_thread() -> None:
            try:
                state["exit_code"] = main_module.run_gateway(
                    load_config(str(env.config_path)),
                    stop_event,
                    state_store=StateStore(str(env.state_path)),
                    capture_pane_fn=recording_capture,
                    install_signal_handlers=False,
                    on_ready=lambda gateway: (
                        gateway_box.setdefault("gateway", gateway),
                        ready.set(),
                    ),
                )
            except BaseException as exc:  # noqa: BLE001
                state["error"] = repr(exc)
                ready.set()

        gateway_thread = threading.Thread(
            target=run_gateway_thread, name="real-gateway", daemon=True
        )
        gateway_thread.start()
        try:
            if not ready.wait(20):
                raise RuntimeError("gateway did not become ready")
            if "error" in state:
                raise RuntimeError(state["error"])
            env.wait_http(20)

            # First-run kimi asks whether to trust the working directory.
            time.sleep(5.0)
            text = _capture_text(env)
            if "Trust this folder?" in text:
                env.tmux(
                    "send-keys", "-t", f"={env.session}:", "C-m"
                )
                time.sleep(3.0)
            wait_until(
                lambda: "Welcome to Kimi Code" in _capture_text(env)
                or "│ >" in _capture_text(env),
                30,
                description="kimi input box",
            )
            time.sleep(3.0)

            env.send_event("#mirror on", 1)
            env.wait_for_text("mirror 已开启", 15)
            time.sleep(2.0)

            schedule = [
                ("A", "long", 12, "http"),
                ("B", "short", 1, "local"),
                ("C", "long", 12, "http"),
                ("D", "short", 1, "local"),
                ("E", "long", 12, "http"),
                ("F", "short", 1, "local"),
            ]
            expected_all: set[str] = set()
            prompt_log: list[dict[str, Any]] = []
            for index, (label, kind, lines, channel) in enumerate(schedule):
                prompt = _answer_prompt(run, label, lines, kind)
                expected = _expected_tags(run, label, lines, kind)
                expected_all |= expected
                prompt_started = time.monotonic()
                if channel == "http":
                    status, _ = env.send_event(prompt, 100 + index)
                    if status != 200:
                        raise RuntimeError(f"injection returned {status}")
                else:
                    _submit_local(env, prompt)
                try:
                    _wait_for_tags(env, expected, args.tag_timeout)
                except TimeoutError:
                    print("capture at timeout:\n" + _capture_text(env)[-6000:], flush=True)
                    print(
                        "receiver at timeout:\n"
                        + "\n---\n".join(
                            message.text[-800:]
                            for message in env.receiver.snapshot()[-8:]
                        ),
                        flush=True,
                    )
                    raise
                prompt_log.append(
                    {
                        "label": label,
                        "kind": kind,
                        "channel": channel,
                        "lines": len(expected),
                        "elapsed_s": round(time.monotonic() - prompt_started, 2),
                    }
                )
                print(
                    f"prompt {label} ({channel}) answered in "
                    f"{time.monotonic() - prompt_started:.1f}s",
                    flush=True,
                )
                time.sleep(8.0)

            elapsed = time.monotonic() - started
            if elapsed < args.min_duration:
                print(
                    f"waiting {args.min_duration - elapsed:.1f}s to reach the "
                    "5-minute minimum run length",
                    flush=True,
                )
                time.sleep(args.min_duration - elapsed)
            time.sleep(8.0)
        finally:
            stop_event.set()
            gateway_thread.join(timeout=30)
            if gateway_thread.is_alive():
                state["thread_alive"] = True
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)
            capture_module.find_overlap = original_find
            capture_module.recover_new_lines = original_recover

        elapsed = time.monotonic() - started
        messages = env.receiver.snapshot()
        tag_counts: dict[str, int] = {}
        tag_first_received: dict[str, float] = {}
        for message in messages:
            for tag in TAG_RE.findall(message.text):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
                tag_first_received.setdefault(tag, message.ts_mono)

        expected_all = {
            tag
            for label, kind, lines, _channel in schedule
            for tag in _expected_tags(run, label, lines, kind)
        }
        expected_lines: dict[str, str] = {}
        for label, kind, lines, _channel in schedule:
            if kind == "long":
                for tag in _expected_tags(run, label, lines, kind):
                    expected_lines[tag] = f"{tag} {label.lower()}"
            else:
                for tag in _expected_tags(run, label, lines, kind):
                    expected_lines[tag] = f"{tag} OK"

        answer_line_counts: dict[str, int] = {
            tag: 0 for tag in expected_all
        }
        duplicate_examples: dict[str, str] = {}
        for message in messages:
            for line in message.text.splitlines():
                clean = re.sub(r"^[●✦❯]\s*", "", line.strip())
                compact = re.sub(r"\s+", "", clean)
                if not compact:
                    continue
                for tag, expected in expected_lines.items():
                    expected_compact = re.sub(r"\s+", "", expected)
                    if compact == expected_compact:
                        answer_line_counts[tag] += 1
                    elif tag in compact and tag not in duplicate_examples:
                        duplicate_examples[tag] = clean[:160]

        received = set(tag_counts)
        lost = sorted(expected_all - received)
        lost_answer_lines = sorted(
            tag for tag in expected_all if answer_line_counts[tag] == 0
        )
        unexpected = sorted(received - expected_all)
        duplicate_tag_mentions = {
            tag: count for tag, count in tag_counts.items() if count > 1
        }
        duplicate_answer_lines = {
            tag: count
            for tag, count in answer_line_counts.items()
            if count > 1
        }
        tag_duplicate_examples = {
            tag: duplicate_examples.get(tag, "")
            for tag in duplicate_tag_mentions
        }
        latencies = sorted(
            tag_first_received[tag] - metrics.tag_first_seen[tag]
            for tag in expected_all
            if tag in tag_first_received and tag in metrics.tag_first_seen
        )
        p50 = percentile(latencies, 0.50)
        p95 = percentile(latencies, 0.95)
        maximum = max(latencies) if latencies else None
        prompt_echo_messages = [
            message
            for message in messages
            if "只输出" in message.text or "只回复" in message.text
        ]

        exit_code = state.get("exit_code")
        passed = (
            not lost_answer_lines
            and not duplicate_answer_lines
            and not unexpected
            and metrics.capture_errors == 0
            and p95 is not None
            and p95 <= 2.0
            and elapsed >= args.min_duration
            and exit_code == 0
        )
        summary = {
            "run_id": run,
            "status": "PASS" if passed else "FAIL",
            "duration_s": round(elapsed, 2),
            "min_duration_s": args.min_duration,
            "gateway_exit_code": exit_code,
            "gateway_thread_alive": state.get("thread_alive", False),
            "metrics": metrics.as_dict(),
            "answers": {
                "expected_tags": len(expected_all),
                "received_tags": len(received),
                "delivered_tags": len(expected_all & received),
                "lost_tag_mentions": lost,
                "lost_answer_lines": lost_answer_lines,
                "duplicate_tag_mentions": duplicate_tag_mentions,
                "duplicate_answer_lines": duplicate_answer_lines,
                "duplicate_examples": tag_duplicate_examples,
                "unexpected_tags": unexpected,
                "prompt_echo_messages": len(prompt_echo_messages),
                "duplicate_explanation": (
                    "duplicate_tag_mentions outside answer lines are model "
                    "preamble/thinking text and are not counted as duplicate "
                    "answer lines; see duplicate_examples"
                ),
            },
            "latency_s": {
                "count": len(latencies),
                "p50": round(p50, 3) if p50 is not None else None,
                "p95": round(p95, 3) if p95 is not None else None,
                "max": round(maximum, 3) if maximum is not None else None,
                "target_p95": 2.0,
            },
            "prompts": prompt_log,
            "total_receiver_messages": len(messages),
        }
        if args.out:
            Path(args.out).write_text(
                json.dumps(summary, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        if args.dump_messages:
            Path(args.dump_messages).write_text(
                json.dumps(
                    [
                        {
                            "ts_mono": round(message.ts_mono, 3),
                            "text": message.text,
                        }
                        for message in messages
                    ],
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
