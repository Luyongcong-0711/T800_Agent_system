from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SECRET_KEYWORDS = (
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "password",
    "secret",
    "token",
    "ciphertext",
    "nonce",
    "tag",
)

SECRET_STRING_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|password|plaintext|ciphertext|nonce|tag|"
        r"authorization|cookie|access[_-]?token|refresh[_-]?token|secret|token)"
        r"\b\s*[:=]?\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)?"
    ),
)


def mask_secret(value: Any) -> str:
    text = str(value)
    if len(text) <= 8:
        return "***"
    return f"{text[:3]}***{text[-4:]}"


def is_secret_key(key: Any) -> bool:
    lowered = str(key).lower().replace("-", "_").replace(" ", "_")
    return any(keyword in lowered for keyword in SECRET_KEYWORDS)


def redact_string(value: str) -> str:
    redacted = value
    for pattern in SECRET_STRING_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        if is_secret_key(key):
            redacted[key] = mask_secret(value)
        elif isinstance(value, Mapping):
            redacted[key] = redact_mapping(value)
        elif isinstance(value, list):
            redacted[key] = [
                redact_mapping(item) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            redacted[key] = value
    return redacted


def redact_log_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        redacted_count = 0
        for key, item in value.items():
            if is_secret_key(key):
                redacted_count += 1
                redacted[f"redacted_field_{redacted_count}"] = "***"
            else:
                redacted[str(key)] = redact_log_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_log_value(item) for item in value]
    if isinstance(value, str):
        return redact_string(value)
    return value
