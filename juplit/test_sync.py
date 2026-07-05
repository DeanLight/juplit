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
    assert "synced py → ipynb: nb.py" in out
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
    assert "synced ipynb → py: nb.py" in out
    assert "from_nb = 2" in py.read_text()


def test_sync_reports_unchanged(tmp_path, monkeypatch, capsys):
    _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()  # settle
    capsys.readouterr()

    sync_notebooks()
    out = capsys.readouterr().out
    assert "sync unchanged: nb.py" in out
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
    assert "nb.py" in out
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
    assert "synced py → ipynb: nb.py" in out
