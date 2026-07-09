# Design — guard an edited `.py` from silent overwrite by a newer `.ipynb`

**Task:** juplit: guard edited `.py` from silent overwrite by a newer `.ipynb`
**Branch:** `claude/juplit-sync-direction-nrwr5l` (restarted from `main` after #1 merged) · **Status:** design in review

## Problem

`juplit sync` runs one `jupytext --sync` over every pair and trusts jupytext's
rule that the **newer-by-mtime** side wins. If a `.py` carries edits made since
the last sync but its `.ipynb` is newer by mtime — a `git checkout` / `stash` /
`rebase` bumped the ipynb's mtime, or a stale generated ipynb lingers — jupytext
regenerates the `.py` from that ipynb and the edits vanish silently (exit 0).

The conflict flag shipped in #1 only reports "both sides changed since the last
sync" **after** the sync, and it misses this case entirely: the ipynb's *content*
never changed (only its mtime), so "both changed" is false, yet the `.py` still
gets clobbered. `.py` is juplit's source of truth, so this is real data loss.

## 0. Justify existence

- **Does it need to exist?** Yes — silent loss of source-of-truth `.py` edits.
  #1's conflict flag is post-hoc and blind to the stale-ipynb (mtime-only) case.
- **Already solved?** *Partially, by jupytext.* `jupytext --sync
  --check-source-is-newer <py>` raises when the passed file (`.py`) is not the
  newest of the pair (exit 1); `--warn-only` downgrades that to a warning and
  **skips the file without overwriting** (verified: it prints `Error: Source
  <py> is older than paired file <ipynb>` and does not write). So we do **not**
  reimplement the mtime comparison — jupytext already does it. What jupytext
  *cannot* see is whether the `.py` has **unsynced edits**: applied
  unconditionally, `--check-source-is-newer` would also reject *intended*
  `ipynb → py` edits (editing in Jupyter), breaking juplit's bidirectional
  sync. That missing signal is exactly what juplit's `.sync_hashes.json`
  baseline records. **The only new logic is the gate**: apply jupytext's check
  *only* to files whose `.py` changed since the last sync.
- **Smallest form?** No new files, no new deps, no new state, no new mtime math.
  One predicate (`_py_edited_since_sync`) + split the existing single jupytext
  invocation into two groups by that predicate, reusing jupytext's own flags.

## 1. Pseudocode

```python
def _py_edited_since_sync(f: Path, root: Path, prev: dict) -> bool:
    """True if f's .py content differs from the hash recorded at the last sync.

    False when there is no baseline yet (first sync) — nothing to protect."""
    # rec = prev.get(_key(f, root))
    # return bool(rec) and _hash_file(f) != rec.get("py")
    raise NotImplementedError


def _run_jupytext(args, files) -> tuple[groups, errors]:
    """UNCHANGED except: (a) parse the '--check-source-is-newer' skip warning
    into a new `overwrite_risk` group, (b) no longer writes .sync_hashes.json —
    the caller saves once (see sync_notebooks / generate_notebooks)."""
    # ... existing before/after snapshot + direction classification ...
    # NEW, in the stderr loop, BEFORE the generic "error" branch:
    #   if "is older than paired file" in line:            # jupytext's guard fired
    #       src = substring between "Source " and " is older than paired file"
    #       risk_paths.add(Path(src).resolve())            # this file was NOT synced
    #       continue                                        # do NOT treat as fatal error
    # ... per-file loop: if f.resolve() in risk_paths -> overwrite_risk.append(key); continue
    # groups now also has key "overwrite_risk"; DROP the internal _save_hashes(files) call
    raise NotImplementedError


def sync_notebooks() -> None:
    """Sync, but protect edited .py files from being overwritten by a newer ipynb."""
    # files = _find_percent_notebook_py_files(); early-return if none
    # prev = _load_hashes(); root = _repo_root()
    # guarded = [f for f in files if _py_edited_since_sync(f, root, prev)]
    # normal  = [f for f in files if f not in guarded]
    # g_normal, e1 = _run_jupytext(["--sync"], normal) if normal else (empty, [])
    # g_guard,  e2 = _run_jupytext(
    #       ["--sync", "--check-source-is-newer", "--warn-only"], guarded
    #   ) if guarded else (empty, [])
    # groups = merge(g_normal, g_guard)   # concatenate each list-valued key
    # _save_hashes(files)                 # ONCE, over ALL files (not per subgroup)
    # print direction lines + unchanged + skipped + conflicts (as today)
    # if groups["overwrite_risk"]:
    #     print("sync OVERWRITE BLOCKED — .py has unsynced edits but its .ipynb is "
    #           "newer; refused to overwrite. Resolve, then re-sync: ...")
    # print errors; raise SystemExit(1) if errors OR overwrite_risk  # see Decision D3
    raise NotImplementedError


def generate_notebooks() -> None:
    """UNCHANGED except it now calls _save_hashes(files) itself (moved out of
    _run_jupytext). `nb` is always py→ipynb, so no guard applies."""
    raise NotImplementedError
```

**Error / edge cases**
- No baseline for a file (first sync) → `_py_edited_since_sync` is False → file
  takes the normal path; nothing to protect yet.
- Empty `guarded` or `normal` list → skip that jupytext call (don't invoke it
  with zero file args).
- `--warn-only` makes jupytext print the skip as `Error: Source …`; the new
  parse branch must catch `is older than paired file` **before** the generic
  `"error" in line` branch, or it would be mis-counted as fatal and exit 1
  spuriously.
- An overwrite-risk file is *not* synced, so its content is unchanged on disk —
  it must land in `overwrite_risk`, not `unchanged`.

**Implementation note (refinement during build).** A blocked file must **keep
its prior baseline** — if `_save_hashes` recorded its current (divergent) state,
`_py_edited_since_sync` would read False on the next run and the guard would
stop firing, so the edit could then be lost. So `_save_hashes` **merges** into
the existing state (rather than replacing it), and `sync_notebooks` saves every
file **except** those in `overwrite_risk`. This preserves the intent of the
approved design; it only pins down how state is written.

## 2. Libraries and dependencies

**Already in the project / stdlib:** `hashlib`, `json`, `subprocess`, `pathlib`
(all imported), plus jupytext's own `--check-source-is-newer` / `--warn-only`
flags (jupytext is already the sync engine). The warning is parsed with plain
`str` slicing — **no `re`, no new import**.

**New dependencies: none.**

## 3. File locations

**`juplit/juplit/tasks.py`** (modify) — the only file:
- Add `_py_edited_since_sync(f, root, prev)` under the hash-helpers banner.
- `_run_jupytext`: add the overwrite-risk stderr branch + `overwrite_risk`
  group; **remove** the internal `_save_hashes(files)` call.
- `sync_notebooks`: partition into `guarded` / `normal`, two `_run_jupytext`
  calls, merge groups, `_save_hashes(files)` once, print the overwrite line,
  decide exit (Decision D3).
- `generate_notebooks`: add its own `_save_hashes(files)` call (moved out of
  `_run_jupytext`).

**`juplit/juplit/test_sync.py`** (modify): add the tests in §4. Existing tests
stay green (the merged behavior is unchanged for non-edited or py-newest files).

## 4. Testing outline

- guard blocks loss — `.py` edited since baseline, `.ipynb` touched newer
  (stale content): sync → file reported under `overwrite_risk`, warned, and the
  `.py` is **byte-for-byte unchanged** (edits preserved).
- guard lets safe syncs through — `.py` edited and `.py` newest → syncs
  `py → ipynb` normally, no overwrite-risk.
- bidirectional preserved — `.py` unchanged since baseline, `.ipynb` edited +
  newer → normal group → syncs `ipynb → py`, no overwrite-risk.
- state saved once (regression) — two files, one guarded + one normal → after
  sync, `.sync_hashes.json` still contains **both** files' baselines (guards the
  "two calls, second save clobbers the first" refactor hazard).
