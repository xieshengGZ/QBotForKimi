#!/usr/bin/env python3
"""Step 0 v2 spike: redraw-aware capture replay against a real kimi TUI.

Instead of assuming an append-only stream and matching prev suffix to cur
prefix, this version aligns on the stable common prefix and replaces the live
tail. It keeps the old KMP overlap only as a fallback for a slid capture
window, and difflib recovery as a last resort.
"""

import argparse
import json
import time
from pathlib import Path

from spike_capture import TAG_RE, capture_pane, find_overlap, mask_tail, recover_new_lines


def common_prefix(prev, cur):
    n = min(len(prev), len(cur))
    i = 0
    while i < n and prev[i] == cur[i]:
        i += 1
    return i


class RedrawTracker:
    """Pending = unreleased stable-prefix lines + current live tail."""

    def __init__(self, stable_rounds):
        self.stable_rounds = stable_rounds
        self.pending = []
        self.start = 0

    def reset(self):
        self.pending = []
        self.start = 0

    def shift(self, delta):
        if delta <= 0 or not self.pending:
            self.start = max(0, self.start - delta)
            return
        drop = max(0, delta - self.start)
        self.start = max(0, self.start - delta)
        self.pending = self.pending[drop:]
        if not self.pending:
            self.start = 0

    def sync(self, cur, cp):
        if not self.pending and self.start > cp:
            self.start = cp
        kept = []
        for j, (line, count) in enumerate(self.pending):
            pos = self.start + j
            if pos < cp and pos < len(cur) and cur[pos] == line:
                kept.append((line, count + 1))
            else:
                break
        if not kept:
            self.start = cp
        block = kept + [(line, 1) for line in cur[cp:]]
        last = max((i for i, (line, _) in enumerate(block) if line.strip() != ""), default=-1)
        cut = 0
        for i, (_line, count) in enumerate(block):
            need = self.stable_rounds + 1 if i == last else self.stable_rounds
            if count >= need:
                cut = i + 1
            else:
                break
        released = [line for (line, _) in block[:cut]]
        self.pending = block[cut:]
        self.start += cut
        return released


