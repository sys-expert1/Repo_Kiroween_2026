"""Secret-pattern scan over text files that the Python AST scan does not cover.

Covers .env files, ini/yaml/json/toml/properties, shell and JavaScript sources. A finding names the
kind of secret and the line. The matched value is never copied into the finding, the evidence or
anything derived from them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.adapters.models import Finding

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "dist", "build"}
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
             "uv.lock", "pdm.lock", "composer.lock", "Cargo.lock", "Gemfile.lock"}  # fmt: skip
SUFFIXES = {".env", ".ini", ".cfg", ".conf", ".yml", ".yaml", ".json", ".toml", ".properties",
            ".sh", ".bash", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".xml",
            ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".cmake"}  # fmt: skip
MAX_FILE_BYTES = 200_000
MAX_FILES = 5_000
MAX_FINDINGS = 25

_KEY_BLOCK = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY")
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "high"),
    (
        "GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"),
        "high",
    ),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "high"),
]  # fmt: skip
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<key>[\w.-]*(?:secret|passw(?:or)?d|passwd|api[_-]?key|token|private[_-]?key|credential)[\w.-]*)
    ["']?\s*[:=]\s*["']?(?P<value>[^\s"'#,;]{8,})
    """
)
_PLACEHOLDER = re.compile(
    r"(?i)^(?:\$|\{\{|<|%\(|\[|changeme|change[_-]?me|your[_-]|xxx|\*{3,}|example|dummy|sample|"
    r"placeholder|todo|none$|null$|true$|false$|undefined$|process\.env|os\.environ|getenv)"
)


@dataclass
class SecretScan:
    files: int = 0
    findings: list[Finding] = field(default_factory=list)
    truncated: bool = False


def _wanted(name: str) -> bool:
    if name in LOCKFILES:
        return False
    return name.startswith(".env") or Path(name).suffix.lower() in SUFFIXES


def _finding(kind: str, severity: str, rel: str, line: int, how: str) -> Finding:
    return Finding(
        id="", category="security", severity=severity,  # type: ignore[arg-type]
        title=f"Possible secret in {rel}: {kind}",
        evidence=f"{how} on line {line} (the value is not shown).",
        file_path=rel, line=line, source="secrets",
    )  # fmt: skip


def scan_secrets(root: Path) -> SecretScan:
    result = SecretScan()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if not _wanted(name):
                continue
            path = Path(dirpath) / name
            if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            if result.files >= MAX_FILES:
                result.truncated = True
                return result
            raw = path.read_bytes()
            if b"\x00" in raw[:2048]:
                continue  # binary
            result.files += 1
            rel = path.relative_to(root).as_posix()
            for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                for hit in _classify(line):
                    if len(result.findings) >= MAX_FINDINGS:
                        result.truncated = True
                        return result
                    result.findings.append(_finding(*hit[:2], rel, number, hit[2]))
    return result


def _classify(line: str) -> list[tuple[str, str, str]]:
    """(kind, severity, how) for each secret-like thing on one line."""
    if _KEY_BLOCK.search(line):
        return [("private key", "critical", "A private key block starts")]
    for kind, pattern, severity in _PATTERNS:
        if pattern.search(line):
            return [(kind, severity, f"A {kind} pattern appears")]
    match = _ASSIGNMENT.search(line)
    if match and not _PLACEHOLDER.match(match.group("value")):
        return [
            (f"value assigned to `{match.group('key')}`", "medium", "A literal value is assigned")
        ]
    return []
