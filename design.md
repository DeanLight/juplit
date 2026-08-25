# Design — artifact notebooks: committed outputs and the agent execution loop

- **Task:** [Spec: \[juplit\] Artifact notebooks — committed outputs and the agent execution loop](https://app.notion.com/p/3c30dbff56478187a081ef2a1c00eba2) (TASK-96, P1)
- **Spec:** [\[juplit\] Artifact notebooks — committed outputs and the agent execution loop](https://app.notion.com/p/3c30dbff56478130b1cac805161c95cb) — approved 2026-08-21, all 8 questions answered
- **Branch:** `claude/artifact-notebooks-execution-0xt0im` — one branch, one PR, per the spec's answer to Q8
- **Closes:** [issue #3](https://github.com/DeanLight/juplit/issues/3)
- **Companion PR:** [DeanLight/juplit_template](https://github.com/DeanLight/juplit_template) — same branch name, design in that repo's `design.md`

Every mechanism this design depends on was re-verified in this repo's `uv` environment during
this session (jupytext 1.19.1, jupyter_client 8.8.0, ipykernel 7.2.0, nbformat 5.10.4,
CPython 3.12.3). Transcripts are in **Appendix A**. One finding is new relative to the earlier
(parked, pre-spec) design and is load-bearing: **the kernelspec `argv[0]` must be resolved to
`sys.executable`** or the detached kernel dies instantly inside a `uv` venv (A4).

---

## Changes from the spec's `## Tentative interfaces`

Per Code Design, the sketch is intent, not contract. Adopted as written except for these seven.
Each one gives the old shape, the new shape, and the one-line reason.

**1. `juplit view` — the cells you name, not the whole file.** *(revised after review — the first draft folded this into a `--source` flag; it is a command again.)*

- *Spec sketch:* `juplit cells`, plus a TODO — "we need to have a view command or a view source etc".
- *This design:* two commands with distinct jobs, and no `--source` flag on either.
  - `juplit cells <nb>` — the **index**: one line per cell, output digests, provenance state. No source. This is what an agent reads first, and what keeps a 40-cell, 4 MB notebook to a few hundred tokens.
  - `juplit view <nb> [3-7] [--full]` — the **read**: the source of the cells you name, each with its rendered outputs. No range means the whole notebook, still bounded (outputs truncated, images as digests).
- *Why:* the ability to pull just the cells you are working on is worth a command of its own — an agent that has to `cat` a 600-line `.py` to see cell 7 has paid for the notebook it was avoiding. Splitting index from read is also *fewer* flags than the alternative: `view` subsumes the earlier `juplit cell <nb> <i> --full`, so the surface is two commands with one flag rather than two commands with three. `view` reads the `.py` for source and the paired `.ipynb` for outputs, so it also works on ordinary pairs that have no notebook on disk (source only) — useful outside the artifact case.

**2. `try` never writes, `run` always writes.** *(revised after review — the first draft called these `run` and `rerun`, which differ by three letters and by whether they spend money.)*

- *Spec sketch:* `juplit run` for scratch execution, `juplit rerun <nb> --stale|--all` for repair, plus a TODO — "add ability to run only some cells".
- *This design:* the same two jobs, renamed so the dangerous one is not a prefix of the safe one:
  - `juplit try CODE | --file F | --from <nb> --cells 3-7` — executes on the kernel and **prints** the outputs. Nothing on disk changes, the `.ipynb` is never opened for writing.
  - `juplit run <nb> --stale | --all | --cells 3-7` — executes those cells and **saves** the outputs into the paired `.ipynb`, re-stamping them as current. The only one of the two that produces a git diff.
- *Why:* one rule covers the pair — **`try` never writes, `run` always writes** — and `run` gets the meaning it already has everywhere else (papermill, `nbconvert --execute`): running a notebook puts the outputs in it. It also matches the spec's own phase names, `# 1. VIEW / # 2. TRY / # 3. ADD ONCE HAPPY`, so the three loop commands are `view`, `try`, `add-cell`. **`run`'s selector is mandatory**: a bare `juplit run x.py` errors and prints the three options, because the plausible default (`--all`) is the ~20-minute real-money path.

**3. `juplit html` added.**

- *Spec sketch:* listed under out of scope — "exporting to standalone HTML" — then reversed by your TODO.
- *This design:* `juplit html <nb> [--out DIR]`, a ~10-line `subprocess` wrapper around `jupyter nbconvert --to html`, plus a `poe html` target in the template.
- *Why:* per the TODO, the point is **discoverability** — agents read `[tool.poe.tasks]` to learn what a repo can do, and nbconvert never appears there today. Wrapping it costs no dependency (subprocess, exactly like the existing `jupytext` call) and buys a clean error when nbconvert is absent.

**4. Three config keys, not one.**

- *Spec sketch:* "One new key; nothing else changes" — `artifact_notebooks`.
- *This design:* `artifact_notebooks` (required) plus optional `artifact_max_output_bytes` (default 1 MB) and `artifact_max_notebook_bytes` (default 10 MB).
- *Why:* the same spec asks for "size budgets enforced by the same check". A budget nobody can tune is a budget that gets disabled; two optional keys with working defaults keep the zero-config path at one key.

**5. No timestamp in the provenance stamp.**

- *Spec sketch:* record "which version of its cell's source produced it".
- *This design:* `cell.metadata.juplit.src_sha256` only — no `executed` field.
- *Why:* the spec also requires that "re-running and getting the same results produces no diff". A wall-clock stamp guarantees a diff on every re-run, defeating the requirement it sits next to. The source hash answers the only question `check` asks.

**6. `view.py`, not `inspect.py`.**

- *Spec sketch:* n/a — this was the module name in the earlier parked design.
- *This design:* `juplit/view.py`.
- *Why:* `juplit/inspect.py` shadows a stdlib module name inside the package. Renaming costs nothing and matches the spec's own "view" vocabulary.

**7. `add-cell` and `normalize`, not `commit-cell` and `scrub`.** *(revised after review, same naming pass as 2.)*

- *Spec sketch:* `juplit commit-cell <nb> --from-last` and `juplit scrub <nb>`.
- *This design:* `juplit add-cell <nb> --from-last` and `juplit normalize <nb>`.
- *Why:* two collisions, both in a tool that lives next to git. `commit-cell` reads as though it stages or commits something — it touches git not at all — and `add-cell` completes the try → add story. `scrub` was the worse of the two: juplit already has `clean`, which **deletes notebooks**, so a near-synonym that merely rewrites metadata is a name one bad guess away from data loss. `normalize` says what it does and cannot be confused with `clean`.

Everything else — `artifact_notebooks` glob config, `juplit cells / kernel / check / stamp`,
`--from-last`, `clean --force`, the one-line additions to `sync|nb|clean` — is implemented with
the spec's names and flags.

Every per-notebook command is addressed by the **`.py`** path (the source of truth) and finds the
`.ipynb` itself; passing the `.ipynb` works too, so an agent never has to think about which half
to name.

Answers carried straight from the spec's Q&A: unverified outputs **warn** by default and fail
under `--strict` (Q1); `jupyter_client` is a **hard** dependency (Q2); kernels are **named**,
defaulting to `default` (Q3); kernel cwd is the **repo root**, overridable with `--cwd` (Q4);
idle kernels are **reaped by `clean`** (Q5); **globs only** (Q6); oversized images are
**reported, never rewritten or spilled** (Q7).

---

## 0. Justify existence

**The feature as a whole.** Two of the three problems are data-loss bugs in juplit's own commands
(`nb` wipes committed outputs, `clean` unlinks the file, `sync` re-pairs new source with old
output — all three reproduced, A1/A2). juplit owns the `.py ↔ .ipynb` contract, so nothing else
can fix them. The third — the agent loop — is new surface and has to earn its place hardest: the
alternative actually evaluated, `datalayer/jupyter-mcp-server`, needs a running Jupyter server
plus real-time collaboration (ruled out by the headless constraint: CI, a SLURM login node, an
ephemeral sandbox), adds 30+ tools to the agent's surface, and does not address staleness at all.

Component by component. Each answers the three gate questions: **does it need to exist**,
**is it already solved**, and **what is the smallest form**.

- **Artifact registry (`artifact_notebooks`)**
  - *Needs to exist:* yes — it is the opt-in scope; without it this changes default behaviour for everyone.
  - *Already solved:* no.
  - *Smallest form:* a config list plus stdlib `fnmatch` on the repo-relative path. ~20 lines.
- **`--update` on the `nb` path**
  - *Needs to exist:* yes — this is issue #3's data loss.
  - *Already solved:* **yes, by jupytext** — `--to notebook --update` preserves outputs (A2).
  - *Smallest form:* one extra flag in the existing `_run_jupytext` arg list.
- **`clean` skip + `--force`**
  - *Needs to exist:* yes — otherwise `clean` deletes the deliverable.
  - *Already solved:* no.
  - *Smallest form:* one filter and one summary line.
- **Per-cell stamp (`src_sha256`)**
  - *Needs to exist:* yes — it is the only carrier of provenance that survives a fresh clone. `.sync_hashes.json` is gitignored **and** whole-file, so it can neither run in CI nor drive per-cell repair.
  - *Already solved:* no.
  - *Smallest form:* `hashlib.sha256` into `cell.metadata`. ~15 lines.
- **`cell_metadata_filter: -juplit`**
  - *Needs to exist:* yes — without it the stamp is written into the `.py` cell markers and corrupts the source of truth (A3).
  - *Already solved:* yes, by jupytext — we only have to *set* it.
  - *Smallest form:* one key in the notebook metadata.
- **Staleness check**
  - *Needs to exist:* yes — the highest-priority item in the spec.
  - *Already solved:* no.
  - *Smallest form:* one comparison per cell.
- **`normalize` (the running-state scrub)**
  - *Needs to exist:* yes — the spec makes it always-on: "what gets cleaned is the bookkeeping of the run".
  - *Already solved:* partly. `nbconvert`'s `ClearMetadataPreprocessor` does the metadata half, at the cost of a heavyweight dependency to replace ~15 lines of dict deletion.
  - *Smallest form:* ~20 lines of `del` plus one `\r` collapse.
- **Detached kernel session**
  - *Needs to exist:* yes — capability 2; the spec's whole loop rests on it.
  - *Already solved:* partly. `jupyter_client` gives the kernelspec lookup and the client; it does **not** give a kernel that survives the CLI process (A4).
  - *Smallest form:* `subprocess.Popen` of the kernelspec argv plus a JSON session file. ~70 lines.
- **IOPub → nbformat outputs**
  - *Needs to exist:* yes — capabilities 3 and 4.
  - *Already solved:* `nbclient` does it, but pulling a second execution engine — with its own kernel lifecycle, and no cross-invocation persistence — to save ~30 lines of message mapping is a bad trade.
  - *Smallest form:* `nbformat.v4.new_output` per message type. ~30 lines.
- **Digest index (`cells`)**
  - *Needs to exist:* yes — capability 1, and it is the entire token argument.
  - *Already solved:* no.
  - *Smallest form:* a pure `nbformat` walk plus string formatting. ~90 lines.
- **Bounded read (`view`)**
  - *Needs to exist:* yes — an agent that cannot pull the two cells it is editing has to read the whole `.py`, which is the cost the index just avoided.
  - *Already solved:* no. `sed -n '120,180p'` on the `.py` gets close, but it cannot map a cell index to a line range and it never shows the outputs.
  - *Smallest form:* reuses the digest and truncation helpers; ~60 lines on top of them.
- **`add-cell`**
  - *Needs to exist:* yes — capability 4.
  - *Already solved:* no.
  - *Smallest form:* `jupytext.reads/writes` (byte-stable, A5) plus an `nbformat` insert. ~60 lines.
- **`run` (notebook execution)**
  - *Needs to exist:* yes — the spec's repair path. Without it a stale cell can only be fixed by a full re-run, which is the ~20-minute real-money path.
  - *Already solved:* no.
  - *Smallest form:* reuses `kernel.execute` and the stamp writer. ~50 lines.
- **`stamp`**
  - *Needs to exist:* yes — the *only* way out of `unverified` for a human-run notebook, which Q1's answer creates.
  - *Already solved:* no.
  - *Smallest form:* `scan` plus a stamp write. ~20 lines.
- **`html`**
  - *Needs to exist:* yes — per the spec TODO, for discoverability.
  - *Already solved:* **yes, by nbconvert** — we only shell out to it.
  - *Smallest form:* ~10 lines of `subprocess`, zero new dependencies.
- **Size budgets in `check`**
  - *Needs to exist:* yes — the spec asks for "size budgets enforced by the same check".
  - *Already solved:* no.
  - *Smallest form:* two comparisons against `len()`. ~15 lines.

**Cut outright** — four things that did not survive the gate:

- **Image downscaling or spilling to files** — spec Q7 and the out-of-scope list both say report-only. No Pillow, no `--spill`.
- **Notebook scaffolding** — spec out of scope: "a template is a file you copy".
- **`juplit status`** — folded into `juplit kernel status` and `juplit check`.
- **Regex path patterns** — spec Q6: globs only.

---

## 1. Pseudocode

### 1a. Registry, provenance and hygiene — `juplit/artifacts.py` (new)

```python
STAMP_KEY = "juplit"                 # cell.metadata key holding the provenance stamp
METADATA_FILTER = "-juplit"          # jupytext cell_metadata_filter; keeps the stamp out of the .py
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
DEFAULT_MAX_NOTEBOOK_BYTES = 10_000_000


def artifact_globs() -> list[str]:
    """The `artifact_notebooks` globs from [tool.juplit], as repo-relative posix patterns."""
    # read pyproject with the existing tasks._find_pyproject_toml()
    # return cfg.get("artifact_notebooks", []); [] means the feature is entirely off
    raise NotImplementedError


def is_artifact(py_file: Path) -> bool:
    """True if this paired .py is declared an artifact notebook (its .ipynb is committed)."""
    # key = repo-relative posix path, same normalisation as tasks._key()
    # return any(fnmatch(key, g) for g in artifact_globs())
    raise NotImplementedError


def artifact_py_files() -> list[Path]:
    """Every declared artifact .py, including ones outside notebook_src_dirs.

    The globs are additive to the scanned set, so `experiments/**/*.py` need not also be
    listed in notebook_src_dirs. Discovery of non-artifact pairs is unchanged.
    """
    # for each glob: repo_root.glob(pattern), keep files that tasks._is_paired_notebook()
    # union with tasks._find_percent_notebook_py_files(), dedup, sort
    raise NotImplementedError


def source_sha(source: str) -> str:
    """The first 16 hex chars of sha256(source) — a cell's provenance stamp."""
    raise NotImplementedError


def ensure_filter(nb: NotebookNode) -> bool:
    """Set nb.metadata.jupytext.cell_metadata_filter = "-juplit". True if it changed.

    Load-bearing, not cosmetic: without it jupytext writes the stamp into the .py cell
    markers on the ipynb -> py direction (A3), which would corrupt the source of truth.
    Called on every artifact write; `check` fails when it is missing.
    """
    raise NotImplementedError


def stamp_cell(cell: NotebookNode) -> None:
    """Record src_sha256 for a cell whose outputs we just produced.

    No timestamp: a wall-clock field would churn the diff on every re-run, and the spec
    requires that a re-run with identical results produces no diff. Cells with no outputs
    carry no stamp (and any stale stamp on them is removed).
    """
    raise NotImplementedError


def cell_state(cell: NotebookNode) -> Literal["clean", "stale", "unverified", "empty"]:
    """Provenance verdict for one cell of an artifact notebook.

    empty       — no outputs; nothing to vouch for.
    clean       — stamped, and source_sha(cell.source) == the stamp.
    stale       — stamped, and the stamp disagrees: the outputs describe older source.
    unverified  — has outputs but no stamp: executed outside juplit (a human in Jupyter).
    """
    raise NotImplementedError


def scan(ipynb: Path) -> dict[str, list[int]]:
    """Group an artifact notebook's cell indices by cell_state. Pure read, never writes."""
    raise NotImplementedError


def normalize(nb: NotebookNode) -> bool:
    """Always-on running-state scrub applied on every artifact write. True if it changed.

    Keeps: outputs, cell ids, cell sources, notebook kernelspec.
    Drops:  cell.execution_count (-> None), cell.metadata.execution (per-run wall clock),
            nb.metadata.widgets, nb.metadata.language_info.version (patch-level drift
            between machines), and STAMP_KEY on cells with no outputs.
    Rewrites: stream outputs containing "\\r" are collapsed to the last segment of each
            line (a tqdm bar re-drawn 400 times is one line on screen and 400 in the file).
    """
    raise NotImplementedError


def write_artifact(ipynb: Path, nb: NotebookNode) -> None:
    """The single write path for an artifact .ipynb: ensure_filter + normalize + nbformat.write."""
    # every mutator in this module goes through here, so no caller can forget the filter
    raise NotImplementedError
```

### 1b. Repair and blessing — `juplit/artifacts.py` (continued)

```python
def add_cell(py_file: Path, source: str, outputs: list[NotebookNode], *,
             index: int | None = None, cell_type: str = "code") -> int:
    """Insert one cell into the .py AND its captured outputs into the paired .ipynb.

    Returns the inserted index. index=None appends. Both halves are built from the same
    in-memory cell list, so they cannot disagree about ordering. Nothing is re-executed:
    the outputs written are the ones the caller already looked at.
    """
    # nb_py    = jupytext.reads(py_file.read_text(), fmt="py:percent")     # in memory
    # nb_ipynb = nbformat.read(paired_ipynb(py_file))
    # refuse unless the two agree cell-for-cell on source (see errors below)
    # build the cell; attach outputs to the ipynb copy; stamp_cell() it
    # insert into both at `index`
    # py_file.write_text(jupytext.writes(nb_py, fmt="py:percent"))         # byte-stable (A5)
    # write_artifact(paired_ipynb(py_file), nb_ipynb)
    # post-condition: re-read both; assert equal cell count and equal per-cell source
    # tasks._save_hashes([py_file])  -> refresh the pair's baseline so the next `sync`
    #                                   does not report a false "both sides changed" conflict
    raise NotImplementedError


def run_cells(py_file: Path, *, cells: list[int] | None = None, stale_only: bool = False,
              all_cells: bool = False, name: str = "default") -> dict[str, list[int]]:
    """Execute selected cells on a kernel and SAVE their outputs + fresh stamps into the .ipynb.

    Exactly one selector, and one is required — there is no default, because the
    plausible default (`all_cells`) is the expensive one: `cells` (explicit indices),
    `stale_only` (whatever scan() calls stale), or `all_cells` (the clean build: stop the
    session kernel, start a fresh one, run every code cell top to bottom). Returns
    {"executed": [...], "failed": [...]}.

    The writing counterpart of `kernel.execute`, which only ever returns outputs. Imports
    juplit.kernel lazily so `sync` / `check` never pay the jupyter_client import.
    """
    # resolve the selector -> ordered list of code-cell indices
    # if all_cells: kernel.stop(name); kernel.start(name)
    # for each index: outputs = kernel.execute(cell.source, name=name)
    #     write outputs into the ipynb cell; stamp_cell()
    #     if any output is an error: record in "failed" and keep going only under --keep-going
    # write_artifact(...) once at the end; refresh the sync baseline
    raise NotImplementedError


def stamp(py_file: Path, *, cells: list[int] | None = None, force: bool = False) -> list[int]:
    """Bless outputs juplit did not produce: write stamps for `unverified` cells.

    This is the documented way out of the `unverified` warning for a human who ran the
    notebook in Jupyter. Refuses to stamp a `stale` cell unless force=True — stamping
    stale outputs asserts something known to be false.
    """
    raise NotImplementedError


def normalize_notebook(py_file: Path) -> dict[str, object]:
    """Apply normalize() to a committed artifact on disk and report what it cost.

    The file-level counterpart of normalize(), which works on an in-memory node.

    Returns {"changed": bool, "bytes": int, "oversized": [(cell_index, digest), ...]}.
    Oversized outputs are reported only — never downscaled, never spilled to files
    (spec Q7 and the out-of-scope list: a spilled image renders as nothing on GitHub).
    """
    raise NotImplementedError


def check_artifacts(strict: bool = False) -> None:
    """The pre-commit / CI guard. Reads committed files only — no kernel, no jupytext, no sidecar.

    Fails (exit 1) on: any `stale` cell; a missing cell_metadata_filter; a declared
    artifact with no .ipynb; a notebook or single output over its size budget; and a
    declared artifact whose .ipynb is gitignored (R2 — preserved locally and silently
    never committed is indistinguishable from the feature not working, issue #3).
    Warns on: `unverified` cells; --strict promotes that warning to a failure.
    """
    raise NotImplementedError


def artifacts_errors() -> None:
    """Error cases for this module."""
    # add_cell: .py not declared an artifact    -> ValueError("<p> is not in artifact_notebooks")
    # add_cell: paired .ipynb missing           -> ValueError("... run `juplit nb` first")
    # add_cell: halves disagree on source       -> ValueError("<p> and its .ipynb are out of sync;
    #                                              run `juplit sync` first")  [refuse, never pick a winner]
    # add_cell: index out of range              -> ValueError naming the valid range
    # add_cell: post-condition fails            -> restore both files from the pre-write bytes,
    #                                              raise RuntimeError
    # run_cells: no selector given              -> ValueError("pass one of --cells / --stale / --all;
    #                                              --all restarts the kernel and re-runs everything")
    # run_cells: more than one selector         -> ValueError("pass exactly one of --cells/--stale/--all")
    # run_cells: index is a markdown cell       -> ValueError("cell <i> is markdown; nothing to run")
    # stamp: cell is stale and not force        -> ValueError("cell <i> is STALE; `juplit run <nb> --cells <i>`
    #                                              it, or pass --force to assert the outputs are current")
    # normalize/check: unreadable as nbformat   -> ValueError("<p> is not a valid notebook")
```

### 1c. Detached kernel — `juplit/kernel.py` (new)

```python
def session_path(name: str = "default") -> Path:
    """<repo>/.juplit/kernels/<name>.json — pid, connection file, cwd, kernel name."""
    raise NotImplementedError


def start(name: str = "default", *, kernel: str = "python3", cwd: Path | None = None) -> dict:
    """Launch a kernel that outlives this process, and record it. Idempotent if one is alive.

    Deliberately does NOT use KernelManager.start_kernel: jupyter_client forces
    JPY_PARENT_PID to the caller's pid and ipykernel's ParentPollerUnix then kills the
    kernel when the CLI exits — verified, the kernel does not even survive to the first
    re-attach (A4b). We launch the kernelspec argv ourselves with JPY_PARENT_PID=1.
    """
    # if session_path(name).exists() and alive(): return the recorded session unchanged
    # spec = KernelSpecManager().get_kernel_spec(kernel)
    # argv = [a.replace("{connection_file}", str(conn_file)) for a in spec.argv]
    # if argv[0] in {"python", "python3", "python3.12"}: argv[0] = sys.executable
    #     ^ REQUIRED. In a uv venv the kernelspec's bare "python" resolves to the system
    #       interpreter, which has no ipykernel, and the kernel dies at launch (A4a).
    # connection info: transport=ipc on posix (unix sockets under .juplit/kernels/, not a
    #   world-visible TCP port on a shared login node), tcp on win32 or when the socket
    #   path would exceed the ~104-char sun_path limit; random hmac-sha256 key
    # Popen(argv, env={**os.environ, "JPY_PARENT_PID": "1"}, start_new_session=True,
    #       cwd=cwd or repo_root, stdin=DEVNULL, stdout=stderr=<.juplit/kernels/<name>.log>)
    # write session_path(name); wait_for_ready; return the session dict
    raise NotImplementedError


def alive(name: str = "default") -> bool:
    """True if the recorded pid exists and the kernel answers kernel_info within ~5s."""
    raise NotImplementedError


def status() -> list[dict]:
    """One row per recorded session: name, pid, cwd, kernel, alive. Backs `juplit kernel status`."""
    raise NotImplementedError


def stop(name: str = "default") -> bool:
    """Shut a kernel down: control-channel shutdown_request, SIGTERM as fallback.

    We did not spawn it in this process, so there is no KernelManager to ask — the pid in
    the session file plus the control channel are all we have. Removes the session file,
    its log and its ipc sockets.
    """
    raise NotImplementedError


def stop_all() -> list[str]:
    """Stop every recorded session. Called by `juplit clean` — the spec's answer to
    "how long should an idle kernel live": reaped by clean."""
    raise NotImplementedError


def execute(code: str, *, name: str = "default", timeout: float = 300) -> list[NotebookNode]:
    """Run code on the session kernel and return its outputs as nbformat output nodes.

    Touches no file. This is what makes "try it, look, iterate" free of the notebook: a
    failed attempt exists only in the kernel's history and in this return value.
    """
    # kc = BlockingKernelClient(connection_file=...); load_connection_file(); start_channels()
    # msg_id = kc.execute(code)
    # drain get_iopub_msg(), skipping messages whose parent_header.msg_id != msg_id:
    #   stream                        -> new_output("stream", name=, text=)
    #   execute_result | display_data -> new_output(type, data=, metadata=)
    #   error                         -> new_output("error", ename=, evalue=, traceback=)
    #   status/idle for our msg_id    -> stop
    # on timeout: os.killpg(pid, SIGINT) to interrupt, then raise RuntimeError carrying
    #   the partial outputs so the agent still sees what happened before the hang
    raise NotImplementedError


def kernel_errors() -> None:
    """Error cases for the kernel module."""
    # no session file / recorded pid gone   -> RuntimeError("no live kernel '<n>'; run `juplit kernel start`")
    # pid alive but kernel_info times out   -> RuntimeError("kernel '<n>' is wedged; `juplit kernel stop`")
    # kernelspec not installed              -> RuntimeError("kernel '<n>' not found; pip install ipykernel")
    # execute() exceeds timeout             -> interrupt, RuntimeError with partial outputs
    # ipc socket path over sun_path limit   -> fall back to tcp on 127.0.0.1, warn once
```

### 1d. Cheap reads — `juplit/view.py` (new)

```python
def output_digest(output: NotebookNode) -> str:
    """One-line, base64-free summary of a single output.

    stream         -> "stream(stdout) 1.2KB 14L 'Fitting fold 3…' … 'done'"
    image/png      -> "image/png 640x480 82KB"   (dimensions from the IHDR bytes inside the
                      first 64 base64 chars — 48 bytes decoded, no image library; A6)
    text/html      -> "text/html 41KB <table>"
    execute_result -> "text/plain 3L '[1200 rows x 8 columns]'"
    error          -> "error ZeroDivisionError: division by zero (+12 frames)"
    """
    raise NotImplementedError


def cells_table(ipynb: Path) -> str:
    """The index (capability 1). One line per cell, no source, never any base64.

        [00] md          6L
        [06] code  12L   out: stream 1.2KB, image/png 640x480 82KB      clean
        [07] code   4L   out: text/plain 3L "[1200 rows x 8 columns]"   STALE

    A 40-cell, 4 MB notebook renders in a few hundred tokens. The index tells the agent
    which cells it cares about; `view_cells` then pulls those.
    """
    raise NotImplementedError


def view_cells(py_file: Path, cells: list[int] | None = None, *, full: bool = False) -> str:
    """The read: source of the named cells, each followed by its rendered outputs.

    cells=None shows the whole notebook — still bounded, because outputs go through
    truncate_output() and binary mime types are always digests.

    Source comes from the `.py` (the source of truth) and outputs from the paired
    `.ipynb`, so this works on an ordinary pair with no notebook on disk too: it just
    prints source. Cell indices are the same ones `cells_table` prints and `run
    --cells` accepts.
    """
    raise NotImplementedError


def truncate_output(output: NotebookNode, *, max_lines: int = 40, max_chars: int = 4000) -> str:
    """Render one output for an agent's context: head and tail lines with an elision marker.

    Binary mime types are ALWAYS rendered as their digest, even under full=True:
    `--full` means "the whole text", never "the base64".
    """
    raise NotImplementedError


def parse_cell_range(spec: str) -> list[int]:
    """"3", "3-7", "1,4,9-11" -> a sorted list of indices. Shared by --cells everywhere."""
    raise NotImplementedError
```

### 1e. Command wiring — `juplit/tasks.py` (modified)

```python
def sync_notebooks() -> None:                       # MODIFIED
    """Unchanged for ordinary pairs — byte for byte. Artifact pairs additionally get the
    hygiene pass and the staleness report.

    jupytext --sync already preserves outputs positionally (A1) — that is the hazard, not
    the fix — so the artifact work is a post-pass: write_artifact() each artifact pair,
    then scan() it.
    """
    # ...existing guarded/normal split and summary lines, unchanged...
    # for each artifact pair: write_artifact(); states = scan()
    # if stale: print "artifact STALE: <nb> cells 4,7 — outputs predate the current .py
    #                  (`juplit run <nb> --stale`, or revert the source edit)"
    # if unverified: print the warning line
    # if gitignored: print "artifact <nb> is gitignored — its outputs will never be committed"
    # exit 1 on stale, alongside today's exit 1 on overwrite_risk


def generate_notebooks() -> None:                   # MODIFIED
    """Adds --update for artifact pairs whose .ipynb exists, so outputs survive `juplit nb`.

    Three groups now: ordinary pairs (--to notebook, as today), artifact pairs with an
    existing .ipynb (--to notebook --update, which preserves outputs — A2), artifact pairs
    with none (--to notebook). Then the same post-pass as sync.
    """
    raise NotImplementedError


def clean_notebooks(force: bool = False) -> None:   # MODIFIED
    """Skips artifact notebooks unless force, and reaps kernel sessions.

    Prints "clean kept N artifact notebooks (--force to delete them too)" and
    "clean stopped N kernels". Reaping here is the spec's answer to idle-kernel lifetime.
    """
    raise NotImplementedError


def html(py_or_ipynb: Path, out_dir: Path | None = None) -> Path:   # NEW
    """Thin wrapper: `jupyter nbconvert --to html <ipynb> [--output-dir]`. Returns the html path.

    Exists for discoverability, not capability — agents read `[tool.poe.tasks]` to learn
    what a repo can do, and nbconvert is not in that table today. Same subprocess shape as
    the existing jupytext call; missing nbconvert is a clean error, not a traceback.
    """
    raise NotImplementedError
```

CLI (`juplit/cli.py`, cyclopts, same `@app.command` shape as today). Every per-notebook command
takes the `.py` path and finds the `.ipynb` itself; the `.ipynb` path is accepted too:

- `juplit cells <nb>` — the digest index: one line per cell, no source, never any base64.
- `juplit view <nb> [R] [--full]` — the named cells' source plus their rendered outputs; no range means all of them.
- `juplit kernel start|stop|status [--name] [--cwd] [--kernel]` — the persistent kernel.
- `juplit try [CODE] [--file F] [--from <nb> --cells R] [--name] [--timeout]` — execute, print the outputs, **write nothing**.
- `juplit add-cell <nb> [--from-last] [--file F] [--index i] [--markdown]` — insert the accepted cell plus its captured outputs.
- `juplit run <nb> (--stale | --all | --cells R) [--name]` — execute those cells and **save** their outputs + stamps. Selector required.
- `juplit stamp <nb> [--cells R] [--force]` — bless `unverified` outputs.
- `juplit check [--strict]` — the CI / pre-commit guard; reads committed files only.
- `juplit normalize <nb>` — strip the running state from a committed notebook, report sizes.
- `juplit html <nb> [--out DIR]` — nbconvert wrapper.
- `juplit clean [--force]` — existing; now keeps artifacts and reaps kernels.
- `juplit sync`, `juplit nb` — existing; one extra summary line when artifacts are involved.

`--from-last` is the ergonomic core of the loop: `juplit try` caches its snippet and captured
outputs in `.juplit/last-run.json`, so accepting an attempt is
`juplit add-cell experiments/ablation.py --from-last` — the agent does not re-send the code,
and what lands is provably what it looked at, not a re-execution.

---

## 2. Libraries and dependencies

**Already in the project (reused):**

- `jupytext` — `--sync` and `--to notebook --update` through the existing `_run_jupytext`;
  `jupytext.reads/writes` in memory for `add_cell` (byte-stable round trip, A5).
- `cyclopts` — every new command, same decorator style.
- stdlib — `hashlib` (stamps), `fnmatch` (globs), `subprocess` (kernel launch, `git check-ignore`,
  nbconvert), `json`, `pathlib`, `signal`/`os` (interrupt, liveness), `base64`+`struct` (PNG
  dimensions, 48 decoded bytes), `tomllib`, `uuid`, `sys`.

**Promoted from transitive to declared runtime dependencies:**

- `nbformat>=5.10` — already installed as a jupytext dependency; juplit now imports it directly,
  so it must be declared. Zero install-size change.
- `jupyter_client>=8.0` — new to the install (~1 MB with `pyzmq`, `traitlets`, `jupyter-core`).
  Approved by spec Q2. Used for exactly two things: `KernelSpecManager` (find the kernel argv)
  and `BlockingKernelClient` (talk to it). Ruled out: hand-rolling the ZMQ wire protocol (HMAC
  framing, five channels — no), and `nbclient` (a second execution engine with its own kernel
  lifecycle, which cannot do the across-invocations part at all).

**Deliberately not depended on:**

- `ipykernel` — needed at runtime *by the consuming project* to have a kernel at all, but juplit
  never imports it. juplit's own dev group already has it. A missing kernelspec is a clean error.
- `nbconvert` — `juplit html` shells out to the `jupyter` CLI exactly as juplit shells out to
  `jupytext`; if it is absent the command says `pip install nbconvert`. Its
  `ClearMetadataPreprocessor` also overlaps `normalize`, but importing it to replace ~15 lines of
  dict deletion fails the hierarchy.
- `Pillow` — would only be needed for image downscaling, which spec Q7 cut.

**New dependencies requiring approval: `jupyter_client>=8.0`** (pre-approved in spec Q2), plus
declaring the already-present `nbformat`.

---

## 3. File locations

**`juplit/artifacts.py`** — NEW, ~280 lines. Registry (`artifact_globs`, `is_artifact`,
`artifact_py_files`), provenance (`source_sha`, `ensure_filter`, `stamp_cell`, `cell_state`,
`scan`), hygiene (`normalize`, `write_artifact`, `normalize_notebook`), repair (`add_cell`,
`run_cells`, `stamp`), and the guard (`check_artifacts`). Sectioned with the existing
`# ── banner ──` convention. Imports `juplit.kernel` **lazily** inside `run_cells` so `sync`/`check` never pay the
`jupyter_client` import.

**`juplit/kernel.py`** — NEW, ~200 lines. `session_path`, `start`, `alive`, `status`, `stop`,
`stop_all`, `execute`, and the IOPub→nbformat mapping. Knows nothing about artifacts or the `.py`
format — it is a kernel module, not a notebook module.

**`juplit/view.py`** — NEW, ~170 lines. `output_digest`, `cells_table`, `view_cells`,
`truncate_output`, `parse_cell_range`. Pure rendering: nbformat nodes in, strings out.

**`juplit/tasks.py`** — MODIFIED (~90 lines changed):
- `sync_notebooks()` — artifact post-pass (write_artifact + scan), stale/unverified/gitignore
  summary lines, exit 1 on stale. Ordinary pairs untouched.
- `generate_notebooks()` — three-way split with `--update` for existing artifact `.ipynb`s; same post-pass.
- `clean_notebooks(force=False)` — exclude artifact `.ipynb`s from the unlink loop; reap kernels; two new summary lines.
- `html()` — NEW, at the end of the public-tasks section.
- `_find_percent_notebook_py_files()` — unchanged; `artifacts.artifact_py_files()` unions on top.
- `_save_hashes()` — unchanged, now also called from `add_cell`/`run_cells` to refresh a pair's baseline.

**`juplit/cli.py`** — MODIFIED (~120 lines added): the twelve commands listed above;
`clean` gains `--force`; `main()` unchanged.

**`juplit/test_artifacts.py`**, **`juplit/test_kernel.py`**, **`juplit/test_view.py`** — NEW
pytest files following `test_sync.py`'s tmp-path-project fixture style.

**`pyproject.toml`** — MODIFIED: `nbformat`, `jupyter_client` in `[project] dependencies`;
`[tool.juplit] artifact_notebooks = ["docs/artifact_notebooks.py"]` (juplit dogfoods its own
feature); poe targets `check = juplit check` and `html`.

**`.gitignore`** — MODIFIED: add `.juplit/`; add `!docs/artifact_notebooks.ipynb` to un-ignore
the one committed artifact.

**`.github/workflows/ci.yml`** — MODIFIED: one step, `uv run juplit check`, after pytest.

**`docs/artifact_notebooks.py`** (+ its committed `.ipynb`), **`docs/api.md`**, **`mkdocs.yml`** —
see the docs design below.

**`README.md`**, **`juplit/SKILL.md`** — MODIFIED: the artifact-notebook section and the agent
loop. The SKILL change is what makes the loop discoverable to Claude, so it is in scope here, not
a documentation afterthought.

---

## 4. Testing outline

pytest files in `juplit/`, using `test_sync.py`'s tmp-path project fixture. Kernel tests are
marked `@pytest.mark.kernel` and skipped when no `python3` kernelspec exists, so a CI box without
ipykernel stays green.

**Registry — `test_artifacts.py`**
- happy: a path matching `experiments/**/*.py` is an artifact; boundary: the same basename outside the glob is not.
- happy: a declared artifact outside `notebook_src_dirs` is discovered by `artifact_py_files`.
- regression: with no `artifact_notebooks` key, every discovery function returns exactly today's set.

**Provenance — `test_artifacts.py`**
- happy: stamped cell, unchanged source → `clean`.
- error-path: **the A1 repro end to end** — execute a paired notebook, stamp it, edit `x = 40 + 2` → `x = 40 + 3` in the `.py`, run `sync_notebooks()`; assert the cell is `stale`, **the outputs are still there**, and `SystemExit(1)` is raised.
- boundary: outputs without a stamp → `unverified`; no outputs → `empty`.
- happy: after a full sync round trip in **both** directions the `.py` contains no `juplit=` cell marker and the `.ipynb` still carries its stamps (the A3 mechanism — the single most important test in the suite).
- boundary: inserting a cell in the `.py` shifts outputs positionally and the shifted cells are reported `stale` rather than silently mispaired.
- happy: `normalize` nulls `execution_count`, drops `metadata.execution`, collapses a `\r` progress bar, and leaves outputs intact.

**Commands — `test_artifacts.py`**
- happy: an artifact `.ipynb` with outputs keeps them across `generate_notebooks()`; error-path: a *non*-artifact still gets today's wipe (no regression).
- happy: `clean_notebooks()` keeps the artifact and counts it; `force=True` deletes it; a live kernel session is stopped either way.
- error-path: `check_artifacts()` exits 1 on a stale cell, on a missing filter, on a missing `.ipynb`, and on a notebook over `artifact_max_notebook_bytes`; exits 0 with a warning on `unverified` and on a gitignored artifact; exits 1 on both under `strict=True`.
- happy: `stamp()` turns `unverified` into `clean`; error-path: refuses a `stale` cell without `force`.
- happy: `html()` produces an `.html` next to the notebook; error-path: a clean `RuntimeError` when `jupyter` is not on PATH.
- regression: a project with **no** artifacts produces byte-identical `sync` output and the same `.sync_hashes.json` as before this change.

**Kernel — `test_kernel.py`** (marked)
- happy: `execute("1+1")` returns one `execute_result` output.
- **persistence, the acceptance criterion:** start a kernel, set a variable in one `subprocess.run([sys.executable, "-m", "juplit.cli", "run", …])`, read it back in a *second* subprocess. Must be out-of-process — an in-process test passes even with the broken `start_new_kernel` approach.
- boundary: the kernelspec's bare `python` argv[0] is rewritten to `sys.executable` (A4a) — assert the recorded argv, so a venv regression fails loudly instead of "kernel died".
- error-path: `1/0` returns an `error` output and does **not** raise.
- boundary: a snippet exceeding `--timeout` is interrupted and raises `RuntimeError` carrying partial outputs.
- happy: `stop()` removes the session file and sockets and the pid is gone; `start()` twice reuses the same pid.
- happy: two `--name`s are independent — a variable set in one is absent in the other.

**View — `test_view.py`**
- happy per type: `output_digest` on stream, `image/png` (dimensions from a crafted IHDR), error, `text/html`.
- **token budget, the acceptance criterion:** a synthetic 40-cell notebook holding 4 MB of base64 renders through `cells_table` to under 4 000 characters, and the notebook's longest base64 blob does not appear as a substring of the rendered text.
- boundary: a 10 000-line stream renders head+tail with an elision marker; `--full` on an image still yields the digest.
- happy: `view_cells(py, [3, 4])` prints exactly those two cells' source, with their outputs, and nothing from cell 5 — boundary: on a pair with no `.ipynb` it prints source and says the notebook is not generated, rather than raising.
- happy: `parse_cell_range("1,4,9-11") == [1, 4, 9, 10, 11]`; error-path: `"7-3"` raises `ValueError`.

**End to end — `test_artifacts.py`**
- **the agent loop, the acceptance criterion:** run a failing snippet through `kernel.execute`, assert the notebook on disk is byte-identical; run the fixed snippet; `add_cell` it; assert the `.ipynb` gained exactly one cell carrying the successful outputs, that no `error` output ever reaches the file, and that `git diff --numstat` on the `.ipynb` shows insertions and **zero** deletions.
- happy: `run_cells(stale_only=True)` clears the stale verdict and leaves untouched cells byte-identical; `run_cells(all_cells=True)` restarts the kernel first (a variable set beforehand is gone) — boundary: `run_cells` with no selector raises `ValueError` naming the three, and writes nothing.
- error-path: `add_cell` on out-of-sync halves raises `ValueError` and leaves both files unmodified.
- happy: after `add_cell`, `sync_notebooks()` reports no conflict (the baseline refresh works).

---

## 5. Estimated scope

~3 new source files, ~4 modified, ~3 new test files, ~4 docs/config files.

- `juplit/artifacts.py`, `kernel.py`, `view.py` — **~650 added**.
- `juplit/tasks.py`, `cli.py` — ~120 added, **~90 modified**.
- tests (`test_artifacts.py`, `test_kernel.py`, `test_view.py`) — ~520 added.
- docs (`docs/artifact_notebooks.py`, `api.md`, `mkdocs.yml`) — ~230 added, ~15 modified.
- `README.md`, `juplit/SKILL.md` — ~150 added, ~10 modified.
- `pyproject.toml`, `.gitignore`, `ci.yml` — ~15 added, ~5 modified.

**~1 690 added, ~120 modified**, of which **~770 is production code** — against the spec's
~560-line estimate. The gap is the three things the spec's estimate predates: `juplit html`
(~15), `juplit stamp` (~25), `run --cells` (~20), and the size-budget half of `check` (~30),
plus ~120 lines of digest rendering that the sketch showed but did not cost.

**Deletable without failing a spec requirement**, in the order I would cut them:
1. `juplit normalize` as a *command* — the scrub runs on every artifact write anyway, so the command only exists to fix a notebook a human hand-edited. (~25 lines)
2. `--markdown` on `add-cell` — an agent can write markdown cells into the `.py` directly and re-sync. (~10 lines)
3. `--full` on `view` — the default truncation is generous, and `juplit try` re-prints an output in full anyway. (~15 lines)

The largest single risk is `add_cell`'s post-condition and rollback path: a bug there corrupts
both halves of a pair at once. It is deliberately the smallest and most heavily tested function
in the design.

---

## 6. Stacking plan

Six PRs, merged bottom-up. The seams are the ones the design already draws: the data-loss fix
needs no provenance, provenance needs no kernel, reading needs no execution, executing needs no
write path, and the docs demonstrate a public interface that by then exists. Each layer is a
thing you would want even if the layers above it were abandoned.

Branches are rooted at the task branch name, `claude/artifact-notebooks-execution-0xt0im-NN-<slug>`,
shortened to `…-NN-<slug>` below. **`…-01-artifacts` branches off `main`**, and each later layer
off the one below it. The design PR (#4) is not part of the stack — it carries `design.md` only,
and can merge whenever.

- **`…-01-artifacts`**
  - *Lands:* the `artifact_notebooks` registry (`artifact_globs`, `is_artifact`, `artifact_py_files`), `normalize` / `write_artifact`, `--update` for artifact pairs in `nb`, `clean` keeping artifacts with `--force` to delete them, the gitignore warning, and `nbformat` declared as a dependency.
  - *Stands alone because:* it closes issue #3 by itself — outputs stop being destroyed. Nothing here needs a stamp or a kernel, and the tests are the `nb`/`clean`/regression set.
  - *Depends on:* nothing — branches off `main`.
- **`…-02-provenance`**
  - *Lands:* `src_sha256` stamps, `cell_metadata_filter` enforcement, `cell_state` / `scan`, the staleness report and non-zero exit in `sync` and `nb`, and the commands `check --strict`, `stamp`, `normalize`, with the size budgets. Adds `uv run juplit check` to CI.
  - *Stands alone because:* the highest-priority bug — outputs silently contradicting their source — is caught and blocked with no kernel anywhere in the picture. Repair at this layer is "revert the edit, or re-run in Jupyter and `juplit stamp`"; `juplit run` arrives in 05 and makes it cheaper.
  - *Depends on:* `…-01-artifacts`.
- **`…-03-read`**
  - *Lands:* `juplit/view.py` and the three no-kernel reads: `cells` (digest index), `view` (named cells' source plus outputs), `html` (nbconvert wrapper).
  - *Stands alone because:* it is a pure reader over committed files; an agent gets the whole token argument here, before anything can execute. `html` rides along because it is the third way of looking at a notebook without running it, and a 15-line command is not worth a layer.
  - *Depends on:* `…-02-provenance` — the index shows each cell's `clean`/`STALE`/`unverified` state.
- **`…-04-kernel`**
  - *Lands:* `juplit/kernel.py` (detached launch with the `sys.executable` argv fix, `alive`, `status`, `stop`, `stop_all`, `execute`), the `kernel` and `try` commands, `.juplit/` in `.gitignore`, `clean` reaping kernels, and `jupyter_client` as a dependency.
  - *Stands alone because:* start a kernel, run a snippet across separate CLI calls, see the output rendered by 03. Writes nothing into any notebook, so it is complete without the layer above.
  - *Depends on:* `…-03-read` — `try` prints its outputs through the same digest/truncation renderer.
- **`…-05-writeback`**
  - *Lands:* the two commands that change a committed notebook — `add-cell` (insert the accepted snippet plus the outputs `try` captured) and `run` (execute selected cells, save outputs, re-stamp), with the mandatory selector and the rollback path.
  - *Stands alone because:* it completes the loop and every write goes through `write_artifact` from 01 and the stamps from 02; its tests are the diff-locality and out-of-sync-halves cases.
  - *Depends on:* `…-04-kernel`.
- **`…-06-docs`**
  - *Lands:* `docs/artifact_notebooks.py` and its committed `.ipynb` (the dogfooded artifact notebook, un-ignored), the `api.md` reference additions, the mkdocs nav entry, the `README.md` section and the two new `SKILL.md` sections.
  - *Stands alone because:* source below, docs above — the page demonstrates a public interface that is fully landed by 05, and it cannot be written before then because its committed outputs are produced by running the real commands.
  - *Depends on:* `…-05-writeback`.

Notes on the split:

- **Every layer ships its own tests.** No layer is "the tests for the one below".
- **Two candidate seams rejected.** Splitting `artifacts.py` from `tasks.py` would cut mutually dependent files rather than ideas; splitting `check` from the stamps would leave a bottom PR whose only feature is a guard with nothing to guard.
- **The template PR is not a layer** — different repo. It lands as one PR after the juplit release that ships these commands, per §8.

---

## 7. Docs design

The task ships source **and** docs, so the Code row and the Docs row both run (Code first).

### 7.1 Audience & job

A researcher or agent in a repo that has just produced an expensive result — a notebook executed
against a live endpoint, ~20 minutes and real money — who needs that result to survive in git,
to be reviewable on GitHub, and to be re-derivable cell by cell when the code moves. Secondary
audience: the agent doing the producing, which needs to know the loop exists at all (that part is
carried by `SKILL.md`, not by the docs site).

### 7.2 Page plan

- **`docs/artifact_notebooks.py`** — **Tutorial**, new.
  - *Reader need:* "take me from a normal juplit repo to a committed, checked, agent-produced experiment notebook, once, end to end."
- **`docs/api.md`** — **Reference**, extended.
  - *Reader need:* "what exactly does `juplit check` fail on, and what are the config keys and their defaults?"
  - *Carved from:* the existing page — add mkdocstrings for `juplit.artifacts` / `kernel` / `view`, plus the CLI and config surface.
- **`README.md`** — not a docs page, extended.
  - *Reader need:* the 20-line version, for someone deciding whether to opt in at all.

One page, one type. No how-to page: there is exactly one task here and the tutorial is it —
a second page would repeat it with different words.

### 7.3 The running example

**Scenario & data.** A three-arm ablation. The notebook `experiments/ablation.py` has three
cells: a hard-coded 12-row result table (`ARMS = [...]`, printed with `display`), a group-mean
summary, and a text histogram of the score distribution. No network, no model calls, no
matplotlib — the *point* being demonstrated is the plumbing, and a doc that needs an API key or a
plotting dependency to build is a doc that stops building. Data is shown before it is used, as
the style guide requires.

The tutorial builds this project in a fixed scratch directory (`.cache/artifact-demo/`, already
gitignored) and drives it through `subprocess.run(["juplit", ...])`, printing real stdout. The
CLI **is** the public interface for this feature, so there is no wrapper to object to; the one
concession is a two-line `sh()` helper that prints a command and its output — **flagged here for
approval per the style guide's escape-hatch rule**, because writing
`print(subprocess.run([...], capture_output=True, text=True).stdout)` at eleven call sites would
bury the lesson under boilerplate. If the reviewer refuses it, the fallback is `!juplit …` IPython
magics, which jupytext round-trips as commented lines.

**How it grows** — one story, each step the natural next thing:

1. A normal paired notebook with executed outputs → `juplit nb` wipes them. The bug, shown, not described.
2. Declare `artifact_notebooks = ["experiments/**/*.py"]` → `juplit nb` now keeps them, and warns that the `.ipynb` is still gitignored.
3. Un-ignore it, commit → `juplit cells` shows the notebook as an index of digests, not 4 MB of JSON.
4. Edit one cell's source → `juplit check` reports it STALE and exits 1. The outputs are still there.
5. `juplit run --stale` → back to clean, and only that cell's outputs changed.
6. The agent loop: `juplit kernel start`, a `juplit try` that fails (`ZeroDivisionError` — the notebook on disk is unchanged), the fixed attempt, `juplit add-cell --from-last`, `git diff --numstat` showing insertions only.
7. `juplit clean` keeps the artifact and reaps the kernel; `juplit html` renders it for someone with no Python.

**Why this example.** Every juplit command in the feature is the natural tool for exactly one
step, and steps 1 and 4 are the two failures the feature exists to prevent — shown failing first,
which is what makes the fix legible.

**Dogfooding.** This page is itself declared an artifact notebook in juplit's own
`pyproject.toml`, and its `.ipynb` is committed with outputs. Three reasons: mkdocs-jupyter runs
with `execute: false`, so a non-artifact page would render with no outputs at all; the docs build
(`poe docs-build` → `juplit nb`) then exercises the issue-#3 fix on every build; and the repo
demonstrates the un-ignore line it tells users to write. Authoring constraint that follows: the
page must print **no** wall-clock times, temp paths, or pids, or its committed outputs churn on
every re-run — the scratch dir is fixed, and pids are filtered out of the printed `kernel status`.

### 7.4 Public-interface walkthrough

In the order the running example reaches them:

```python
juplit nb                                            # the wipe, before opting in — the bug
# pyproject.toml: artifact_notebooks = ["experiments/**/*.py"]
juplit nb                                            # outputs survive; warns "still gitignored"
juplit cells experiments/ablation.py                 # the digest index — the token argument
juplit view experiments/ablation.py 2                # that cell's source and outputs
juplit check                                         # clean → exit 0
# edit cell 1 in the .py
juplit check                                         # "cell 1 STALE" → exit 1, outputs intact
juplit run experiments/ablation.py --stale           # repair just that cell
juplit kernel start --name ablation                  # the loop begins
juplit try 'df["score"].mean() / 0' --name ablation  # fails; notebook on disk untouched
juplit try --file attempt.py --name ablation         # the fixed attempt
juplit add-cell experiments/ablation.py --from-last  # the snippet + the outputs just seen
juplit clean                                         # keeps the artifact, reaps the kernel
juplit html experiments/ablation.py                  # standalone HTML for a non-Python reader
```

`juplit stamp` and `juplit normalize` are **reference-only** — they exist for a human who ran the
notebook in Jupyter, which is not the tutorial's story.

### 7.5 Out of scope / folds

- No page on the persistent kernel's internals (ipc transport, `JPY_PARENT_PID`) — that is design
  rationale, and it lives in this file and in code comments, not in user docs.
- No migration guide: artifact notebooks are opt-in, and a repo that adds one key changes nothing else.
- Cut order if fewer pages are wanted: `api.md`'s CLI table folds into the tutorial's last cell;
  the tutorial itself is the last thing to cut, because the feature is unusable undocumented.

### 7.6 `SKILL.md` (agent-facing, not a docs page)

Two new sections, ~120 lines: **"Artifact notebooks"** (what they are, the one config key, the
un-ignore line, the rule that `.py` stays output-free) and **"The execution loop"** — the
commands in the order an agent uses them (`cells` → `view` → `kernel start` → `try` →
`add-cell`, with `run` / `check` for repair), led by the three rules that keep an agent out of
trouble: **`try` never writes and `run` always writes**; never read a raw `.ipynb` into context,
use `juplit cells`; never add a cell whose output you have not seen, use `--from-last`.
`juplit skill` already ships the file, so nothing else changes.

---

## 8. Companion PR — `juplit_template`

Same branch name, separate PR, ~60 lines. Design lives in that repo's `design.md`; summary:
the `artifact_notebooks` key (commented, empty by default), `poe check` / `poe html` targets so
agents discover the commands in the poe table, a `juplit check` pre-commit hook and CI step,
`.juplit/` plus the un-ignore comment in `.gitignore`, `notebook_src_dir` → `notebook_src_dirs`,
a README section, and a `juplit_version` default bump to the release that ships this.

---

## 9. Decisions — all resolved

The spec answered the eight big ones; these four were raised in this design and answered in
review on 2026-08-25. They are recorded here as decisions, not open questions.

- **R1 — the docs helper: allowed.** The tutorial may define a two-line `sh()` that prints a command and its output. The Docs Style Guide requires design approval for any helper in a doc; this is that approval, and it is limited to `sh()` — no other helper appears in the page.
- **R2 — a gitignored artifact fails.** `juplit check` exits non-zero when a declared artifact's `.ipynb` is gitignored, without needing `--strict`. Stricter than the spec's "warns", and deliberately so: outputs preserved locally and never committed is indistinguishable from the feature not working, which is the exact failure issue #3 reported. `sync` and `nb` still only warn — they are not the guard.
- **R3 — `normalize` nulls `execution_count`.** The rendered `[1]`, `[2]` markers disappear from a committed notebook; in exchange, a re-run that produces identical results produces no diff, which is what the scrub exists for.
- **R4 — juplit dogfoods its own docs page.** `docs/artifact_notebooks.py` is declared in juplit's own `artifact_notebooks`, its `.ipynb` is committed with outputs and un-ignored. Authoring constraint follows: the page prints no wall-clock times, temp paths or pids, or its outputs churn on every re-run.
- **Size budgets (decided with them):** 1 MB per output, 10 MB per notebook, overridable by `artifact_max_output_bytes` / `artifact_max_notebook_bytes`; `check` fails when either is exceeded.

---

## Appendix A — verification transcripts (this session)

All run in this repo's `uv` environment: jupytext 1.19.1, jupyter_client 8.8.0, ipykernel 7.2.0,
nbformat 5.10.4, CPython 3.12.3.

**A1 — the silent-staleness bug reproduces.** Paired notebook executed (`x = 40 + 2` → `42`),
`.py` edited to `x = 40 + 3`, `jupytext --sync`:

```
V1 after sync: [('x = 40 + 3', 1, ['42\n']), ('y = "unchanged"', 2, ['unchanged\n'])]
```

New source, old output, execution count untouched. No warning, exit 0.

**A2 — `--update` preserves outputs; plain `--to notebook` wipes them.** Same notebook, `.py`
edited from `b = 2` to `b = 3`:

```
V2 --update : [('a = 7', 1, ['7\n']), ('b = 3', 2, ['2\n'])]
V2 plain    : [('a = 7', None, []),   ('b = 3', None, [])]
```

So issue #3's proposed mechanism works, and one staleness guard has to cover `sync`, `nb
--update` and CI.

**A3 — the stamp needs `cell_metadata_filter`.** Without it the `ipynb → py` direction writes the
stamp into the source of truth; with it, both directions are clean and the stamps survive:

```
V3 filter=None    : markers=['# %% juplit={"src_sha256": "f1b8e8d91de5b851"}', '# %% juplit={…}']
V3 filter='-juplit': markers=['# %%', '# %%']   stamps_after_roundtrip=[{'src_sha256': 'f1b8e8d91de5b851'}, …]
```

Round-tripped again through `--sync` in **both** directions, with an embedded PNG output:

```
V6 py->ipynb sync : stamps [{…f1b8e8d91de5b851}, {…4e7995224ef440ae}]  outputs kept [1, 1]  img still png True
                    py markers: ['# %%', '# %%']
V6 ipynb->py sync : stamps [{…}, {…}]  py has juplit marker False
```

(The `.py` YAML header does gain one `cell_metadata_filter: -juplit` line — that is the
mechanism, and it is the only trace of juplit in the source of truth.)

**A4 — kernel persistence, two findings.**

*A4a (new, and the reason the earlier design would have failed in CI):* the kernelspec's argv
starts with a bare `"python"`. Launched verbatim from inside a `uv` venv it resolves to the system
interpreter:

```
started pid 11429
/usr/local/bin/python: No module named ipykernel_launcher
```

Rewriting `argv[0]` to `sys.executable` when it is `python` / `python3` / `python3.12` — the same
substitution `jupyter_client`'s own `format_kernel_cmd` does — fixes it.

*A4b:* with `JPY_PARENT_PID` left at the launching process's pid, ipykernel 7.2.0's parent poller
kills the kernel as designed — here it did not even survive to the first re-attach:

```
[IPKernelApp] WARNING | Parent appears to have exited, shutting down.
FIRST ATTACH FAILED / SECOND ATTACH FAILED
```

With `JPY_PARENT_PID=1`, `start_new_session=True` and the resolved argv, across four separate CLI
processes with sleeps in between:

```
OUT: [('stream', 'stdout', 'set zz\n')]
OUT: [('stream', 'stdout', 'zz still 123\n')]
OUT: [('error', 'ZeroDivisionError', 'division by zero')]
OUT: [('execute_result', ['text/plain', 'text/html'])]
  PID  PPID STAT
11794     1 Ssl
```

**A5 — diff locality and byte stability.** `jupytext --to notebook --update` on an unchanged pair
rewrites the `.ipynb` byte-identically, and cell ids are preserved:

```
bytes identical after --update: True
ids before/after: ['0887fd93', '4a125d41'] / ['0887fd93', '4a125d41']
```

So "unstable cell ids" is a non-issue on the paths this design uses — ids are assigned once, when
an artifact `.ipynb` is first generated, and never re-randomised. `normalize` therefore preserves
them rather than rewriting them.

**A6 — image dimensions with no decode and no payload.** A 640×480 PNG output's dimensions come
out of the first 64 base64 characters (48 decoded bytes,
`struct.unpack(">II", head[16:24])`), and its byte size from `len(b64) * 3 // 4`:

```
V5 dims from first 64 b64 chars: (640, 480)  bytes≈ 972  actual 972
```

**A7 — `juplit html` needs no dependency.** `jupyter nbconvert --to html` on the same notebook,
invoked as a subprocess:

```
V7 nbconvert rc 0  html exists True  size 279569
```
