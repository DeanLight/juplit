"""Smoke tests for CI."""

import pytest

from juplit.cli import app


def test_package_exports():
    import juplit

    for name in ("sync_notebooks", "generate_notebooks", "clean_notebooks", "test"):
        assert hasattr(juplit, name)


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc:
        app(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Usage" in out
    assert "juplit" in out
