"""test() helper — separates exportable logic from inline tests."""

import sys
from pathlib import Path

#: The directories whose modules run their `if test():` blocks under pytest: the
#: `notebook_src_dirs` of the pytest run's own pyproject, resolved. Set by
#: `juplit.pytest_plugin`; None until it runs.
_TEST_ROOTS: tuple[Path, ...] | None = None


def test() -> bool:
    """True when the calling module is run directly (`__main__`), or under a pytest run
    for a module inside that run's own `[tool.juplit] notebook_src_dirs`.

    Use this to gate test code in percent-format notebook files so that tests
    run interactively (in Jupyter) and under pytest, but never on import — and
    never for an installed package that happens to be imported by someone else's
    pytest run.

    Example::

        # %%
        from juplit import test

        # %%
        def add(a, b):
            return a + b

        # %%
        if test():
            assert add(1, 2) == 3
    """
    caller = sys._getframe(1).f_globals
    if caller.get("__name__") == "__main__":
        return True
    if "pytest" not in sys.modules:
        return False
    file = caller.get("__file__")
    if not file:
        return False
    path = Path(file).resolve()
    return any(path.is_relative_to(root) for root in _test_roots())


def _test_roots() -> tuple[Path, ...]:
    """The plugin's roots, or, when it never ran, the cwd's pyproject's src dirs."""
    if _TEST_ROOTS is not None:
        return _TEST_ROOTS
    from juplit.tasks import _get_src_dirs

    return tuple(d.resolve() for d in _get_src_dirs())
