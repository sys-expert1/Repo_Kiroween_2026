"""Static scan of C and C++ sources: heuristic, dependency-free, never compiles or runs anything.

It blanks comments, string and character literals first, so patterns only match real code, then
looks for unbounded-copy and command-execution calls. This is not a compiler or an analyser (that
is clang-tidy's job). Treat results as signals.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.adapters.models import Finding

SKIP_DIRS = {
    ".git",
    "build",
    "out",
    "bin",
    "obj",
    "_deps",
    "third_party",
    "thirdparty",
    "external",
    "extern",
    "vendor",
    "deps",
    "node_modules",
    ".venv",
    "__pycache__",
    "CMakeFiles",
}
SOURCE_EXT = {".c", ".cc", ".cpp", ".cxx", ".c++"}
HEADER_EXT = {".h", ".hh", ".hpp", ".hxx", ".h++"}
EXTENSIONS = SOURCE_EXT | HEADER_EXT
MAX_FILES = 20_000
MAX_FILE_BYTES = 1_000_000

_TEST_FILE = re.compile(
    r"(?:^|/)(?:tests?|unittests?|testing)/|(?:^|/)(?:test_[^/]*|[^/]*_tests?|[^/]*[.-]tests?)\.[a-z+]+$",
    re.I,
)
_TEST_MACROS = re.compile(
    r"\b(?:TEST|TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P|TEST_CASE|TEST_CASE_METHOD|SCENARIO|"
    r"BOOST_AUTO_TEST_CASE|BOOST_FIXTURE_TEST_CASE|BOOST_TEST_CASE)\s*\("
)
_TEST_INCLUDE = re.compile(
    r"#\s*include\s*[<\"](?:gtest/|gmock/|catch2?/|catch\.hpp|doctest|boost/test/|CppUTest)"
)
_BAD_CALLS: list[tuple[str, str, re.Pattern[str]]] = [
    ("`gets` reads without a length limit", "high", re.compile(r"(?<![\w.>:])gets\s*\(")),
    ("Unbounded `strcpy`", "medium", re.compile(r"(?<![\w.>:])(?:std::)?strcpy\s*\(")),
    ("Unbounded `strcat`", "medium", re.compile(r"(?<![\w.>:])(?:std::)?strcat\s*\(")),
    ("Unbounded `sprintf`", "medium", re.compile(r"(?<![\w.>:])(?:std::)?v?sprintf\s*\(")),
    (
        "Insecure temporary file name",
        "medium",
        re.compile(r"(?<![\w.>:])(?:mktemp|tmpnam|tempnam)\s*\("),
    ),
]
_SHELL = re.compile(r"(?<![\w.>:])(?:std::|::)?(?P<fn>system|popen)\s*\((?P<rest>.*)")
_CONSTANT_ARG = re.compile(r'^\s*(?:"[^"\n]*"\s*)+[),]')
_SCANF_CALL = re.compile(r"(?<![\w.>:])(?:std::)?f?scanf\s*\(")
_UNBOUNDED_S = re.compile(r'"[^"\n]*%s')
_FORMAT_STRING = re.compile(
    r"(?<![\w.>:])(?:std::)?(?:printf|fprintf|syslog)\s*\(\s*(?:\w+\s*,\s*)?(?P<arg>[A-Za-z_]\w*)\s*[,)]"
)
_USING_STD = re.compile(r"^\s*using\s+namespace\s+std\s*;", re.M)


@dataclass
class CppScan:
    files: int = 0
    lines: int = 0
    test_files: int = 0
    test_functions: int = 0
    headers_using_std: int = 0
    findings: list[Finding] = field(default_factory=list)
    truncated: bool = False
    includes: set[str] = field(default_factory=set)


def blank(source: str) -> str:
    """Blank comments, string/char literals and raw strings, keeping newlines and length."""
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
            while i < n and source[i : i + 2] != "*/":
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
        elif c == "R" and nxt == '"' and not (out and (out[-1].isalnum() or out[-1] == "_")):
            end_open = source.find("(", i + 2, i + 20)
            delimiter = source[i + 2 : end_open] if end_open != -1 else None
            if delimiter is None or any(ch in delimiter for ch in ' )\\"\n'):
                out.append(c)
                i += 1
                continue
            close = source.find(")" + delimiter + '"', end_open)
            stop = n if close == -1 else close + len(delimiter) + 2
            out.append('R"')
            for ch in source[i + 2 : stop - 1]:
                out.append("\n" if ch == "\n" else " ")
            out.append('"')
            i = stop
        elif c == "'" and out and out[-1].isalnum():
            out.append(c)  # C++14 digit separator such as 1'000'000
            i += 1
        elif c in "\"'":
            quote = c
            out.append(quote)
            i += 1
            while i < n and source[i] != quote:
                if source[i] == "\\" and i + 1 < n:
                    out.append("  ")
                    i += 2
                    continue
                if source[i] == "\n":
                    break  # unterminated literal: stop at the line end
                out.append(" ")
                i += 1
            if i < n and source[i] == quote:
                out.append(quote)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _find(title: str, severity: str, rel: str, line: int, how: str) -> Finding:
    return Finding(
        id="",
        category="security",
        severity=severity,  # type: ignore[arg-type]
        title=title,
        evidence=how,
        file_path=rel,
        line=line,
        source="cpp-scan",
    )


def scan_source(source: str, rel: str) -> CppScan:
    """Scan one in-memory file. Test files are counted but not scanned for unsafe calls."""
    suffix = Path(rel).suffix.lower()
    result = CppScan(
        files=1, lines=source.count("\n") + (1 if source and not source.endswith("\n") else 0)
    )
    result.includes = set(re.findall(r'#\s*include\s*[<"]([^>"\n]+)[>"]', source))
    code = blank(source)
    is_test_path = bool(_TEST_FILE.search(rel))
    macros = len(_TEST_MACROS.findall(code))
    if macros or (is_test_path and suffix in SOURCE_EXT) or _TEST_INCLUDE.search(source):
        result.test_files = 1
        result.test_functions = macros
    if suffix in HEADER_EXT and _USING_STD.search(code):
        result.headers_using_std = 1
    if result.test_files:
        return result
    raw_lines = source.splitlines()
    for number, line in enumerate(code.splitlines(), start=1):
        for title, severity, pattern in _BAD_CALLS:
            if pattern.search(line):
                result.findings.append(
                    _find(title, severity, rel, number, f"`{title}` appears in code")
                )
        shell = _SHELL.search(line)
        if shell:
            constant = bool(_CONSTANT_ARG.match(shell.group("rest")))
            kind = "constant" if constant else "dynamic"
            result.findings.append(
                _find(
                    f"Shell command via `{shell.group('fn')}`",
                    "medium" if constant else "high",
                    rel,
                    number,
                    f"`{shell.group('fn')}` with a {kind} argument runs through a shell",
                )
            )
        if _SCANF_CALL.search(line) and _UNBOUNDED_S.search(raw_lines[number - 1]):
            result.findings.append(
                _find(
                    "`scanf` with an unbounded `%s`",
                    "high",
                    rel,
                    number,
                    "`%s` without a field width can overflow the destination buffer",
                )
            )
        fmt = _FORMAT_STRING.search(line)
        if fmt:
            result.findings.append(
                _find(
                    "Format string taken from a variable",
                    "medium",
                    rel,
                    number,
                    f'`{fmt.group("arg")}` is passed as the format; use a literal such as "%s"',
                )
            )
    return result


def scan_cpp(root: Path) -> CppScan:
    total = CppScan()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith("cmake-build")
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
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
            total.headers_using_std += one.headers_using_std
            total.includes |= one.includes
            total.findings.extend(one.findings)
    return total
