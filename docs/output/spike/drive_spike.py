#!/usr/bin/env python3
"""Drive the real kimi TUI for the Step 0 capture spike.

Sends alternating short and long prompts, performs a bracketed multi-line
paste test (tmux paste-buffer -p -r), and logs driver events as JSONL.
"""

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

SOCKET = "kimico-spike"
SESSION = "kimico"
HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("SPIKE_OUT", str(HERE / "run")))
EVENTS = OUT / "driver-events.jsonl"
PASTE_FILE = Path("/tmp/kimico-spike-paste.txt")


def tmux(*args, timeout=10):
    return subprocess.run(
        ["tmux", "-L", SOCKET, *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
    )


def cap(lines=200):
    proc = tmux("capture-pane", "-p", "-J", "-S", f"-{lines}", "-t", f"={SESSION}:")
    return proc.stdout.splitlines() if proc.returncode == 0 else []


def log(label, action, detail=None):
    rec = {"ts": round(time.time(), 3), "label": label, "action": action}
    if detail is not None:
        rec["detail"] = detail
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def input_box():
    lines = cap(200)
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*[│|]\s*>", line):
            start = i
    if start is None:
        return None
    out = []
    for line in lines[start:]:
        if re.match(r"^\s*[╰+]", line):
            break
        out.append(line)
    return "\n".join(out)


def box_first_line():
    box = input_box()
    if not box:
        return ""
    first = box.splitlines()[0]
    m = re.match(r"^\s*[│|]\s*>\s*(.*?)\s*[│|]?\s*$", first)
    return (m.group(1) if m else "").strip()


def stable(seconds=8, timeout=300):
    deadline = time.monotonic() + timeout
    last = None
    since = time.monotonic()
    while time.monotonic() < deadline:
        text = "\n".join(cap(400))
        digest = hashlib.md5(text.encode("utf-8", "replace")).hexdigest()
        now = time.monotonic()
        if digest != last:
            last = digest
            since = now
        elif now - since >= seconds:
            return True
        time.sleep(1)
    return False


def submit():
    for _ in range(4):
        tmux("send-keys", "-t", SESSION, "C-m")
        time.sleep(2)
        if not box_first_line():
            return True
    return False


def send_prompt(label, text, settle=8, timeout=300):
    stable(settle, timeout=120)
    log(label, "send", text[:120])
    tmux("send-keys", "-l", "-t", SESSION, "--", text)
    time.sleep(0.8)
    ok = submit()
    log(label, "submitted" if ok else "submit_failed", {"box": box_first_line()[:120]})
    done = stable(settle, timeout=timeout)
    log(label, "settled" if done else "timeout")
    return done


def paste_test(label):
    stable(8, timeout=120)
    text = (
        "请原样重复以下三行，每行一次，不要添加任何其他内容：\n"
        "PASTE-ALPHA\nPASTE-BETA\nPASTE-GAMMA\n"
    )
    PASTE_FILE.write_text(text, encoding="utf-8")
    tmux("load-buffer", "-b", "spike", str(PASTE_FILE))
    tmux("paste-buffer", "-p", "-r", "-b", "spike", "-t", SESSION)
    time.sleep(1.5)
    box = input_box()
    log(label, "pasted", box)
    lines_in_box = [ln for ln in (box or "").splitlines() if ln.strip()]
    log(label, "box_line_count", len(lines_in_box))
    ok = submit()
    log(label, "submitted" if ok else "submit_failed")
    done = stable(8, timeout=300)
    transcript = "\n".join(cap(5000))
    counts = {tag: transcript.count(tag) for tag in ("PASTE-ALPHA", "PASTE-BETA", "PASTE-GAMMA")}
    user_echoes = transcript.count("❯ 请原样重复以下三行")
    log(label, "settled" if done else "timeout", {"counts": counts, "user_echoes": user_echoes})
    return done


def main():
    EVENTS.unlink(missing_ok=True)
    send_prompt(
        "A",
        "请只输出一个代码块，共 80 行。第 1 行以 SPIKE-A-001 开头，最后一行以 SPIKE-A-080 结尾，"
        "每行格式为 SPIKE-A-XXX alpha，其中 XXX 是三位递增编号。代码块外不要写任何内容。",
        timeout=300,
    )
    time.sleep(40)
    send_prompt("B", "请只回复：SPIKE-B-OK", timeout=180)
    time.sleep(25)
    send_prompt(
        "C",
        "请只输出一个代码块，共 60 行。第 1 行以 SPIKE-C-001 开头，最后一行以 SPIKE-C-060 结尾，"
        "每行格式为 SPIKE-C-XXX charlie，其中 XXX 是三位递增编号。代码块外不要写任何内容。",
        timeout=300,
    )
    time.sleep(25)
    paste_test("P")
    time.sleep(25)
    send_prompt("D", "请只回复：SPIKE-D-OK", timeout=180)
    time.sleep(20)
    send_prompt(
        "E",
        "请只输出一个代码块，共 70 行。第 1 行以 SPIKE-E-001 开头，最后一行以 SPIKE-E-070 结尾，"
        "每行格式为 SPIKE-E-XXX echo，其中 XXX 是三位递增编号。代码块外不要写任何内容。",
        timeout=300,
    )
    time.sleep(30)
    send_prompt("F", "请只回复：SPIKE-F-OK", timeout=180)
    time.sleep(20)
    log("all", "done")
    Path("/tmp/kimico-spike.stop").write_text("done", encoding="utf-8")
    print("driver done")


if __name__ == "__main__":
    main()
