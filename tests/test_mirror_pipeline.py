"""Tests for the mirror pipeline, echo suppression, batching, and chunking (11)."""

from __future__ import annotations

import re
import threading
import time

import pytest

from kimico_gateway.mirror import (
    EchoSuppressor,
    MirrorPipeline,
    MirrorSwitch,
    OutboundBus,
    OutputEpoch,
    chunk_lines,
    compile_patterns,
    normalize,
    passes_filters,
)

DEFAULT_PATTERNS = compile_patterns([r"^\s*$"])
PROMPT_PATTERNS = compile_patterns([r"^>\s*"])


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_pipeline(
    *,
    mirror_on: bool = True,
    patterns: list[re.Pattern[str]] | None = None,
    prefix_patterns: list[re.Pattern[str]] | None = None,
    echo: EchoSuppressor | None = None,
    chunk_chars: int = 100,
    batch_quiet_ms: int = 300,
    batch_max_chars: int = 1000,
    bulk_max: int = 50,
) -> tuple[MirrorPipeline, MirrorSwitch, OutputEpoch, EchoSuppressor, OutboundBus]:
    switch = MirrorSwitch(mirror_on)
    epoch = OutputEpoch()
    if echo is None:
        echo = EchoSuppressor(prefix_patterns=prefix_patterns or [])
    bus = OutboundBus(bulk_max=bulk_max, overflow_notice="[overflow]")
    pipeline = MirrorPipeline(
        switch,
        epoch,
        echo,
        bus,
        DEFAULT_PATTERNS if patterns is None else patterns,
        chunk_chars,
        batch_quiet_ms,
        batch_max_chars,
    )
    return pipeline, switch, epoch, echo, bus


def drain(bus: OutboundBus) -> list[str]:
    texts: list[str] = []
    while bus.qsize_bulk():
        item = bus.get(timeout=0.01)
        assert item is not None
        texts.append(item.text)
    return texts


# --- normalize --------------------------------------------------------------


def test_normalize_strips_lines_and_folds_whitespace() -> None:
    text = "  hello   world \n\n\t foo    bar  \n"

    assert normalize(text) == "hello world foo bar"


def test_normalize_empty_input() -> None:
    assert normalize("") == ""
    assert normalize(" \n\t\n ") == ""


# --- echo suppression -------------------------------------------------------


def test_echo_register_match_consumes_the_entry_once() -> None:
    echo = EchoSuppressor()
    echo.register("hello")

    assert echo.suppress(["hello"]) == []
    assert echo.suppress(["hello"]) == ["hello"]


def test_echo_line_level_keeps_other_lines_in_order() -> None:
    echo = EchoSuppressor()
    echo.register("injected")

    lines = echo.suppress(["first answer", "injected", "second answer"])

    assert lines == ["first answer", "second answer"]


def test_echo_prompt_prefix_is_stripped_before_comparing() -> None:
    echo = EchoSuppressor(prefix_patterns=PROMPT_PATTERNS)
    echo.register("测试消息")

    assert echo.suppress(["> 测试消息"]) == []


def test_echo_unmatched_block_consumes_nothing() -> None:
    echo = EchoSuppressor()
    echo.register("expected")

    assert echo.suppress(["a different answer"]) == ["a different answer"]
    assert echo.suppress(["expected"]) == []


def test_echo_no_partial_match() -> None:
    echo = EchoSuppressor()
    echo.register("the whole message")

    assert echo.suppress(["the whole"]) == ["the whole"]
    assert echo.suppress(["the whole message"]) == []


def test_echo_fifo_two_identical_injections_consume_one_line_each() -> None:
    echo = EchoSuppressor()
    echo.register("same")
    echo.register("same")

    assert echo.suppress(["same", "same", "same"]) == ["same"]
    assert echo.suppress(["same"]) == ["same"]


def test_echo_compares_only_the_oldest_entry() -> None:
    echo = EchoSuppressor()
    echo.register("blocking")
    echo.register("target")

    assert echo.suppress(["target"]) == ["target"]
    assert echo.suppress(["blocking"]) == []
    assert echo.suppress(["target"]) == []


def test_echo_entries_expire_after_ten_seconds() -> None:
    clock = FakeClock()
    echo = EchoSuppressor(clock=clock)
    echo.register("hello")

    clock.now = 11.0

    assert echo.suppress(["hello"]) == ["hello"]


def test_echo_expired_entries_are_pruned_on_register() -> None:
    clock = FakeClock()
    echo = EchoSuppressor(clock=clock)
    echo.register("old")

    clock.now = 11.0
    echo.register("new")

    # If "old" had survived, it would still sit at the head after "new" is
    # consumed and would then suppress the "old" line.
    assert echo.suppress(["new", "old"]) == ["old"]


