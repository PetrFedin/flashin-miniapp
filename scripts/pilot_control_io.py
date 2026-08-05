"""Crash-durable atomic filesystem writes for pilot control evidence."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        replaced = True
        _fsync_directory(path.parent)
    except OSError as exc:
        raise ValueError(f"Durable pilot evidence write failed for {path}: {exc}") from exc
    finally:
        if not replaced and temporary_path.exists():
            temporary_path.unlink()
