#!/usr/bin/env python3
"""Replay saved real-TUI frames against candidate capture algorithms.

Algorithm under test: keep the stable common prefix between consecutive
captures, replace the live tail, and release lines that stop changing.
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRAMES = HERE / "frames" / "frames.jsonl"
TAG_RE = re.compile(r"(SPIKE-H-\d{3})")


def common_prefix(prev, cur):
    n = min(len(prev), len(cur))
    i = 0
    while i < n and prev[i] == cur[i]:
        i += 1
    return i


class RedrawTracker:
    """Pending block = stable-prefix lines not yet released + current live tail."""

    def __init__(self, stable_rounds=3):
        self.stable_rounds = stable_rounds
        self.pending = []  # list[(line, count)]
        self.start = None  # absolute index of pending[0] in the last capture

    def reset(self):
        self.pending = []
        self.start = None

    def sync(self, cur, cp):
        if self.start is None:
            self.start = cp
        kept = []
        # Keep only pending lines that are before cp and still identical.
        for j, (line, count) in enumerate(self.pending):
            pos = self.start + j
            if pos < cp and pos < len(cur) and cur[pos] == line:
                kept.append((line, count + 1))
            else:
                break
        if not kept:
            self.start = cp
        new_tail = [(line, 1) for line in cur[cp:]]
        block = kept + new_tail
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


def replay(frames, stable_rounds=3):
    tracker = RedrawTracker(stable_rounds)
    prev = frames[0]["lines"]
    prev_cp = 0
    tracker.reset()
    tracker.start = 0
    delivered = []
    tag_at = {}
    falls = 0
    for frame in frames[1:]:
        cur = frame["lines"]
        cp = common_prefix(prev, cur)
        # If the capture scrolled (common prefix collapsed), fall back to a
        # full reset for this replay; count it.
        if cp < prev_cp and cp < 5:
            falls += 1
            tracker.reset()
            tracker.start = 0
            prev, prev_cp = cur, len(cur)
            continue
        released = tracker.sync(cur, cp)
        for line in released:
            delivered.append((frame["t"], line))
            m = TAG_RE.match(line.strip())
            if m:
                tag_at.setdefault(m.group(1), frame["t"])
        prev, prev_cp = cur, cp
    return delivered, tag_at, falls


def main():
    frames = [json.loads(line) for line in FRAMES.read_text().splitlines() if line.strip()]
    final_tags = sorted(set(TAG_RE.findall("\n".join(frames[-1]["lines"]))))
    first_seen = {}
    for frame in frames:
        for line in frame["lines"]:
            m = TAG_RE.match(line.strip())
            if m:
                first_seen.setdefault(m.group(1), frame["t"])
    for rounds in (1, 2, 3, 4):
        delivered, tag_at, falls = replay(frames, rounds)
        missing = sorted(set(final_tags) - set(tag_at))
        dup = len([1 for _, line in delivered if TAG_RE.match(line.strip())]) - len(tag_at)
        lat = sorted(round(tag_at[t] - first_seen[t], 3) for t in tag_at if t in first_seen)
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None
        print(
            f"stable_rounds={rounds}: delivered_lines={len(delivered)} "
            f"tags_delivered={len(tag_at)}/{len(final_tags)} missing={len(missing)} "
            f"tag_duplicates={dup} falls={falls} "
            f"latency p50={lat[len(lat)//2] if lat else None} p95={p95} max={lat[-1] if lat else None}"
        )
        if missing:
            print("  missing:", missing[:10])
    delivered, tag_at, falls = replay(frames, 3)
    for t, line in delivered:
        if TAG_RE.match(line.strip()):
            print("first tag line:", t, line.strip()[:80])
            break


if __name__ == "__main__":
    main()
