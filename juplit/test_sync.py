"""Tests for sync-direction reporting and conflict flagging in juplit.tasks."""

import os
from pathlib import Path

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
    out = capsys.readouterr().out
    assert "sync unchanged: src/nb.py" in out
    assert "synced py → ipynb" not in out
    assert "synced ipynb → py" not in out


def test_sync_flags_conflict_when_both_sides_change(tmp_path, monkeypatch, capsys):
    import nbformat

    src = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()  # establish a clean baseline in .sync_hashes.json
    capsys.readouterr()

    py, ipynb = src / "nb.py", src / "nb.ipynb"

    # Edit BOTH sides since the last sync; make the .ipynb win by mtime.
    py.write_text(py.read_text() + "\n# %%\nfrom_py = 1\n")
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells.append(nbformat.v4.new_code_cell("from_nb = 2"))
    nbformat.write(nb, ipynb)
    _bump_mtime(ipynb, py)

    sync_notebooks()
    out = capsys.readouterr().out
    assert "sync CONFLICT" in out
    assert "src/nb.py" in out
    # jupytext kept the .ipynb (newer mtime); the .py edit was overwritten.
    assert "from_nb = 2" in py.read_text()
    assert "from_py = 1" not in py.read_text()


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
    second sync reports both as unchanged and touches neither file."""
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

    assert "sync CONFLICT" not in out
    assert "synced py → ipynb" not in out
    assert "synced ipynb → py" not in out
    assert "pkg/a/x.py" in out and "pkg/b/x.py" in out  # both distinguishable
    assert a_py.read_text() == a_before
    assert b_py.read_text() == b_before
