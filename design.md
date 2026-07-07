# Design — `juplit sync` direction reporting & conflict flagging

**Task:** juplit: report sync direction (py↔ipynb) and flag conflicts
**Branch:** `claude/juplit-sync-direction-nrwr5l` · **PR:** #1 · **Status:** approved, implemented

## Revision — path keying (post-approval bug fix)

A basename-keying bug was found after approval: the sync state and summary
groups were keyed by `f.name`, so two paired notebooks sharing a basename
across directories (`a/x.py` vs `b/x.py`) collided on one state key — the last
written won, the other was falsely flagged (a spurious `conflict` every run
that never stabilized), and the summary couldn't tell them apart. Fix: key the
state and the reported groups by a stable **repo-root-relative posix path** via
two small helpers, `_repo_root()` and `_key(f, root)`, and match jupytext's
"not a paired notebook" warnings by resolved path rather than basename. This is
reflected in §1/§3/§4 below. Backward-compat: existing `.sync_hashes.json`
files use basename keys, so the first sync after this change shows a one-time
full re-classification as the state is rewritten with path keys — acceptable,
no migration.

## Problem

`juplit sync` calls `jupytext --sync` on every paired `.py` and prints a flat
`updated / unchanged / skipped` summary. It never says **which way** a file
synced. The user wants to see `py → ipynb` syncs separately from `ipynb → py`
syncs.

While scoping this we confirmed (empirically, jupytext 1.19) that
`jupytext --sync` decides direction **purely by file modification time**: the
newer file wins, the older is regenerated. When *both* sides changed since the
last sync, jupytext keeps the newer-mtime side and **silently overwrites the
other — no warning, exit 0**. So the design also surfaces that case, since it is
a real data-loss footgun the current tool hides.

## 0. Justify existence

- **Direction reporting** — *needs to exist* (it is the request). *Already
  solved?* No — jupytext only emits per-file `[jupytext] Updating X` lines on
  stderr, which are noisy, locale/version-dependent, and don't survive as a
  summary. *Smallest form:* no new files, no new state. Hash both the `.py` and
  its `.ipynb` immediately before and after the existing `jupytext` subprocess
  call and read the direction off of which side's content changed. This is a
  few lines inside the existing `_run_jupytext`.

- **Conflict flagging** — *needs to exist* because the alternative is silent
  data loss. *Already solved?* jupytext does not detect it. *Smallest form:*
  reuse the `.sync_hashes.json` state juplit **already maintained** (this PR
  keeps it rather than deleting it). Compare each side's pre-sync hash to the
  hash recorded at the last sync; if **both** differ, both sides were edited →
  flag. No new dependency, no new file, ~6 lines.

- **`_paired_ipynb(py)` helper** — one line (`py.with_suffix(".ipynb")`), but
  used in four places (before-snapshot, after-snapshot, conflict check,
  `_save_hashes`). A named helper documents the same-dir/same-basename pairing
  assumption once instead of scattering `.with_suffix` literals.

- **Deliberately NOT built:** no parsing of jupytext's stderr for direction
  (fragile across versions); no interactive conflict prompt or abort (jupytext
  has already written by the time we can tell — a post-hoc warning is the
  honest, minimal surface); no merge/3-way reconciliation.

Reporting stays on `print`, matching juplit's existing CLI-output style. (The
deep_reasoner `structlog`/`if test():` conventions don't apply here: juplit is a
standalone package with no `structlog` dependency, its source files are plain
modules — not `py:percent` notebooks — and its tests live in `test_*.py` files.)

## 1. Pseudocode

```python
def _paired_ipynb(py_file: Path) -> Path:
    """The .ipynb paired with a percent-format .py (same dir, same basename)."""
    # return py_file with its suffix replaced by .ipynb
    raise NotImplementedError


def _repo_root() -> Path:
    """The dir the state + keys anchor to (pyproject parent, resolved)."""
    raise NotImplementedError


def _key(f: Path, root: Path) -> str:
    """Stable per-file key: f's repo-root-relative posix path (NOT basename)."""
    # try: f.resolve().relative_to(root).as_posix()
    # except ValueError: f.resolve().as_posix()
    raise NotImplementedError


def _load_hashes() -> dict[str, dict[str, str]]:
    """Read {path: {"py": hash, "ipynb": hash}} recorded at the last sync."""
    # if .sync_hashes.json exists: parse and return it
    # on missing/corrupt file: return {}
    raise NotImplementedError


def _save_hashes(files: list[Path]) -> None:
    """Record the .py and .ipynb hashes of each file as the new baseline."""
    # root = _repo_root()
    # for each existing file: store _key(f, root) -> {"py": hash, "ipynb": hash}
    # write .sync_hashes.json (pretty, sorted) next to pyproject.toml
    raise NotImplementedError


def _run_jupytext(args: list[str], files: list[Path]) -> tuple[dict[str, list[str]], list[str]]:
    """Run jupytext once and classify each file by the direction it synced.

    groups keys: to_ipynb, to_py, unchanged, skipped, conflicts.
    """
    # prev   = _load_hashes()                       # last recorded sync
    # root   = _repo_root()
    # before = {f: (hash(py), hash(ipynb)) for f in files}   # pre-sync snapshot
    # run `jupytext <args> <files>` as a subprocess, capture stderr
    # parse stderr for "not a paired" warnings -> skipped_paths (resolved), + errors
    # for each file f:
    #     key = _key(f, root)                        # all groups keyed by path
    #     if f.resolve() in skipped_paths: -> skipped.append(key); continue
    #     # conflict: BOTH sides differ from last recorded sync (looked up by key)
    #     rec = prev.get(key)
    #     if rec and before.py != rec["py"] and before.ipynb != rec["ipynb"]:
    #         conflicts.append(key)
    #     # direction: which side did jupytext rewrite during THIS run?
    #     if hash(py)   != before.py:    -> to_py.append(key)     # ipynb was newer
    #     elif hash(ipynb) != before.ipynb: -> to_ipynb.append(key) # py was newer
    #     else:                          -> unchanged.append(key)
    # _save_hashes(files)                            # new baseline
    # return groups, errors
    raise NotImplementedError


def sync_notebooks() -> None:
    """Sync all paired notebooks; print a direction-split summary + conflicts."""
    # groups, errors = _run_jupytext(["--sync"], files)
    # print "synced py → ipynb: ..."   if groups["to_ipynb"]
    # print "synced ipynb → py: ..."   if groups["to_py"]
    # print "sync unchanged: ..."      if groups["unchanged"]
    # print "sync skipped (not paired): ..." if groups["skipped"]
    # print "sync CONFLICT — both sides changed since last sync; jupytext kept
    #        the newer by mtime and overwrote the other: ..." if groups["conflicts"]
    # print "Sync: nothing to do" if all groups empty
    # print each error; raise SystemExit(1) if errors
    raise NotImplementedError


def generate_notebooks() -> None:
    """`juplit nb`: same _run_jupytext, but --to notebook only ever goes py→ipynb."""
    # groups, errors = _run_jupytext(["--to", "notebook"], files)
    # report groups["to_ipynb"] as "nb created/updated"; unchanged; skipped
    # (conflicts not surfaced here: `nb` intentionally regenerates the ipynb)
    raise NotImplementedError
```