def histogram(values, cap=12):
    out = {}
    for v in values:
        key = str(v) if v < cap else f"{cap}+"
        out[key] = out.get(key, 0) + 1
    return out


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int(round((len(values) - 1) * p / 100.0))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", default="kimico-spike")
    ap.add_argument("--session", default="kimico")
    ap.add_argument("--duration", type=float, default=600)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "run-v2"))
    ap.add_argument("--stop-file", default="/tmp/kimico-spike-v2.stop")
    ap.add_argument("--stable-rounds", type=int, default=3)
    ap.add_argument("--mode", choices=("redraw", "legacy"), default="redraw")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stop_file = Path(args.stop_file)
    if stop_file.exists():
        stop_file.unlink()
    metrics_file = (out / "metrics.jsonl").open("w", encoding="utf-8")
    snap_file = (out / "snapshots.jsonl").open("w", encoding="utf-8")

    tracker = RedrawTracker(args.stable_rounds)
    prev = None
    start = time.monotonic()
    tag_first = {}
    tag_delivered = {}
    records = []
    saved = 0
    errors = 0
    fallbacks = {"slide": 0, "recover": 0, "reset": 0, "none": 0}

    try:
        while time.monotonic() - start < args.duration and not stop_file.exists():
            t = time.monotonic() - start
            t0 = time.monotonic()
            try:
                cur = capture_pane(args.socket, args.session)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                metrics_file.write(json.dumps({"t": round(t, 3), "error": str(exc)}) + "\n")
                metrics_file.flush()
                time.sleep(0.3)
                continue
            capture_ms = (time.monotonic() - t0) * 1000.0
            if prev is None:
                prev = cur
                tracker.reset()
                continue

            for line in cur:
                m = TAG_RE.match(line)
                if m:
                    tag_first.setdefault(m.group(1), round(t, 3))

            cp = common_prefix(prev, cur)
            k = find_overlap(mask_tail(prev, 0), mask_tail(cur, 0))
            fallback = "none"
            released = []
            if args.mode == "redraw":
                if cp >= 5 and (not tracker.pending or cp >= tracker.start):
                    released = tracker.sync(cur, cp)
                elif k > 0:
                    shift = len(prev) - k
                    tracker.shift(shift)
                    released = tracker.sync(cur, k)
                    fallback = "slide"
                else:
                    recovered = recover_new_lines(prev, cur)
                    fallback = "recover" if recovered else "reset"
                    tracker.reset()
            else:
                if k > 0:
                    released = tracker.sync(cur, k)
                else:
                    recovered = recover_new_lines(prev, cur)
                    if recovered:
                        released = recovered
                    fallback = "recover" if recovered else "reset"

            fallbacks[fallback] += 1
            for line in released:
                m = TAG_RE.match(line)
                if m:
                    tag_delivered.setdefault(m.group(1), round(t, 3))

            prev = cur
            proc_ms = (time.monotonic() - t0) * 1000.0
            rec = {
                "t": round(t, 3),
                "cp": cp,
                "k": k,
                "mode": args.mode if fallback == "none" else f"{args.mode}:{fallback}",
                "lines": len(cur),
                "released": len(released),
                "live_tail": max(0, len(cur) - cp),
                "capture_ms": round(capture_ms, 2),
                "proc_ms": round(proc_ms, 2),
            }
            records.append(rec)
            metrics_file.write(json.dumps(rec) + "\n")
            metrics_file.flush()
            if fallback != "none" and saved < 12:
                snap_file.write(
                    json.dumps({"t": round(t, 3), "fallback": fallback, "cp": cp, "k": k, "prev_tail": prev[-40:], "cur_tail": cur[-40:]}, ensure_ascii=False)
                    + "\n"
                )
                saved += 1
            elapsed = time.monotonic() - start
            sleep_for = (t + 0.3) - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        metrics_file.close()
        snap_file.close()

    elapsed = time.monotonic() - start
    cp_vals = [r["cp"] for r in records]
    proc = [r["proc_ms"] for r in records]
    cap = [r["capture_ms"] for r in records]
    intervals = [records[i]["t"] - records[i - 1]["t"] for i in range(1, len(records))]
    seen = set(tag_first)
    delivered = set(tag_delivered)
    lat = sorted(round(tag_delivered[t] - tag_first[t], 3) for t in delivered if t in tag_first)
    summary = {
        "algorithm": "redraw-aware stable-prefix + live-tail sync",
        "mode": args.mode,
        "stable_rounds": args.stable_rounds,
        "duration_s": round(elapsed, 2),
        "rounds": len(records),
        "capture_errors": errors,
        "fallbacks": fallbacks,
        "cp": {"p50": pct(cp_vals, 50), "p95": pct(cp_vals, 95), "max": max(cp_vals) if cp_vals else None, "hist": histogram(cp_vals)},
        "released_total": sum(r["released"] for r in records),
        "proc_ms": {"p50": pct(proc, 50), "p95": pct(proc, 95), "max": max(proc) if proc else None},
        "capture_ms": {"p50": pct(cap, 50), "p95": pct(cap, 95), "max": max(cap) if cap else None},
        "interval_s": {"p50": pct(intervals, 50), "p95": pct(intervals, 95), "max": max(intervals) if intervals else None},
        "tags_seen": len(seen),
        "tags_delivered": len(delivered),
        "tags_lost": sorted(seen - delivered),
        "tag_latency_s": {"count": len(lat), "p50": pct(lat, 50), "p95": pct(lat, 95), "max": max(lat) if lat else None},
        "tag_first_seen": tag_first,
        "tag_delivered_at": tag_delivered,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if not k.startswith("tag_")}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
