"""Redaction of secrets from text that is logged, streamed or stored."""

from __future__ import annotations

import re

_URL_CREDS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s:@]+(:[^/\s@]*)?@", re.I)
_KEY_VALUE = re.compile(
    r"(?P<key>[\w.-]*(?:secret|passw(?:or)?d|token|api[_-]?key|credential)[\w.-]*)"
    r"(?P<sep>\s*[=:]\s*)(?P<q>['\"]?)(?P<val>[^\s'\"]+)(?P=q)",
    re.I,
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
REDACTED = "***"


def redact(text: str) -> str:
    text = _URL_CREDS.sub(lambda m: f"{m.group('scheme')}{REDACTED}@", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    return _KEY_VALUE.sub(
        lambda m: f"{m.group('key')}{m.group('sep')}{m.group('q')}{REDACTED}{m.group('q')}", text
    )