**Error / edge cases**
- `.sync_hashes.json` missing or corrupt → `_load_hashes` returns `{}` → no
  conflicts flagged (correct: no baseline to compare against, e.g. first sync).
- A file with no recorded baseline (`rec is None`) → never a conflict.
- jupytext non-zero exit with no parsed error line → synthesize one error from
  stderr so `sync_notebooks` still exits 1 (unchanged from today).
- A conflicted file is *also* reported under its applied direction, so the user
  sees both "synced ipynb → py: nb.py" and the conflict line for `nb.py`.

## 2. Libraries and dependencies

**Already in the project / stdlib** (all already imported in `tasks.py`):
`hashlib` (md5 content hash), `json` (read/write the state file), `subprocess`
(invoke `jupytext`), `pathlib` (paths), `jupytext` (runtime dep, the sync
engine itself). Reporting uses builtin `print`, consistent with the rest of
`tasks.py`.

**New dependencies: none.**

## 3. File locations

**`juplit/juplit/tasks.py`** (modify):
- Re-add `import json` (removed earlier this PR).
- Under the `# Hash-based …` banner: keep `_hash_file`; add `_paired_ipynb`;
  add `_repo_root` and `_key` (path keying); `_state_path`/`_load_hashes`/
  `_save_hashes` keyed by `_key(f, root)` (repo-root-relative path), with
  `_save_hashes` recording **both** `py` and `ipynb` hashes per file
  (`{path: {"py":…, "ipynb":…}}`).
- Rewrite `_run_jupytext`: before/after both-sides snapshot for direction, plus
  the `prev`-vs-pre-sync conflict check; every group entry is a `_key` path;
  "not a paired" skips matched by resolved path. Return dict gains `to_ipynb`,
  `to_py`, `conflicts`.
- `sync_notebooks`: print the two direction lines + the conflict line.
- `generate_notebooks`: map `to_ipynb` → "nb created/updated".

**`juplit/juplit/test_sync.py`** (new): end-to-end tests (see §4).

**`.sync_hashes.json`** — already git-ignored; regenerated at runtime, not
committed. No `.gitignore` change needed.

## 4. Testing outline

New `juplit/test_sync.py` (juplit's convention: `pytest` `test_*.py`, real
jupytext round-trips in a `tmp_path` project; `os.utime` forces mtimes so
direction is deterministic without sleeps):

- `_run_jupytext` / `sync_notebooks`, happy path — edit `.py`, sync → asserts
  "synced py → ipynb" printed, "ipynb → py" absent.
- happy path — edit `.ipynb` (real `nbformat`), sync → asserts "synced ipynb →
  py" printed **and** the edit landed in the `.py`.
- boundary — no edits after a settle sync → asserts "sync unchanged", neither
  direction line.
- error/data-loss path — edit **both** sides, `.ipynb` newer → asserts "sync
  CONFLICT" printed, and confirms the `.py` edit was overwritten (documents the
  jupytext behavior the flag warns about).
- boundary — edit only one side after a baseline → asserts **no** "sync
  CONFLICT".
- regression (path keying) — two paired notebooks sharing a basename in
  different dirs (`pkg/a/x.py`, `pkg/b/x.py`) with differing content; a no-edit
  second sync asserts **no** "sync CONFLICT", both reported as unchanged under
  their distinct `pkg/a/x.py` / `pkg/b/x.py` keys, and neither `.py` modified.

Existing `test_smoke.py` (package exports, CLI `--help`) stays green.

## 5. Estimated scope

~2 files: `juplit/tasks.py` modified (~90 lines added, ~25 changed across the
hash helpers, `_run_jupytext`, and the two task functions) and a new
`juplit/test_sync.py` (~130 lines). Nothing here is deletable without dropping a
tested behavior: cut conflict detection and `.sync_hashes.json` + `_load_hashes`
+ the conflict test go with it; cut `_paired_ipynb` and four call sites inline a
`.with_suffix` literal each.
