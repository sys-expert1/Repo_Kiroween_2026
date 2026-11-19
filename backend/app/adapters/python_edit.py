"""Small, conservative source edits for Python files and requirements files.

Each function either returns the complete new text or raises `EditNotApplicable` with a reason.
Nothing here executes the code it edits, and every result of a Python edit is re-parsed.
"""

from __future__ import annotations

import ast
import re
import shlex

from packaging.version import InvalidVersion, Version

from app.adapters.python_deps import parse_requirement_line


class EditNotApplicable(Exception):
    """The edit cannot be done safely. The message is shown to the user."""


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _parse(source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        raise EditNotApplicable("The file does not parse as Python 3.") from None


def _replace_span(source: str, node: ast.expr, replacement: str) -> str:
    if node.end_lineno != node.lineno or node.end_col_offset is None:
        raise EditNotApplicable("The expression spans several lines.")
    lines = source.splitlines(keepends=True)
    raw = lines[node.lineno - 1].encode()
    lines[node.lineno - 1] = (
        raw[: node.col_offset] + replacement.encode() + raw[node.end_col_offset :]
    ).decode()
    return "".join(lines)


def ensure_import(source: str, module: str) -> str:
    tree = _parse(source)
    for node in tree.body:
        if isinstance(node, ast.Import) and any(
            a.name == module and a.asname is None for a in node.names
        ):
            return source
    lines = source.splitlines(keepends=True)
    imports = [
        n
        for n in tree.body
        if isinstance(n, ast.Import | ast.ImportFrom)
        and not (isinstance(n, ast.ImportFrom) and n.module == "__future__")
    ]
    if imports:
        at = imports[0].lineno
    else:
        idx = 0
        if (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)
        ):
            idx = 1
        at = tree.body[idx].lineno if idx < len(tree.body) else len(lines) + 1
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.insert(at - 1, f"import {module}\n")
    return "".join(lines)


def replace_secret_with_env(source: str, line: int) -> str:
    """Replace `NAME = "literal"` at `line` with `NAME = os.environ.get("NAME", "")`."""
    tree = _parse(source)
    for node in ast.walk(tree):
        if getattr(node, "lineno", None) != line:
            continue
        target = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            new = _replace_span(source, value, f'os.environ.get("{target.id}", "")')
            new = ensure_import(new, "os")
            _parse(new)
            return new
    raise EditNotApplicable('Not a simple `NAME = "literal"` assignment on that line.')


def replace_popen(source: str, line: int) -> str:
    """Replace `os.popen("cmd " + x).read()` with an argument-list `subprocess.run`."""
    tree = _parse(source)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.lineno == line and not node.args):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "read"):
            continue
        inner = func.value
        if not (
            isinstance(inner, ast.Call)
            and _dotted(inner.func) == "os.popen"
            and len(inner.args) == 1
            and isinstance(inner.args[0], ast.BinOp)
            and isinstance(inner.args[0].op, ast.Add)
        ):
            continue
        left, right = inner.args[0].left, inner.args[0].right
        if not (
            isinstance(left, ast.Constant)
            and isinstance(left.value, str)
            and left.value.strip()
            and left.value[-1].isspace()
        ):
            raise EditNotApplicable("The command prefix is not a constant ending in a space.")
        right_src = ast.get_source_segment(source, right)
        if right_src is None:
            raise EditNotApplicable("Could not read the argument expression.")
        tokens = ", ".join(repr(t) for t in shlex.split(left.value))
        new = _replace_span(
            source,
            node,
            f"subprocess.run([{tokens}, {right_src}], capture_output=True, text=True).stdout",
        )
        new = ensure_import(new, "subprocess")
        _parse(new)
        return new
    raise EditNotApplicable(
        'Only `os.popen("cmd " + value).read()` can be rewritten automatically.'
    )


def choose_fix_version(installed: str, fix_versions: list[str]) -> str:
    """Highest listed fix version above the installed one. Raises if none can be chosen."""
    try:
        current = Version(installed)
    except InvalidVersion:
        raise EditNotApplicable(
            f"Installed version {installed!r} is not a valid version."
        ) from None
    candidates: list[Version] = []
    for raw in fix_versions:
        try:
            v = Version(raw)
        except InvalidVersion:
            continue
        if v > current and not v.is_prerelease:
            candidates.append(v)
    if not candidates:
        raise EditNotApplicable("No listed fix version is newer than the installed version.")
    return str(max(candidates))


def bump_requirement(text: str, package: str, old: str, new: str) -> str:
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        dep = parse_requirement_line(line, "requirements")
        if dep and dep.name == package and dep.pinned_version == old:
            changed = re.sub(
                r"(===?\s*)" + re.escape(old) + r"(?![\w.])",
                lambda m: m.group(1) + new,
                line,
                count=1,
            )
            if changed == line:
                break
            lines[i] = changed
            return "".join(lines)
    raise EditNotApplicable(f"`{package}=={old}` was not found as an exact pin in this file.")
