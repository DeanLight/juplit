"""test() helper — separates exportable logic from inline tests."""

import sys


def test() -> bool:
    """Return True when the calling module is run directly or under pytest.

    Use this to gate test code in percent-format notebook files so that tests
    run interactively (in Jupyter) and under pytest, but never on import.

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
    frame = sys._getframe(1)
    caller_name = frame.f_globals.get("__name__", "")
    return caller_name == "__main__" or "pytest" in sys.modules
