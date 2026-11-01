"""Structured logging for the container tooling: one JSON object per line, or readable text.

Why: the quick start spans several containers (model puller, model server, harness), and a failure is usually in the seam
between two of them. A uniform line format with a component name, an event name and key=value fields can be filtered with
``jq`` or grep, and the same lines go to stderr and (optionally) a file under the mounted data directory.

Configuration is by environment variable so a kube YAML can set it without code changes:

* ``WATTSON_LOG_LEVEL``  DEBUG, INFO (default), WARNING or ERROR
* ``WATTSON_LOG_FORMAT`` ``json`` (default when stderr is not a terminal) or ``text``
* ``WATTSON_LOG_FILE``   also append every line to this file (created with its parent directory)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

LEVEL_ENV: str = "WATTSON_LOG_LEVEL"
FORMAT_ENV: str = "WATTSON_LOG_FORMAT"
FILE_ENV: str = "WATTSON_LOG_FILE"
DEFAULT_LEVEL: str = "INFO"
ROOT: str = "wattson"
# Attributes every LogRecord has; anything else on a record was passed through ``extra`` and is emitted as a field.
_STANDARD: frozenset[str] = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    return {key: value for key, value in vars(record).items() if key not in _STANDARD}


class JsonFormatter(logging.Formatter):
    """``{"ts": ..., "level": ..., "component": ..., "event": ..., **fields}`` on one line."""

    def format(self, record: logging.LogRecord) -> str:
        body: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname, "component": record.name, "event": record.getMessage(), **_fields(record)}
        if record.exc_info:
            body["exception"] = self.formatException(record.exc_info)
        return json.dumps(body, default=str, sort_keys=False)


class TextFormatter(logging.Formatter):
    """``12:00:01 INFO  doctor  solver.smoke  seconds=1.2 status=PASS``"""

    def format(self, record: logging.LogRecord) -> str:
        pairs = " ".join(f"{key}={value}" for key, value in _fields(record).items())
        line = f"{time.strftime('%H:%M:%S', time.localtime(record.created))} {record.levelname:<5} {record.name:<16} {record.getMessage()}"
        text = f"{line}  {pairs}" if pairs else line
        return f"{text}\n{self.formatException(record.exc_info)}" if record.exc_info else text


def configure(level: str | None = None, fmt: str | None = None, file: str | None = None) -> logging.Logger:
    """Install the handlers on the ``wattson`` logger (idempotent) and return it."""
    logger = logging.getLogger(ROOT)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    chosen_level = (level or os.environ.get(LEVEL_ENV) or DEFAULT_LEVEL).upper()
    chosen_format = (fmt or os.environ.get(FORMAT_ENV) or ("text" if sys.stderr.isatty() else "json")).lower()
    if chosen_format not in {"json", "text"}:
        raise ValueError(f"{FORMAT_ENV} must be json or text, not {chosen_format!r}")
    formatter: logging.Formatter = JsonFormatter() if chosen_format == "json" else TextFormatter()
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(formatter)
    logger.addHandler(stderr)
    logger.setLevel(getattr(logging, chosen_level, logging.INFO))  # before anything below can log, or a stale level would filter it
    logger.propagate = False
    target = file or os.environ.get(FILE_ENV)
    if target:
        try:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            to_file = logging.FileHandler(target)
        except OSError as error:
            # An unwritable log directory (a bind mount owned by another user) must not stop the tool it is meant to diagnose.
            logger.warning("log.file.unavailable", extra={"path": target, "error": f"{type(error).__name__}: {error}"})
        else:
            to_file.setFormatter(JsonFormatter())  # the file is always machine-readable
            logger.addHandler(to_file)
    return logger


def get(component: str) -> logging.Logger:
    """A child logger, e.g. ``get("doctor")`` logs as component ``wattson.doctor``."""
    return logging.getLogger(f"{ROOT}.{component}")
