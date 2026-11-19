"""Git URL validation. Only https:// , ssh:// and scp-like git@host:path forms are accepted."""

from __future__ import annotations

import re

_HTTPS = re.compile(r"^https://[A-Za-z0-9.-]+(:\d+)?/[A-Za-z0-9._~/%+-]+$")
_SSH = re.compile(r"^ssh://([A-Za-z0-9._-]+@)?[A-Za-z0-9.-]+(:\d+)?/[A-Za-z0-9._~/%+-]+$")
_SCP = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._~/%+-]+$")


class UrlValidationError(ValueError):
    pass


def validate_git_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        raise UrlValidationError("URL is empty.")
    if url.startswith("-"):
        raise UrlValidationError("URL must not start with '-'.")
    if any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise UrlValidationError("URL contains whitespace or control characters.")
    if ".." in url.split("/"):
        raise UrlValidationError("URL contains a '..' segment.")
    if not (_HTTPS.match(url) or _SSH.match(url) or _SCP.match(url)):
        raise UrlValidationError("Only https://, ssh:// and git@host:path URLs are accepted.")
    return url