def test_echo_entries_are_capped_at_fifty() -> None:
    echo = EchoSuppressor(max_entries=50)
    for index in range(51):
        echo.register(f"message-{index}")

    lines = [f"message-{index}" for index in range(51)]

    # The oldest entry was dropped: its line is the only survivor.
    assert echo.suppress(lines) == ["message-0"]


def test_echo_false_positive_tradeoff_removes_identical_answer_line() -> None:
    """Documented trade-off: an answer line equal to the injected text is lost.

    Echo matching is line based so that only the echoed line is removed, but a
    legitimate answer line that happens to equal the injected text cannot be
    distinguished from the echo. The implementation prefers de-duplicating the
    echo over risking a duplicated message; the block itself is never dropped.
    """
    echo = EchoSuppressor()
    echo.register("the answer is 42")

    lines = echo.suppress(["the answer is 42", "but check the logs"])

    assert lines == ["but check the logs"]


# --- filters ----------------------------------------------------------------


def test_default_filter_drops_blank_lines_only() -> None:
    lines = ["  ", "\t", "answer one", "", "answer two"]

    kept = [line for line in lines if passes_filters(line, DEFAULT_PATTERNS)]

    assert kept == ["answer one", "answer two"]


def test_ignore_patterns_use_search_semantics() -> None:
    patterns = compile_patterns([r"^\s*$", r"traceback"])

    assert passes_filters("noise", patterns) is True
    assert passes_filters("a traceback appears mid-line", patterns) is False
    # re.search is case sensitive; a differently cased line passes.
    assert passes_filters("Traceback (most recent call last):", patterns) is True


# --- chunking ---------------------------------------------------------------


def test_chunk_aggregates_lines_with_newlines_counted() -> None:
    assert chunk_lines(["aaa", "bbb"], 10) == ["aaa\nbbb"]
    assert chunk_lines(["ab", "cd"], 4) == ["ab", "cd"]
    assert chunk_lines(["ab", "cd"], 5) == ["ab\ncd"]


def test_chunk_exact_limit_is_not_hard_split() -> None:
    assert chunk_lines(["12345"], 5) == ["12345"]
    assert chunk_lines(["12", "345"], 6) == ["12\n345"]


def test_chunk_hard_splits_only_an_overlong_line() -> None:
    line = "x" * 25

    chunks = chunk_lines([line], 10)

    assert chunks == ["x" * 10, "x" * 10, "x" * 5]
    assert all(len(chunk) <= 10 for chunk in chunks)


def test_chunk_flushes_the_current_chunk_before_an_overlong_line() -> None:
    chunks = chunk_lines(["abc", "1234567890"], 5)

    assert chunks == ["abc", "12345", "67890"]


def test_chunk_preserves_order_and_boundaries() -> None:
    lines = ["one", "two", "three", "four"]

    chunks = chunk_lines(lines, 8)

    assert chunks == ["one\ntwo", "three", "four"]


def test_chunk_empty_input() -> None:
    assert chunk_lines([], 10) == []


def test_chunk_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        chunk_lines(["x"], 0)


# --- mirror switch and epoch ------------------------------------------------


def test_switch_calls_on_change_every_time_outside_the_lock() -> None:
    seen: list[bool] = []
    switch: MirrorSwitch

    def on_change(value: bool) -> None:
        seen.append(value)
        seen.append(switch.is_on())

    switch = MirrorSwitch(True, on_change)

    switch.set(True)
    switch.set(False)
    switch.set(False)

    assert seen == [True, True, False, False, False, False]
    assert switch.is_on() is False


def test_switch_callback_errors_do_not_change_the_switch() -> None:
    def on_change(value: bool) -> None:
        raise RuntimeError("state store unavailable")

    switch = MirrorSwitch(False, on_change)

    switch.set(True)

    assert switch.is_on() is True


def test_epoch_bump_is_monotonic_and_thread_safe() -> None:
    epoch = OutputEpoch()
    assert epoch.current() == 0
    assert epoch.bump() == 1
    assert epoch.bump() == 2

    results: list[int] = []

    def worker() -> None:
        for _ in range(100):
            results.append(epoch.bump())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2.0)

    assert epoch.current() == 802
    assert sorted(results) == list(range(3, 803))


# --- pipeline ---------------------------------------------------------------


def test_pipeline_mirror_off_drops_lines_without_touching_echo_or_bus() -> None:
    pipeline, _switch, _epoch, echo, bus = make_pipeline(mirror_on=False)
    echo.register("hello")

    pipeline.handle_stable_lines(["hello"], 0)
    pipeline.flush_if_due(now=time.monotonic() + 3600)

    assert drain(bus) == []
    # The short circuit happens before echo handling: the entry is still there.
    assert echo.suppress(["hello"]) == []


def test_pipeline_drops_stale_generation_before_filter_echo_and_batch() -> None:
    pipeline, _switch, _epoch, echo, bus = make_pipeline()
    echo.register("hello")

    pipeline.handle_stable_lines(["hello"], 1)
    pipeline.handle_stable_lines(["stale line"], 1)
    pipeline.handle_stable_lines(["hello"], 0)
    pipeline.handle_stable_lines(["fresh line"], 0)
    pipeline.flush_if_due(now=time.monotonic() + 3600)

    assert drain(bus) == ["fresh line"]


