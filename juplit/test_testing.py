"""Tests for `juplit.test()`: which modules run their `if test():` blocks."""

import importlib.util
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from juplit import pytest_plugin, testing

pytest_plugins = ["pytester"]

#: A module that records what `test()` answered for it.
PROBE = """\
from juplit import test
ANSWER = test()
"""


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def probes(tmp_path, monkeypatch):
    """Two copies of PROBE, one inside the test root `src/` and one outside it."""
    for d in ("src", "elsewhere"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "probe.py").write_text(PROBE)
    monkeypatch.setattr(testing, "_TEST_ROOTS", ((tmp_path / "src").resolve(),))
    return tmp_path


def test_module_inside_the_test_roots_runs_its_blocks(probes):
    assert _load(probes / "src" / "probe.py", "probe_in").ANSWER is True


def test_module_outside_the_test_roots_does_not(probes):
    assert _load(probes / "elsewhere" / "probe.py", "probe_out").ANSWER is False


def test_main_runs_its_blocks_wherever_it_lives(probes):
    ran = runpy.run_path(str(probes / "elsewhere" / "probe.py"), run_name="__main__")
    assert ran["ANSWER"] is True


def test_module_without_a_file_does_not(probes):
    namespace = {"__name__": "fileless"}
    exec(PROBE, namespace)
    assert namespace["ANSWER"] is False


def test_without_pytest_imported_nothing_runs(probes, monkeypatch):
    monkeypatch.delitem(sys.modules, "pytest")
    assert _load(probes / "src" / "probe.py", "probe_nopytest").ANSWER is False


def test_without_the_plugin_the_cwd_pyproject_decides(probes, monkeypatch):
    (probes / "pyproject.toml").write_text('[tool.juplit]\nnotebook_src_dirs = ["elsewhere"]\n')
    monkeypatch.chdir(probes)
    monkeypatch.setattr(testing, "_TEST_ROOTS", None)
    assert _load(probes / "elsewhere" / "probe.py", "probe_cwd_in").ANSWER is True
    assert _load(probes / "src" / "probe.py", "probe_cwd_out").ANSWER is False


def test_plugin_records_the_run_pyproject_dirs(pytester):
    pytester.makepyprojecttoml('[tool.juplit]\nnotebook_src_dirs = ["lib", "docs"]\n')
    pytester.makepyfile(test_roots="""
        from pathlib import Path
        from juplit import testing

        def test_roots():
            root = Path.cwd().resolve()
            assert testing._TEST_ROOTS == (root / "lib", root / "docs")
    """)
    pytester.runpytest_subprocess().assert_outcomes(passed=1)


@pytest.mark.parametrize("pyproject", [
    None,
    "this is [not toml",
    "[tool.juplit]\nnotebook_src_dirs = 3\n",
])
def test_plugin_falls_back_to_src_when_the_pyproject_says_nothing_usable(
        tmp_path, monkeypatch, pyproject):
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject)
    monkeypatch.setattr(testing, "_TEST_ROOTS", None)
    pytest_plugin.pytest_configure(SimpleNamespace(rootpath=tmp_path))
    assert testing._TEST_ROOTS == ((tmp_path / "src").resolve(),)


def test_a_run_collects_its_own_blocks_and_not_an_installed_packages(pytester):
    """The #102 case: a project's pytest run imports an installed package that is itself
    written with `if test():` blocks. Only the project's own blocks may run."""
    site = pytester.mkdir("site_packages")
    (site / "installed.py").write_text(
        "from juplit import test\n"
        "if test():\n"
        "    raise AssertionError('an installed package ran its blocks')\n"
    )
    pytester.makepyprojecttoml(
        '[tool.juplit]\nnotebook_src_dirs = ["pkg"]\n'
        '[tool.pytest.ini_options]\npython_files = ["*.py"]\npython_functions = ["test_*"]\n'
    )
    pkg = pytester.mkdir("pkg")
    (pkg / "mod.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(site)!r})\n"
        "import installed\n"
        "from juplit import test\n"
        "RAN = []\n"
        "if test():\n"
        "    RAN.append('mod')\n"
        "def test_own_blocks_ran():\n"
        "    assert RAN == ['mod']\n"
    )
    pytester.runpytest_subprocess("pkg").assert_outcomes(passed=1)
