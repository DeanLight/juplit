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

import tomllib
from functools import lru_cache
from pathlib import Path

import nbformat
from nbformat import NotebookNode

from juplit.tasks import (
    _find_percent_notebook_py_files,
    _find_pyproject_toml,
    _is_paired_notebook,
    _paired_ipynb,
    _repo_root,
)

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
    """The single write path for an artifact `.ipynb`: normalize, then write.

    Every mutator in this package goes through here so no caller can forget the scrub.
    """
    normalize(nb)
    nbformat.write(nb, ipynb)


def read_artifact(py_file: Path) -> NotebookNode:
    """Read the `.ipynb` paired with an artifact `.py`."""
    ipynb = _paired_ipynb(py_file)
    if not ipynb.exists():
        raise ValueError(f"{ipynb} does not exist — run `juplit nb` first")
    return nbformat.read(ipynb, as_version=4)
