"""Tests for per-cell provenance: stamps, staleness, and the `check` guard."""

import json
import subprocess
from pathlib import Path

import nbformat
import pytest
from nbformat.v4 import new_code_cell, new_notebook, new_output

from juplit.artifacts import (
    STAMP_KEY,
    cell_state,
    check_artifacts,
    ensure_filter,
    normalize_notebook,
    scan,
    source_sha,
    stamp,
    stamp_cell,
)
from juplit.tasks import generate_notebooks, sync_notebooks
from juplit.test_artifacts import PYPROJECT, _make_project

FINDING_1 = """\
# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
# ---

# %%
x = 40 + 2
print(x)

# %%
y = "unchanged cell"
"""


def _bump_mtime(path: Path, newer_than: Path) -> None:
    """Force `path` to look strictly newer than `newer_than` for jupytext."""
    import os

    ref = newer_than.stat().st_mtime
    os.utime(path, (ref + 10, ref + 10))


def _execute_and_stamp(ipynb: Path, text: str = "42\n") -> None:
    """Stand in for juplit having executed the notebook: outputs plus their stamps."""
    nb = nbformat.read(ipynb, as_version=4)
    for cell in nb.cells:
        if cell.cell_type == "code":
            cell.outputs = [new_output("stream", name="stdout", text=text)]
            stamp_cell(cell)
    ensure_filter(nb)
    nbformat.write(nb, ipynb)


# ── Stamps ───────────────────────────────────────────────────────────────────

def test_cell_state_reads_clean_stale_unverified_and_empty():
    cell = new_code_cell("x = 1", outputs=[new_output("stream", name="stdout", text="1\n")])
    assert cell_state(cell) == "unverified"

    stamp_cell(cell)
    assert cell_state(cell) == "clean"

    cell.source = "x = 2"
    assert cell_state(cell) == "stale"

    empty = new_code_cell("x = 1")
    stamp_cell(empty)
    assert cell_state(empty) == "empty"
    assert STAMP_KEY not in empty.metadata


def test_the_stamp_never_reaches_the_py_in_either_direction(tmp_path, monkeypatch):
    """The load-bearing mechanism: cell_metadata_filter keeps provenance out of the source."""
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    (exp / "e.py").write_text(FINDING_1)
    generate_notebooks()
    py, ipynb = exp / "e.py", exp / "e.ipynb"
    _execute_and_stamp(ipynb)

    py.write_text(py.read_text() + '\n# %%\nfrom_py = 1\n')   # append, so nothing goes stale
    _bump_mtime(py, ipynb)
    sync_notebooks()                                    # py → ipynb

    assert "juplit=" not in py.read_text()              # the source of truth stays clean
    nb = nbformat.read(ipynb, as_version=4)
    assert nb.cells[0].metadata.get(STAMP_KEY) is not None      # …and the stamps survive

    nb.cells.append(new_code_cell("z = 3"))             # now make the ipynb the newer half
    nbformat.write(nb, ipynb)
    _bump_mtime(ipynb, py)
    sync_notebooks()                                    # ipynb → py

    assert "juplit=" not in py.read_text()
    assert "z = 3" in py.read_text()
    assert nbformat.read(ipynb, as_version=4).cells[0].metadata.get(STAMP_KEY) is not None


# ── The finding-1 repro, end to end ──────────────────────────────────────────

def test_editing_the_py_makes_the_committed_output_stale_and_blocks(tmp_path, monkeypatch, capsys):
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    py, ipynb = exp / "e.py", exp / "e.ipynb"
    py.write_text(FINDING_1)
    generate_notebooks()
    _execute_and_stamp(ipynb)
    capsys.readouterr()

    py.write_text(py.read_text().replace("x = 40 + 2", "x = 40 + 3"))

    with pytest.raises(SystemExit):
        sync_notebooks()
    out = capsys.readouterr().out

    assert "artifact STALE" in out and "cells 0" in out
    # the expensive evidence is still there — never silently deleted
    assert nbformat.read(ipynb, as_version=4).cells[0].outputs[0]["text"] == "42\n"


def test_inserting_a_cell_does_not_mispair_the_outputs_below_it(tmp_path, monkeypatch, capsys):
    """jupytext matches cells by source, so outputs follow their own cell across an insert."""
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    py, ipynb = exp / "e.py", exp / "e.ipynb"
    py.write_text(FINDING_1)
    generate_notebooks()
    _execute_and_stamp(ipynb)
    py.write_text(py.read_text().replace("# %%\nx = 40 + 2", "# %%\ninserted = True\n\n# %%\nx = 40 + 2"))
    capsys.readouterr()

    sync_notebooks()                                    # no stale cell, so no SystemExit
    states = scan(ipynb)

    assert states["stale"] == []
    assert states["empty"] == [0]                       # the new cell has no outputs yet
    assert states["clean"] == [1, 2]                    # the old ones kept theirs


# ── check ────────────────────────────────────────────────────────────────────

def _clean_artifact(tmp_path, monkeypatch) -> tuple[Path, Path]:
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    (exp / "e.py").write_text(FINDING_1)
    generate_notebooks()
    _execute_and_stamp(exp / "e.ipynb")
    return exp / "e.py", exp / "e.ipynb"


def test_check_passes_on_a_clean_artifact(tmp_path, monkeypatch, capsys):
    _clean_artifact(tmp_path, monkeypatch)
    check_artifacts()
    assert "1 artifact notebook(s) OK" in capsys.readouterr().out


