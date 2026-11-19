from __future__ import annotations

import ast
import shutil
from functools import partial
from pathlib import Path

import pytest

from app.adapters.models import BaselineReport, TestCounts
from app.adapters.python_edit import (
    EditNotApplicable,
    bump_requirement,
    choose_fix_version,
    ensure_import,
    replace_popen,
    replace_secret_with_env,
)
from app.agents.patches import (
    PatchApplyError,
    apply_patchset,
    generate_patchset,
    sha256,
)
from app.agents.roadmap import build_roadmap, revise_roadmap
from app.config import FIXTURES_DIR
from tests.conftest import OfflineAdapter

FAILING = BaselineReport(
    status="completed", counts=TestCounts(passed=1, failed=1),
    failing_tests=["tests/test_inventory.py::test_total_items_known_failure"],
)  # fmt: skip


# ------------------------------------------------------------------- edit helpers
def test_secret_becomes_env_lookup_and_value_disappears():
    src = 'import os\n\nTOKEN = "s3cret-value"\nOTHER = "keep"\n'
    out = replace_secret_with_env(src, 3)
    assert out == 'import os\n\nTOKEN = os.environ.get("TOKEN", "")\nOTHER = "keep"\n'
    assert "s3cret-value" not in out


def test_secret_edit_adds_the_import_when_missing_and_respects_docstring():
    out = replace_secret_with_env('"""Doc."""\nKEY = "abc"\n', 2)
    assert out == '"""Doc."""\nimport os\nKEY = os.environ.get("KEY", "")\n'
    ast.parse(out)


@pytest.mark.parametrize(
    ("src", "line"),
    [
        ('KEY = ("a"\n       "b")\n', 1),  # spans lines
        ("KEY = compute()\n", 1),  # not a literal
        ("KEY, OTHER = 'a', 'b'\n", 1),  # tuple target
        ("x = 1\n", 5),  # wrong line
        ("def broken(:\n", 1),  # does not parse
    ],
)
def test_secret_edit_refuses_what_it_cannot_do_safely(src: str, line: int):
    with pytest.raises(EditNotApplicable):
        replace_secret_with_env(src, line)


def test_popen_becomes_argument_list_without_a_shell():
    src = 'import os\n\ndef run(cmd):\n    return os.popen("report --fast " + cmd).read()\n'
    out = replace_popen(src, 4)
    assert "subprocess.run(['report', '--fast', cmd], capture_output=True, text=True).stdout" in out
    assert out.startswith("import subprocess\nimport os\n") and "shell" not in out
    ast.parse(out)


@pytest.mark.parametrize(
    "src",
    [
        'import os\nx = os.popen("a " + b).read()\nf = os.popen(b)\n',  # line 3: no concatenation
        'import os\nx = os.popen("report" + b).read()\n',  # prefix has no trailing space
        'import os\nx = os.popen(f"report {b}").read()\n',  # f-string
        'import os\nx = os.popen("a " + b)\n',  # no .read()
    ],
)
def test_popen_edit_refuses_other_shapes(src: str):
    with pytest.raises(EditNotApplicable):
        replace_popen(src, 3 if src.count("\n") >= 3 else 2)


def test_ensure_import_is_idempotent_and_keeps_future_imports_first():
    src = "from __future__ import annotations\nimport sys\n"
    once = ensure_import(src, "os")
    assert once == "from __future__ import annotations\nimport os\nimport sys\n"
    assert ensure_import(once, "os") == once


def test_choose_fix_version():
    assert choose_fix_version("2.19.1", ["2.20.0", "2.31.0", "2.9.0"]) == "2.31.0"
    assert choose_fix_version("1.0", ["1.1.0rc1", "1.0.5"]) == "1.0.5"  # no pre-releases
    with pytest.raises(EditNotApplicable):
        choose_fix_version("3.0", ["1.0", "2.0"])
    with pytest.raises(EditNotApplicable):
        choose_fix_version("not-a-version", ["1.0"])


def test_bump_requirement_keeps_comments_extras_and_other_lines():
    text = "# deps\nrequests[socks]==2.19.1  # pinned\nFlask==1.0\n"
    out = bump_requirement(text, "requests", "2.19.1", "2.31.0")
    assert out == "# deps\nrequests[socks]==2.31.0  # pinned\nFlask==1.0\n"
    with pytest.raises(EditNotApplicable):
        bump_requirement(text, "requests", "9.9", "2.31.0")
    with pytest.raises(EditNotApplicable):
        bump_requirement("requests>=2\n", "requests", "2", "3")


# ------------------------------------------------------------------ patch set
@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    dest = tmp_path / "snapshot"
    shutil.copytree(
        FIXTURES_DIR / "legacy-python-cli", dest, ignore=shutil.ignore_patterns("__pycache__")
    )
    return dest


