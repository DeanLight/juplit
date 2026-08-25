"""Tests for artifact notebooks — the pairs whose `.ipynb` is committed with outputs."""

import subprocess
from pathlib import Path

import nbformat
import pytest
from nbformat.v4 import new_code_cell, new_notebook, new_output

from juplit.artifacts import (
    artifact_py_files,
    is_artifact,
    normalize,
    write_artifact,
)
from juplit.tasks import clean_notebooks, generate_notebooks, sync_notebooks

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
notebook_src_dirs = ["src"]
artifact_notebooks = ["experiments/**/*.py"]

[tool.jupytext]
formats = "ipynb,py:percent"
"""

PYPROJECT_NO_ARTIFACTS = """\
[tool.juplit]
notebook_src_dirs = ["src"]

[tool.jupytext]
formats = "ipynb,py:percent"
"""


def _make_project(root: Path, pyproject: str = PYPROJECT) -> tuple[Path, Path]:
    """A project with one ordinary pair (src/nb.py) and one artifact (experiments/e.py)."""
    (root / "pyproject.toml").write_text(pyproject)
    src = root / "src"
    src.mkdir()
    (src / "nb.py").write_text(PAIRED_HEADER)
    exp = root / "experiments"
    exp.mkdir()
    (exp / "e.py").write_text(PAIRED_HEADER)
    return src, exp


def _execute(ipynb: Path, text: str = "42\n") -> None:
    """Stand in for a real run: give every code cell an output and an execution count."""
    nb = nbformat.read(ipynb, as_version=4)
    for i, cell in enumerate(nb.cells, start=1):
        if cell.cell_type == "code":
            cell.outputs = [new_output("stream", name="stdout", text=text)]
            cell.execution_count = i
    nbformat.write(nb, ipynb)


def _outputs(ipynb: Path) -> list[list[str]]:
    nb = nbformat.read(ipynb, as_version=4)
    return [[o.get("text", "") for o in c.get("outputs", [])] for c in nb.cells]


# ── Registry ─────────────────────────────────────────────────────────────────

def test_is_artifact_matches_only_declared_globs(tmp_path, monkeypatch):
    src, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert is_artifact(exp / "e.py")
    assert not is_artifact(src / "nb.py")


def test_artifact_py_files_finds_pairs_outside_src_dirs(tmp_path, monkeypatch):
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert artifact_py_files() == [(exp / "e.py").resolve()]


def test_no_artifact_key_disables_the_feature(tmp_path, monkeypatch):
    _, exp = _make_project(tmp_path, PYPROJECT_NO_ARTIFACTS)
    monkeypatch.chdir(tmp_path)
    assert artifact_py_files() == []
    assert not is_artifact(exp / "e.py")


# ── Hygiene ──────────────────────────────────────────────────────────────────

def test_normalize_strips_running_state_but_keeps_outputs():
    cell = new_code_cell(
        "print('x')",
        outputs=[new_output("stream", name="stdout", text="done\n")],
        execution_count=7,
    )
    cell.metadata["execution"] = {"iopub.execute_input": "2026-08-25T00:00:00Z"}
    nb = new_notebook(cells=[cell])
    nb.metadata["widgets"] = {"state": {}}
    nb.metadata["language_info"] = {"name": "python", "version": "3.12.3"}

    assert normalize(nb) is True
    assert nb.cells[0].execution_count is None
    assert "execution" not in nb.cells[0].metadata
    assert "widgets" not in nb.metadata
    assert "version" not in nb.metadata["language_info"]
    assert nb.cells[0].outputs[0]["text"] == "done\n"
    assert normalize(nb) is False


def test_normalize_collapses_progress_bar_spam():
    text = "".join(f"\r{i}%" for i in range(0, 101, 10)) + "\ndone\n"
    nb = new_notebook(cells=[new_code_cell("fit()", outputs=[
        new_output("stream", name="stdout", text=text)])])
    normalize(nb)
    assert nb.cells[0].outputs[0]["text"] == "100%\ndone\n"


def test_write_artifact_normalizes_on_the_way_out(tmp_path):
    nb = new_notebook(cells=[new_code_cell("x", outputs=[
        new_output("stream", name="stdout", text="1\n")], execution_count=3)])
    out = tmp_path / "a.ipynb"
    write_artifact(out, nb)
    assert nbformat.read(out, as_version=4).cells[0].execution_count is None


# ── nb / sync / clean ────────────────────────────────────────────────────────

def test_nb_keeps_artifact_outputs_and_wipes_ordinary_ones(tmp_path, monkeypatch, capsys):
    src, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    _execute(src / "nb.ipynb")
    _execute(exp / "e.ipynb")
    capsys.readouterr()

    generate_notebooks()

    assert _outputs(exp / "e.ipynb") == [["42\n"]]      # the deliverable survives
    assert _outputs(src / "nb.ipynb") == [[]]           # unchanged behaviour
    assert "nb kept outputs (artifact)" in capsys.readouterr().out


def test_sync_keeps_artifact_outputs_when_the_py_moves(tmp_path, monkeypatch, capsys):
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    sync_notebooks()
    _execute(exp / "e.ipynb")
    py = exp / "e.py"
    py.write_text(py.read_text() + "\n# %%\nextra = 1\n")
    capsys.readouterr()

    sync_notebooks()

    assert _outputs(exp / "e.ipynb")[0] == ["42\n"]


def test_clean_keeps_artifacts_and_force_deletes_them(tmp_path, monkeypatch, capsys):
    src, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    _execute(exp / "e.ipynb")
    capsys.readouterr()

    clean_notebooks()
    out = capsys.readouterr().out
    assert (exp / "e.ipynb").exists()
    assert not (src / "nb.ipynb").exists()
    assert "clean kept artifact notebooks" in out

    clean_notebooks(force=True)
    assert not (exp / "e.ipynb").exists()


def test_gitignored_artifact_is_reported_and_ordinary_pairs_are_not(tmp_path, monkeypatch, capsys):
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("*.ipynb\n")
    generate_notebooks()
    capsys.readouterr()

    generate_notebooks()
    warning = next(l for l in capsys.readouterr().out.splitlines() if "GITIGNORED" in l)
    assert "experiments/e.py" in warning
    assert "src/nb.py" not in warning        # an ordinary pair is meant to be gitignored


def test_project_without_artifacts_is_unaffected(tmp_path, monkeypatch, capsys):
    src, exp = _make_project(tmp_path, PYPROJECT_NO_ARTIFACTS)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    _execute(src / "nb.ipynb")
    capsys.readouterr()

    generate_notebooks()
    out = capsys.readouterr().out

    assert _outputs(src / "nb.ipynb") == [[]]      # still wiped, as before
    assert not (exp / "e.ipynb").exists()          # experiments/ is not scanned
    assert "artifact" not in out

    clean_notebooks()
    assert "kept artifact" not in capsys.readouterr().out


def test_a_normalized_notebook_still_validates_against_the_nbformat_schema():
    """GitHub's rich diff refuses a notebook that fails validation, so the scrub must not
    produce one. `execution_count` is required on execute_result — nullable, not optional."""
    nb = new_notebook(cells=[new_code_cell(
        "1 + 1",
        outputs=[
            new_output("execute_result", data={"text/plain": "2"}, execution_count=7),
            new_output("stream", name="stdout", text="hi\n"),
        ],
        execution_count=7,
    )])

    normalize(nb)

    nbformat.validate(nb)                                    # raises if the scrub broke it
    result, stream = nb.cells[0].outputs
    assert result["execution_count"] is None                 # nulled, not removed
    assert "execution_count" not in stream                   # never valid on a stream


def test_write_artifact_produces_a_notebook_github_can_render(tmp_path, monkeypatch):
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    ipynb = exp / "e.ipynb"
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells[0].outputs = [new_output("execute_result", data={"text/plain": "42"},
                                      execution_count=3)]
    write_artifact(ipynb, nb)

    nbformat.validate(nbformat.read(ipynb, as_version=4))
