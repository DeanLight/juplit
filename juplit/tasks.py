"""Core notebook workflow tasks for juplit.

These functions back both the `poe` task targets and the `juplit` CLI commands.
They can also be imported and called directly from Python.
"""

import hashlib
import json
import os
import re
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


def _get_src_dirs(toml_path: Path | None = None) -> list[Path]:
    """Read notebook_src_dirs (or legacy notebook_src_dir) from [tool.juplit] of
    `toml_path`, by default the pyproject.toml nearest the cwd."""
    if toml_path is None:
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


# ── Hash-based sync-direction detection ───────────────────────────────────────

def _hash_file(path: Path) -> str:
    try:
        return hashlib.md5(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _paired_ipynb(py_file: Path) -> Path:
    """Return the `.ipynb` path paired with a percent-format `.py` file.

    juplit uses the default jupytext convention of same directory and base
    name (``formats = "ipynb,py:percent"``), so the paired notebook is simply
    the `.py` file with an `.ipynb` suffix.
    """
    return py_file.with_suffix(".ipynb")


def _repo_root() -> Path:
    """The directory the sync state and file keys are anchored to (pyproject parent)."""
    toml_path = _find_pyproject_toml()
    root = toml_path.parent if toml_path is not None else Path.cwd()
    return root.resolve()


def _key(f: Path, root: Path) -> str:
    """Stable per-file key: the repo-root-relative posix path.

    Keying by path (not basename) keeps same-named notebooks in different
    directories (`a/x.py` vs `b/x.py`) from colliding in the sync state.
    """
    try:
        return f.resolve().relative_to(root).as_posix()
    except ValueError:
        return f.resolve().as_posix()


def _state_path() -> Path:
    return _repo_root() / ".sync_hashes.json"


def _load_hashes() -> dict[str, dict[str, str]]:
    """Return the per-file `{path: {"py": hash, "ipynb": hash}}` from the last sync."""
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _save_hashes(files: list[Path]) -> None:
    """Record the `.py` and `.ipynb` hashes of each file as the new sync baseline.

    Merges into the existing state rather than replacing it, so callers can save
    a subset (e.g. everything except overwrite-blocked files, whose old baseline
    must survive) without dropping the others' recorded hashes.
    """
    root = _repo_root()
    state = _load_hashes()
    for f in files:
        if f.exists():
            state[_key(f, root)] = {"py": _hash_file(f), "ipynb": _hash_file(_paired_ipynb(f))}
    _state_path().write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _py_edited_since_sync(f: Path, root: Path, prev: dict[str, dict[str, str]]) -> bool:
    """True if `f`'s `.py` content differs from the hash recorded at the last sync.

    False when there is no baseline yet (first sync) — nothing to protect.
    """
    recorded = prev.get(_key(f, root))
    return bool(recorded) and _hash_file(f) != recorded.get("py")


def _is_gitignored(path: Path) -> bool:
    """True if git would ignore `path`. False when git is unavailable or this is no repo."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=_repo_root(), capture_output=True,
        )
    except OSError:
        return False
    return result.returncode == 0


def _warn_gitignored_artifacts(py_files: list[Path]) -> None:
    """Warn for each declared artifact whose `.ipynb` git would never commit.

    Preserved locally and silently never committed looks identical to the feature not
    working, so this is worth a line every time. `juplit check` turns it into a failure.
    """
    from juplit.artifacts import is_artifact

    root = _repo_root()
    hidden = [
        _key(f, root) for f in py_files
        if is_artifact(f) and _paired_ipynb(f).exists() and _is_gitignored(_paired_ipynb(f))
    ]
    if hidden:
        print(_fmt(
            "artifact GITIGNORED — outputs are preserved locally but will never be "
            "committed; un-ignore the .ipynb (e.g. `!experiments/ablation.ipynb`)",
            hidden,
        ))


# ── No jupytext_version in headers ────────────────────────────────────────────

# jupytext stamps its installed version into every `.py` header it writes, so upgrading
# it would rewrite every notebook. juplit strips that line after each jupytext write;
# this filter in `[tool.jupytext]` makes Jupyter and plain jupytext leave it out too.
# The second entry keeps the filter itself out of every header.
VERSION_FILTER = (
    "-jupytext.text_representation.jupytext_version",
    "-jupytext.notebook_metadata_filter",
)
_HEADER = re.compile(r"\A((?:#!.*\n|# -\*-.*\n)*)(# ---\r?\n.*?\n# ---\r?\n)", re.S)
_VERSION_LINE = re.compile(r"^#\s+jupytext_version:.*\n", re.M)


def strip_jupytext_version(text: str) -> str:
    """`text` without the `jupytext_version` line of its jupytext header, if any."""
    header = _HEADER.match(text)
    if header is None:
        return text
    return header.group(1) + _VERSION_LINE.sub("", header.group(2)) + text[header.end():]


def _strip_version_in_place(py_file: Path) -> None:
    """Strip the header's version line, keeping the mtime jupytext gave the file."""
    try:
        text = py_file.read_bytes().decode()
    except (OSError, UnicodeDecodeError):
        return
    stripped = strip_jupytext_version(text)
    if stripped == text:
        return
    stat = py_file.stat()
    py_file.write_bytes(stripped.encode())
    os.utime(py_file, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def _migrated_py(before: bytes | None, after: bytes | None) -> bool:
    """True if the `.py` changed only by losing its `jupytext_version` line."""
    return (None not in (before, after) and before != after
            and after == strip_jupytext_version(before.decode(errors="replace")).encode())


def _config_has_version_filter() -> bool:
    """True if the project's jupytext config carries every `VERSION_FILTER` entry."""
    from jupytext.config import load_jupytext_config

    config = load_jupytext_config(str(_repo_root()))
    entries = (config.notebook_metadata_filter if config else "").split(",")
    return set(VERSION_FILTER) <= set(entries)


def _gained_only_the_filter(before: bytes | None, after: bytes | None) -> bool:
    """True if the `.ipynb` changed only by recording `notebook_metadata_filter`.

    With the filter in `[tool.jupytext]`, jupytext records it in the notebook the
    next time it reads the `.py`: the migration finishing, not an edit.
    """
    if None in (before, after) or before == after:
        return False
    try:
        old, new = json.loads(before), json.loads(after)
    except ValueError:
        return False
    new.get("metadata", {}).get("jupytext", {}).pop("notebook_metadata_filter", None)
    return old == new


def _lacks_the_filter(ipynb: Path) -> bool:
    try:
        return "notebook_metadata_filter" not in json.loads(ipynb.read_text())["metadata"]["jupytext"]
    except (OSError, ValueError, KeyError):
        return False


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


# ── Jupytext runner ───────────────────────────────────────────────────────────

def _run_jupytext(args: list[str], files: list[Path]) -> tuple[dict[str, list[str]], list[str]]:
    """Run jupytext and classify each file by the sync direction it triggered.

    Hashes both the `.py` file and its paired `.ipynb` immediately before and
    after invoking jupytext, then reads the direction off of which side's
    content jupytext rewrote:

    * the `.py` content changed  → the `.ipynb` was newer, so `ipynb → py`
    * only the `.ipynb` changed   → the `.py` was newer, so `py → ipynb`
    * neither changed             → already in sync

    After the run, every `.py` header loses its `jupytext_version` line
    (`strip_jupytext_version`), so a jupytext upgrade changes no file. A pair
    whose only change is the `.py` losing that line, or the `.ipynb` recording
    the project's filter, is a one-time *migration*, not a sync in either
    direction. A sync that migrated a `.py` from the `.ipynb` side syncs that
    pair once more from the `.py`, so the `.ipynb` records the filter in the
    same run rather than reporting it on the next.

    A file is also flagged as a *conflict* when, comparing the pre-sync state
    against the hashes recorded at the previous sync (`.sync_hashes.json`),
    **both** the `.py` and its `.ipynb` changed. jupytext resolves that case
    purely by modification time — it keeps the newer file and silently
    overwrites the other — so the losing side's edits may be gone. The updated
    baseline hashes are written back after the run.

    Files are keyed and reported by their repo-root-relative path (not
    basename), so same-named notebooks in different directories don't collide.

    When run with `--check-source-is-newer`, jupytext refuses to overwrite a
    `.py` that is not the newest of its pair, printing
    `Error: Source <py> is older than paired file <ipynb>` and skipping it. Such
    files are collected into the `overwrite_risk` group (they were *not* synced)
    rather than treated as fatal errors.

    Does not persist state — the caller writes `.sync_hashes.json` once (so a
    two-group sync doesn't have one group clobber the other's baseline, and
    overwrite-blocked files keep their prior baseline).

    Returns (groups, errors) where groups has keys: to_ipynb, to_py,
    unchanged, skipped, conflicts, overwrite_risk, migrated. A conflict path also appears
    in whichever direction jupytext ended up applying.
    """
    empty = {k: [] for k in
             ("to_ipynb", "to_py", "unchanged", "skipped", "conflicts", "overwrite_risk",
              "migrated")}
    if not files:
        return empty, []

    prev = _load_hashes()
    root = _repo_root()
    before = {
        f: (_hash_file(f), _hash_file(_paired_ipynb(f)))
        for f in files
    }
    originals = {f: (_read_bytes(f), _read_bytes(_paired_ipynb(f))) for f in files}

    result = subprocess.run(
        ["jupytext", *args, *(str(f) for f in files)],
        capture_output=True,
        text=True,
    )

    skipped_paths: set[Path] = set()
    risk_paths: set[Path] = set()
    errors: list[str] = []

    for line in result.stderr.splitlines():
        low = line.lower()
        if "is older than paired file" in line:
            start = line.index("Source ") + len("Source ")
            end = line.index(" is older than paired file")
            src = line[start:end].strip().strip("'\"")
            risk_paths.add(Path(src).resolve())
        elif "warning" in low and "not a paired" in low:
            words = line.split()
            try:
                idx = next(i for i, w in enumerate(words) if w == "Warning:")
                skipped_paths.add(Path(words[idx + 1]).resolve())
            except (StopIteration, IndexError):
                pass
        elif "error" in low:
            errors.append(line)

    if result.returncode != 0 and not errors:
        errors.append(result.stderr.strip() or f"jupytext exited with code {result.returncode}")

    for f in files:
        if f.resolve() not in risk_paths | skipped_paths:
            _strip_version_in_place(f)

    lagging = [f for f in files if _migrated_py(originals[f][0], _read_bytes(f))
               and _lacks_the_filter(_paired_ipynb(f))]
    if "--sync" in args and lagging and _config_has_version_filter():
        again = subprocess.run(["jupytext", "--sync", *map(str, lagging)],
                               capture_output=True, text=True)
        if again.returncode != 0:
            errors.append(again.stderr.strip())

    to_ipynb: list[str] = []
    to_py: list[str] = []
    unchanged: list[str] = []
    skipped: list[str] = []
    conflicts: list[str] = []
    overwrite_risk: list[str] = []
    migrated: list[str] = []

    for f in files:
        key = _key(f, root)
        resolved = f.resolve()
        if resolved in risk_paths:
            overwrite_risk.append(key)
            continue
        if resolved in skipped_paths:
            skipped.append(key)
            continue
        py_before, ipynb_before = before[f]

        recorded = prev.get(key)
        if (
            recorded
            and py_before != recorded.get("py")
            and ipynb_before != recorded.get("ipynb")
        ):
            conflicts.append(key)

        (py_original, ipynb_original), py_after, ipynb_after = (
            originals[f], _read_bytes(f), _read_bytes(_paired_ipynb(f)))
        py_migrated = _migrated_py(py_original, py_after)
        ipynb_migrated = _gained_only_the_filter(ipynb_original, ipynb_after)
        if py_migrated or ipynb_migrated:
            migrated.append(key)
        py_changed = py_after != py_original and not py_migrated
        ipynb_changed = ipynb_after != ipynb_original and not ipynb_migrated
        if py_changed:
            to_py.append(key)
        elif ipynb_changed:
            to_ipynb.append(key)
        else:
            unchanged.append(key)

    return {
        "to_ipynb": to_ipynb,
        "to_py": to_py,
        "unchanged": unchanged,
        "skipped": skipped,
        "conflicts": conflicts,
        "overwrite_risk": overwrite_risk,
        "migrated": migrated,
    }, errors


def _prepare_artifacts(py_files: list[Path]) -> None:
    """Make sure every artifact `.py` declares the metadata filter before jupytext runs.

    Order matters: jupytext carries a notebook's `juplit` cell metadata across a
    py → ipynb conversion only when the `.py` it reads declares that metadata
    py-invisible. Adding the filter *after* the conversion is too late — the stamps are
    already gone, and every cell comes back `unverified`.
    """
    from juplit.artifacts import ensure_filter_py, is_artifact

    for f in py_files:
        if is_artifact(f):
            ensure_filter_py(f)


def _normalize_artifacts(py_files: list[Path]) -> None:
    """Scrub the running state from every declared artifact notebook on disk."""
    import nbformat

    from juplit.artifacts import (
        ensure_filter,
        ensure_filter_py,
        is_artifact,
        normalize,
        write_artifact,
    )

    for f in py_files:
        if not is_artifact(f):
            continue
        ensure_filter_py(f)
        ipynb = _paired_ipynb(f)
        if not ipynb.exists():
            continue
        nb = nbformat.read(ipynb, as_version=4)
        if normalize(nb) | ensure_filter(nb):
            write_artifact(ipynb, nb)


def _report_stale_artifacts(py_files: list[Path]) -> bool:
    """Name every artifact cell whose outputs predate its source. True if any is stale.

    Stale outputs are never deleted here: they are the expensive evidence, and the human
    decides between re-running and reverting the source edit.
    """
    from juplit.artifacts import is_artifact, scan

    root, stale_found = _repo_root(), False
    for f in py_files:
        ipynb = _paired_ipynb(f)
        if not (is_artifact(f) and ipynb.exists()):
            continue
        states = scan(ipynb)
        name = _key(f, root)
        if states["stale"]:
            stale_found = True
            cells = ",".join(str(i) for i in states["stale"])
            print(
                f"artifact STALE: {name} cells {cells} — outputs predate the current "
                f".py (`juplit run {name} --stale`, or revert the source edit)"
            )
        if states["unverified"]:
            cells = ",".join(str(i) for i in states["unverified"])
            print(
                f"artifact unverified: {name} cells {cells} — outputs juplit did not "
                f"produce (`juplit stamp {name}` to vouch for them)"
            )
    return stale_found


def _report_migration(count: int) -> None:
    """Say how many headers lost `jupytext_version`, and how to keep editors in step."""
    print(f"removed jupytext_version from {count} notebook{'s' if count > 1 else ''} "
          "(one-time migration)")
    if not _config_has_version_filter():
        print(f'add notebook_metadata_filter = "{",".join(VERSION_FILTER)}" to [tool.jupytext] '
              "so Jupyter and plain jupytext leave it out too")


# ── Public tasks ─────────────────────────────────────────────────────────────

def sync_notebooks() -> None:
    """Sync `.py` and `.ipynb` files for all paired percent-format notebooks.

    Walks the configured `notebook_src_dirs` (from `[tool.juplit]` in
    `pyproject.toml`) and calls `jupytext --sync` on every `.py` file that has
    a jupytext percent-format header pairing it with an `.ipynb`.

    Prints only what changed: the synced files split by direction (`py → ipynb`
    vs `ipynb → py`), conflicts, overwrite-blocked files, errors and stale
    artifacts. Unchanged and unpaired files are not listed; when nothing
    changed it prints `Sync: nothing changed`. Headers are kept without
    `jupytext_version` (see `strip_jupytext_version`), so upgrading jupytext
    changes no file; a header that still has the line loses it once, counted
    as `removed jupytext_version from N notebooks`. Also flags any *conflict*
    — a file whose `.py` and `.ipynb` both changed since the last sync, where
    jupytext keeps the newer by mtime and overwrites the other.

    Protects a `.py` that has edits since the last sync from being silently
    overwritten by a newer (mtime) `.ipynb`: such files are run with jupytext's
    `--check-source-is-newer` guard, so instead of clobbering the `.py` they are
    left untouched and reported as *overwrite blocked*. Files whose `.py` is
    unchanged since the last sync still sync both ways as usual.

    Raises `SystemExit(1)` if jupytext reports any errors, or if any file was
    overwrite-blocked.
    """
    from juplit.artifacts import artifact_py_files

    files = sorted(set(_find_percent_notebook_py_files()) | set(artifact_py_files()))
    if not files:
        print("No percent notebook .py files found.")
        return

    _prepare_artifacts(files)
    prev = _load_hashes()
    root = _repo_root()
    guarded = [f for f in files if _py_edited_since_sync(f, root, prev)]
    normal = [f for f in files if f not in guarded]

    g_normal, e_normal = _run_jupytext(["--sync"], normal)
    g_guard, e_guard = _run_jupytext(
        ["--sync", "--check-source-is-newer", "--warn-only"], guarded
    )
    groups = {k: g_normal[k] + g_guard[k] for k in g_normal}
    errors = e_normal + e_guard

    # Blocked files were not synced — keep their prior baseline so the guard
    # keeps firing until the user resolves them; save everything else once.
    risk_keys = set(groups["overwrite_risk"])
    _normalize_artifacts([f for f in files if _key(f, root) not in risk_keys])
    _save_hashes([f for f in files if _key(f, root) not in risk_keys])
    _warn_gitignored_artifacts(files)

    if groups["to_ipynb"]:
        print(_fmt("synced py → ipynb", groups["to_ipynb"]))
    if groups["to_py"]:
        print(_fmt("synced ipynb → py", groups["to_py"]))
    if groups["conflicts"]:
        print(_fmt(
            "sync CONFLICT — both .py and .ipynb changed since last sync; "
            "jupytext kept the newer by mtime and overwrote the other",
            groups["conflicts"],
        ))
    if groups["overwrite_risk"]:
        print(_fmt(
            "sync OVERWRITE BLOCKED — .py has unsynced edits but its .ipynb is "
            "newer; refused to overwrite the .py (resolve, e.g. delete the stale "
            ".ipynb or touch the .py, then re-sync)",
            groups["overwrite_risk"],
        ))
    if groups["migrated"]:
        _report_migration(len(groups["migrated"]))
    changed = ("to_ipynb", "to_py", "conflicts", "overwrite_risk", "migrated")
    if not errors and not any(groups[k] for k in changed):
        print("Sync: nothing changed")
    for err in errors:
        print(f"sync error: {err}")
    stale = _report_stale_artifacts(files)
    if errors or groups["overwrite_risk"] or stale:
        raise SystemExit(1)


def generate_notebooks() -> None:
    """Generate `.ipynb` files from `.py` percent-format files.

    Calls `jupytext --to notebook` on every paired `.py` file found in the
    configured `notebook_src_dirs`. Use this after cloning a repo where only
    the `.py` sources are committed.

    Prints a summary of created/updated, unchanged, and skipped files.
    Raises `SystemExit(1)` if jupytext reports any errors.
    """
    from juplit.artifacts import artifact_py_files, is_artifact

    files = sorted(set(_find_percent_notebook_py_files()) | set(artifact_py_files()))
    if not files:
        print("No percent notebook .py files found.")
        return

    # An artifact whose .ipynb already exists is regenerated with --update, which keeps
    # its outputs; plain --to notebook wipes them, which is the issue #3 data loss.
    _prepare_artifacts(files)
    keep_outputs = [f for f in files if is_artifact(f) and _paired_ipynb(f).exists()]
    plain = [f for f in files if f not in keep_outputs]

    g_plain, e_plain = _run_jupytext(["--to", "notebook"], plain)
    g_keep, e_keep = _run_jupytext(["--to", "notebook", "--update"], keep_outputs)
    groups = {k: g_plain[k] + g_keep[k] for k in g_plain}
    errors = e_plain + e_keep
    _normalize_artifacts(files)
    _save_hashes(files)
    if keep_outputs:
        print(_fmt("nb kept outputs (artifact)", [_key(f, _repo_root()) for f in keep_outputs]))
    _warn_gitignored_artifacts(files)
    if groups["to_ipynb"]:
        print(_fmt("nb created/updated", groups["to_ipynb"]))
    if groups["migrated"]:
        _report_migration(len(groups["migrated"]))
    if groups["unchanged"]:
        print(_fmt("nb unchanged", groups["unchanged"]))
    if groups["skipped"]:
        print(_fmt("nb skipped", groups["skipped"]))
    if not any(groups.values()):
        print("Notebooks: nothing to do")
    for err in errors:
        print(f"nb error: {err}")
    stale = _report_stale_artifacts(files)
    if errors or stale:
        raise SystemExit(1)


def clean_notebooks(force: bool = False) -> None:
    """Sync then delete `.ipynb` files, keeping artifact notebooks unless `force`.

    First calls `sync_notebooks()` to flush any unsaved changes from the
    `.ipynb` files back into their paired `.py` sources, then removes every
    `.ipynb` found under `notebook_src_dirs`. Keeps the working directory
    clean for AI agents and CI environments that only need the `.py` sources.

    An artifact notebook is the deliverable, not a build product, so it is kept and
    counted; `force=True` deletes it too.

    Prints a summary of removed and kept files.
    """
    from juplit.artifacts import artifact_py_files

    sync_notebooks()
    artifacts = {_paired_ipynb(f).resolve() for f in artifact_py_files()}
    protected = set() if force else artifacts
    candidates = {f.resolve() for d in _get_src_dirs() for f in d.rglob("*.ipynb")}
    candidates |= {f for f in artifacts if f.exists()}
    removed, kept = [], []
    for f in sorted(candidates):
        if f in protected:
            kept.append(f.name)
            continue
        removed.append(f.name)
        f.unlink()
    if removed:
        print(_fmt("clean removed", sorted(removed)))
    if kept:
        print(_fmt("clean kept artifact notebooks (--force to delete them too)", sorted(kept)))
    if not removed and not kept:
        print("clean: nothing to remove")

    from juplit.kernel import stop_all

    stopped = stop_all()
    if stopped:
        print(_fmt("clean stopped kernels", stopped))


def html(notebook: Path, out_dir: Path | None = None) -> Path:
    """Render a notebook to standalone HTML with `jupyter nbconvert`. Returns the path.

    A thin wrapper, on purpose: nbconvert already does this well, but agents learn what
    a repo can do by reading its `[tool.poe.tasks]` table, and nbconvert never appears
    there. Same subprocess shape as the jupytext calls above.
    """
    ipynb = _paired_ipynb(notebook) if notebook.suffix == ".py" else notebook
    if not ipynb.exists():
        raise ValueError(f"{ipynb} does not exist — run `juplit nb` first")

    cmd = ["jupyter", "nbconvert", "--to", "html", str(ipynb)]
    if out_dir is not None:
        cmd += ["--output-dir", str(out_dir)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(
            "`jupyter` is not on PATH — install nbconvert (`pip install nbconvert`)"
        ) from None
    if result.returncode != 0:
        raise RuntimeError(f"nbconvert failed: {result.stderr.strip()}")

    target = (out_dir or ipynb.parent) / f"{ipynb.stem}.html"
    print(f"html: {target}")
    return target
