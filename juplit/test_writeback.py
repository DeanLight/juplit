"""Tests for the two commands that change a committed notebook: add-cell and run."""

import subprocess
from pathlib import Path

import nbformat
import pytest
from nbformat.v4 import new_output

from juplit import kernel as kernel_module
from juplit.artifacts import add_cell, cell_state, run_cells, scan, stamp_cell
from juplit.tasks import generate_notebooks, sync_notebooks
from juplit.test_artifacts import _make_project
from juplit.trying import try_code


def _has_kernelspec() -> bool:
    try:
        from jupyter_client.kernelspec import KernelSpecManager

        KernelSpecManager().get_kernel_spec("python3")
        return True
    except Exception:
        return False


pytestmark = [pytest.mark.kernel,
              pytest.mark.skipif(not _has_kernelspec(), reason="no python3 kernelspec")]


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    """A git repo with one artifact notebook, generated and committed."""
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    yield exp / "e.py", exp / "e.ipynb"
    for row in kernel_module.status():
        kernel_module.stop(row["name"])


def _numstat(repo: Path, path: Path) -> tuple[int, int]:
    out = subprocess.run(["git", "diff", "--numstat", "--", str(path)],
                         cwd=repo, capture_output=True, text=True).stdout.split()
    return (int(out[0]), int(out[1])) if out else (0, 0)


# ── The agent loop, end to end ───────────────────────────────────────────────

def test_a_failed_attempt_never_reaches_the_notebook_and_a_good_one_appends_cleanly(artifact, capsys):
    py, ipynb = artifact
    kernel_module.start()
    before = ipynb.read_bytes()

    try_code(code="1 / 0")                                  # the failed attempt
    assert ipynb.read_bytes() == before

    try_code(code="print('mean 0.67')")                     # the good one
    from juplit.trying import load_last_run

    last = load_last_run()
    index = add_cell(py, last["source"], last["outputs"])

    nb = nbformat.read(ipynb, as_version=4)
    assert nb.cells[index].source == "print('mean 0.67')"
    assert "0.67" in nb.cells[index].outputs[0]["text"]
    assert cell_state(nb.cells[index]) == "clean"
    assert not any(o.get("output_type") == "error"
                   for cell in nb.cells for o in cell.get("outputs", []))
    assert "print('mean 0.67')" in py.read_text()

    added, deleted = _numstat(py.parent.parent, ipynb)      # diff locality
    assert added > 0 and deleted == 0


def test_add_cell_refuses_when_the_halves_disagree_and_changes_nothing(artifact):
    py, ipynb = artifact
    py.write_text(py.read_text() + "\n# %%\nunsynced = 1\n")
    py_before, ipynb_before = py.read_bytes(), ipynb.read_bytes()

    with pytest.raises(ValueError, match="out of sync"):
        add_cell(py, "x = 1", [])

    assert py.read_bytes() == py_before
    assert ipynb.read_bytes() == ipynb_before


def test_add_cell_honours_an_index_and_rejects_a_bad_one(artifact):
    py, ipynb = artifact
    add_cell(py, "first = True", [], index=0)
    assert nbformat.read(ipynb, as_version=4).cells[0].source == "first = True"
    with pytest.raises(ValueError, match="out of range"):
        add_cell(py, "nope = True", [], index=99)


def test_add_cell_leaves_sync_with_nothing_to_report(artifact, capsys):
    py, _ = artifact
    add_cell(py, "later = 1", [])
    capsys.readouterr()

    sync_notebooks()

    assert "CONFLICT" not in capsys.readouterr().out


def test_add_cell_refuses_a_pair_that_is_not_declared(artifact):
    py, _ = artifact
    ordinary = py.parent.parent / "src" / "nb.py"
    with pytest.raises(ValueError, match="not in artifact_notebooks"):
        add_cell(ordinary, "x = 1", [])


# ── run ──────────────────────────────────────────────────────────────────────

def _seed_two_cells(py: Path, ipynb: Path) -> None:
    py.write_text(py.read_text() + "\n# %%\nprint('first')\n\n# %%\nprint('second')\n")
    generate_notebooks()
    nb = nbformat.read(ipynb, as_version=4)
    for cell in nb.cells:
        cell.outputs = [new_output("stream", name="stdout", text="stale text\n")]
        stamp_cell(cell)
    nbformat.write(nb, ipynb)


def test_run_stale_repairs_only_the_stale_cell(artifact):
    py, ipynb = artifact
    _seed_two_cells(py, ipynb)
    kernel_module.start()
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells[1].source = "print('first, edited')"           # only this one goes stale
    nbformat.write(nb, ipynb)
    untouched = nbformat.read(ipynb, as_version=4).cells[2].outputs

    report = run_cells(py, stale_only=True)

    assert report["executed"] == [1]
    nb = nbformat.read(ipynb, as_version=4)
    assert "first, edited" in nb.cells[1].outputs[0]["text"]
    assert nb.cells[2].outputs == untouched
    assert scan(ipynb)["stale"] == []


def test_run_all_restarts_the_kernel_first(artifact):
    py, ipynb = artifact
    _seed_two_cells(py, ipynb)
    kernel_module.start()
    kernel_module.execute("leftover = 'from the old kernel'")

    run_cells(py, all_cells=True)

    outputs = kernel_module.execute("print(leftover)")
    assert any(o.get("output_type") == "error" for o in outputs)


def test_run_writes_an_error_output_and_reports_the_cell(artifact):
    py, ipynb = artifact
    _seed_two_cells(py, ipynb)
    kernel_module.start()
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells[1].source = "1 / 0"
    nbformat.write(nb, ipynb)

    report = run_cells(py, cells=[1])

    assert report["failed"] == [1]
    assert nbformat.read(ipynb, as_version=4).cells[1].outputs[0]["ename"] == "ZeroDivisionError"


def test_run_requires_exactly_one_selector(artifact):
    py, ipynb = artifact
    _seed_two_cells(py, ipynb)
    with pytest.raises(ValueError, match="exactly one"):
        run_cells(py)
    with pytest.raises(ValueError, match="exactly one"):
        run_cells(py, cells=[1], stale_only=True)


def test_run_refuses_a_markdown_cell(artifact):
    py, ipynb = artifact
    py.write_text(py.read_text() + "\n# %% [markdown]\n# just prose\n")
    generate_notebooks()
    kernel_module.start()
    last = len(nbformat.read(ipynb, as_version=4).cells) - 1
    with pytest.raises(ValueError, match="markdown"):
        run_cells(py, cells=[last])
