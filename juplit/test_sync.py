"""Tests for sync-direction reporting and conflict flagging in juplit.tasks."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from IPython.display import display
from jupytext import __version__ as jupytext_version

from juplit.tasks import generate_notebooks, sync_notebooks

PAIRED_HEADER = """\
# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
# ---

# %%
val = "seed"
"""

PYPROJECT = """\
[tool.juplit]
notebook_src_dirs = "src"

[tool.jupytext]
formats = "ipynb,py:percent"
"""


def _make_project(root: Path) -> Path:
    (root / "pyproject.toml").write_text(PYPROJECT)
    src = root / "src"
    src.mkdir()
    (src / "nb.py").write_text(PAIRED_HEADER)
    return src


def _bump_mtime(path: Path, newer_than: Path) -> None:
    """Force `path` to look strictly newer than `newer_than` for jupytext."""
    ref = newer_than.stat().st_mtime
    os.utime(path, (ref + 10, ref + 10))


def test_sync_reports_py_to_ipynb(tmp_path, monkeypatch, capsys):
    src = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()  # let jupytext settle .py metadata
    capsys.readouterr()

    py, ipynb = src / "nb.py", src / "nb.ipynb"
    py.write_text(py.read_text() + "\n# %%\nfrom_py = 1\n")
    _bump_mtime(py, ipynb)

    sync_notebooks()
    out = capsys.readouterr().out
    assert "synced py → ipynb: src/nb.py" in out
    assert "synced ipynb → py" not in out


def test_sync_reports_ipynb_to_py(tmp_path, monkeypatch, capsys):
    import nbformat

    src = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()  # let jupytext settle .py metadata
    capsys.readouterr()

    py, ipynb = src / "nb.py", src / "nb.ipynb"
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells.append(nbformat.v4.new_code_cell("from_nb = 2"))
    nbformat.write(nb, ipynb)
    _bump_mtime(ipynb, py)

    sync_notebooks()
    out = capsys.readouterr().out
    assert "synced ipynb → py: src/nb.py" in out
    assert "from_nb = 2" in py.read_text()


def test_sync_reports_unchanged(tmp_path, monkeypatch, capsys):
    _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()  # settle
    capsys.readouterr()

    sync_notebooks()
    assert capsys.readouterr().out == "Sync: nothing changed\n"


def test_sync_flags_conflict_when_both_sides_change(tmp_path, monkeypatch, capsys):
    """Both sides changed since the last sync with the .py newest: jupytext
    syncs py → ipynb (discarding the ipynb edits, a regenerable artifact) and
    the conflict is flagged. (The mirror case — both changed, .ipynb newer — is
    now caught earlier by the overwrite guard; see the guard tests.)"""
    import nbformat

    src = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()  # establish a clean baseline in .sync_hashes.json
    capsys.readouterr()

    py, ipynb = src / "nb.py", src / "nb.ipynb"

    # Edit BOTH sides since the last sync; make the .py win by mtime.
    py.write_text(py.read_text() + "\n# %%\nfrom_py = 1\n")
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells.append(nbformat.v4.new_code_cell("from_nb = 2"))
    nbformat.write(nb, ipynb)
    _bump_mtime(py, ipynb)

    sync_notebooks()
    out = capsys.readouterr().out
    assert "sync CONFLICT" in out
    assert "src/nb.py" in out
    # .py was newest → its edit is kept and flows into the .ipynb.
    assert "from_py = 1" in py.read_text()


def test_sync_no_conflict_when_only_one_side_changes(tmp_path, monkeypatch, capsys):
    src = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()  # baseline
    capsys.readouterr()

    py, ipynb = src / "nb.py", src / "nb.ipynb"
    py.write_text(py.read_text() + "\n# %%\nonly_py = 1\n")
    _bump_mtime(py, ipynb)

    sync_notebooks()
    out = capsys.readouterr().out
    assert "sync CONFLICT" not in out
    assert "synced py → ipynb: src/nb.py" in out


def test_same_basename_notebooks_do_not_collide(tmp_path, monkeypatch, capsys):
    """Regression: two paired notebooks sharing a basename across dirs must not
    collide in the sync state (basename keying flagged one as a false conflict
    and never stabilized). Keys are repo-root-relative paths, so a no-edit
    second sync reports nothing changed and touches neither file."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.juplit]\nnotebook_src_dirs = "pkg"\n\n'
        '[tool.jupytext]\nformats = "ipynb,py:percent"\n'
    )
    # Same basename (x.py), different directories AND different content.
    for sub, val in (("a", "aaa"), ("b", "bbb")):
        d = tmp_path / "pkg" / sub
        d.mkdir(parents=True)
        (d / "x.py").write_text(PAIRED_HEADER.replace('"seed"', f'"{val}"'))

    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()  # settle baseline
    capsys.readouterr()

    a_py = tmp_path / "pkg" / "a" / "x.py"
    b_py = tmp_path / "pkg" / "b" / "x.py"
    a_before, b_before = a_py.read_text(), b_py.read_text()

    sync_notebooks()  # no edits since baseline
    out = capsys.readouterr().out

    assert out == "Sync: nothing changed\n"
    assert a_py.read_text() == a_before
    assert b_py.read_text() == b_before


