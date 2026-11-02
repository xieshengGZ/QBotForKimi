"""Fast unit tests for the acceptance harness helpers (ticket 16).

These tests do not run the long acceptance suite, do not require root, and do
not touch the user's tmux server. They exercise config rendering, port
selection, the isolated process environment, fake scripts, the fake NapCat
receiver, and the check registry.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acceptance.fake_napcat import FakeNapcat  # noqa: E402
from acceptance.harness import (  # noqa: E402
    FAKE_KIMICO,
    ONEBOT_TOKEN,
    SERVER_TOKEN,
    AcceptanceEnv,
    answer_lines,
    fake_kimico_command,
    free_port,
    render_config,
    wait_until,
)
from acceptance import run_acceptance  # noqa: E402
from kimico_gateway.config import load_config  # noqa: E402
from kimico_gateway.onebot import send_private_msg  # noqa: E402


def _render(tmp_path: Path, **overrides):
    state_path = tmp_path / "state.json"
    command = fake_kimico_command("plain")
    text = render_config(
        server_port=free_port(),
        receiver_port=free_port(),
        session="kimico-test-session",
        state_path=str(state_path),
        kimico_command=command,
        **overrides,
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(text, encoding="utf-8")
    return text, load_config(str(config_path))


def test_rendered_config_round_trips_through_load_config(tmp_path: Path) -> None:
    text, config = _render(tmp_path)

    assert 'host = "127.0.0.1"' in text
    assert "port = " in text
    assert config.server.access_token == SERVER_TOKEN
    assert config.onebot.access_token == ONEBOT_TOKEN
    assert config.auth.main_qq == 1000000000
    assert config.tmux.session == "kimico-test-session"
    assert config.tmux.kimico_command.endswith("fake_kimico.py plain")
    assert config.state.path == str(tmp_path / "state.json")
    assert config.mirror.echo_prefix_patterns == (r"^\s*[>›❯]\s*",)
    assert "http://127.0.0.1:3000" not in text


def test_free_port_is_currently_bindable() -> None:
    port = free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))
    assert 1024 < port < 65536


def test_fake_kimico_is_executable_and_has_help() -> None:
    mode = FAKE_KIMICO.stat().st_mode
    assert mode & stat.S_IXUSR
    completed = subprocess.run(
        [sys.executable, str(FAKE_KIMICO), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0
    assert "plain" in completed.stdout


def test_fake_napcat_receives_send_private_msg() -> None:
    receiver = FakeNapcat().start()
    try:
        send_private_msg(
            receiver.base_url,
            ONEBOT_TOKEN,
            1000000000,
            "mirror check",
        )
        message = receiver.wait_for(
            lambda item: item.text == "mirror check", 5
        )
        assert message.path == "/send_private_msg"
        assert message.authorization == f"Bearer {ONEBOT_TOKEN}"
        assert message.user_id == 1000000000
    finally:
        receiver.stop()


def test_fake_napcat_wait_for_count() -> None:
    receiver = FakeNapcat().start()
    try:
        for text in ("one", "two", "three"):
            send_private_msg(receiver.base_url, ONEBOT_TOKEN, 1, text)
        messages = receiver.wait_for_count(
            lambda item: True, 3, timeout=5
        )
        assert [item.text for item in messages] == ["one", "two", "three"]
    finally:
        receiver.stop()


def test_process_env_removes_tmux_and_restores_it(monkeypatch) -> None:
    env = AcceptanceEnv(mode="plain")
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    monkeypatch.setenv("TMUX_PANE", "%1")
    try:
        assert os.environ["TMUX"].startswith("/tmp/tmux-1000")
        with env.process_env():
            assert "TMUX" not in os.environ
            assert "TMUX_PANE" not in os.environ
            assert os.environ["TMUX_TMPDIR"] == str(env.tmux_dir)
        assert os.environ["TMUX"].startswith("/tmp/tmux-1000")
    finally:
        env.cleanup()


def test_process_env_hang_shim_is_installed() -> None:
    env = AcceptanceEnv(mode="plain")
    try:
        assert Path(env.real_tmux).is_file()
        shim = env.shim_dir / "tmux"
        assert shim.is_file()
        assert os.access(shim, os.X_OK)
        env.hang_tmux()
        assert env.hang_flag.is_file()
        env.unhang_tmux()
        assert not env.hang_flag.exists()
    finally:
        env.cleanup()


def test_percentile_and_answer_lines_helpers() -> None:
    assert run_acceptance.percentile([], 0.95) is None
    assert run_acceptance.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == 5.0
    assert run_acceptance.percentile([1.0, 2.0, 3.0], 0.5) == 2.0
    assert answer_lines(
        ["banner", "you said: hello", "answer-line 1", "other"]
    ) == ["you said: hello", "answer-line 1"]


def test_next_session_name_is_unique() -> None:
    from acceptance.harness import next_session_name

    names = {next_session_name() for _ in range(20)}
    assert len(names) == 20


def test_check_registry_names_are_unique_and_cover_ticket_items() -> None:
    names = [name for name, _title, _check in run_acceptance.CHECKS]
    assert len(names) == len(set(names))
    required = {
        "base_01_mirror_switch",
        "base_02_injection_echo",
        "base_03_sequential_order",
        "base_04_status",
        "base_05_clear",
        "base_06_long_output",
        "base_07_security",
        "base_08_crash_recovery",
        "base_09_restart_pid",
        "base_10_local_consistency",
        "supp_11_config_permissions",
        "supp_13_start_dead_first_backoff",
        "supp_14_failed_respawn_once",
        "supp_15_concurrent_clear",
        "supp_16_control_priority",
        "supp_17_echo_edges",
        "real_18_kimi_gate",
        "supp_19_concurrent_ingress",
        "supp_20_security_boundaries",
        "supp_21_tmux_hang",
        "supp_22_thread_death",
        "supp_23_state_robustness",
    }
    assert required <= set(names)
    # Acceptance 12 is emitted together with base_06 so the long run is shared.
    assert "supp_12_history_limit" not in names


def test_recovery_contract_is_deterministic_and_fast() -> None:
    evidence = run_acceptance._recovery_contract()
    assert evidence["sleeps"] == [5, 15, 60]
    assert evidence["respawns"] == 3
    assert evidence["startup_sleeps"] == [5]
    assert evidence["notifications"].count(evidence["alarm"]) == 1


def test_results_are_json_serializable() -> None:
    result = run_acceptance.Result(
        "x", "title", "PASS", "evidence", {"elapsed_s": 1.0}
    )
    payload = json.dumps(run_acceptance.asdict(result), ensure_ascii=False)
    assert json.loads(payload)["status"] == "PASS"


def test_wait_until_raises_timeout() -> None:
    started = time.monotonic()
    try:
        wait_until(lambda: False, 0.2, interval=0.01)
    except TimeoutError:
        pass
    else:
        raise AssertionError("wait_until did not time out")
    assert time.monotonic() - started < 2.0
