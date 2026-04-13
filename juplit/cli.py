"""juplit CLI — notebook workflow commands."""

from importlib.resources import files

import cyclopts

from juplit._tasks import clean_notebooks, generate_notebooks, sync_notebooks

app = cyclopts.App(
    name="juplit",
    help="Jupytext percent-format notebook workflow manager.",
)


@app.command
def sync() -> None:
    """Sync .py <-> .ipynb for all paired percent-format notebooks."""
    sync_notebooks()


@app.command
def nb() -> None:
    """Generate .ipynb files from .py percent-format files (run after cloning)."""
    generate_notebooks()


@app.command
def clean() -> None:
    """Sync notebooks then delete all .ipynb files (keeps workspace clean for AI agents)."""
    clean_notebooks()


@app.command
def skill() -> None:
    """Print the juplit skill file for use with Claude Code.

    Pipe the output into your project's .claude/skills/ directory:

        juplit skill > .claude/skills/juplit-programming.md
    """
    skill_text = files("juplit").joinpath("SKILL.md").read_text()
    print(skill_text, end="")


def main() -> None:
    app()
