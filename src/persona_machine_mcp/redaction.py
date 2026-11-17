"""Scrub credentials from every log record before it reaches stderr.

The MCP SDK and httpx log raw payloads at WARNING when something is malformed (for example
``Raw result: {...}`` after a failed ``InitializeResult`` validation, or pydantic ``input_value``
excerpts). A misbehaving or compromised upstream could echo our bearer or assertion back inside
such a payload, so every record is rewritten here: known secret values first, then shape-based
patterns for Persona access tokens, bearer headers, JWTs and private-key PEM blocks. Formatted
tracebacks are folded into the message and redacted the same way.
"""

from __future__ import annotations

import logging
import re
import sys
import threading

REDACTED = "[REDACTED]"
_MIN_SECRET_LEN = 8

_SECRETS: set[str] = set()
_LOCK = threading.Lock()

_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
)
_PUAT_RE = re.compile(r"\bpuat_[A-Za-z0-9_\-]+")
_BEARER_RE = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/\-]+=*")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")


def register_secret(value: str | bytes | None) -> None:
    """Remember a concrete secret so any log line containing it is scrubbed verbatim."""
    if value is None:
        return
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    with _LOCK:
        for part in (text, *text.splitlines()):
            part = part.strip()
            if len(part) >= _MIN_SECRET_LEN and not part.startswith("-----"):
                _SECRETS.add(part)


def redact(text: str) -> str:
    with _LOCK:
        known = sorted(_SECRETS, key=len, reverse=True)
    for secret in known:
        text = text.replace(secret, REDACTED)
    text = _PEM_RE.sub(REDACTED, text)
    text = _PUAT_RE.sub(REDACTED, text)
    text = _JWT_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub(rf"\1 {REDACTED}", text)
    return text


class RedactingFilter(logging.Filter):
    """Rewrite each record into a single pre-formatted, redacted message (no args, no exc_info)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let a formatting error escape the logger
            message = str(record.msg)
        if record.exc_info:
            message += "\n" + logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
            record.exc_text = None
        if record.stack_info:
            message += "\n" + record.stack_info
            record.stack_info = None
        record.msg = redact(message)
        record.args = ()
        return True


class _StderrHandler(logging.StreamHandler):
    """Always write to the *current* ``sys.stderr`` (hosts and test runners may swap it)."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, value) -> None:  # pragma: no cover - StreamHandler.__init__ assigns once
        pass


def install_stderr_logging(level: int | str = logging.INFO) -> logging.Handler:
    """Attach one redacting stderr handler to the root logger (idempotent) and return it."""
    root = logging.getLogger()
    for existing in root.handlers:
        if getattr(existing, "_persona_machine_mcp", False):
            handler = existing
            break
    else:
        handler = _StderrHandler()
        handler._persona_machine_mcp = True  # type: ignore[attr-defined]
        handler.addFilter(RedactingFilter())
        handler.setFormatter(logging.Formatter("persona-machine-mcp %(levelname)s %(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return handler
