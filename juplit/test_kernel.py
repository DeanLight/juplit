"""Tests for the detached kernel and `juplit try`.

Marked `kernel` and skipped when no python3 kernelspec is installed, so a CI box
without ipykernel stays green.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from juplit import kernel as kernel_module
from juplit.test_artifacts import _make_project
from juplit.trying import load_last_run, try_code

pytestmark = pytest.mark.kernel


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
def project(tmp_path, monkeypatch):
    _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    for row in kernel_module.status():
        kernel_module.stop(row["name"])


def _texts(outputs) -> str:
    return "".join(o.get("text", "") for o in outputs)


def test_execute_returns_a_result_and_start_is_idempotent(project):
    first = kernel_module.start()
    assert kernel_module.alive()
    assert kernel_module.start()["pid"] == first["pid"]

    outputs = kernel_module.execute("1 + 1")
    assert outputs[0]["output_type"] == "execute_result"
    assert outputs[0]["data"]["text/plain"] == "2"


def test_the_kernel_survives_separate_cli_processes(project):
    """The acceptance criterion: state set in one invocation is visible in the next.

    Runs out of process on purpose — an in-process test passes even against a kernel
    that dies with its launcher.
    """
    def juplit(*args) -> str:
        result = subprocess.run([sys.executable, "-m", "juplit.cli", *args],
                                cwd=project, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout

    juplit("kernel", "start")
    juplit("try", "zz = 123; print('set')")
    assert "123" in juplit("try", "print(zz)")


def test_the_kernelspec_argv_is_resolved_to_this_interpreter(project):
    """A bare `python` in the kernelspec resolves to the system interpreter in a venv."""
    conn = kernel_module.kernels_dir() / "x-connection.json"
    argv = kernel_module._kernel_argv("python3", conn)
    assert argv[0] == sys.executable
    assert str(conn) in argv


def test_an_exception_comes_back_as_an_error_output_not_a_raise(project):
    kernel_module.start()
    outputs = kernel_module.execute("1 / 0")
    assert outputs[0]["output_type"] == "error"
    assert outputs[0]["ename"] == "ZeroDivisionError"


def test_a_hanging_snippet_is_interrupted_and_reported(project):
    kernel_module.start()
    with pytest.raises(RuntimeError, match="interrupted"):
        kernel_module.execute("import time; time.sleep(30)", timeout=2)
    assert kernel_module.execute("2 + 2")[0]["data"]["text/plain"] == "4"


def test_named_kernels_are_isolated(project):
    kernel_module.start("a")
    kernel_module.start("b")
    kernel_module.execute("shared = 'from a'", name="a")
    assert "NameError" in str(kernel_module.execute("print(shared)", name="b"))


def test_execute_without_a_kernel_says_how_to_start_one(project):
    with pytest.raises(RuntimeError, match="juplit kernel start"):
        kernel_module.execute("1 + 1")


def test_stop_removes_the_session_and_its_sockets(project):
    session = kernel_module.start()
    assert kernel_module.stop() is True
    assert not kernel_module.session_path().exists()
    assert not Path(session["connection_file"]).exists()
    assert kernel_module.stop() is False


def test_clean_reaps_the_kernels(project, capsys):
    from juplit.tasks import clean_notebooks

    kernel_module.start()
    clean_notebooks()
    assert "clean stopped kernels" in capsys.readouterr().out
    assert not kernel_module.alive()


# ── try ──────────────────────────────────────────────────────────────────────

def test_try_prints_output_writes_nothing_and_remembers_the_attempt(project, capsys):
    kernel_module.start()
    exp = project / "experiments"
    before = (exp / "e.py").read_bytes()

    try_code(code="print('hello from try')")

    assert "hello from try" in capsys.readouterr().out
    assert (exp / "e.py").read_bytes() == before
    assert not (exp / "e.ipynb").exists()
    assert load_last_run()["source"] == "print('hello from try')"


def test_try_runs_named_cells_of_a_notebook_without_touching_it(project, capsys):
    kernel_module.start()
    py = project / "experiments" / "e.py"
    py.write_text(py.read_text() + "\n# %%\nprint('cell one')\n\n# %%\nprint('cell two')\n")
    before = py.read_bytes()

    try_code(nb=py, cells="1")
    out = capsys.readouterr().out

    assert "cell one" in out and "cell two" not in out
    assert py.read_bytes() == before


def test_try_rejects_ambiguous_input(project):
    with pytest.raises(ValueError, match="exactly one"):
        try_code(code="1", file=Path("x.py"))
