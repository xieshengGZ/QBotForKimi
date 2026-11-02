"""Persist the mirror switch in a JSON state file.

Only the ``mirror`` boolean is stored. Reads never raise: a missing file means
first startup, and a damaged file falls back to mirror off. Writes are atomic
and serialized so a crash or concurrent save cannot leave a partial file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading

logger = logging.getLogger("kimico_gateway.state")


class StateStore:
    """Read and write the mirror boolean stored at ``path``."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()

    def load(self) -> bool:
        mirror = False
        try:
            with open(self._path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            logger.info("state file %s not found, mirror is off", self._path)
        except (OSError, ValueError) as exc:
            logger.warning(
                "cannot read state file %s (%s), mirror is off", self._path, exc
            )
        else:
            if not isinstance(data, dict):
                logger.warning(
                    "state file %s has a non-object root, mirror is off", self._path
                )
            else:
                candidate = data.get("mirror", False)
                if isinstance(candidate, bool):
                    mirror = candidate
                else:
                    logger.warning(
                        "state file %s has a non-boolean mirror value, mirror is off",
                        self._path,
                    )
        logger.debug("mirror loaded: %s", str(mirror).lower())
        return mirror

    def save(self, mirror: bool) -> None:
        directory = os.path.dirname(os.path.abspath(self._path))
        tmp_path: str | None = None
        with self._lock:
            try:
                os.makedirs(directory, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(
                    prefix="state.", suffix=".tmp", dir=directory
                )
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps({"mirror": mirror}))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_path, self._path)
                tmp_path = None
            except OSError as exc:
                logger.error("failed to save state file %s: %s", self._path, exc)
            finally:
                if tmp_path is not None:
                    try:
                        os.unlink(tmp_path)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        logger.warning(
                            "failed to remove temporary state file %s: %s",
                            tmp_path,
                            exc,
                        )
