"""juplit CLI — notebook workflow commands."""

import sys
from importlib.resources import files
from pathlib import Path

import cyclopts

from juplit.tasks import clean_notebooks, generate_notebooks, sync_notebooks

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
def clean(force: bool = False) -> None:
    """Sync notebooks then delete .ipynb files (keeps workspace clean for AI agents).

    Artifact notebooks are kept, because their outputs are the deliverable. Pass
    --force to delete those too.
    """
    clean_notebooks(force=force)


@app.command
def check(strict: bool = False) -> None:
    """Fail if a committed notebook's outputs no longer match the .py that produced them.

    Reads committed files only — no kernel, no local state — so it works on a fresh
    clone, in CI and in a pre-commit hook. --strict also fails on unverified outputs
    (outputs juplit did not produce).
    """
    from juplit.artifacts import check_artifacts

    check_artifacts(strict=strict)


@app.command
def stamp(notebook: str, cells: str | None = None, force: bool = False) -> None:
    """Vouch for outputs juplit did not produce, marking them current for their source.

    Use after running a notebook by hand in Jupyter. CELLS is a range like 3-7 or
    1,4,9-11; omit it to stamp every unverified cell.
    """
    from juplit.artifacts import stamp as stamp_artifact

    indices = _parse_cell_range(cells) if cells else None
    stamped = stamp_artifact(Path(notebook), cells=indices, force=force)
    print(f"stamped {len(stamped)} cell(s): {','.join(str(i) for i in stamped)}"
          if stamped else "stamp: nothing to do")


@app.command
def normalize(notebook: str) -> None:
    """Strip the running state from a committed notebook and report its size.

    Execution counts, per-run timings, widget state and carriage-return progress bars
    go; the outputs stay. Runs automatically on every artifact write — this command is
    for a notebook edited by hand.
    """
    from juplit.artifacts import normalize_notebook

    report = normalize_notebook(Path(notebook))
    print(f"normalize: {'rewrote' if report['changed'] else 'already clean'} "
          f"({report['bytes']:,} bytes)")
    for index, size in report["oversized"]:
        print(f"normalize OVERSIZED: cell {index} output is {size:,} bytes")


@app.command
def skill() -> None:
    """Print the juplit skill file for use with Claude Code.

    Pipe the output into your project's .claude/skills/ directory:

        juplit skill > .claude/skills/juplit-programming.md
    """
    print(files("juplit").joinpath("SKILL.md").read_text(), end="")


@app.command
def skill_migrate() -> None:
    """Print the nbdev-to-juplit migration skill file for use with Claude Code.

    Pipe the output into your project's .claude/skills/ directory:

        juplit skill-migrate > .claude/skills/juplit-migrate.md
    """
    print(files("juplit").joinpath("SKILL_migrate_from_nbdev.md").read_text(), end="")


def _parse_cell_range(spec: str) -> list[int]:
    """"3", "3-7", "1,4,9-11" -> a sorted list of cell indices."""
    indices: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            if end < start:
                raise ValueError(f"invalid cell range {part!r}: {end} is before {start}")
            indices.update(range(start, end + 1))
        else:
            indices.add(int(part))
    return sorted(indices)


def main() -> None:
    """Run the CLI, reporting expected failures as one line rather than a traceback."""
    try:
        app()
    except (ValueError, RuntimeError) as error:
        print(f"juplit: {error}", file=sys.stderr)
        raise SystemExit(1) from None
