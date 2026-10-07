"""pytest plugin: tells `juplit.test()` which modules belong to this pytest run.

Registered as the `pytest11` entry point `juplit`, so pytest loads it by itself.
"""

import pytest

from juplit import testing
from juplit.tasks import _get_src_dirs


def pytest_configure(config: pytest.Config) -> None:
    """Record this run's test roots for `juplit.test()`: the `notebook_src_dirs` of the
    pyproject at `config.rootpath`, resolved. Never raises; an unreadable pyproject
    leaves `<rootpath>/src`."""
    root = config.rootpath
    try:
        dirs = _get_src_dirs(root / "pyproject.toml")
    except (OSError, TypeError, ValueError):
        dirs = [root / "src"]
    testing._TEST_ROOTS = tuple(d.resolve() for d in dirs)
