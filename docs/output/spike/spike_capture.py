#!/usr/bin/env python3
"""Step 0 spike: sample a real kimi TUI via tmux capture-pane every 300ms.

Implements the offline baseline algorithms from docs/prd/15-mod-capture.md
(find_overlap / mask_tail / recover_new_lines / StabilityTracker) and replays
the monitor-thread pipeline against the real TUI.

Usage:
    python3 spike_capture.py --duration 600
Stop early by creating the --stop-file. Writes metrics.jsonl, summary.json and
up to 12 snapshots around k=0 rounds into the output directory.
"""

import argparse
import difflib
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

VOLATILE_SENTINEL = "\x00__volatile__"
TAG_RE = re.compile(r"^\s*(?:[│|]\s*)?(SPIKE-[A-Z]-\d{3})\b")
POLL_MS = 300
CAPTURE_LINES = 5000
STABLE_ROUNDS = 3
K0_REBUILD_THRESHOLD = 3
MASK_NS = (0, 1, 2)


def tmux(socket, *args, timeout=5.0):
    return subprocess.run(
        ["tmux", "-L", socket, *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
    )


def capture_pane(socket, session, lines=CAPTURE_LINES, timeout=5.0):
    proc = tmux(
        socket,
        "capture-pane",
        "-p",
        "-J",
        "-S",
        f"-{lines}",
        "-t",
        f"={session}:",
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:200] or f"exit {proc.returncode}")
    return proc.stdout.splitlines()


def mask_tail(lines, n):
    if n <= 0:
        return list(lines)
    if n >= len(lines):
        return [VOLATILE_SENTINEL] * len(lines)
    return list(lines[:-n]) + [VOLATILE_SENTINEL] * n


def find_overlap(prev, cur):
    if not prev or not cur:
        return 0
    seq = cur + [VOLATILE_SENTINEL] + prev
    pi = [0] * len(seq)
    for i in range(1, len(seq)):
        j = pi[i - 1]
        while j > 0 and seq[i] != seq[j]:
            j = pi[j - 1]
        if seq[i] == seq[j]:
            j += 1
        pi[i] = j
    return min(pi[-1], len(prev), len(cur))


def recover_new_lines(prev, cur):
    if not prev or not cur:
        return []
    sm = difflib.SequenceMatcher(a=prev, b=cur, autojunk=False)
    out = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            out.extend(cur[j1:j2])
    return out


class StabilityTracker:
    def __init__(self, stable_rounds):
        self.stable_rounds = stable_rounds
        self.pending = []

    def feed(self, new_lines):
        block = [line for (line, _) in self.pending] + list(new_lines)
        new_pending = []
        for i, line in enumerate(block):
            if i < len(self.pending) and self.pending[i][0] == line:
                new_pending.append((line, self.pending[i][1] + 1))
            else:
                new_pending.append((line, 1))
        last = max(
            (i for i, (line, _) in enumerate(new_pending) if line.strip() != ""),
            default=-1,
        )
        cut = 0
        for i, (_line, count) in enumerate(new_pending):
            need = self.stable_rounds + 1 if i == last else self.stable_rounds
            if count >= need:
                cut = i + 1
            else:
                break
        released = [line for (line, _) in new_pending[:cut]]
        self.pending = new_pending[cut:]
        return released

    def reset(self):
        self.pending = []


def last_nonempty(lines):
    for line in reversed(lines):
        if line.strip():
            return line
    return ""


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, max(0, int(round((len(values) - 1) * p / 100.0))))
    return values[idx]


