#!/usr/bin/env python3
"""Capture full pane frames during one streamed answer and analyze redraws.

Saves every 300ms frame to frames.jsonl so candidate diff algorithms can be
replayed offline against the same real-TUI data.
"""

import difflib
import json
import subprocess
import time
from pathlib import Path

from spike_capture import find_overlap, mask_tail, recover_new_lines

SOCKET = "kimico-spike"
SESSION = "kimico"
HERE = Path(__file__).resolve().parent
OUT = HERE / "frames"
FRAMES = OUT / "frames.jsonl"
TAG_RE = __import__("re").compile(r"SPIKE-H-\d{3}")


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
    OUT.mkdir(parents=True, exist_ok=True)
    FRAMES.unlink(missing_ok=True)
    prompt = (
        "请只输出一个代码块，共 60 行。第 1 行以 SPIKE-H-001 开头，最后一行以 SPIKE-H-060 结尾，"
        "每行格式为 SPIKE-H-XXX hotel，其中 XXX 是三位递增编号。代码块外不要写任何内容。"
    )
    start = time.monotonic()
    sent = False
    n = 0
    with FRAMES.open("w", encoding="utf-8") as fh:
        while time.monotonic() - start < 24:
            t = time.monotonic() - start
            lines = cap()
            n += 1
            fh.write(json.dumps({"t": round(t, 3), "lines": lines}, ensure_ascii=False) + "\n")
            fh.flush()
            if not sent and t > 1.5:
                tmux("send-keys", "-l", "-t", SESSION, "--", prompt)
                time.sleep(0.8)
                tmux("send-keys", "-t", SESSION, "C-m")
                sent = True
            time.sleep(0.3)

    frames = [json.loads(line) for line in FRAMES.read_text().splitlines() if line.strip()]
    analysis = []
    for prev_f, cur_f in zip(frames, frames[1:]):
        prev, cur = prev_f["lines"], cur_f["lines"]
        k = find_overlap(prev, cur)
        k1 = find_overlap(mask_tail(prev, 1), mask_tail(cur, 1))
        recovered = recover_new_lines(prev, cur) if k == 0 else []
        cp = 0
        while cp < min(len(prev), len(cur)) and prev[cp] == cur[cp]:
            cp += 1
        cs = 0
        while cs < min(len(prev), len(cur)) and prev[-1 - cs] == cur[-1 - cs]:
            cs += 1
        sm = difflib.SequenceMatcher(a=prev, b=cur, autojunk=False)
        blocks = sorted(sm.get_matching_blocks()[:-1], key=lambda b: b.size, reverse=True)[:6]
        ops = [(tag, i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
        analysis.append(
            {
                "t": cur_f["t"],
                "lens": [len(prev), len(cur)],
                "k": k,
                "k1": k1,
                "recovered": len(recovered),
                "prefix_eq": cur[: len(prev)] == prev,
                "cp": cp,
                "cs": cs,
                "opcodes": ops[:12],
                "blocks": [
                    {"a": b.a, "b": b.b, "size": b.size} for b in blocks
                ],
                "prev_text_at_cp": prev[cp] if cp < len(prev) else None,
                "cur_text_at_cp": cur[cp] if cp < len(cur) else None,
                "prev_text_at_end_cs": prev[len(prev) - 1 - cs] if cs < len(prev) else None,
                "cur_text_at_end_cs": cur[len(cur) - 1 - cs] if cs < len(cur) else None,
            }
        )

    growth = [a for a in analysis if a["lens"][1] != a["lens"][0]]
    (OUT / "analysis.json").write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    print("frames", len(frames), "growth pairs", len(growth))
    print("tag counts per frame (first/last):", len(set(TAG_RE.findall("\n".join(frames[0]["lines"])))), len(set(TAG_RE.findall("\n".join(frames[-1]["lines"])))))
    for a in growth[:10]:
        print(
            "t", a["t"], "lens", a["lens"], "k", a["k"], "k1", a["k1"], "rec", a["recovered"],
            "prefix_eq", a["prefix_eq"], "cp", a["cp"], "cs", a["cs"],
        )
        print("  first diff prev:", repr((a["prev_text_at_cp"] or "")[:100]))
        print("  first diff cur :", repr((a["cur_text_at_cp"] or "")[:100]))
        print("  blocks:", a["blocks"])
        print("  ops:", a["opcodes"][:6])


if __name__ == "__main__":
    main()
