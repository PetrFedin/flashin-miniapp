"""Cross-process fail-closed lock for pilot control state mutations."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time
from typing import Iterator

DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
_LOCK_POLL_SECONDS = 0.05


def lock_path_for(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.name}.lock")


@contextmanager
def exclusive_state_lock(
    state_path: Path,
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> Iterator[Path]:
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("Pilot control lock timeout must be a number") from exc
    if timeout <= 0 or timeout > 60:
        raise ValueError("Pilot control lock timeout must be between 0 and 60 seconds")

    lock_path = lock_path_for(state_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    handle = os.fdopen(descriptor, "a+", encoding="utf-8")
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        os.fchmod(handle.fileno(), 0o600)
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError(
                        f"Pilot control state lock acquisition timed out: {lock_path}"
                    )
                time.sleep(min(_LOCK_POLL_SECONDS, remaining))
        yield lock_path
    finally:
        if acquired:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
