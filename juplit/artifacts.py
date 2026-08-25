"""Artifact notebooks — pairs whose `.ipynb` is committed *with* its outputs.

juplit's default model is "the `.py` is the source of truth, the `.ipynb` is a
disposable local artifact". That is wrong for one case: a notebook whose executed
outputs *are* the deliverable. A project opts those pairs in by path pattern::

    [tool.juplit]
    artifact_notebooks = ["experiments/**/*.py"]

For a declared pair, no juplit command destroys the outputs. Committed does not mean
untouched, though: every write strips the *running state* of the run that produced the
outputs — execution counts, per-run timestamps, widget state, carriage-return progress
bars — so re-running and getting the same results produces no diff.
"""

import hashlib
import tomllib
from functools import lru_cache
from typing import Literal
from pathlib import Path

import jupytext
import nbformat
from nbformat import NotebookNode

from juplit.tasks import (
    _find_percent_notebook_py_files,
    _find_pyproject_toml,
    _is_paired_notebook,
    _paired_ipynb,
    _repo_root,
)

STAMP_KEY = "juplit"
METADATA_FILTER = "-juplit"
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
DEFAULT_MAX_NOTEBOOK_BYTES = 10_000_000


# ── Registry ─────────────────────────────────────────────────────────────────

def _juplit_config() -> dict:
    """The `[tool.juplit]` table, or an empty dict when there is no pyproject."""
    toml_path = _find_pyproject_toml()
    if toml_path is None:
        return {}
    try:
        with open(toml_path, "rb") as f:
            return tomllib.load(f).get("tool", {}).get("juplit", {})
    except OSError:
        return {}


def artifact_globs() -> list[str]:
    """The `artifact_notebooks` glob patterns from `[tool.juplit]`.

    An empty list means the feature is entirely off and every command behaves exactly
    as it did before artifact notebooks existed.
    """
    globs = _juplit_config().get("artifact_notebooks") or []
    return [globs] if isinstance(globs, str) else list(globs)


@lru_cache(maxsize=8)
def _matched_paths(root: Path, globs: tuple[str, ...]) -> frozenset[Path]:
    """Every existing file the globs match, resolved.

    Matching goes through `Path.glob` rather than `fnmatch` so that `**` means what it
    means everywhere else — `experiments/**/*.py` matches `experiments/e.py` as well as
    `experiments/a/b.py`. `fnmatch` treats `/` as an ordinary character and would miss
    the first.
    """
    return frozenset(
        f.resolve() for pattern in globs for f in root.glob(pattern) if f.is_file()
    )


def is_artifact(py_file: Path) -> bool:
    """True if this paired `.py` is declared an artifact notebook."""
    globs = artifact_globs()
    if not globs:
        return False
    return py_file.resolve() in _matched_paths(_repo_root(), tuple(globs))


def artifact_py_files() -> list[Path]:
    """Every declared artifact `.py`, including ones outside `notebook_src_dirs`.

    The globs are additive to the scanned set, so `experiments/**/*.py` need not also be
    listed in `notebook_src_dirs`. Discovery of ordinary pairs is unchanged.
    """
    globs = artifact_globs()
    found = {
        f for f in _matched_paths(_repo_root(), tuple(globs)) if _is_paired_notebook(f)
    } if globs else set()
    found.update(f.resolve() for f in _find_percent_notebook_py_files() if is_artifact(f))
    return sorted(found)


