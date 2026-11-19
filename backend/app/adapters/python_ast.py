"""Static AST scan of Python files. Parses source; never imports or executes it."""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.adapters.models import Finding

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache"}
MAX_FILES = 20_000
MAX_FILE_BYTES = 1_000_000
MAX_PARSE_FINDINGS = 20
_SECRET_NAME = re.compile(
    r"(secret|passw(or)?d|api[_-]?key|token|private[_-]?key|credential)", re.I
)
_SUBPROCESS = {"run", "call", "check_call", "check_output", "Popen"}


@dataclass
class ScanResult:
    files: int = 0
    lines: int = 0
    test_files: int = 0
    test_functions: int = 0
    imports: set[str] = field(default_factory=set)
    findings: list[Finding] = field(default_factory=list)
    skipped_large: int = 0
    truncated: bool = False


def _is_test_file(name: str) -> bool:
    return name.startswith("test_") or name.endswith("_test.py")


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _dynamic(call: ast.Call) -> bool:
    if not call.args:
        return False
    return not (isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str))


def _finding(
    severity: str, title: str, evidence: str, rel: str, line: int, category: str = "security"
) -> Finding:
    return Finding(
        id="",
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        title=title,
        evidence=evidence,
        file_path=rel,
        line=line,
        source="ast",
    )


def _scan_tree(tree: ast.AST, rel: str, is_test: bool, result: ScanResult) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            result.imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if is_test and node.name.startswith("test"):
                result.test_functions += 1
        elif isinstance(node, ast.Assign | ast.AnnAssign) and not is_test:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value:
                for target in targets:
                    name = target.id if isinstance(target, ast.Name) else _dotted(target)
                    if name and _SECRET_NAME.search(name.split(".")[-1]):
                        result.findings.append(
                            _finding(
                                "high",
                                f"Hardcoded secret-like value assigned to `{name}`",
                                "A string literal is assigned to a secret-like name "
                                "(the value is not shown).",
                                rel,
                                node.lineno,
                            )
                        )
        elif isinstance(node, ast.Call):
            _scan_call(node, rel, result)


def _scan_call(node: ast.Call, rel: str, result: ScanResult) -> None:
    name = _dotted(node.func)
    if name is None:
        return
    dyn = "dynamic" if _dynamic(node) else "constant"
    where = f"`{name}(...)` with a {dyn} argument"
    if name in {"eval", "exec"}:
        sev = "high" if _dynamic(node) else "medium"
        result.findings.append(_finding(sev, f"Use of `{name}`", where, rel, node.lineno))
    elif name in {"os.system", "os.popen"}:
        sev = "high" if _dynamic(node) else "medium"
        result.findings.append(
            _finding(sev, f"Shell command via `{name}`", where, rel, node.lineno)
        )
    elif name.startswith("subprocess.") and name.split(".")[-1] in _SUBPROCESS:
        shell = any(
            kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in node.keywords
        )
        if shell:
            sev = "high" if _dynamic(node) else "medium"
            result.findings.append(
                _finding(sev, f"`{name}` with shell=True", where, rel, node.lineno)
            )
    elif name in {"pickle.load", "pickle.loads"}:
        result.findings.append(
            _finding("medium", f"Unsafe deserialization via `{name}`", where, rel, node.lineno)
        )
    elif name == "yaml.load" and not any(kw.arg == "Loader" for kw in node.keywords):
        if len(node.args) < 2:
            result.findings.append(
                _finding(
                    "medium", "`yaml.load` without an explicit Loader", where, rel, node.lineno
                )
            )


def scan_python(root: Path) -> ScanResult:
    result = ScanResult()
    parse_failures = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            path = Path(dirpath) / fname
            if path.is_symlink():
                continue
            if result.files >= MAX_FILES:
                result.truncated = True
                return result
            rel = path.relative_to(root).as_posix()
            if path.stat().st_size > MAX_FILE_BYTES:
                result.skipped_large += 1
                continue
            result.files += 1
            source = path.read_text(encoding="utf-8", errors="replace")
            result.lines += source.count("\n") + (1 if source and not source.endswith("\n") else 0)
            is_test = _is_test_file(fname)
            result.test_files += int(is_test)
            try:
                tree = ast.parse(source, filename=rel)
            except (SyntaxError, ValueError, RecursionError) as exc:
                parse_failures += 1
                if parse_failures <= MAX_PARSE_FINDINGS:
                    line = getattr(exc, "lineno", None) or 1
                    msg = getattr(exc, "msg", None) or str(exc)
                    result.findings.append(
                        _finding(
                            "medium",
                            "File does not parse as Python 3 (possible Python 2 syntax)",
                            f"{msg} at line {line}",
                            rel,
                            line,
                            category="code_quality",
                        )
                    )
                continue
            _scan_tree(tree, rel, is_test, result)
    if parse_failures > MAX_PARSE_FINDINGS:
        result.findings.append(
            _finding(
                "medium",
                "More files fail to parse as Python 3",
                f"{parse_failures - MAX_PARSE_FINDINGS} additional files not listed individually.",
                ".",
                1,
                category="code_quality",
            )
        )
    return result