def test_pipeline_filters_before_echo_and_produces_no_batch_for_empty_block() -> None:
    patterns = compile_patterns([r"^\s*$", r"^#"])
    pipeline, _switch, _epoch, echo, bus = make_pipeline(patterns=patterns)
    echo.register("secret")

    pipeline.handle_stable_lines(["   ", "# secret"], 0)
    pipeline.flush_if_due(now=time.monotonic() + 3600)

    assert drain(bus) == []
    # Filtered lines never reached echo, so the registered entry is untouched.
    assert echo.suppress(["secret"]) == []


def test_pipeline_echo_removes_only_the_matching_line() -> None:
    pipeline, _switch, _epoch, echo, bus = make_pipeline()
    echo.register("secret")

    pipeline.handle_stable_lines(["secret", "keep me"], 0)
    pipeline.flush_if_due(now=time.monotonic() + 3600)

    assert drain(bus) == ["keep me"]


def test_pipeline_empty_result_after_echo_produces_no_batch() -> None:
    pipeline, _switch, _epoch, echo, bus = make_pipeline()
    echo.register("echoed")

    pipeline.handle_stable_lines(["echoed"], 0)
    pipeline.flush_if_due(now=time.monotonic() + 3600)

    assert drain(bus) == []


def test_pipeline_waits_for_quiet_window_then_flushes_one_batch() -> None:
    pipeline, _switch, _epoch, _echo, bus = make_pipeline(batch_quiet_ms=60_000)
    pipeline.handle_stable_lines(["first"], 0)
    pipeline.handle_stable_lines(["second"], 0)

    pipeline.flush_if_due(now=time.monotonic())
    assert drain(bus) == []

    future = time.monotonic() + 61
    pipeline.flush_if_due(now=future)
    assert drain(bus) == ["first\nsecond"]

    pipeline.flush_if_due(now=future + 3600)
    assert drain(bus) == []


def test_pipeline_flushes_immediately_at_batch_max_chars() -> None:
    pipeline, _switch, _epoch, _echo, bus = make_pipeline(
        batch_quiet_ms=60_000, batch_max_chars=10
    )

    pipeline.handle_stable_lines(["123456", "7890"], 0)

    assert drain(bus) == ["123456\n7890"]


def test_pipeline_chunks_batches_and_preserves_order() -> None:
    pipeline, _switch, _epoch, _echo, bus = make_pipeline(
        chunk_chars=5, batch_quiet_ms=60_000
    )
    pipeline.handle_stable_lines(["abc", "def", "ghij"], 0)

    pipeline.flush_if_due(now=time.monotonic() + 61)

    items = drain(bus)
    assert items == ["abc", "def", "ghij"]
    assert all(len(item) <= 5 for item in items)


def test_pipeline_tags_output_items_with_generation() -> None:
    pipeline, _switch, epoch, _echo, bus = make_pipeline(batch_quiet_ms=60_000)
    epoch.bump()
    epoch.bump()

    pipeline.handle_stable_lines(["answer"], 2)
    pipeline.flush_if_due(now=time.monotonic() + 61)

    item = bus.get(timeout=0.01)
    assert item is not None
    assert item.text == "answer"
    assert item.generation == 2


def test_pipeline_discards_pending_lines_after_generation_bump() -> None:
    pipeline, _switch, epoch, _echo, bus = make_pipeline(batch_quiet_ms=60_000)
    pipeline.handle_stable_lines(["old output"], 0)

    epoch.bump()
    pipeline.flush_if_due(now=time.monotonic() + 61)

    assert drain(bus) == []


def test_pipeline_discards_pending_lines_when_mirror_turns_off() -> None:
    pipeline, switch, _epoch, _echo, bus = make_pipeline(batch_quiet_ms=60_000)
    pipeline.handle_stable_lines(["pending"], 0)

    switch.set(False)
    pipeline.flush_if_due(now=time.monotonic() + 61)

    assert drain(bus) == []


def test_pipeline_clear_drops_pending_lines() -> None:
    pipeline, _switch, _epoch, _echo, bus = make_pipeline(batch_quiet_ms=60_000)
    pipeline.handle_stable_lines(["pending"], 0)

    pipeline.clear()
    pipeline.flush_if_due(now=time.monotonic() + 61)

    assert drain(bus) == []


def test_echo_kimi_chevron_prompt_prefix_is_stripped() -> None:
    echo = EchoSuppressor(
        prefix_patterns=compile_patterns([r"^\s*[>›❯]\s*"])
    )
    echo.register("只回复 RK-ACCEPT-OK")

    assert echo.suppress(["❯ 只回复 RK-ACCEPT-OK"]) == []
