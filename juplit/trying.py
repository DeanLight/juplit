"""`juplit try` — run something on the kernel and look at the result, writing nothing."""

import json
from pathlib import Path

from nbformat import NotebookNode

from juplit.kernel import DEFAULT_NAME, DEFAULT_TIMEOUT, execute
from juplit.tasks import _repo_root
from juplit.view import parse_cell_range, truncate_output


def last_run_path() -> Path:
    return _repo_root() / ".juplit" / "last-run.json"


def save_last_run(source: str, outputs: list[NotebookNode]) -> None:
    """Remember the snippet and the outputs it produced, for `add-cell --from-last`.

    Keeping the captured outputs — rather than re-executing at commit time — is what
    makes what lands provably the thing the agent looked at.
    """
    path = last_run_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": source, "outputs": outputs}, indent=2))


def load_last_run() -> dict:
    """The previous `juplit try`, with its outputs back as nbformat nodes.

    They round-trip through JSON as plain dicts, which nbformat's writer rejects.
    """
    import nbformat

    path = last_run_path()
    if not path.exists():
        raise ValueError("no previous `juplit try` to take — run one first")
    last = json.loads(path.read_text())
    return last | {"outputs": [nbformat.from_dict(o) for o in last["outputs"]]}


def _snippets(code: str | None, file: Path | None, nb: Path | None,
              cells: str | None) -> list[tuple[str, str]]:
    """(label, source) pairs to execute, from exactly one of the three input forms."""
    given = [x for x in (code, file, nb) if x is not None]
    if len(given) != 1:
        raise ValueError("pass exactly one of CODE, --file or --nb")

    if code is not None:
        return [("inline", code)]
    if file is not None:
        return [(file.name, file.read_text())]

    import jupytext

    notebook = jupytext.reads(nb.read_text(), fmt="py:percent")
    wanted = parse_cell_range(cells) if cells else range(len(notebook.cells))
    pairs = []
    for index in wanted:
        if index >= len(notebook.cells):
            raise ValueError(f"cell {index} is out of range (0..{len(notebook.cells) - 1})")
        cell = notebook.cells[index]
        if cell.cell_type == "code":
            pairs.append((f"cell {index}", cell.source))
    return pairs


def try_code(code: str | None = None, file: Path | None = None, nb: Path | None = None,
             cells: str | None = None, name: str = DEFAULT_NAME,
             timeout: float = DEFAULT_TIMEOUT) -> list[NotebookNode]:
    """Execute and print. Returns the outputs of the last snippet; writes no notebook."""
    outputs: list[NotebookNode] = []
    for label, source in _snippets(code, file, nb, cells):
        outputs = execute(source, name=name, timeout=timeout)
        if label != "inline":
            print(f"── {label} " + "─" * 40)
        for output in outputs:
            print(truncate_output(output))
        if not outputs:
            print("(no output)")
        save_last_run(source, outputs)
    return outputs