def test_sync_names_only_the_files_that_changed(tmp_path, monkeypatch, capsys):
    """With many paired files and one edit, the summary names the edited file
    and no other: unchanged files are not listed."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    src = tmp_path / "src"
    src.mkdir()
    for name in ("a", "b", "c"):
        (src / f"{name}.py").write_text(PAIRED_HEADER.replace('"seed"', f'"{name}"'))
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()
    capsys.readouterr()

    b = src / "b.py"
    b.write_text(b.read_text() + "\n# %%\nedited = 1\n")
    _bump_mtime(b, src / "b.ipynb")

    sync_notebooks()
    assert capsys.readouterr().out == "1 synced py → ipynb: src/b.py\n"


# ── No jupytext_version in headers ────────────────────────────────────────────

VERSION_FILTER = "-jupytext.text_representation.jupytext_version,-jupytext.notebook_metadata_filter"

OLD_VERSION_HEADER = """\
# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.13.0
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %%
val = "seed"
"""
MIGRATED_HEADER = OLD_VERSION_HEADER.replace("#       jupytext_version: 1.13.0\n", "")
ADD_FILTER_HINT = (
    f'add notebook_metadata_filter = "{VERSION_FILTER}" to [tool.jupytext] so Jupyter '
    "and plain jupytext leave it out too\n"
)

# The project either declares the filter in [tool.jupytext] or relies on juplit stripping the line.
CONFIGS = {
    "filter in pyproject": PYPROJECT + f'notebook_metadata_filter = "{VERSION_FILTER}"\n',
    "juplit strips it": PYPROJECT,
}


def _make_old_version_project(root: Path, pyproject: str = PYPROJECT) -> Path:
    (root / "pyproject.toml").write_text(pyproject)
    src = root / "src"
    src.mkdir()
    (src / "nb.py").write_text(OLD_VERSION_HEADER)
    return src


def _fake_jupytext(bin_dir: Path, version: str) -> Path:
    """A `jupytext` on PATH that behaves as an install of `version`; it touches `ran`."""
    bin_dir.mkdir()
    script = bin_dir / "jupytext"
    script.write_text(
        f"#!{sys.executable}\n"
        f"import pathlib, jupytext.header\n"
        f"pathlib.Path({str(bin_dir / 'ran')!r}).touch()\n"
        f"jupytext.header.__version__ = {version!r}\n"
        f"from jupytext.cli import jupytext\n"
        f"raise SystemExit(jupytext())\n"
    )
    script.chmod(0o755)
    return script


@pytest.mark.parametrize("pyproject", CONFIGS.values(), ids=CONFIGS.keys())
def test_fresh_sync_writes_no_version_line(tmp_path, monkeypatch, capsys, pyproject):
    """jupytext fills in the header's text_representation, but neither the
    version nor the filter that drops it reaches the .py, with or without the
    filter in pyproject."""
    src = _make_project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(pyproject)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()

    header = (src / "nb.py").read_text().split("# %%")[0]
    assert "format_name: percent" in header
    assert "formats: ipynb,py:percent" in header
    assert "jupytext_version" not in header
    assert "notebook_metadata_filter" not in header
    display(header)


@pytest.mark.parametrize("pyproject, hint", [
    (CONFIGS["filter in pyproject"], ""),
    (CONFIGS["juplit strips it"], ADD_FILTER_HINT),
], ids=CONFIGS.keys())
@pytest.mark.parametrize("version", ["1.13.0", jupytext_version], ids=["older", "installed"])
def test_old_header_is_migrated_once(tmp_path, monkeypatch, capsys, pyproject, hint, version):
    """A header written before this change loses its version line on the first
    sync, reported as a migration (not as a synced file), even when it names the
    installed jupytext and jupytext itself would leave it alone; the next sync
    has nothing to do. Without the filter in pyproject, the line says to add it."""
    src = _make_old_version_project(tmp_path, pyproject)
    (src / "nb.py").write_text(OLD_VERSION_HEADER.replace("1.13.0", version))
    monkeypatch.chdir(tmp_path)

    sync_notebooks()
    out = capsys.readouterr().out
    assert out == (
        "1 synced py → ipynb: src/nb.py\n"
        "removed jupytext_version from 1 notebook (one-time migration)\n" + hint
    )
    assert (src / "nb.py").read_text() == MIGRATED_HEADER

    sync_notebooks()
    assert capsys.readouterr().out == "Sync: nothing changed\n"
    assert (src / "nb.py").read_text() == MIGRATED_HEADER


def test_migration_of_an_existing_pair_is_not_a_sync(tmp_path, monkeypatch, capsys):
    """The dr-evo case: the pair exists and is in step, the .py still has the
    old line and is newest. Sync only migrates it."""
    src = _make_old_version_project(tmp_path, CONFIGS["filter in pyproject"])
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    py = src / "nb.py"
    py.write_text(OLD_VERSION_HEADER)
    _bump_mtime(py, src / "nb.ipynb")
    capsys.readouterr()

    sync_notebooks()
    assert capsys.readouterr().out == "removed jupytext_version from 1 notebook (one-time migration)\n"
    assert py.read_text() == MIGRATED_HEADER


@pytest.mark.parametrize("pyproject", CONFIGS.values(), ids=CONFIGS.keys())
def test_migration_with_the_ipynb_newer_finishes_in_one_sync(tmp_path, monkeypatch, capsys, pyproject):
    """A pair made before this change, with the .ipynb newest, so jupytext
    reads the .ipynb, which has not recorded the filter. One sync migrates the
    .py; the next has nothing to do, even once the .py is newest."""
    import nbformat

    src = _make_old_version_project(tmp_path, pyproject)
    monkeypatch.chdir(tmp_path)
    py, ipynb = src / "nb.py", src / "nb.ipynb"
    subprocess.run(["jupytext", "--to", "notebook", str(py)], check=True, capture_output=True)
    nb = nbformat.read(ipynb, as_version=4)
    nb.metadata["jupytext"].pop("notebook_metadata_filter", None)   # as an older juplit left it
    nbformat.write(nb, ipynb)
    py.write_text(OLD_VERSION_HEADER)
    then = ipynb.stat().st_mtime - 10       # the .py is older, as on any real disk
    os.utime(py, (then, then))
    capsys.readouterr()

    sync_notebooks()
    assert capsys.readouterr().out.startswith(
        "removed jupytext_version from 1 notebook (one-time migration)\n")
    assert py.read_text() == MIGRATED_HEADER

    _bump_mtime(py, ipynb)
    sync_notebooks()
    assert capsys.readouterr().out == "Sync: nothing changed\n"


@pytest.mark.parametrize("pyproject", CONFIGS.values(), ids=CONFIGS.keys())
def test_new_jupytext_version_changes_no_file(tmp_path, monkeypatch, capsys, pyproject):
    """Upgrading jupytext must not touch a single notebook: sync under a fake
    jupytext 9.9.9 rewrites neither half and reports nothing changed."""
    src = _make_project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(pyproject)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()
    py, ipynb = src / "nb.py", src / "nb.ipynb"
    before = py.read_bytes(), ipynb.read_bytes()
    capsys.readouterr()

    fake = _fake_jupytext(tmp_path / "bin", "9.9.9")
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    _bump_mtime(py, ipynb)

    sync_notebooks()
    assert (fake.parent / "ran").exists()
    assert capsys.readouterr().out == "Sync: nothing changed\n"
    assert (py.read_bytes(), ipynb.read_bytes()) == before


def test_fake_jupytext_would_stamp_its_version(monkeypatch):
    """The fake above is a real probe: patching the version is what an
    unfiltered write would put in the header."""
    import jupytext
    import jupytext.header

    monkeypatch.setattr(jupytext.header, "__version__", "9.9.9")
    text = jupytext.writes(jupytext.reads(OLD_VERSION_HEADER, fmt="py:percent"), fmt="py:percent")
    assert "jupytext_version: 9.9.9" in text


BODY_LOOKALIKE = """\
# %% [markdown]
# ---
#       jupytext_version: not a header
# ---
"""


@pytest.mark.parametrize("text, cleaned", [
    (OLD_VERSION_HEADER, MIGRATED_HEADER),
    (MIGRATED_HEADER, MIGRATED_HEADER),
    ("#!/usr/bin/env python\n" + OLD_VERSION_HEADER, "#!/usr/bin/env python\n" + MIGRATED_HEADER),
    (MIGRATED_HEADER + BODY_LOOKALIKE, MIGRATED_HEADER + BODY_LOOKALIKE),
    (BODY_LOOKALIKE, BODY_LOOKALIKE),
    (OLD_VERSION_HEADER.replace("\n", "\r\n"), MIGRATED_HEADER.replace("\n", "\r\n")),
], ids=["old header", "no version", "after a shebang", "body after header", "no header", "crlf"])
def test_strip_jupytext_version_touches_only_the_header(text, cleaned):
    from juplit.tasks import strip_jupytext_version

    assert strip_jupytext_version(text) == cleaned


def test_nb_also_migrates_an_old_header(tmp_path, monkeypatch, capsys):
    src = _make_old_version_project(tmp_path, CONFIGS["filter in pyproject"])
    monkeypatch.chdir(tmp_path)

    generate_notebooks()
    out = capsys.readouterr().out
    assert "removed jupytext_version from 1 notebook (one-time migration)\n" in out
    assert (src / "nb.py").read_text() == MIGRATED_HEADER


# ── Overwrite guard (edited .py vs newer stale .ipynb) ────────────────────────

def _edit_py(src):
    """Edit nb.py and return (py, ipynb) paths; caller sets mtimes."""
    py, ipynb = src / "nb.py", src / "nb.ipynb"
    py.write_text(py.read_text() + "\n# %%\nedited_py = 1\n")
    return py, ipynb


def test_guard_blocks_overwrite_of_edited_py(tmp_path, monkeypatch, capsys):
    """.py edited since last sync + a newer (stale) .ipynb → jupytext would
    regenerate the .py from the stale ipynb. The guard must block it, leave the
    .py untouched, and exit non-zero."""
    src = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()  # baseline: .py and .ipynb agree
    capsys.readouterr()

    py, ipynb = _edit_py(src)
    edited = py.read_text()
    _bump_mtime(ipynb, py)  # stale ipynb is newer by mtime → would overwrite .py

    with pytest.raises(SystemExit) as exc:
        sync_notebooks()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "OVERWRITE BLOCKED" in out
    assert "src/nb.py" in out
    assert py.read_text() == edited  # .py edits preserved, not clobbered


def test_guard_reblocks_until_resolved(tmp_path, monkeypatch, capsys):
    """A blocked file keeps its old baseline, so a second sync blocks again
    (the baseline was not silently advanced to the divergent state)."""
    src = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()
    capsys.readouterr()

    py, ipynb = _edit_py(src)
    _bump_mtime(ipynb, py)

    for _ in range(2):
        with pytest.raises(SystemExit):
            sync_notebooks()
        assert "OVERWRITE BLOCKED" in capsys.readouterr().out


def test_guard_allows_py_newest(tmp_path, monkeypatch, capsys):
    """.py edited AND newest → normal py → ipynb sync, no block."""
    src = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()
    capsys.readouterr()

    py, ipynb = _edit_py(src)
    _bump_mtime(py, ipynb)  # .py is newest → legitimate py → ipynb

    sync_notebooks()
    out = capsys.readouterr().out
    assert "OVERWRITE BLOCKED" not in out
    assert "synced py → ipynb: src/nb.py" in out


def test_guard_preserves_bidirectional_ipynb_edit(tmp_path, monkeypatch, capsys):
    """.py unchanged since baseline + .ipynb edited & newer → intended Jupyter
    edit still flows ipynb → py (guard only applies to edited .py files)."""
    import nbformat

    src = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()
    capsys.readouterr()

    py, ipynb = src / "nb.py", src / "nb.ipynb"
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells.append(nbformat.v4.new_code_cell("from_nb = 9"))
    nbformat.write(nb, ipynb)
    _bump_mtime(ipynb, py)

    sync_notebooks()
    out = capsys.readouterr().out
    assert "OVERWRITE BLOCKED" not in out
    assert "synced ipynb → py: src/nb.py" in out
    assert "from_nb = 9" in py.read_text()


def test_save_hashes_keeps_both_groups(tmp_path, monkeypatch, capsys):
    """Two files, one guarded (edited .py) + one normal: the single post-sync
    save must retain BOTH baselines (regression against a per-group save that
    would clobber the other group's state)."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.juplit]\nnotebook_src_dirs = "pkg"\n\n'
        '[tool.jupytext]\nformats = "ipynb,py:percent"\n'
    )
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "edited.py").write_text(PAIRED_HEADER.replace('"seed"', '"e"'))
    (pkg / "plain.py").write_text(PAIRED_HEADER.replace('"seed"', '"p"'))
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()  # baseline for both
    capsys.readouterr()

    # Edit one .py (guarded) but keep it newest so it syncs cleanly.
    ep = pkg / "edited.py"
    ep.write_text(ep.read_text() + "\n# %%\nx = 1\n")
    _bump_mtime(ep, pkg / "edited.ipynb")

    sync_notebooks()
    import json
    state = json.loads((tmp_path / ".sync_hashes.json").read_text())
    assert "pkg/edited.py" in state and "pkg/plain.py" in state
