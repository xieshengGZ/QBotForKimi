#!/usr/bin/env python3
"""Probe: why streamed answers are lost, and whether pipe-pane sees them.

Captures full pane snapshots every 300ms during one 40-line answer, compares
consecutive frames with find_overlap / recover_new_lines, and counts the same
tags in the raw tmux pipe-pane byte stream.
"""

import json
import re
import subprocess
import time
from pathlib import Path

from spike_capture import find_overlap, mask_tail, recover_new_lines

SOCKET = "kimico-spike"
SESSION = "kimico"
HERE = Path(__file__).resolve().parent
OUT = HERE / "run"
RAW = Path("/tmp/kimico-pipe-raw.log")
TAG_RE = re.compile(r"SPIKE-G-\d{3}")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]")


def tmux(*args, timeout=10):
    return subprocess.run(
        ["tmux", "-L", SOCKET, *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
    )


def cap():
    proc = tmux("capture-pane", "-p", "-J", "-S", "-5000", "-t", f"={SESSION}:")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout.splitlines()


def main():
    RAW.unlink(missing_ok=True)
    tmux("pipe-pane", "-t", SESSION)
    tmux("pipe-pane", "-O", "-t", SESSION, f"cat > {RAW}")
    time.sleep(0.5)
    prompt = (
        "请只输出一个代码块，共 40 行。第 1 行以 SPIKE-G-001 开头，最后一行以 SPIKE-G-040 结尾，"
        "每行格式为 SPIKE-G-XXX golf，其中 XXX 是三位递增编号。代码块外不要写任何内容。"
    )
    frames = []
    start = time.monotonic()
    sent = False
    while time.monotonic() - start < 22:
        t = time.monotonic() - start
        frames.append({"t": round(t, 3), "lines": cap()})
        if not sent and t > 1.5:
            tmux("send-keys", "-l", "-t", SESSION, "--", prompt)
            time.sleep(0.8)
            tmux("send-keys", "-t", SESSION, "C-m")
            sent = True
        time.sleep(0.3)
    tmux("pipe-pane", "-t", SESSION)
    time.sleep(1.5)

    pairs = []
    for prev, cur in zip(frames, frames[1:]):
        k = find_overlap(prev["lines"], cur["lines"])
        recovered = recover_new_lines(prev["lines"], cur["lines"]) if k == 0 else []
        pairs.append(
            {
                "t": cur["t"],
                "prev_lines": len(prev["lines"]),
                "cur_lines": len(cur["lines"]),
                "k": k,
                "k0": k == 0,
                "recovered": len(recovered),
                "prev_prefix": prev["lines"][:3],
                "cur_prefix": cur["lines"][:3],
                "prev_is_prefix": cur["lines"][: len(prev["lines"])] == prev["lines"],
            }
        )

    raw_bytes = RAW.read_bytes() if RAW.exists() else b""
    raw_text = raw_bytes.decode("utf-8", "replace")
    plain = ANSI_RE.sub("", raw_text)
    tags_raw = sorted(set(TAG_RE.findall(plain)))
    tags_raw_seen_at = {}
    pos = 0
    for tag in TAG_RE.findall(plain):
        if tag not in tags_raw_seen_at:
            tags_raw_seen_at[tag] = pos
        pos += 1
    final_tags = sorted(set(TAG_RE.findall("\n".join(frames[-1]["lines"]))))
    result = {
        "frames": len(frames),
        "send_t": next((p["t"] for p in pairs if p["prev_lines"] != p["cur_lines"]), None),
        "k0_pairs": sum(1 for p in pairs if p["k0"]),
        "recovered_pairs": sum(1 for p in pairs if p["recovered"] > 0),
        "prev_is_prefix_pairs": sum(1 for p in pairs if p["prev_is_prefix"]),
        "pairs": pairs,
        "final_tags": len(final_tags),
        "raw_bytes": len(raw_bytes),
        "raw_text_bytes_after_ansi_strip": len(plain),
        "raw_tags_unique": len(tags_raw),
        "raw_has_ansi_cursor_moves": bool(re.search(r"\x1b\[[0-9;]*[HfABCDJK]", raw_text)),
        "raw_has_erase": bool(re.search(r"\x1b\[[0-9;]*[JK]", raw_text)),
        "tags_missing_from_final": sorted(set(f"SPIKE-G-{i:03d}" for i in range(1, 41)) - set(final_tags)),
        "tags_missing_from_raw": sorted(set(f"SPIKE-G-{i:03d}" for i in range(1, 41)) - set(tags_raw)),
    }
    (OUT / "probe-stream.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "probe-final-capture.txt").write_text("\n".join(frames[-1]["lines"]), encoding="utf-8")
    for key in (
        "frames",
        "k0_pairs",
        "recovered_pairs",
        "prev_is_prefix_pairs",
        "final_tags",
        "raw_bytes",
        "raw_tags_unique",
        "raw_has_ansi_cursor_moves",
        "raw_has_erase",
        "tags_missing_from_final",
        "tags_missing_from_raw",
    ):
        print(key, "=", result[key])
    print("first pairs with line growth:")
    for p in [p for p in pairs if p["cur_lines"] != p["prev_lines"]][:8]:
        print(p["t"], "lines", p["prev_lines"], "->", p["cur_lines"], "k", p["k"], "rec", p["recovered"], "prefix", p["prev_is_prefix"])


if __name__ == "__main__":
    main()
