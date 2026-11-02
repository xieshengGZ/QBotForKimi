"""Contract tests for mirror state persistence (ticket 05)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

import pytest

import kimico_gateway.state as state_module
from kimico_gateway.state import StateStore

LOGGER = "kimico_gateway.state"


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == LOGGER]


def _tmp_files(directory: Path) -> list[Path]:
    return list(directory.glob("state.*.tmp"))


def test_load_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "state.json"

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = StateStore(str(path)).load()

    assert result is False
    records = _records(caplog)
    assert any(record.levelno == logging.INFO for record in records)
    assert not any(record.levelno >= logging.WARNING for record in records)
    assert any("mirror loaded: false" in record.getMessage() for record in records)


@pytest.mark.parametrize("value", [True, False])
def test_load_valid(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, value: bool
) -> None:
    path = _write(tmp_path / "state.json", json.dumps({"mirror": value}))

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = StateStore(str(path)).load()

    assert result is value
    records = _records(caplog)
    assert not any(record.levelno >= logging.WARNING for record in records)
    assert any(
        f"mirror loaded: {str(value).lower()}" in record.getMessage()
        for record in records
    )


@pytest.mark.parametrize("content", ["", "not json", "{", '{"mirror": tru'])
def test_load_corrupt(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: str
) -> None:
    path = _write(tmp_path / "state.json", content)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = StateStore(str(path)).load()

    assert result is False
    assert any(record.levelno == logging.WARNING for record in _records(caplog))


@pytest.mark.parametrize("content", ["null", "[true]", '"true"', "42"])
def test_load_non_object_root(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: str
) -> None:
    path = _write(tmp_path / "state.json", content)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = StateStore(str(path)).load()

    assert result is False
    assert any(record.levelno == logging.WARNING for record in _records(caplog))


@pytest.mark.parametrize(
    "content",
    ['{"mirror": "yes"}', '{"mirror": 1}', '{"mirror": 0}', '{"mirror": null}'],
)
def test_load_wrong_type(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: str
) -> None:
    path = _write(tmp_path / "state.json", content)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = StateStore(str(path)).load()

    assert result is False
    assert any(record.levelno == logging.WARNING for record in _records(caplog))


def test_load_missing_key_defaults_without_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(tmp_path / "state.json", "{}")

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = StateStore(str(path)).load()

    assert result is False
    assert not any(record.levelno >= logging.WARNING for record in _records(caplog))


def test_load_extra_keys_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(
        tmp_path / "state.json", json.dumps({"mirror": True, "x": 1, "extra": "ok"})
    )

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = StateStore(str(path)).load()

    assert result is True
    assert not any(record.levelno >= logging.WARNING for record in _records(caplog))


def test_load_path_is_directory(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = StateStore(str(tmp_path)).load()

    assert result is False
    assert any(record.levelno == logging.WARNING for record in _records(caplog))


@pytest.mark.parametrize("value", [True, False])
def test_save_roundtrip(tmp_path: Path, value: bool) -> None:
    path = tmp_path / "state.json"
    store = StateStore(str(path))

    store.save(value)

    assert store.load() is value
    assert json.loads(path.read_text(encoding="utf-8")) == {"mirror": value}
    assert _tmp_files(tmp_path) == []


def test_save_creates_dirs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "nested" / "deeper" / "state.json"

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        StateStore(str(path)).save(True)

    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == {"mirror": True}
    assert not any(record.levelno >= logging.ERROR for record in _records(caplog))
    assert _tmp_files(path.parent) == []


def test_save_uses_unique_tempfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[tuple[str, dict[str, str]]] = []
    real_mkstemp = tempfile.mkstemp

    def recording_mkstemp(*args: object, **kwargs: str) -> tuple[int, str]:
        fd, name = real_mkstemp(*args, **kwargs)
        created.append((name, kwargs))
        return fd, name

    monkeypatch.setattr(state_module.tempfile, "mkstemp", recording_mkstemp)
    store = StateStore(str(tmp_path / "state.json"))

    store.save(True)
    store.save(False)

    assert len(created) == 2
    assert len({name for name, _ in created}) == 2
    for _, kwargs in created:
        assert kwargs == {
            "prefix": "state.",
            "suffix": ".tmp",
            "dir": str(tmp_path),
        }
    assert _tmp_files(tmp_path) == []


def test_save_fsync_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def recording_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def recording_replace(src: str, dst: str) -> None:
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(state_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(state_module.os, "replace", recording_replace)

    StateStore(str(tmp_path / "state.json")).save(True)

    assert events == ["fsync", "replace"]
    assert _tmp_files(tmp_path) == []


def test_save_failure_no_raise(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "state-dir"
    directory.mkdir()

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        StateStore(str(directory)).save(True)

    assert any(record.levelno == logging.ERROR for record in _records(caplog))
    assert _tmp_files(tmp_path) == []


def test_save_concurrent(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    stop = threading.Event()
    errors: list[BaseException] = []

    def writer(value: bool) -> None:
        for _ in range(50):
            store.save(value)

    def reader() -> None:
        while not stop.is_set():
            try:
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data.get("mirror"), bool):
                        errors.append(AssertionError(f"invalid state: {data!r}"))
            except BaseException as exc:  # recorded for the final assertion
                errors.append(exc)

    writers = [
        threading.Thread(target=writer, args=(value,)) for value in (True, False)
    ]
    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join()
    stop.set()
    reader_thread.join()

    assert errors == []
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"mirror": True} or data == {"mirror": False}
    assert _tmp_files(tmp_path) == []