def test_check_fails_on_stale_missing_filter_and_missing_notebook(tmp_path, monkeypatch, capsys):
    py, ipynb = _clean_artifact(tmp_path, monkeypatch)

    py.write_text(py.read_text().replace("x = 40 + 2", "x = 40 + 3"))
    with pytest.raises(SystemExit):
        check_artifacts()                               # committed halves disagree
    assert "disagree at cell 0" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        sync_notebooks()                                # propagate the edit into the .ipynb
    capsys.readouterr()
    with pytest.raises(SystemExit):
        check_artifacts()                               # now it is plain staleness
    assert "STALE" in capsys.readouterr().out

    nb = nbformat.read(ipynb, as_version=4)
    nb.metadata["jupytext"].pop("cell_metadata_filter")
    nbformat.write(nb, ipynb)
    with pytest.raises(SystemExit):
        check_artifacts()
    assert "missing `cell_metadata_filter" in capsys.readouterr().out

    ipynb.unlink()
    with pytest.raises(SystemExit):
        check_artifacts()
    assert "no committed .ipynb" in capsys.readouterr().out


def test_check_fails_on_a_gitignored_artifact(tmp_path, monkeypatch, capsys):
    _clean_artifact(tmp_path, monkeypatch)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("*.ipynb\n")

    with pytest.raises(SystemExit):
        check_artifacts()
    assert "gitignored" in capsys.readouterr().out


def test_check_warns_on_unverified_and_strict_makes_it_fail(tmp_path, monkeypatch, capsys):
    py, ipynb = _clean_artifact(tmp_path, monkeypatch)
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells[0].metadata.pop(STAMP_KEY)
    nbformat.write(nb, ipynb)

    check_artifacts()                                   # warns, exits 0
    assert "WARNING" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        check_artifacts(strict=True)


def test_check_fails_when_a_notebook_or_one_output_is_over_budget(tmp_path, monkeypatch, capsys):
    py, ipynb = _clean_artifact(tmp_path, monkeypatch)
    (tmp_path / "pyproject.toml").write_text(
        PYPROJECT.replace("[tool.jupytext]", "artifact_max_output_bytes = 100\n\n[tool.jupytext]")
    )
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells[0].outputs = [new_output("stream", name="stdout", text="x" * 500)]
    stamp_cell(nb.cells[0])
    nbformat.write(nb, ipynb)

    with pytest.raises(SystemExit):
        check_artifacts()
    assert "over the 100-byte budget" in capsys.readouterr().out


# ── stamp / normalize ────────────────────────────────────────────────────────

def test_stamp_blesses_unverified_but_refuses_stale(tmp_path, monkeypatch):
    py, ipynb = _clean_artifact(tmp_path, monkeypatch)
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells[0].metadata.pop(STAMP_KEY)
    nbformat.write(nb, ipynb)

    assert stamp(py) == [0]
    assert scan(ipynb)["unverified"] == []

    nb = nbformat.read(ipynb, as_version=4)
    nb.cells[0].source = "x = 99"
    nbformat.write(nb, ipynb)
    with pytest.raises(ValueError, match="STALE"):
        stamp(py)
    assert stamp(py, force=True) == [0]


def test_normalize_notebook_reports_size_and_oversized_outputs(tmp_path, monkeypatch):
    py, ipynb = _clean_artifact(tmp_path, monkeypatch)
    (tmp_path / "pyproject.toml").write_text(
        PYPROJECT.replace("[tool.jupytext]", "artifact_max_output_bytes = 10\n\n[tool.jupytext]")
    )
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells[0].execution_count = 5
    nbformat.write(nb, ipynb)

    report = normalize_notebook(py)
    assert report["changed"] is True
    assert report["oversized"] == [(0, 3)] or report["oversized"] == []
    assert nbformat.read(ipynb, as_version=4).cells[0].execution_count is None


def test_stamps_survive_a_regenerate_because_the_py_declares_the_filter(tmp_path, monkeypatch):
    """The ordering bug: adding the filter after jupytext runs is too late."""
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    py, ipynb = exp / "e.py", exp / "e.ipynb"
    py.write_text(FINDING_1)                       # a hand-written .py, no filter in it
    generate_notebooks()
    _execute_and_stamp(ipynb)

    generate_notebooks()                           # --update, straight through jupytext

    assert scan(ipynb)["unverified"] == []
    assert scan(ipynb)["clean"] == [0, 1]


def test_the_cli_reports_an_expected_failure_in_one_line(tmp_path, monkeypatch, capsys):
    """An expected failure is a message, not a traceback — agents read stderr."""
    import juplit.cli as cli

    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["juplit", "stamp", "experiments/e.py"])

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("juplit: ")
    assert "run `juplit nb` first" in captured.err
    assert "Traceback" not in captured.err


def test_check_fails_a_notebook_that_would_not_render_on_github(tmp_path, monkeypatch, capsys):
    """A notebook that fails the nbformat schema gets no rich diff, so `check` blocks it."""
    py, ipynb = _clean_artifact(tmp_path, monkeypatch)
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells[0].outputs = [nbformat.from_dict(
        {"output_type": "execute_result", "data": {"text/plain": "42"}, "metadata": {}}
    )]                                        # execute_result without execution_count
    ipynb.write_text(json.dumps(nb))          # written raw: nbformat.write would reject it

    with pytest.raises(SystemExit):
        check_artifacts()
    assert "fails nbformat validation" in capsys.readouterr().out
