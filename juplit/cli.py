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
def cells(notebook: str) -> None:
    """List a notebook's cells: markdown headings, and a digest of each output.

    The cheap first read — no source, no base64, so a 40-cell, 4 MB notebook costs a few
    hundred tokens. The headings make it a table of contents you can navigate by; use the
    indices with `juplit view`, `juplit run --cells` and `juplit stamp`.
    """
    from juplit.view import cells_table

    print(cells_table(_paired_notebook(notebook)))


@app.command
def view(notebook: str, cells: str | None = None, full: bool = False) -> None:
    """Show the source of the named cells, each with its outputs.

    CELLS is a range like 3-7 or 1,4,9-11; omit it for the whole notebook. Long outputs
    are truncated head-and-tail, and images are always shown as a digest — --full lifts
    the text truncation, never the image rule.
    """
    from juplit.view import parse_cell_range, view_cells

    indices = parse_cell_range(cells) if cells else None
    print(view_cells(_source_file(notebook), cells=indices, full=full))


@app.command
def html(notebook: str, out: str | None = None) -> None:
    """Render a notebook to standalone HTML (wraps `jupyter nbconvert`)."""
    from juplit.tasks import html as render_html

    render_html(Path(notebook), Path(out) if out else None)


@app.command
def kernel(action: str, name: str = "default", cwd: str | None = None,
           kernel_name: str = "python3") -> None:
    """Manage the persistent kernel: ACTION is start, stop or status.

    The kernel outlives the command that started it, so separate `juplit try` calls
    share one session. It runs at the repo root unless --cwd says otherwise, and lives
    until `juplit kernel stop` or `juplit clean`.
    """
    from juplit import kernel as kernel_module

    if action == "start":
        session = kernel_module.start(name, kernel=kernel_name,
                                      cwd=Path(cwd) if cwd else None)
        print(f"kernel {session['name']!r} ready (pid {session['pid']}, cwd {session['cwd']})")
    elif action == "stop":
        print(f"kernel {name!r} stopped" if kernel_module.stop(name)
              else f"no kernel {name!r} to stop")
    elif action == "status":
        rows = kernel_module.status()
        for row in rows:
            state = "alive" if row["alive"] else "DEAD"
            print(f"{row['name']:<12} {state:<6} pid {row['pid']:<8} cwd {row['cwd']}")
        if not rows:
            print("no kernels")
    else:
        raise ValueError(f"unknown action {action!r} — use start, stop or status")


@app.command(name="try")
def try_(code: str | None = None, file: str | None = None, nb: str | None = None,
         cells: str | None = None, name: str = "default", timeout: float = 300.0) -> None:
    """Execute on the kernel and print the outputs. NEVER writes to any notebook.

    Give it inline CODE, a --file to run, or --nb with --cells to rehearse cells that
    are already in a notebook. The attempt leaves no trace on disk, so a failure costs
    nothing; `juplit add-cell --from-last` is how a successful one gets committed.
    """
    from juplit.trying import try_code

    try_code(code=code, file=Path(file) if file else None,
             nb=Path(nb) if nb else None, cells=cells, name=name, timeout=timeout)


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
    from juplit.view import parse_cell_range

    indices = parse_cell_range(cells) if cells else None
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


def _source_file(notebook: str) -> Path:
    """Accept either half of a pair and return the `.py` — the source of truth."""
    path = Path(notebook)
    return path.with_suffix(".py") if path.suffix == ".ipynb" else path


def _paired_notebook(notebook: str) -> Path:
    """Accept either half of a pair and return the `.ipynb`."""
    path = Path(notebook)
    return path.with_suffix(".ipynb") if path.suffix == ".py" else path


def main() -> None:
    """Run the CLI, reporting expected failures as one line rather than a traceback."""
    try:
        app()
    except (ValueError, RuntimeError) as error:
        print(f"juplit: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":  # `python -m juplit.cli`, e.g. when the console script is not on PATH
    main()