def max_output_bytes() -> int:
    return int(_juplit_config().get("artifact_max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))


def max_notebook_bytes() -> int:
    return int(_juplit_config().get("artifact_max_notebook_bytes", DEFAULT_MAX_NOTEBOOK_BYTES))


# ── Provenance ───────────────────────────────────────────────────────────────

def source_sha(source: str) -> str:
    """The 16-hex-char sha256 prefix of a cell's source — its provenance stamp.

    No timestamp goes with it: a wall-clock field would churn the diff on every
    re-execution, and re-running with identical results must produce no diff.
    """
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def ensure_filter(nb: NotebookNode) -> bool:
    """Keep the stamp out of the `.py`. True if the notebook changed.

    Load-bearing, not cosmetic: without `cell_metadata_filter: -juplit`, jupytext writes
    the stamp into the `.py` cell markers on the ipynb → py direction, e.g.
    `# %% juplit={"src_sha256": "..."}`, which corrupts the source of truth.
    """
    jupytext = nb.metadata.setdefault("jupytext", {})
    if jupytext.get("cell_metadata_filter") == METADATA_FILTER:
        return False
    jupytext["cell_metadata_filter"] = METADATA_FILTER
    return True


def ensure_filter_py(py_file: Path) -> bool:
    """Put the same filter in the `.py` header. True if the file changed.

    Both halves need it, and the `.py` half is the one that bites: on a py → ipynb sync
    jupytext rebuilds the notebook from the `.py`, and it only knows to carry the
    existing notebook's `juplit` cell metadata across if the *source* it is reading
    declares that metadata py-invisible. Without this the stamps are silently dropped by
    the first `juplit sync` after an edit — reproduced.
    """
    nb = jupytext.reads(py_file.read_text(), fmt="py:percent")
    if not ensure_filter(nb):
        return False
    py_file.write_text(jupytext.writes(nb, fmt="py:percent"))
    return True


def stamp_cell(cell: NotebookNode) -> None:
    """Record which source produced this cell's outputs. A cell with none carries none."""
    if cell.get("outputs"):
        cell.metadata[STAMP_KEY] = {"src_sha256": source_sha(cell.source)}
    else:
        cell.metadata.pop(STAMP_KEY, None)


CellState = Literal["clean", "stale", "unverified", "empty"]


def cell_state(cell: NotebookNode) -> CellState:
    """Provenance verdict for one cell.

    empty       — no outputs; nothing to vouch for.
    clean       — stamped, and the stamp matches the current source.
    stale       — stamped, and the stamp disagrees: the outputs describe older source.
    unverified  — has outputs but no stamp: executed outside juplit (a human in Jupyter).
    """
    if cell.cell_type != "code" or not cell.get("outputs"):
        return "empty"
    stamp = cell.get("metadata", {}).get(STAMP_KEY, {}).get("src_sha256")
    if stamp is None:
        return "unverified"
    return "clean" if stamp == source_sha(cell.source) else "stale"


def scan(ipynb: Path) -> dict[str, list[int]]:
    """Group an artifact notebook's cell indices by state. Pure read, never writes."""
    nb = nbformat.read(ipynb, as_version=4)
    states: dict[str, list[int]] = {"clean": [], "stale": [], "unverified": [], "empty": []}
    for index, cell in enumerate(nb.cells):
        states[cell_state(cell)].append(index)
    return states


# ── Running-state hygiene ────────────────────────────────────────────────────

def _collapse_progress_bars(text: str) -> str:
    """Keep only what a terminal would show: the last segment of each `\\r`-split line.

    A tqdm bar redrawn 400 times is one line on screen and 400 in the committed file.
    """
    if "\r" not in text:
        return text
    lines = [line.split("\r")[-1] for line in text.split("\n")]
    return "\n".join(lines)


def normalize(nb: NotebookNode) -> bool:
    """Strip the bookkeeping of the run, keep the evidence. True if anything changed.

    Keeps outputs, cell ids, sources and the kernelspec. Drops execution counts, the
    per-run `metadata.execution` timings, widget state and the interpreter patch version
    that differs between machines, and collapses carriage-return progress spam.
    """
    changed = False
    for key in ("widgets", "signature"):
        if nb.metadata.pop(key, None) is not None:
            changed = True
    if nb.metadata.get("language_info", {}).pop("version", None) is not None:
        changed = True

    for cell in nb.cells:
        if cell.get("execution_count") is not None:
            cell["execution_count"] = None
            changed = True
        for key in ("execution", "widgets"):
            if cell.get("metadata", {}).pop(key, None) is not None:
                changed = True
        for output in cell.get("outputs", []):
            if output.get("output_type") == "stream":
                collapsed = _collapse_progress_bars(output.get("text", ""))
                if collapsed != output.get("text"):
                    output["text"] = collapsed
                    changed = True
            # execution_count is REQUIRED on execute_result (nullable, but required):
            # removing it produces a notebook GitHub's rich diff refuses to render.
            if output.get("output_type") == "execute_result":
                if output.get("execution_count") is not None:
                    output["execution_count"] = None
                    changed = True
            elif output.pop("execution_count", None) is not None:
                changed = True
    return changed


def write_artifact(ipynb: Path, nb: NotebookNode) -> None:
    """The single write path for an artifact `.ipynb`: filter, normalize, then write.

    Every mutator in this package goes through here so no caller can forget either the
    metadata filter or the scrub.
    """
    ensure_filter(nb)
    normalize(nb)
    nbformat.write(nb, ipynb)


def read_artifact(py_file: Path) -> NotebookNode:
    """Read the `.ipynb` paired with an artifact `.py`."""
    ipynb = _paired_ipynb(py_file)
    if not ipynb.exists():
        raise ValueError(f"{ipynb} does not exist — run `juplit nb` first")
    return nbformat.read(ipynb, as_version=4)


# ── Guards ───────────────────────────────────────────────────────────────────

def stamp(py_file: Path, cells: list[int] | None = None, force: bool = False) -> list[int]:
    """Bless outputs juplit did not produce: stamp `unverified` cells as current.

    This is the way out of the `unverified` warning for a human who ran the notebook in
    Jupyter. Refuses a `stale` cell unless `force` — stamping stale outputs asserts
    something already known to be false. Returns the indices stamped.
    """
    ipynb = _paired_ipynb(py_file)
    nb = read_artifact(py_file)
    targets = cells if cells is not None else range(len(nb.cells))
    stamped = []
    for index in targets:
        if index >= len(nb.cells):
            raise ValueError(f"cell {index} is out of range (0..{len(nb.cells) - 1})")
        cell = nb.cells[index]
        state = cell_state(cell)
        if state == "stale" and not force:
            raise ValueError(
                f"cell {index} is STALE; re-execute it, or pass --force to assert "
                "these outputs are current"
            )
        if state in ("unverified", "stale"):
            stamp_cell(cell)
            stamped.append(index)
    if stamped:
        write_artifact(ipynb, nb)
    return stamped


def normalize_notebook(py_file: Path) -> dict[str, object]:
    """Scrub a committed artifact on disk and report what it costs.

    The file-level counterpart of `normalize`, which works on an in-memory notebook.
    Oversized outputs are reported only — never downscaled, never spilled to separate
    files: a spilled image renders as nothing in GitHub's notebook viewer, which is the
    one surface where reviewers actually read these notebooks.
    """
    ipynb = _paired_ipynb(py_file)
    nb = read_artifact(py_file)
    changed = normalize(nb) | ensure_filter(nb)
    if changed:
        write_artifact(ipynb, nb)
    return {
        "changed": changed,
        "bytes": ipynb.stat().st_size,
        "oversized": _oversized_outputs(nb),
    }


def _output_bytes(output: NotebookNode) -> int:
    """Rough on-disk size of one output — the payload, not the JSON scaffolding."""
    if output.get("output_type") == "stream":
        return len(output.get("text", ""))
    if output.get("output_type") == "error":
        return sum(len(line) for line in output.get("traceback", []))
    return sum(len(value) if isinstance(value, str) else len(str(value))
               for value in output.get("data", {}).values())


def _oversized_outputs(nb: NotebookNode) -> list[tuple[int, int]]:
    """(cell index, bytes) for every output over the per-output budget."""
    cap = max_output_bytes()
    return [
        (index, _output_bytes(output))
        for index, cell in enumerate(nb.cells)
        for output in cell.get("outputs", [])
        if _output_bytes(output) > cap
    ]


def check_artifacts(strict: bool = False) -> None:
    """The pre-commit / CI guard. Reads committed files only — no kernel, no sidecar.

    Fails on: a stale cell, a missing metadata filter, a declared artifact with no
    committed `.ipynb`, a gitignored artifact, and a notebook or single output over
    budget. Warns on `unverified` cells; `strict` promotes that warning to a failure.

    Raises `SystemExit(1)` when anything failed.
    """
    from juplit.tasks import _is_gitignored, _key

    py_files = artifact_py_files()
    if not py_files:
        print("check: no artifact notebooks configured")
        return

    root, failures, warnings = _repo_root(), [], []
    for py_file in py_files:
        name = _key(py_file, root)
        ipynb = _paired_ipynb(py_file)
        if not ipynb.exists():
            failures.append(f"{name}: no committed .ipynb — run `juplit nb`")
            continue
        if _is_gitignored(ipynb):
            failures.append(
                f"{name}: its .ipynb is gitignored, so the outputs will never be "
                f"committed — un-ignore it (`!{_key(ipynb, root)}`)"
            )
        nb = nbformat.read(ipynb, as_version=4)
        try:
            nbformat.validate(nb)
        except nbformat.ValidationError as invalid:
            # GitHub's rich notebook diff refuses to render a notebook that fails the
            # schema, so an artifact that does not validate is not reviewable.
            failures.append(f"{name}: .ipynb fails nbformat validation — {invalid.message}")
        py_nb = jupytext.reads(py_file.read_text(), fmt="py:percent")
        for half, node in ((name, py_nb), (_key(ipynb, root), nb)):
            if node.metadata.get("jupytext", {}).get("cell_metadata_filter") != METADATA_FILTER:
                failures.append(
                    f"{half}: missing `cell_metadata_filter: {METADATA_FILTER}` — "
                    "provenance stamps leak into the .py without it, and the next sync "
                    "drops them; run `juplit normalize`"
                )
        drift = _source_drift(py_nb, nb)
        if drift is not None:
            failures.append(
                f"{name}: the .py and its .ipynb disagree at cell {drift} — the "
                "committed notebook does not show the committed code (run `juplit sync`)"
            )
        states = scan(ipynb)
        if states["stale"]:
            failures.append(
                f"{name}: cells {_indices(states['stale'])} STALE — outputs predate the "
                f"current .py (`juplit run {name} --stale`, or revert the source edit)"
            )
        if states["unverified"]:
            warnings.append(
                f"{name}: cells {_indices(states['unverified'])} unverified — outputs "
                f"juplit did not produce (`juplit stamp {name}` to vouch for them)"
            )
        size = ipynb.stat().st_size
        if size > max_notebook_bytes():
            failures.append(
                f"{name}: {size:,} bytes exceeds the {max_notebook_bytes():,}-byte "
                "notebook budget"
            )
        for index, output_size in _oversized_outputs(nb):
            failures.append(
                f"{name}: cell {index} has a {output_size:,}-byte output, over the "
                f"{max_output_bytes():,}-byte budget"
            )

    for warning in warnings:
        print(f"check WARNING {warning}")
    for failure in failures:
        print(f"check FAIL {failure}")
    if failures or (strict and warnings):
        raise SystemExit(1)
    print(f"check: {len(py_files)} artifact notebook(s) OK")


def _source_drift(py_nb: NotebookNode, ipynb_nb: NotebookNode) -> int | None:
    """The first cell index where the two halves disagree on source, or None.

    A `.py` edit committed without syncing leaves an internally consistent notebook —
    old source, old outputs, matching stamp — that nonetheless does not show the code
    that was committed. Comparing the halves is what lets `check` catch that on a fresh
    clone, where there is no sync state to consult.
    """
    for index, (a, b) in enumerate(zip(py_nb.cells, ipynb_nb.cells)):
        if a.source.strip() != b.source.strip():
            return index
    if len(py_nb.cells) != len(ipynb_nb.cells):
        return min(len(py_nb.cells), len(ipynb_nb.cells))
    return None


def _indices(values: list[int]) -> str:
    return ",".join(str(v) for v in values)


# ── Write-back ───────────────────────────────────────────────────────────────

def add_cell(py_file: Path, source: str, outputs: list[NotebookNode],
             index: int | None = None, cell_type: str = "code") -> int:
    """Insert one cell into the `.py` and its captured outputs into the paired `.ipynb`.

    Nothing is re-executed: the outputs written are the ones the caller already looked
    at. Both halves are built from the same in-memory insert, so they cannot disagree
    about ordering. Returns the index the cell landed at; `index=None` appends.
    """
    from juplit.tasks import _save_hashes

    if not is_artifact(py_file):
        raise ValueError(f"{py_file} is not in artifact_notebooks")
    ipynb = _paired_ipynb(py_file)
    py_before, ipynb_before = py_file.read_bytes(), None
    py_nb = jupytext.reads(py_file.read_text(), fmt="py:percent")
    nb = read_artifact(py_file)
    ipynb_before = ipynb.read_bytes()

    drift = _source_drift(py_nb, nb)
    if drift is not None:
        raise ValueError(
            f"{py_file} and its .ipynb are out of sync at cell {drift} — run "
            "`juplit sync` first"
        )
    if index is None:
        index = len(py_nb.cells)
    if not 0 <= index <= len(py_nb.cells):
        raise ValueError(f"index {index} is out of range (0..{len(py_nb.cells)})")

    make = nbformat.v4.new_code_cell if cell_type == "code" else nbformat.v4.new_markdown_cell
    py_nb.cells.insert(index, make(source))
    executed = make(source)
    if cell_type == "code":
        executed.outputs = list(outputs)
        stamp_cell(executed)
    nb.cells.insert(index, executed)

    ensure_filter(py_nb)
    py_file.write_text(jupytext.writes(py_nb, fmt="py:percent"))
    write_artifact(ipynb, nb)

    check = _source_drift(
        jupytext.reads(py_file.read_text(), fmt="py:percent"),
        nbformat.read(ipynb, as_version=4),
    )
    if check is not None:
        py_file.write_bytes(py_before)
        ipynb.write_bytes(ipynb_before)
        raise RuntimeError(
            f"insert left {py_file} and its .ipynb disagreeing at cell {check}; "
            "both files were restored"
        )
    _save_hashes([py_file])
    return index


def run_cells(py_file: Path, cells: list[int] | None = None, stale_only: bool = False,
              all_cells: bool = False, name: str = "default",
              timeout: float = 300.0) -> dict[str, list[int]]:
    """Execute selected cells and SAVE their outputs, freshly stamped, into the `.ipynb`.

    Exactly one selector, and one is required: there is no default because the plausible
    default — re-running everything — is the expensive one. `all_cells` is the clean
    build: the kernel is restarted first so the run starts from nothing.

    Returns {"executed": [...], "failed": [...]}; a cell whose output is an error counts
    as failed but is still written, because that error is what the notebook now shows.
    """
    from juplit import kernel as kernel_module   # lazy: `sync` and `check` never pay for it
    from juplit.tasks import _save_hashes

    selectors = [cells is not None, stale_only, all_cells]
    if sum(selectors) != 1:
        raise ValueError(
            "pass exactly one of --cells / --stale / --all "
            "(--all restarts the kernel and re-runs the whole notebook)"
        )

    ipynb = _paired_ipynb(py_file)
    nb = read_artifact(py_file)
    if stale_only:
        targets = scan(ipynb)["stale"]
    elif all_cells:
        targets = [i for i, cell in enumerate(nb.cells) if cell.cell_type == "code"]
        kernel_module.stop(name)
        kernel_module.start(name)
    else:
        targets = cells

    executed, failed = [], []
    for index in targets:
        if index >= len(nb.cells):
            raise ValueError(f"cell {index} is out of range (0..{len(nb.cells) - 1})")
        cell = nb.cells[index]
        if cell.cell_type != "code":
            raise ValueError(f"cell {index} is markdown; nothing to run")
        cell.outputs = kernel_module.execute(cell.source, name=name, timeout=timeout)
        stamp_cell(cell)
        executed.append(index)
        if any(o.get("output_type") == "error" for o in cell.outputs):
            failed.append(index)

    if executed:
        write_artifact(ipynb, nb)
        _save_hashes([py_file])
    return {"executed": executed, "failed": failed}
