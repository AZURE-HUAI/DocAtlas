"""Timestamps and logging."""

from __future__ import annotations

import datetime as dt
from pathlib import Path


_LOG_FILE_PATH: Path | None = None


def set_log_file(path: str | Path | None) -> None:
    """Mirror every log line into a UTF-8 file, for reviewing long crawls."""
    global _LOG_FILE_PATH
    _LOG_FILE_PATH = Path(path).resolve() if path else None


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    if _LOG_FILE_PATH:
        with _LOG_FILE_PATH.open("a", encoding="utf-8", newline="\n") as output:
            output.write(line + "\n")