- exit code (per D3) — an overwrite-risk sync raises `SystemExit(1)`.

## 5. Estimated scope

~1 source file (`juplit/tasks.py`, ~55 lines added / ~20 changed) + ~5 tests in
`test_sync.py` (~90 lines). No new files, no new dependencies. The gate predicate
and the two-group split are the whole feature; everything protective is borrowed
from jupytext's existing flags.

## Decisions to confirm before implementing

- **D1 — mechanism.** Reuse jupytext's `--check-source-is-newer --warn-only`
  gated by the `.sync_hashes.json` baseline (above), vs. juplit predicting the
  overwrite itself from mtimes. *Recommend the jupytext-reuse approach* — no
  duplicated mtime logic, uses jupytext's tested guard.
- **D2 — scope of protection.** Guard only protects the `.py` (block when a
  newer ipynb would overwrite edited py). It does **not** block the reverse
  (`.py` newest but the `.ipynb` also changed → ipynb edits lost), because the
  ipynb is a regenerable, git-ignored artifact and #1's conflict flag already
  notes that case. *Recommend keeping the guard `.py`-only.*
- **D3 — exit code.** Should an overwrite-block make `juplit sync` exit `1`
  (so the pre-commit hook / CI fails and forces resolution) or exit `0` with
  only a printed warning? *Recommend exit `1`* — juplit runs in pre-commit, and
  a silent pass is how the edit gets lost in the first place.