def build(snapshot: Path, tmp_path: Path, baseline: BaselineReport = FAILING):
    adapter = OfflineAdapter()
    scratch = tmp_path / "scratch"
    report = adapter.autopsy(snapshot, "legacy", scratch, lambda r: None)
    roadmap = build_roadmap(report, baseline)
    fixer = partial(adapter.fix_lint, scratch=scratch, observe=lambda r: None)
    return roadmap, report, generate_patchset(roadmap, report, snapshot, fixer)


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_patchset_covers_the_fixable_tasks_and_skips_the_rest_with_reasons(snapshot, tmp_path):
    before = tree(snapshot)
    roadmap, _, ps = build(snapshot, tmp_path)
    assert tree(snapshot) == before  # generation never writes to the snapshot
    assert ps.files_changed == [
        ".github/workflows/ci.yml",
        "Dockerfile",
        "inventory.py",
        "requirements.txt",
    ]
    by_task = {p.task_id: p for p in ps.patches}
    titles = {t.id: t.title for t in roadmap.tasks}
    assert any("requests" in titles[i] for i in by_task)
    [skip] = ps.skipped
    assert "failing test" in skip.title and "never edited" in skip.reason  # tests are not rewritten
    req = next(p for p in ps.patches if p.path == "requirements.txt")
    assert "requests==2.31.0" in req.after_content  # highest fix version from the audit double
    assert req.before_sha256 == sha256((snapshot / "requirements.txt").read_text())


def test_patches_chain_on_the_same_file_and_hashes_line_up(snapshot, tmp_path):
    _, _, ps = build(snapshot, tmp_path)
    inv = [p for p in ps.patches if p.path == "inventory.py"]
    assert len(inv) >= 3  # secret, popen, lint
    assert inv[0].before_sha256 == sha256((snapshot / "inventory.py").read_text())
    for prev, nxt in zip(inv, inv[1:], strict=False):
        assert nxt.before_sha256 == prev.after_sha256
    assert all(sha256(p.after_content) == p.after_sha256 for p in ps.patches)


def test_apply_produces_valid_code_and_leaves_the_snapshot_alone(snapshot, tmp_path):
    _, _, ps = build(snapshot, tmp_path)
    work = tmp_path / "work"
    shutil.copytree(snapshot, work)
    snapshot_before = tree(snapshot)
    applied = apply_patchset(ps, work)
    assert len(applied) == len(ps.patches) and tree(snapshot) == snapshot_before
    inv = (work / "inventory.py").read_text()
    ast.parse(inv)
    assert "legacy-token" not in inv and "os.popen" not in inv and "import json" not in inv
    assert (work / ".github" / "workflows" / "ci.yml").is_file() and (work / "Dockerfile").is_file()
    assert not [p for p in work.rglob(".necro-*")]  # no temp files left behind


def test_digest_is_deterministic_and_tracks_scope(snapshot, tmp_path):
    roadmap, report, ps = build(snapshot, tmp_path)
    again = build(snapshot, tmp_path)[2]
    assert ps.digest == again.digest and len(ps.digest) == 64
    adapter = OfflineAdapter()
    smaller = generate_patchset(
        revise_roadmap(roadmap, [roadmap.tasks[0].id]), report, snapshot,
        partial(adapter.fix_lint, scratch=tmp_path / "s2", observe=lambda r: None),
    )  # fmt: skip
    assert smaller.digest != ps.digest and len(smaller.patches) < len(ps.patches)
    assert all(p.task_id != roadmap.tasks[0].id for p in smaller.patches)


def test_apply_refuses_a_file_that_changed_after_approval(snapshot, tmp_path):
    _, _, ps = build(snapshot, tmp_path)
    work = tmp_path / "work"
    shutil.copytree(snapshot, work)
    (work / "requirements.txt").write_text("requests==2.19.1\n# edited later\n")
    with pytest.raises(PatchApplyError, match="changed since approval"):
        apply_patchset(ps, work)


def test_apply_refuses_to_overwrite_an_existing_file_on_create(snapshot, tmp_path):
    _, _, ps = build(snapshot, tmp_path)
    work = tmp_path / "work"
    shutil.copytree(snapshot, work)
    (work / "Dockerfile").write_text("FROM scratch\n")
    with pytest.raises(PatchApplyError, match="already exists"):
        apply_patchset(ps, work)


def test_apply_refuses_tampered_content_and_escaping_paths(snapshot, tmp_path):
    _, _, ps = build(snapshot, tmp_path)
    work = tmp_path / "work"
    shutil.copytree(snapshot, work)
    tampered = ps.model_copy(deep=True)
    tampered.patches[0].after_content += "\nimport evil\n"
    with pytest.raises(PatchApplyError, match="does not match its hash"):
        apply_patchset(tampered, work)
    evil = ps.model_copy(deep=True)
    evil.patches[0].path = "../outside.txt"
    evil.patches[0].action = "create"
    with pytest.raises(PatchApplyError, match="Unsafe path"):
        apply_patchset(evil, work)
    assert not (tmp_path / "outside.txt").exists()


def test_patching_through_a_symlink_is_refused(snapshot, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    _, _, ps = build(snapshot, tmp_path)
    work = tmp_path / "work"
    shutil.copytree(snapshot, work)
    (work / ".github").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PatchApplyError, match="symlink"):
        apply_patchset(ps, work)
    assert list(outside.iterdir()) == []


def test_unpatchable_security_findings_are_skipped_not_guessed(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.py").write_text("import os\ndef f(c):\n    os.system(c)\n    eval(c)\n")
    adapter = OfflineAdapter()
    report = adapter.autopsy(repo, "r", tmp_path / "s", lambda r: None)
    roadmap = build_roadmap(report, BaselineReport(status="not_run"))
    ps = generate_patchset(
        roadmap, report, repo,
        partial(adapter.fix_lint, scratch=tmp_path / "s", observe=lambda r: None),
    )  # fmt: skip
    skipped = {s.title: s.reason for s in ps.skipped}
    assert any("os.system" in t and "person must decide" in r for t, r in skipped.items())
    assert any("eval" in t for t in skipped)
