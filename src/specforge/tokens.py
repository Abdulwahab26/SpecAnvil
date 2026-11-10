"""Approximate token counting (no tokenizer dependency) and secret redaction."""

from __future__ import annotations

import re


def estimate_tokens(text: str) -> int:
    """~4 characters per token is a good average for code across tokenizers."""
    if not text:
        return 0
    return max(1, len(text) // 4)


_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED PRIVATE KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED AWS KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "[REDACTED GITHUB TOKEN]"),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"), "[REDACTED SLACK TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED API KEY]"),
    (re.compile(r"(?i)((?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)"
                r"\s*[:=]\s*)([\"'])([^\"'\s]{6,})\2"), r"\1\2[REDACTED]\2"),
    (re.compile(r"(?i)(\b[a-z+]+://[^:/\s]+:)([^@\s]{3,})(@)"), r"\1[REDACTED]\3"),
]


def redact(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text