def histogram(values, cap=10):
    out = {}
    for v in values:
        key = str(v) if v < cap else f"{cap}+"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: int(kv[0].rstrip("+")) if kv[0][:-1].isdigit() or kv[0].rstrip("+").isdigit() else 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", default="kimico-spike")
    ap.add_argument("--session", default="kimico")
    ap.add_argument("--duration", type=float, default=600)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "run"))
    ap.add_argument("--stop-file", default="/tmp/kimico-spike.stop")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    metrics_path = out / "metrics.jsonl"
    snaps_path = out / "snapshots.jsonl"
    stop_file = Path(args.stop_file)
    if stop_file.exists():
        stop_file.unlink()

    tracker = StabilityTracker(STABLE_ROUNDS)
    prev = None
    k0_streak = 0
    start = time.monotonic()
    round_no = 0
    saved_snaps = 0
    tag_first = {}
    tag_delivered = {}
    errors = 0
    rebuilt_count = 0
    records = []
    snap_file = snaps_path.open("w", encoding="utf-8")
    metrics_file = metrics_path.open("w", encoding="utf-8")

    try:
        while time.monotonic() - start < args.duration and not stop_file.exists():
            round_no += 1
            t_round = time.monotonic() - start
            t0 = time.monotonic()
            try:
                cur = capture_pane(args.socket, args.session)
            except Exception as exc:  # noqa: BLE001 - spike records everything
                errors += 1
                metrics_file.write(json.dumps({"round": round_no, "t": round(t_round, 3), "error": str(exc)}) + "\n")
                metrics_file.flush()
                time.sleep(POLL_MS / 1000.0)
                continue
            capture_ms = (time.monotonic() - t0) * 1000.0

            if prev is None:
                prev = cur
                tracker.reset()
                continue

            ks = {n: find_overlap(mask_tail(prev, n), mask_tail(cur, n)) for n in MASK_NS}
            k = ks[0]
            last_changed = bool(prev) and bool(cur) and prev[-1] != cur[-1]
            last_ne_changed = last_nonempty(prev) != last_nonempty(cur)
            recovered = []
            new_lines = []
            released = []
            rebuilt = False
            if k == 0:
                recovered = recover_new_lines(prev, cur)
                if saved_snaps < 12:
                    snap_file.write(
                        json.dumps(
                            {
                                "round": round_no,
                                "t": round(t_round, 3),
                                "k": {str(n): ks[n] for n in MASK_NS},
                                "prev_tail": prev[-60:],
                                "cur_tail": cur[-60:],
                                "recovered_count": len(recovered),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    saved_snaps += 1
                if recovered:
                    new_lines = recovered
                    k0_streak = 0
                    prev = cur
                    released = tracker.feed(new_lines)
                else:
                    k0_streak += 1
                    if k0_streak >= K0_REBUILD_THRESHOLD:
                        rebuilt = True
                        rebuilt_count += 1
                        tracker.reset()
                        prev = cur
                        k0_streak = 0
            else:
                new_lines = cur[k:]
                k0_streak = 0
                prev = cur
                released = tracker.feed(new_lines)

            for line in cur:
                m = TAG_RE.match(line)
                if m:
                    tag_first.setdefault(m.group(1), round(t_round, 3))
            for line in released:
                m = TAG_RE.match(line)
                if m:
                    tag_delivered.setdefault(m.group(1), round(t_round, 3))

            proc_ms = (time.monotonic() - t0) * 1000.0
            rec = {
                "round": round_no,
                "t": round(t_round, 3),
                "k": {str(n): ks[n] for n in MASK_NS},
                "k0": k == 0,
                "rebuilt": rebuilt,
                "recovered": len(recovered),
                "new_lines": len(new_lines),
                "released": len(released),
                "last_changed": last_changed,
                "last_ne_changed": last_ne_changed,
                "lines": len(cur),
                "capture_ms": round(capture_ms, 2),
                "proc_ms": round(proc_ms, 2),
            }
            records.append(rec)
            metrics_file.write(json.dumps(rec) + "\n")
            metrics_file.flush()

            elapsed = time.monotonic() - start
            next_at = t_round + POLL_MS / 1000.0
            sleep_for = next_at - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        metrics_file.close()
        snap_file.close()

    elapsed = time.monotonic() - start
    k0_ratios = {}
    k_stats = {}
    for n in MASK_NS:
        vals = [r["k"][str(n)] for r in records]
        k0_ratios[str(n)] = round(sum(1 for v in vals if v == 0) / len(vals), 4) if vals else None
        k_stats[str(n)] = {
            "p50": pct(vals, 50),
            "p95": pct(vals, 95),
            "max": max(vals) if vals else None,
            "hist": histogram(vals, cap=12),
        }
    rounding = [r for r in records if r["k0"]]
    recovery_rate = (
        round(sum(1 for r in rounding if r["recovered"] > 0) / len(rounding), 4) if rounding else None
    )
    proc = [r["proc_ms"] for r in records]
    cap = [r["capture_ms"] for r in records]
    intervals = [records[i]["t"] - records[i - 1]["t"] for i in range(1, len(records))]
    first_seen_tags = set(tag_first)
    delivered_tags = set(tag_delivered)
    lost = sorted(tag_first.keys() - tag_delivered.keys())
    latencies = [
        round(tag_delivered[tag] - tag_first[tag], 3) for tag in delivered_tags if tag in tag_first
    ]
    summary = {
        "duration_s": round(elapsed, 2),
        "rounds": len(records),
        "capture_errors": errors,
        "rebuilt_rounds": rebuilt_count,
        "k0_ratio": k0_ratios,
        "k_stats": k_stats,
        "k0_rounds": len(rounding),
        "recover_nonempty_rounds": sum(1 for r in rounding if r["recovered"] > 0),
        "recover_rate": recovery_rate,
        "last_line_changed_ratio": round(
            sum(1 for r in records if r["last_changed"]) / len(records), 4
        )
        if records
        else None,
        "last_nonempty_changed_ratio": round(
            sum(1 for r in records if r["last_ne_changed"]) / len(records), 4
        )
        if records
        else None,
        "proc_ms": {"p50": pct(proc, 50), "p95": pct(proc, 95), "max": max(proc) if proc else None},
        "capture_ms": {"p50": pct(cap, 50), "p95": pct(cap, 95), "max": max(cap) if cap else None},
        "interval_s": {
            "p50": pct(intervals, 50),
            "p95": pct(intervals, 95),
            "max": max(intervals) if intervals else None,
        },
        "tags_seen": len(first_seen_tags),
        "tags_delivered": len(delivered_tags),
        "tags_lost": lost,
        "tag_latency_s": {
            "count": len(latencies),
            "p50": pct(latencies, 50),
            "p95": pct(latencies, 95),
            "max": max(latencies) if latencies else None,
        },
        "tag_first_seen": tag_first,
        "tag_delivered_at": tag_delivered,
    }
    final_snapshot = None
    try:
        final_snapshot = capture_pane(args.socket, args.session)
        (out / "final_capture.txt").write_text("\n".join(final_snapshot), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        summary["final_capture_error"] = str(exc)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not k.startswith("tag_")}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
