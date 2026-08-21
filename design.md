# Design — artifact notebooks: agent-facing execution + committed-output hygiene

Task: [juplit: artifact notebooks — agent-facing execution + committed-output hygiene](https://app.notion.com/p/3c30dbff56478187a081ef2a1c00eba2)
Branch: `claude/juplit-committed-output-execution-qeg2wr`
Subsumes: [issue #3 — Artifact notebooks: keep executed outputs for designated .ipynb paths](https://github.com/DeanLight/juplit/issues/3)

Everything asserted about jupytext / jupyter_client behaviour below was reproduced in this
environment (jupytext 1.19.1, jupyter_client 8.8.0, ipykernel 7.2.0, nbformat 5.10.4). The
transcripts are in **Appendix A**; two of them overturn the mechanisms the request assumed.

---

## Pushback before the design

The request asked for pushback. Six items, in descending order of how much they change the shape.

### P1. The sketched kernel does not survive the CLI process. It dies ~1s after it exits.

The request's sketch uses `jupyter_client.manager.start_new_kernel(...)`. That works *within* one
process, and looks like it works across processes — the first re-attach succeeds — but the kernel
is dead by the second. This is the acceptance criterion the whole agent loop rests on, so it is
worth being precise about why.

On POSIX, `jupyter_client.launcher.launch_kernel` unconditionally does
(`launcher.py:153`):

```python
kwargs["start_new_session"] = True
if not independent:
    env["JPY_PARENT_PID"] = str(os.getpid())
```

and ipykernel reads that env var into `IPKernelApp.parent_handle`, then (`kernelapp.py:219`):

```python
elif self.parent_handle and self.parent_handle != 1:
    # PID 1 (init) is special and will never go away, only be reassigned.
    self.poller = ParentPollerUnix(parent_pid=self.parent_handle)
```

`ParentPollerUnix` kills the kernel when its recorded parent dies. So a kernel started by a CLI
invocation is *designed* to die when that invocation exits. Observed exactly that, twice:
`[IPKernelApp] WARNING | Parent appears to have exited, shutting down.` — and passing
`env={"JPY_PARENT_PID": "1"}` to `start_kernel()` does **not** help, because `launch_kernel`
overwrites it. `independent=True` is not plumbed through `KernelManager.start_kernel` in 8.8.0
(the call hangs).

Fix, and it is small: juplit launches the kernelspec argv itself.

```python
argv = KernelSpecManager().get_kernel_spec(name).argv   # ["python", "-m", "ipykernel_launcher", "-f", "{connection_file}"]
subprocess.Popen(argv, env={**os.environ, "JPY_PARENT_PID": "1"}, start_new_session=True, ...)
```

~15 lines, no `KernelManager` at all on the start path. Verified: survives repeated CLI exits,
`zz` set in invocation 1 is visible in invocation 3, PPID 1, `Ssl`. (Appendix A4.)

### P2. Staleness cannot be tracked in `.sync_hashes.json`, and per-file hashes are the wrong grain.

Issue #3 proposes recording "the `.py` hash at the time the notebook was last executed" in the
existing sidecar. Two problems:

- **`.sync_hashes.json` is gitignored.** CI on a fresh clone has the committed `.py` and the
  committed `.ipynb` and nothing else. A guard that lives in untracked local state cannot block
  the thing the acceptance criteria ask it to block.
- **Per-file granularity cannot drive repair.** Capability 5 wants "re-execute *only those*
  cells". A whole-file hash says the file moved, not which cells did, so the only repair it can
  offer is restart-and-run-all — which for these notebooks is the ~20-minute, real-money path.

Instead the provenance goes **into the committed artifact, per cell**:

```json
"metadata": {"juplit": {"src_sha256": "3fc984d090689e33", "executed": "2026-08-21T22:35:28Z"}}
```

Staleness is then a pure function of the committed file — `sha256(cell.source) !=
cell.metadata.juplit.src_sha256` — with no baseline, no sidecar, and per-cell resolution. It
survives a fresh clone, so `juplit check` works in CI and in a pre-commit hook.

The obvious objection is that this pollutes the `.py`. It does — by default. Verified: on the
`ipynb → py` direction jupytext writes unknown cell metadata straight into the cell markers:

```python
# %% juplit={"src_sha256": "3fc984d090689e33"}
```

That is unacceptable (the `.py` is the source of truth and must stay clean). The fix is one line
of notebook metadata, `jupytext.cell_metadata_filter: "-juplit"`, after which the stamp is
invisible to the `.py` in **both** directions and survives both. Verified (Appendix A3). juplit
**writes that filter itself** when a pair is registered as an artifact — it is load-bearing, not
documentation, and `juplit check` fails if it is missing.

### P3. Never auto-clear stale outputs. Mark and fail.

The natural reading of "fail loudly on that condition, and re-execute only those cells" is that
sync clears the outputs it can no longer vouch for. It must not. Those outputs are the artifact of
record and cost real money to produce (issue #3: ~40k model calls, ~20 minutes); deleting them on
a `sync` the user ran for an unrelated reason is the same class of bug as the one we are fixing.
So: stale outputs stay in place, the cells are named, the command exits non-zero, and the human or
agent decides between `juplit rerun --stale` and reverting the source edit.

### P4. `juplit check` belongs in the MVP, not in stage 6.

The request puts the CI/pre-commit check at capability 6, after the MVP. But acceptance criterion
1 is "the finding-1 repro is caught **and blocked**", and only the hook blocks. The check is also
nearly free once stamps exist — it is the same comparison the sync guard already runs, over a file
list. So the stamp check ships in the MVP; the *rest* of capability 6 (size budgets, forbidden
metadata) stays in stage 2 where the request put it.

### P5. Do not spill oversized images to files. Downscale them.

Capability 7 asks scrub to "spill oversized images to files and reference them". This defeats the
purpose of the artifact. Notebook image outputs are inline mime-bundles; the only way to point one
at an external file is to rewrite it as an HTML output containing `<img src="...">`, and GitHub's
notebook renderer sanitises that away — so on the one surface where reviewers actually read these
notebooks, a spilled plot renders as nothing. The artifact stops being self-contained and starts
depending on a directory that is easy to move, gitignore, or lose.

Counter-proposal: cap images by **re-encoding** — downscale to a max dimension and re-encode PNG —
so the notebook stays standalone and the byte cap is still enforced. This needs Pillow, which is a
new dependency, so scrub's image handling is deliberately staged last (stage 2) and can ship as
"report oversized images, fail the budget check, let the author decide" with **zero** new deps if
Pillow is refused. Spilling stays available as an explicit `--spill` opt-out for the pathological
case, never the default.

### P6. Two small corrections to the constraints.

- *"percent-format modules, `if test():` blocks"* — that is juplit's convention for **downstream**
  repos. juplit's own package is plain modules (`tasks.py`, `cli.py`) with `# ── banner ──`
  section comments, and its tests are separate `juplit/test_*.py` files driving tmp-path
  fixtures. The new modules follow **juplit's actual house style**, not the SKILL's.
- *"no new heavyweight deps beyond the jupyter client/nbformat layer"* — taken literally as:
  `nbformat` (already transitive via jupytext) and `jupyter_client` promoted to real runtime
  dependencies, `ipykernel` documented as required-at-runtime-if-you-execute but **not** depended
  on (it is the kernel, not our library), and nothing else in the MVP. `nbclient` is *not* needed —
  the restart-and-run-all path reuses our own kernel layer rather than pulling a second executor.

Everything else in the request I agree with as written, including the non-goals.

---

## 0. Justify existence

**The feature as a whole.** Two of the three problems are data-loss bugs in juplit's existing
commands against a use case juplit does not model (`nb` wipes committed outputs, `clean` deletes
the file, `sync` silently re-pairs new source with old output). Those must be fixed in juplit
because juplit owns the `.py ↔ .ipynb` contract; nothing else can. The third — the agent loop — is
new surface, and is the part that has to earn its place hardest. It does, because the alternative
we actually evaluated (`datalayer/jupyter-mcp-server`) requires a running Jupyter server plus RTC,
which the headless constraint (CI, SLURM login node, ephemeral sandbox) rules out, adds 30+ tools
to the agent's surface, and does not address the staleness bug at all.

Component by component:

| Component | Needs to exist? | Already solved? | Smallest form |
|---|---|---|---|
| Artifact registry (`artifact_notebooks` globs) | Yes — the opt-in scope the constraints demand; without it this is a change to default behaviour | No | Config list + `fnmatch` on the repo-relative path. stdlib. ~15 lines |
| `--update` branch in `nb` | Yes — issue #3's data loss | **Yes, by jupytext** — `--to notebook --update` preserves outputs (verified) | One extra flag in the existing `_run_jupytext` arg list |
| `clean` skip | Yes — deleting the deliverable | No | One filter + one summary line |
| Per-cell stamps | Yes — the only stateless carrier of provenance (P2) | No. `.sync_hashes.json` is the wrong grain *and* gitignored | `hashlib.sha256` into `cell.metadata`, ~20 lines |
| `cell_metadata_filter` enforcement | Yes — without it the stamp corrupts the `.py` (verified) | Yes, by jupytext — we only have to *set* it | One key in notebook metadata |
| Staleness check | Yes — the highest-priority item | No | One comparison per cell |
| Kernel session | Yes — capability 2 | Partly: `jupyter_client` gives the client and the kernelspec lookup. It does **not** give a detached kernel (P1) | Hand-rolled `Popen` + a JSON state file, ~60 lines |
| IOPub drain → outputs | Yes — capability 3/4 | `nbclient` does this, but pulling a second execution engine to reuse ~30 lines of message mapping is a bad trade | `nbformat.v4.new_output` per message type, ~30 lines |
| Digest read | Yes — capability 1; it is the whole token argument | No | Pure `nbformat` walk, ~50 lines |
| `commit-cell` | Yes — capability 4 | No | `jupytext.reads`/`writes` (byte-stable, verified) + `nbformat` insert |
| Scrub | Yes, eventually — committed notebooks churn badly | Partly: `nbconvert`'s `ClearMetadataPreprocessor` does the metadata half | Staged to stage 2; MVP does only the always-on normalisation (drop `metadata.execution` timestamps, null `execution_count`) which is ~8 lines and prevents churn from day one |
| Scaffold / HTML export | **No** — cut from this design | `nbconvert --to html` already exists; a template is a file the user can copy | Not built. Capability 8 is documented as "use `jupyter nbconvert`" |

Cut outright: capability 8 (both halves), `nbclient`, a `juplit status` command (folded into
`juplit kernel status` and `juplit check`), the regex form of `artifact_notebooks` (issue #3 Q1 —
globs cover the stated case; YAGNI).

---

## 1. Pseudocode

### 1a. Registry and config — `juplit/artifacts.py`

```python
def artifact_globs() -> list[str]:
    """The `artifact_notebooks` globs from [tool.juplit], as repo-relative posix patterns."""
    # read pyproject via the existing _find_pyproject_toml()
    # return cfg.get("artifact_notebooks", []) ; [] means the feature is entirely off
    raise NotImplementedError


def is_artifact(py_file: Path) -> bool:
    """True if this paired .py is declared an artifact notebook (its .ipynb is committed)."""
    # key = repo-relative posix path, same normalisation as tasks._key()
    # return any(fnmatch(key, g) for g in artifact_globs())
    raise NotImplementedError


def artifact_py_files() -> list[Path]:
    """Every declared artifact .py, including ones outside notebook_src_dirs.

    The globs are additive to the scanned set: `experiments/**/*.py` need not also be
    listed in notebook_src_dirs. Non-artifact discovery is unchanged.
    """
    # for each glob: root.glob(pattern), keep files that _is_paired_notebook()
    # union with tasks._find_percent_notebook_py_files(), dedup, sort
    raise NotImplementedError
```

### 1b. Stamps and staleness — `juplit/artifacts.py`

```python
STAMP_KEY = "juplit"
FILTER = "-juplit"          # jupytext cell_metadata_filter; keeps the stamp out of the .py


def source_sha(source: str) -> str:
    """The 16-hex-char sha256 prefix of a cell's source, used as its provenance stamp."""
    raise NotImplementedError


def ensure_filter(nb: NotebookNode) -> bool:
    """Set jupytext.cell_metadata_filter = "-juplit" on the notebook. True if it changed.

    Load-bearing: without it, jupytext writes the stamp into the .py cell markers on the
    ipynb -> py direction (verified). Called on every artifact write.
    """
    raise NotImplementedError


def stamp_cell(cell: NotebookNode, executed_at: datetime) -> None:
    """Record src_sha256 + executed on a cell we just executed. No-op for cells with no outputs."""
    raise NotImplementedError


def cell_state(cell: NotebookNode) -> Literal["clean", "stale", "unverified", "empty"]:
    """Provenance verdict for one cell of an artifact notebook.

    empty       — no outputs. Nothing to vouch for.
    clean       — stamped, and source_sha(cell.source) == the stamp.
    stale       — stamped, and the stamp disagrees. The outputs describe older source.
    unverified  — has outputs but no stamp: executed outside juplit (e.g. a human in Jupyter).
    """
    raise NotImplementedError


def scan(ipynb: Path) -> dict[str, list[int]]:
    """Group an artifact notebook's cell indices by cell_state. Pure read, no writes."""
    raise NotImplementedError


def normalize(nb: NotebookNode) -> None:
    """Always-on, output-preserving hygiene applied on every artifact write.

    Drops metadata.execution (wall-clock timestamps that churn on every run) and
    metadata.widgets; leaves outputs and execution_count alone. This is the minimal
    always-on slice of capability 7 — the size caps and image handling are stage 2.
    """
    raise NotImplementedError
```

### 1c. Persistent kernel — `juplit/kernel.py`

```python
def session_path(name: str) -> Path:
    """.juplit/kernels/<name>.json — connection info + pid + cwd for a detached kernel."""
    raise NotImplementedError


def start(name: str = "default", kernel_name: str = "python3", cwd: Path | None = None) -> dict:
    """Launch a kernel that outlives this process, and record it. Idempotent if one is alive.

    Deliberately does NOT use KernelManager.start_kernel: jupyter_client forces
    JPY_PARENT_PID to this process's pid, and ipykernel's ParentPollerUnix then kills the
    kernel when the CLI exits (see P1). We launch the kernelspec argv ourselves.
    """
    # if session_path exists and alive(): return it unchanged
    # build connection dict: transport=ipc on posix (unix sockets under .juplit/, not
    #   world-visible TCP ports on a shared login node), tcp on win32; random hmac key
    # argv = KernelSpecManager().get_kernel_spec(kernel_name).argv, {connection_file} substituted
    # Popen(argv, env={**environ, "JPY_PARENT_PID": "1"}, start_new_session=True,
    #       cwd=cwd or repo_root, stdout/stderr/stdin=DEVNULL)
    # write session_path; wait_for_ready; return session
    raise NotImplementedError


def alive(session: dict) -> bool:
    """True if the recorded pid exists and the kernel answers kernel_info within a short timeout."""
    raise NotImplementedError


def stop(name: str = "default") -> bool:
    """Shut the kernel down: control-channel shutdown_request, SIGTERM as fallback.

    We did not spawn it in this process, so there is no KernelManager to ask; the pid in
    the session file plus the control channel are all we have. Removes the session file
    and its ipc sockets.
    """
    raise NotImplementedError


def execute(code: str, name: str = "default", timeout: float = 300) -> list[NotebookNode]:
    """Run code on the session kernel and return its outputs as nbformat output nodes.

    Never touches any file. This is what makes "try it, look, iterate" free of the
    notebook: a failed attempt exists only in the kernel's history and this return value.
    """
    # kc = BlockingKernelClient(); load_connection_info(session["conn"]); start_channels()
    # msg_id = kc.execute(code)
    # drain get_iopub_msg(), ignoring messages whose parent_header.msg_id != msg_id:
    #   stream                        -> new_output("stream", name=, text=)
    #   execute_result | display_data -> new_output(type, data=, metadata=)
    #   error                         -> new_output("error", ename=, evalue=, traceback=)
    #   status/idle                   -> stop
    # on timeout: interrupt (SIGINT to the process group) and raise RuntimeError
    raise NotImplementedError


def kernel_errors() -> None:
    """Error cases for the kernel module."""
    # no session file, or recorded pid is gone      -> RuntimeError("no live kernel '<name>'; juplit kernel start")
    # kernel_info times out but pid exists          -> RuntimeError(... "kernel is wedged; juplit kernel stop")
    # kernelspec not found                          -> RuntimeError("kernel '<n>' not installed; pip install ipykernel")
    # execute exceeds timeout                       -> interrupt, then RuntimeError with partial outputs attached
    # ipc socket path over the ~104-char sun_path limit -> fall back to tcp on 127.0.0.1, warn
```

### 1d. Digests and bounded rendering — `juplit/inspect.py`

```python
def output_digest(output: NotebookNode) -> str:
    """One-line, base64-free summary of a single output.

    stream            -> "stream(stdout) 1.2KB 14L 'Fitting fold 3...' … 'done'"
    image/png         -> "image/png 640x480 82KB"        (dims from the IHDR bytes of the
                         first 64 base64 chars — no decode, no Pillow; verified)
    text/html         -> "text/html 41KB <table>"
    execute_result    -> "text/plain 3L 'shape: (1200, 8)'" plus "[1200 rows x 8 columns]"
                         parsed out of the repr when pandas emitted it (best effort)
    error             -> "error ZeroDivisionError: division by zero (+12 frames)"
    """
    raise NotImplementedError


def cells_table(ipynb: Path, *, show_source: bool = False, max_source_lines: int = 8) -> str:
    """The cheap read (capability 1). One line per cell by default; never any base64.

        [00] md          6L
        [07] code  12L   out: stream 1.2KB, image/png 640x480 82KB     stale
        [08] code   4L   out: error ZeroDivisionError                  clean

    Default output for a 40-cell notebook is 40 short lines — a few hundred tokens —
    because sources are NOT included by default. --source adds them, truncated to
    max_source_lines per cell; `juplit cell` is the escape hatch for one cell in full.
    """
    raise NotImplementedError


def cell_detail(ipynb: Path, index: int, *, full: bool = False) -> str:
    """One cell: full source, stamp, and outputs rendered per truncate_output()."""
    raise NotImplementedError


def truncate_output(output: NotebookNode, *, max_lines: int = 40, max_chars: int = 4000) -> str:
    """Render one output for an agent's context: head/tail lines with an elision marker.

    Images and other binary mime types are ALWAYS rendered as their digest, even under
    --full — `juplit cell --full` means "the whole text", never "the base64".
    """
    raise NotImplementedError
```

### 1e. Committing the accepted cell — `juplit/artifacts.py`

```python
def commit_cell(py_file: Path, source: str, outputs: list[NotebookNode], *,
                index: int | None = None, cell_type: str = "code") -> int:
    """Insert a cell into the .py AND its captured outputs into the paired .ipynb. Returns the index.

    index=None appends. Both halves are written from the same in-memory cell list, so they
    cannot disagree about ordering.
    """
    # nb_py    = jupytext.reads(py_file.read_text(), fmt="py:percent")   # in memory, no write
    # nb_ipynb = nbformat.read(paired_ipynb(py_file))
    # assert the two agree cell-for-cell on source before touching anything (see errors)
    # cell = new_code_cell(source); stamp_cell(copy with outputs, now)
    # insert into both at `index`
    # ensure_filter(nb_ipynb); normalize(nb_ipynb)
    # py_file.write_text(jupytext.writes(nb_py, fmt="py:percent"))   # byte-stable round-trip
    # nbformat.write(nb_ipynb, paired_ipynb(py_file))                # byte-stable writer
    # post-condition: re-read both, assert equal cell count and equal per-cell source
    # refresh the .sync_hashes.json baseline for this pair (both halves), so the next
    #   `juplit sync` does not see "both sides changed" and report a false conflict
    raise NotImplementedError


def commit_cell_errors() -> None:
    """Error cases for commit_cell."""
    # .py is not a declared artifact          -> ValueError("<p> is not in artifact_notebooks")
    # paired .ipynb missing                   -> ValueError("... run `juplit nb` first")
    # halves disagree on source before insert -> ValueError("<p> and its .ipynb are out of sync; run `juplit sync` first")
    #                                            (refuse rather than pick a winner — this is the data-loss shape)
    # index out of range                      -> ValueError with the valid range
    # post-condition assert fails             -> restore both files from the pre-write bytes, raise RuntimeError
```

### 1f. Command wiring — `juplit/tasks.py` (modified) and `juplit/cli.py`

```python
def sync_notebooks() -> None:          # MODIFIED
    """Unchanged for ordinary pairs. Artifact pairs additionally keep outputs and are checked.

    Ordinary pairs: exactly today's behaviour, byte for byte.
    Artifact pairs: same jupytext call (--sync already preserves outputs positionally —
      that is the bug, not the fix), then re-check every cell's stamp and report staleness.
    """
    # ...existing guarded/normal split, unchanged...
    # for each artifact pair after the jupytext run:
    #     ensure_filter + normalize + write back if changed
    #     states = scan(ipynb); collect stale/unverified cell indices
    # print existing summary lines
    # if stale: print "artifact STALE: <nb> cells 4,7 — outputs predate the current .py
    #                  (juplit rerun --stale <nb>, or revert the source edit)"
    # exit 1 on stale, as today's exit 1 on overwrite_risk


def generate_notebooks() -> None:      # MODIFIED
    """Adds --update for artifact pairs whose .ipynb already exists, so outputs survive.

    Three groups now: ordinary pairs (--to notebook, as today), artifact pairs with an
    existing .ipynb (--to notebook --update), artifact pairs with none (--to notebook).
    Then the same stamp check as sync.
    """
    raise NotImplementedError


def clean_notebooks(force: bool = False) -> None:   # MODIFIED
    """Skips artifact notebooks unless --force, and says how many it kept."""
    # ...sync, then unlink every .ipynb EXCEPT paired_ipynb(f) for artifact f...
    # print "clean kept N artifact notebooks (--force to delete them too)"


def check_artifacts(strict: bool = False) -> None:  # NEW
    """The pre-commit / CI guard. Reads only committed files — no kernel, no jupytext, no sidecar.

    Fails on: any stale cell; a missing cell_metadata_filter; a declared artifact whose
    .ipynb is gitignored (its outputs would be preserved locally and silently never
    committed, which is indistinguishable from the feature not working — issue #3).
    Warns on: unverified cells. --strict promotes that warning to a failure.
    """
    raise NotImplementedError


def rerun(py_file: Path, *, stale_only: bool = True, name: str = "default") -> None:  # NEW, stage 2
    """Re-execute cells on the session kernel and write back outputs + fresh stamps.

    stale_only=True re-runs just the cells scan() called stale, in order, on the existing
    kernel. stale_only=False is the final clean build: stop the kernel, start a fresh one,
    run every cell top to bottom. Restart-and-run-all reuses this module rather than nbclient.
    """
    raise NotImplementedError
```

CLI (cyclopts, matching the existing `app.command` style; every command's stdout is bounded):

| Command | Stage | Purpose |
|---|---|---|
| `juplit cells <nb> [--source] [--json]` | MVP | capability 1 — digest read |
| `juplit cell <nb> <i> [--full]` | MVP | capability 1 — one cell whole |
| `juplit kernel start\|stop\|status [--name]` | MVP | capability 2 |
| `juplit run <code>\|- [--name] [--timeout] [--max-lines]` | MVP | capability 3 — scratch, file untouched |
| `juplit commit-cell <nb> [--index] [--markdown] [--from-last]` | MVP | capability 4 |
| `juplit check [--strict]` | MVP | capabilities 5+6 — the guard |
| `juplit rerun <nb> [--stale\|--all]` | stage 2 | capability 5 — repair |
| `juplit scrub <nb> [--max-output-bytes] [--spill]` | stage 2 | capability 7 |
| `juplit clean --force` | MVP | issue #3 Q4 |

`--from-last` on `commit-cell` is the ergonomic core of the loop: `juplit run` caches the last
snippet and its captured outputs under `.juplit/last-run.json`, so accepting it is
`juplit commit-cell experiments/x.py --from-last` — the agent does not re-send the code, and the
committed outputs are provably the ones it just looked at, not a re-execution.

---

## 2. Libraries and dependencies

**Already in the project (reused):**

- `jupytext` — `--sync` / `--to notebook --update` via the existing `_run_jupytext`; `jupytext.reads`/`writes` in-memory for `commit-cell` (round-trip verified byte-identical on `docs/using_juplit.py`).
- `cyclopts` — all new commands, same `@app.command` shape.
- stdlib: `hashlib` (stamps), `fnmatch` (globs), `subprocess` (kernel launch, `git check-ignore`), `json`, `pathlib`, `struct` + `base64` (PNG dimensions — 48 decoded bytes, no image library), `tomllib` (config), `uuid`, `datetime`.

**Promoted from transitive to declared runtime dependencies:**

- `nbformat>=5.10` — already installed as a jupytext dependency; juplit now imports it directly, so it must be declared. Zero install-size change.
- `jupyter_client>=8.0` — new to the install, ~1 MB, pulls `pyzmq`, `traitlets`, `jupyter-core`. This is the "jupyter client layer" the constraints explicitly permit. Used for exactly two things: `KernelSpecManager` (find the kernel argv) and `BlockingKernelClient` (talk to it). Ruled out: writing the ZMQ wire protocol by hand (HMAC framing, five channels — no), and `nbclient` (a second execution engine, with its own kernel lifecycle, to avoid ~30 lines of message mapping, and it cannot do the persistent-across-invocations part at all).

**Not depended on:**

- `ipykernel` — required at runtime *by the user's project* to have a `python3` kernel at all, but juplit never imports it. Documented in the README/SKILL as a dev-dependency of the consuming repo (juplit's own `[dependency-groups] dev` already has it). A missing kernelspec is a clean error message, not a crash.
- `Pillow` — only for stage-2 image downscaling (P5), and explicitly flagged for a separate approval when that stage is designed. The MVP and stage-2-without-Pillow both work: oversized images are *reported*, not rewritten.
- `nbconvert` — its `ClearMetadataPreprocessor` overlaps scrub, but pulling it (and its pandoc-adjacent tail) to replace ~8 lines of dict deletion fails the hierarchy. HTML export (capability 8) is documented as "run `jupyter nbconvert --to html` yourself" rather than wrapped.

**New dependencies requiring approval: `jupyter_client>=8.0` (and the `nbformat` declaration).**

---

## 3. File locations

**`juplit/artifacts.py`** — NEW, ~230 lines. Everything about the committed pair: registry
(`artifact_globs`, `is_artifact`, `artifact_py_files`), provenance (`source_sha`, `ensure_filter`,
`stamp_cell`, `cell_state`, `scan`), write hygiene (`normalize`), and `commit_cell` /
`commit_cell_errors`. Grouped with the existing `# ── banner ──` convention.

**`juplit/kernel.py`** — NEW, ~180 lines. `session_path`, `start`, `alive`, `stop`, `execute`,
plus the IOPub-message → `nbformat` output mapping. No knowledge of artifacts or of the `.py`
format; it is a kernel, not a notebook module.

**`juplit/inspect.py`** — NEW, ~150 lines. `output_digest`, `cells_table`, `cell_detail`,
`truncate_output`. Pure rendering: takes nbformat nodes, returns strings. No I/O beyond
`nbformat.read`.

**`juplit/tasks.py`** — MODIFIED:
- `sync_notebooks()` — after the existing jupytext split, add the artifact post-pass (filter,
  normalize, stamp scan) and the stale summary line + exit code. Ordinary pairs untouched.
- `generate_notebooks()` — split `files` three ways and add `--update` for existing artifact
  `.ipynb`s; same post-pass.
- `clean_notebooks(force=False)` — exclude artifact `.ipynb`s from the unlink loop; new summary line.
- `check_artifacts(strict=False)` — NEW, at the end of the "Public tasks" section.
- `_find_percent_notebook_py_files()` — unchanged; `artifacts.artifact_py_files()` unions on top,
  so declared artifacts outside `notebook_src_dirs` are picked up.
- `_save_hashes()` — unchanged, but now also called from `commit_cell` to refresh the pair's
  baseline.

**`juplit/cli.py`** — MODIFIED: the nine commands in the table above, in the existing style;
`clean` gains `--force`; `main()` unchanged.

**`juplit/test_artifacts.py`**, **`juplit/test_kernel.py`**, **`juplit/test_inspect.py`** — NEW
pytest files, following `test_sync.py`'s tmp-path-project fixture style.

**`pyproject.toml`** — MODIFIED: `nbformat`, `jupyter_client` in `[project] dependencies`; poe
targets for `check`.

**`README.md`**, **`juplit/SKILL.md`** — MODIFIED: an "Artifact notebooks" section (the config,
the gitignore un-ignore rule with its rationale, the agent loop as a worked example). The SKILL
change is what makes the loop discoverable to Claude, so it is MVP scope, not documentation
afterthought.

**`.pre-commit-config.yaml`** (consuming repos; documented, not shipped) — a `juplit check` hook.

---

## 4. Testing outline

All tests are pytest files in `juplit/`, using `test_sync.py`'s tmp-path project fixture. Kernel
tests are marked `@pytest.mark.kernel` and skipped when no `python3` kernelspec is installed, so
CI without ipykernel stays green.

**Registry — `test_artifacts.py`**
- `is_artifact` happy: a path matching `experiments/**/*.py` is an artifact — boundary: a
  same-named file outside the glob is not.
- `artifact_py_files` happy: a declared artifact outside `notebook_src_dirs` is discovered.
- Regression: with no `artifact_notebooks` key, every discovery function returns exactly today's set.

**Provenance — `test_artifacts.py`**
- `cell_state` happy: stamped cell whose source is unchanged → `clean`.
- `cell_state` error-path: **the finding-1 repro end to end** — build a paired notebook, execute
  it, stamp it, edit `x = 40 + 2` → `x = 40 + 3` in the `.py`, run `sync_notebooks()`; assert the
  cell is `stale`, the outputs are still present (P3), and `SystemExit(1)` is raised.
- `cell_state` boundary: outputs with no stamp → `unverified`; no outputs → `empty`.
- `ensure_filter` happy: after a full `sync` round trip in **both** directions, the `.py` contains
  no `juplit=` marker and the `.ipynb` still carries the stamps. (This is the P2 mechanism; it is
  the single most important test in the suite.)
- `normalize` happy: `metadata.execution` is dropped, outputs and `execution_count` survive.

**Commands — `test_artifacts.py`**
- `generate_notebooks` happy: an artifact `.ipynb` with outputs keeps them across `juplit nb`;
  error-path: a *non*-artifact keeps today's wipe behaviour (no regression).
- `clean_notebooks` happy: artifact `.ipynb` survives and is counted; `--force` deletes it.
- `check_artifacts` error-path: exits 1 on a stale cell; exits 1 when the filter is missing; exits
  1 when a declared artifact's `.ipynb` is gitignored; exits 0 with a warning on `unverified`, and
  1 under `--strict`.
- `sync_notebooks` regression: a project with no artifacts produces byte-identical output and the
  same `.sync_hashes.json` as before the change.

**Kernel — `test_kernel.py`** (marked)
- `start`/`execute` happy: `execute("1+1")` returns one `execute_result` output.
- **Persistence, the acceptance criterion**: start a kernel, set a variable in one
  `subprocess.run([sys.executable, "-m", "juplit.cli", "run", ...])`, read it back in a *second*
  subprocess, assert the value survives. This must be a real out-of-process test — an in-process
  one passes even with the broken `start_new_kernel` approach (P1).
- `execute` error-path: `1/0` returns an `error` output and does **not** raise.
- `execute` boundary: a snippet exceeding `--timeout` interrupts and raises `RuntimeError`.
- `stop` happy: session file and ipc sockets are gone, pid no longer alive.
- `start` idempotence: a second `start` with a live session reuses it (same pid).

**Inspect — `test_inspect.py`**
- `output_digest` happy per type: stream, `image/png` (dims read from a crafted IHDR), error,
  `text/html`.
- **Token budget, the acceptance criterion**: a synthetic 40-cell notebook with 4 MB of embedded
  base64 renders via `cells_table` to under 4000 characters, and the rendered string contains no
  base64 payload — asserted by checking no line exceeds 200 chars and the `.ipynb`'s longest
  base64 blob does not appear as a substring.
- `truncate_output` boundary: a 10k-line stream renders head+tail with an elision marker;
  `--full` on an image still yields the digest, never the payload.

**End-to-end — `test_artifacts.py`**
- **The agent loop, the acceptance criterion**: run a failing snippet through `execute`, assert
  the notebook is unchanged on disk; run a fixed snippet; `commit_cell` it; assert the `.ipynb`
  gained exactly one cell whose outputs are the successful ones, that no error output ever
  appears in the file, and that `git diff --numstat` on the `.ipynb` shows insertions only and
  zero deletions (diff-locality criterion).
- `commit_cell` error-path: halves out of sync → `ValueError`, both files unmodified.
- `commit_cell` happy: after committing, `sync_notebooks()` reports no conflict (the baseline
  refresh works).

---

## 5. Estimated scope

**MVP (capabilities 1–4 + the stale guard + issue #3):** ~4 files added, ~4 modified;
**~560 lines added, ~90 modified** in `juplit/`, plus ~450 lines of tests and ~120 lines of
README/SKILL. Split across three PRs so each is reviewable:

- **PR-1 — artifact registry + issue #3** (~180 added, ~70 modified): config, `is_artifact`,
  `--update` in `nb`, `clean` skip + `--force`, gitignore warning, `normalize`. Ships issue #3
  standalone; no kernel, no stamps.
- **PR-2 — provenance + `check`** (~150 added, ~20 modified): stamps, `cell_metadata_filter`
  enforcement, `cell_state`/`scan`, the sync/nb guard, `juplit check`. Ships the finding-1 fix,
  which is the highest-priority item, without depending on PR-3.
- **PR-3 — the agent loop** (~230 added): `kernel.py`, `inspect.py`, `commit_cell`, and the five
  agent commands. This is the only PR that adds `jupyter_client`.

**Stage 2 (not in this estimate, designed separately):** `rerun --stale/--all`, scrub's size caps
and image handling, size budgets in `check`.

**Deletable without failing a test:** `--json` on `cells` (a convenience for a future MCP wrapper
that the non-goals say we are not building — cut it if you agree); `--from-last` (the loop works
by re-sending the snippet, just less ergonomically); `unverified` as a distinct state (could be
folded into `stale`, at the cost of shouting at every human-run notebook — see D3).

The largest single risk to this estimate is `commit_cell`'s post-condition and rollback path,
which is where a bug would corrupt both halves of a pair at once. It is deliberately the smallest,
most heavily tested function in the design.

---

## Decisions the reviewer should rule on

- **D1 — `unverified` handling.** A human who runs the notebook in Jupyter produces outputs juplit
  never stamped. Proposed: report as `unverified`, exit 0 by default, exit 1 under
  `--strict`, and offer `juplit stamp <nb>` as the explicit "yes, these are current" blessing.
  The alternative (treat unstamped-with-outputs as stale) is stricter and simpler but shouts at
  every notebook not produced by an agent. **Which?**
- **D2 — kernel session scope.** The request says "a persistent kernel per repo". Proposed:
  sessions are *named*, `--name` defaults to `default`, so one repo-wide kernel is the default
  behaviour but two experiments can be isolated when they need to be. Same amount of code. Agree?
- **D3 — kernel cwd.** Jupyter runs a notebook with cwd = the notebook's directory; the proposal
  starts the kernel at the **repo root**, because an agent's `juplit run` is not attached to a
  notebook yet. This means a relative path that works in Jupyter may not work under `juplit run`.
  Alternative: `--cwd`, defaulting to the notebook's dir when `commit-cell`'s target is known.
  **Repo root, or notebook dir?**
- **D4 — `jupyter_client` as a hard dependency.** It is only needed for capability 2–4. It could
  be an extra (`juplit[kernel]`) so `pip install juplit` stays as light as it is today. Proposed:
  hard dependency, because a half-installed CLI that fails at `juplit run` is worse ergonomics
  than 1 MB. **Hard dep, or extra?**
- **D5 — PR-1 alone may be enough for now.** If the immediate pain is only issue #3 (outputs being
  destroyed), PR-1 ships that in ~250 lines and PR-2/3 can wait for the Experiment task type to
  actually exist. **Ship all three, or land PR-1 first and re-evaluate?**

---

## Appendix A — verification transcripts

All run in this repo's `uv` environment: jupytext 1.19.1, jupyter_client 8.8.0, ipykernel 7.2.0,
nbformat 5.10.4, Python 3.12.

**A1 — finding 1 reproduces.** Paired notebook, executed (`x = 40 + 2` → output `42`), edited the
`.py` to `x = 40 + 3`, `jupytext --sync`:

```
0 'x = 40 + 3\nprint(x)' ec= 1 ['42']          <- new source, old output, execution_count untouched
1 'y = "unchanged cell"' ec= 2 ['unchanged cell']
```

**A2 — the same hazard exists on the `nb` path.** `jupytext --to notebook --update` preserves
outputs (confirming issue #3's mechanism) and therefore reproduces the same lie; plain
`--to notebook` wipes them (confirming issue #3's bug):

```
executed:                        [('print("first cell out")', ['first cell out'], 1), ('msg = "second"', ['second'], 2)]
after --to notebook --update:    [('print("first cell out")', ['first cell out'], 1), ('msg = "second edited"', ['second'], 2)]
after plain --to notebook:       [('print("first cell out")', [], None), ('msg = "second edited"', [], None)]
```

One guard therefore covers `sync`, `nb --update`, and CI.

**A3 — the stamp needs `cell_metadata_filter`.** Without it, the `ipynb → py` direction writes the
stamp into the source of truth:

```
markers: ['# %% juplit={"src_sha256": "3fc984d090689e33"}', '# %% juplit={"src_sha256": "beccf3a18840d19e"}']
```

With `cell_metadata_filter: -juplit` in the notebook metadata, both directions behave:

```
1) ipynb->py: py has 'juplit'? False     markers: ['# %%', '# %%']
   stamps in ipynb: [{'src_sha256': '3fc984d090689e33'}, {'src_sha256': 'beccf3a18840d19e'}]
2) py->ipynb: stamps survived? [{'src_sha256': '3fc984d090689e33'}, {'src_sha256': 'beccf3a18840d19e'}]
   sources/outputs: [('a = 7', ['1']), ('b = 2', ['2'])]      <- stale, and now detectable
```

**A4 — kernel persistence.** With `KernelManager.start_kernel()` (the request's sketch), the first
re-attach succeeds and the second fails with
`[IPKernelApp] WARNING | Parent appears to have exited, shutting down.` With the kernelspec argv
launched directly (`start_new_session=True`, `JPY_PARENT_PID=1`), across four separate CLI
processes:

```
OUT: [('stream', 'stdout', 'set zz\n')]
OUT: [('stream', 'stdout', 'after 15s + several process exits, zz = 123\n')]
OUT: [('error', 'ZeroDivisionError', 'division by zero')]
OUT: [('execute_result', {'text/plain': '<IPython.core.display.HTML object>', 'text/html': '<b>hi</b>'})]
  PID  PPID STAT
10450     1 Ssl
```

Verified equivalently over `transport: ipc` (unix sockets under `.juplit/`), which is what the
design uses on POSIX.

**A5 — diff locality.** `nbformat.read` → `nbformat.write` is byte-identical, and appending one
cell to a committed notebook produces `18 0 e.ipynb` (18 insertions, 0 deletions) in
`git diff --numstat`. `jupytext.reads` → `jupytext.writes` is byte-identical on
`docs/using_juplit.py` (5016 bytes in, 5016 out), so the `.py` insertion is local too.

**A6 — image dimensions without a decode.** A 640×480 PNG output is 26 712 base64 characters. Its
dimensions come out of the first 64 of them (48 decoded bytes, `struct.unpack(">II", head[16:24])`),
and its byte size from `len(b64) * 3 // 4`. No Pillow, no decode, no payload in context.
