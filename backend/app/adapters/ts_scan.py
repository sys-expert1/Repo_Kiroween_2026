"""Static scan of TypeScript and JavaScript sources: heuristic, dependency-free, never runs code.

This is not a type checker or a linter. It blanks comments and string contents first, so patterns
only match real code, then looks for dangerous calls and quality smells. Treat results as signals.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.adapters.models import Finding

SKIP_DIRS = {".git", "node_modules", "dist", "build", "coverage", ".next", ".venv", "__pycache__"}
EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"}
MAX_FILES = 20_000
MAX_FILE_BYTES = 1_000_000
_TEST_FILE = re.compile(r"(?:\.(?:test|spec)\.[cm]?[jt]sx?$)|(?:^|/)__tests__/")
_TEST_CALL = re.compile(r"\b(?:it|test)(?:\.(?:only|skip|each|todo))?\s*[(\[`]")

# (title, severity, pattern on blanked code, needs a non-literal argument)
_CALLS: list[tuple[str, str, re.Pattern[str]]] = [
    ("Use of `eval`", "high", re.compile(r"(?<![\w.$])eval\s*\(")),
    ("Use of `new Function`", "high", re.compile(r"\bnew\s+Function\s*\(")),
    ("`document.write` call", "medium", re.compile(r"\bdocument\.write(?:ln)?\s*\(")),
    ("Assignment to `innerHTML`", "medium", re.compile(r"\.(?:inner|outer)HTML\s*=(?!=)")),
    ("Use of `dangerouslySetInnerHTML`", "medium", re.compile(r"\bdangerouslySetInnerHTML\b")),
]
_SHELL = re.compile(r"\b(?:exec|execSync)\s*\(\s*(?P<arg>[^)]*)")
_CHILD = re.compile(
    r"""(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(\s*)['"](?:node:)?child_process['"]"""
)
_TS_SUPPRESS = re.compile(r"@ts-(?:ignore|nocheck|expect-error)")
_ANY = re.compile(r"(?::\s*any\b|\bas\s+any\b|<any>)")


@dataclass
class TsScan:
    files: int = 0
    lines: int = 0
    test_files: int = 0
    test_functions: int = 0
    suppressions: int = 0
    any_uses: int = 0
    findings: list[Finding] = field(default_factory=list)
    truncated: bool = False


def blank(source: str) -> str:
    """Replace comments and the contents of string and template literals with spaces, keeping
    newlines and length, so line numbers still match. Template `${...}` bodies are blanked too."""
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                out.append(" ")
                i += 1
        elif c == "/" and nxt == "*":
            out.append("  ")
            i += 2
            while i < n and not (source[i] == "*" and source[i + 1 : i + 2] == "/"):
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
        elif c in "\"'`":
            quote = c
            out.append(quote)
            i += 1
            while i < n and source[i] != quote:
                if source[i] == "\\" and i + 1 < n:
                    out.append("  ")
                    i += 2
                    continue
                if quote == "`" and source[i : i + 2] == "${":
                    out.append("${")  # keep a marker: an interpolated template is dynamic
                    i += 2
                    depth = 1
                    while i < n and depth:
                        depth += {"{": 1, "}": -1}.get(source[i], 0)
                        if depth:
                            out.append("\n" if source[i] == "\n" else " ")
                        i += 1
                    out.append("}")
                    continue
                if source[i] == "\n" and quote != "`":
                    break  # unterminated string: stop at the line end
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            if i < n and source[i] == quote:
                out.append(quote)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _is_literal(arg: str) -> bool:
    """True if the first argument is a plain string literal (blanked to quotes and spaces)."""
    arg = arg.strip()
    return (
        len(arg) >= 2
        and arg[0] in "\"'`"
        and arg.rstrip().endswith(arg[0])
        and "+" not in arg
        and "${" not in arg
    )


def _find(
    title: str, severity: str, rel: str, line: int, how: str, category: str = "security"
) -> Finding:
    return Finding(
        id="",
        category=category,
        severity=severity,  # type: ignore[arg-type]
        title=title,
        evidence=how,
        file_path=rel,
        line=line,
        source="ts-scan",
    )


def scan_source(source: str, rel: str) -> TsScan:
    result = TsScan(
        files=1, lines=source.count("\n") + (1 if source and not source.endswith("\n") else 0)
    )
    is_test = bool(_TEST_FILE.search(rel))
    code = blank(source)
    result.suppressions = len(_TS_SUPPRESS.findall(source))
    result.any_uses = 0 if is_test else len(_ANY.findall(code))
    if is_test:
        result.test_files = 1
        result.test_functions = len(_TEST_CALL.findall(code))
        return result  # test code is not scanned for dangerous calls
    has_child = bool(_CHILD.search(source))  # import strings are blanked in `code`
    for number, line in enumerate(code.splitlines(), start=1):
        for title, severity, pattern in _CALLS:
            if pattern.search(line):
                result.findings.append(
                    _find(title, severity, rel, number, f"`{title}` appears in code")
                )
        if has_child:
            match = _SHELL.search(line)
            if match:
                dynamic = not _is_literal(match.group("arg"))
                kind = "dynamic" if dynamic else "constant"
                result.findings.append(
                    _find(
                        "Shell command via `child_process.exec`",
                        "high" if dynamic else "medium",
                        rel,
                        number,
                        f"`exec` with a {kind} argument runs through a shell",
                    )
                )
    return result


def scan_typescript(root: Path) -> TsScan:
    total = TsScan()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix not in EXTENSIONS or name.endswith(".d.ts") or path.is_symlink():
                continue
            if total.files >= MAX_FILES:
                total.truncated = True
                return total
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            rel = path.relative_to(root).as_posix()
            one = scan_source(path.read_text(encoding="utf-8", errors="replace"), rel)
            total.files += 1
            total.lines += one.lines
            total.test_files += one.test_files
            total.test_functions += one.test_functions
            total.suppressions += one.suppressions
            total.any_uses += one.any_uses
            total.findings.extend(one.findings)
    return total
