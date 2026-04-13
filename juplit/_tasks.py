"""Notebook sync task functions for juplit.

These are the core poe task implementations. They can be called directly as
poe script targets or invoked via the `juplit` CLI.
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


def _get_src_dir() -> Path:
    """Read notebook_src_dir from [tool.juplit] in the nearest pyproject.toml."""
    toml_path = _find_pyproject_toml()
    if toml_path is not None:
        try:
            with open(toml_path, "rb") as f:
                config = tomllib.load(f)
            src = config.get("tool", {}).get("juplit", {}).get("notebook_src_dir")
            if src:
                # Resolve relative to the pyproject.toml directory
                return toml_path.parent / src
        except OSError:
            pass
    return Path("src")


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
    src_dir = _get_src_dir()
    if not src_dir.exists():
        return []
    return sorted(src_dir.rglob("*.py"))


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
    return _get_src_dir() / ".sync_hashes.json"


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
    """Sync .py <-> .ipynb for all paired percent-format notebooks."""
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
    """Generate .ipynb files from .py percent-format files."""
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
    """Sync notebooks then delete all .ipynb files."""
    sync_notebooks()
    removed = []
    for f in _get_src_dir().rglob("*.ipynb"):
        removed.append(f.name)
        f.unlink()
    if removed:
        print(_fmt("clean removed", sorted(removed)))
    else:
        print("clean: nothing to remove")
