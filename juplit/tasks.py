"""Core notebook workflow tasks for juplit.

These functions back both the `poe` task targets and the `juplit` CLI commands.
They can also be imported and called directly from Python.
"""

import hashlib
import json
import subprocess
import tomllib
from pathlib import Path


def _find_pyproject_toml() -> Path | None:
    """Walk up from cwd to find the nearest pyproject.toml."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            return candidate
    return None


def _get_src_dirs() -> list[Path]:
    """Read notebook_src_dirs (or legacy notebook_src_dir) from [tool.juplit]."""
    toml_path = _find_pyproject_toml()
    root = toml_path.parent if toml_path is not None else Path.cwd()
    if toml_path is not None:
        try:
            with open(toml_path, "rb") as f:
                config = tomllib.load(f)
            juplit_cfg = config.get("tool", {}).get("juplit", {})
            dirs = juplit_cfg.get("notebook_src_dirs") or juplit_cfg.get("notebook_src_dir")
            if dirs:
                if isinstance(dirs, str):
                    dirs = [dirs]
                return [root / d for d in dirs]
        except OSError:
            pass
    return [root / "src"]


def _is_paired_notebook(path: Path) -> bool:
    """Return True if the file is a percent-format notebook paired with an ipynb.

    Checks the jupytext header for both 'ipynb' and 'py:percent' in the formats
    line, so plain py files and non-paired notebooks are excluded.
    """
    try:
        content = path.read_text()
    except OSError:
        return False
    for line in content.splitlines():
        if not line.startswith("#"):
            break
        stripped = line.lstrip("# ").strip()
        if stripped.startswith("formats:"):
            formats = stripped[len("formats:"):].strip()
            return "ipynb" in formats and "py:percent" in formats
    return False


def _find_py_files() -> list[Path]:
    result = []
    for src_dir in _get_src_dirs():
        if src_dir.exists():
            result.extend(src_dir.rglob("*.py"))
    return sorted(result)


def _find_percent_notebook_py_files() -> list[Path]:
    return [f for f in _find_py_files() if _is_paired_notebook(f)]


def _fmt(label: str, names: list[str]) -> str:
    return f"{len(names)} {label}: {', '.join(names)}"


# ── Hash-based change tracking ────────────────────────────────────────────────

def _hash_file(path: Path) -> str:
    try:
        return hashlib.md5(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _state_path() -> Path:
    toml_path = _find_pyproject_toml()
    root = toml_path.parent if toml_path is not None else Path.cwd()
    return root / ".sync_hashes.json"


def _load_hashes() -> dict[str, str]:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _save_hashes(files: list[Path]) -> None:
    state = {f.name: _hash_file(f) for f in files if f.exists()}
    _state_path().write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


# ── Jupytext runner ───────────────────────────────────────────────────────────

def _run_jupytext(args: list[str], files: list[Path]) -> tuple[dict[str, list[str]], list[str]]:
    """Run jupytext and classify files by comparing hashes against the last sync.

    Returns (groups, errors) where groups has keys: updated, unchanged, skipped.
    """
    prev_hashes = _load_hashes()

    result = subprocess.run(
        ["jupytext"] + args + [str(f) for f in files],
        capture_output=True,
        text=True,
    )

    skipped_names: set[str] = set()
    errors: list[str] = []

    for line in result.stderr.splitlines():
        low = line.lower()
        if "warning" in low and "not a paired" in low:
            words = line.split()
            try:
                idx = next(i for i, w in enumerate(words) if w == "Warning:")
                skipped_names.add(Path(words[idx + 1]).name)
            except (StopIteration, IndexError):
                pass
        elif "error" in low:
            errors.append(line)

    if result.returncode != 0 and not errors:
        errors.append(result.stderr.strip() or f"jupytext exited with code {result.returncode}")

    updated: list[str] = []
    unchanged: list[str] = []
    skipped: list[str] = []

    for f in files:
        if f.name in skipped_names:
            skipped.append(f.name)
        elif _hash_file(f) != prev_hashes.get(f.name, ""):
            updated.append(f.name)
        else:
            unchanged.append(f.name)

    _save_hashes(files)
    return {"updated": updated, "unchanged": unchanged, "skipped": skipped}, errors


# ── Public tasks ─────────────────────────────────────────────────────────────

def sync_notebooks() -> None:
    """Sync `.py` and `.ipynb` files for all paired percent-format notebooks.

    Walks the configured `notebook_src_dirs` (from `[tool.juplit]` in
    `pyproject.toml`) and calls `jupytext --sync` on every `.py` file that has
    a jupytext percent-format header pairing it with an `.ipynb`.

    Prints a summary of updated, unchanged, and skipped files.
    Raises `SystemExit(1)` if jupytext reports any errors.
    """
    files = _find_percent_notebook_py_files()
    if not files:
        print("No percent notebook .py files found.")
        return

    groups, errors = _run_jupytext(["--sync"], files)
    if groups["updated"]:
        print(_fmt("sync updated", groups["updated"]))
    if groups["unchanged"]:
        print(_fmt("sync unchanged", groups["unchanged"]))
    if groups["skipped"]:
        print(_fmt("sync skipped (not paired)", groups["skipped"]))
    if not any(groups.values()):
        print("Sync: nothing to do")
    for err in errors:
        print(f"sync error: {err}")
    if errors:
        raise SystemExit(1)


def generate_notebooks() -> None:
    """Generate `.ipynb` files from `.py` percent-format files.

    Calls `jupytext --to notebook` on every paired `.py` file found in the
    configured `notebook_src_dirs`. Use this after cloning a repo where only
    the `.py` sources are committed.

    Prints a summary of created/updated, unchanged, and skipped files.
    Raises `SystemExit(1)` if jupytext reports any errors.
    """
    files = _find_percent_notebook_py_files()
    if not files:
        print("No percent notebook .py files found.")
        return

    groups, errors = _run_jupytext(["--to", "notebook"], files)
    if groups["updated"]:
        print(_fmt("nb created/updated", groups["updated"]))
    if groups["unchanged"]:
        print(_fmt("nb unchanged", groups["unchanged"]))
    if groups["skipped"]:
        print(_fmt("nb skipped", groups["skipped"]))
    if not any(groups.values()):
        print("Notebooks: nothing to do")
    for err in errors:
        print(f"nb error: {err}")
    if errors:
        raise SystemExit(1)


def clean_notebooks() -> None:
    """Sync then delete all `.ipynb` files from the source directories.

    First calls `sync_notebooks()` to flush any unsaved changes from the
    `.ipynb` files back into their paired `.py` sources, then removes every
    `.ipynb` found under `notebook_src_dirs`. Keeps the working directory
    clean for AI agents and CI environments that only need the `.py` sources.

    Prints a summary of removed files.
    """
    sync_notebooks()
    removed = []
    for src_dir in _get_src_dirs():
        for f in src_dir.rglob("*.ipynb"):
            removed.append(f.name)
            f.unlink()
    if removed:
        print(_fmt("clean removed", sorted(removed)))
    else:
        print("clean: nothing to remove")
