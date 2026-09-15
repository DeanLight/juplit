# Design — `run --all` keeps the kernel's working directory, and the artifact set reaches the shell

- **Task:** [T2-126](https://app.notion.com/p/3d90dbff564781ccaedceee3f59bdf45) (P2)
- **Spec:** [\[juplit\] `run --all` keeps the kernel's working directory — and the deletion of
  deep_reasoner's execute/stamp workaround](https://app.notion.com/p/3d90dbff5647814a8bd0dc9b11d5d607)
  — approved, Q1 = (a), Q2 = (b), Q3 = (a)
- **Branch:** `claude/relaxed-hopper-hmj58e`
- **Baseline:** `5be92b9`, the commit every `file:line` in the spec was verified against.

The spec is two independent items in one consumer. This document follows it: **A** is a defect
in one expression, **B** is a flag on two commands. Neither needs a new module; both are seams
that already exist and were not connected.

---

## A · `--all` restarts the kernel, it does not relocate it

### The defect

`run_cells(..., all_cells=True)` restarted the kernel to guarantee a clean namespace and called
`kernel.start(name)` with no `cwd`, whose body is `cwd=str(cwd or _repo_root())`. So a kernel
the user deliberately started elsewhere came back at the repo root, and every relative path in
the notebook — `configs/…`, `logs/…` — resolved somewhere else. Nothing said so, which is why
it survived: the only symptom is files written in the wrong directory.

### The seam, and why it is where it is

The working directory is **already on disk**. `start()` records it in the session JSON
(`.juplit/kernels/<name>.json`), and `stop()` unlinks that file. So the fix has exactly one
ordering constraint, and it is the whole design:

```
read_session(name)  →  stop(name)  →  start(name, cwd=<what was read>)
```

Read *before* stop, because stop destroys the record. Everything else follows.

### Signatures

```python
# juplit/kernel.py — was _read_session, now public. The only change to this module.
def read_session(name: str) -> dict | None: ...

# juplit/artifacts.py — the return type widens by one key.
def run_cells(py_file: Path, cells: list[int] | None = None, stale_only: bool = False,
              all_cells: bool = False, name: str = "default",
              timeout: float = 300.0) -> dict[str, object]:
    """{"executed": list[int], "failed": list[int], "fell_back_to": str | None}"""
```

`fell_back_to` is the Q2(b) answer expressed as data rather than as a `print`. It is set **only**
when `--all` restarted a kernel with no session to read, and names the directory the new kernel
landed in; it is `None` in every other case, including the non-restarting selectors. The library
stays silent and the CLI decides what a human sees — the division the rest of `cli.py` already
follows (`normalize_notebook` reports, `normalize` prints).

`juplit run` therefore prints one extra line, and only in the case that used to be silent:

```
$ juplit kernel stop
kernel 'default' stopped
$ juplit run docs/probe.py --all
running from /…/probe
ran cells 0
```

### The invariant this commits juplit to

> **`--all` restarts the kernel; it does not move it.**

Stated in `run_cells`'s docstring, in `juplit run --help`, in `SKILL.md`, and in
`docs/artifact_notebooks.py`. It is a one-sentence promise and it is the one a reader of the
old code would have assumed was already true.

### What shape (a) does not do

`start()` early-returns a live kernel (`if alive(name): return read_session(name)`), so a `cwd=`
passed to an already-running kernel is silently ignored. Shape (a) never exposes that: the only
caller passing `cwd=` here has just called `stop()`. Making `run` take a public `--cwd` (shape
(b)) would have inherited a parameter that is a no-op half the time — the spec's second argument
for (a), and it still holds. `kernel.py:122-123` is left as it is, as the spec defers.

---

## B · `--all` on `stamp` and `normalize`

`artifact_py_files()` already computes the configured artifact set, including artifacts outside
`notebook_src_dirs`, and nothing in the CLI could reach it. The whole feature is a loop over that
function.

```python
# juplit/cli.py
def stamp(notebook: str | None = None, cells: str | None = None,
          force: bool = False, all: bool = False) -> None: ...
def normalize(notebook: str | None = None, all: bool = False) -> None: ...
```

Both take `--all` **instead of** a notebook, not alongside one: `--all` with a path (or with
`CELLS`) is an error rather than a silent precedence rule. `notebook` becoming optional is what
makes `juplit stamp` with no arguments reachable, so it now errors with the sentence naming both
ways to call it instead of a cyclopts parse error.

The loop lives in `cli.py`, not in `artifacts.py`: it is presentation — a per-notebook line, a
total, and the existing single-notebook functions unchanged underneath.

```
$ juplit normalize --all
normalize docs/probe.py          652 bytes
normalize docs/second.py         358 bytes
2 notebook(s), 1,010 bytes total.
```

`normalize --all` still exits 0 on an over-budget output, per the spec: `check` is the gate,
`normalize` is the report, and one condition does not get two gates.

### B2 · a second path is a path, not a bad integer

`stamp a.py b.py` parsed `b.py` as `CELLS` and died inside `int()`. One helper, `_cell_range()`,
now sits between every command that takes a range and `parse_cell_range()`, and names the actual
mistake:

```
$ juplit stamp docs/probe.py docs/second.py
juplit: 'docs/second.py' is a path, not a cell range — juplit commands take one notebook
at a time; `stamp` and `normalize` take --all for the whole artifact set
```

`view` and `run` route through the same helper, because the trap is identical there. And
`parse_cell_range` itself no longer leaks `int()`'s message for any other garbage — it names the
grammar it wanted.

---

## Tests

Six, all against behaviour, none against the internals:

| Test | What breaks it |
| --- | --- |
| `test_run_all_keeps_the_kernel_where_it_was_started` | the defect itself — a cell printing `os.getcwd()` after `--all` from a `--cwd` kernel (A3) |
| `test_run_all_with_no_recorded_session_says_where_it_fell_back_to` | the Q2(b) line going missing, or the fallback becoming an error |
| `test_stamp_all_reaches_every_declared_artifact` | `--all` missing a declared artifact |
| `test_normalize_all_walks_the_artifact_set_and_totals_the_bytes` | the same, plus the total |
| `test_a_second_path_is_reported_as_a_path_not_as_a_bad_integer` | B2 regressing |
| `test_stamp_without_a_notebook_or_all_says_which_to_pass` | `notebook` becoming optional without a message |

The existing `test_run_all_restarts_the_kernel_first` is what keeps this fix honest in the other
direction: the restart — and the clean namespace it buys — must still happen.

---

## Divergences from the approved spec

1. **`fell_back_to`, not a `print` inside `run_cells`.** The spec's Q2(b) says "print
   `running from <dir>`". A library function that prints cannot be called by a consumer that
   formats its own output — `deep_reasoner_beta`'s fan-out is exactly that consumer — so the
   fact travels back in the report and the CLI prints the spec's line verbatim.
2. **`view` and `run` also route through `_cell_range()`.** The spec asks for B2 on `stamp`. The
   trap is one shared helper's worth of code, and leaving the other two commands with the old
   error would have been a deliberate inconsistency.
3. **`parse_cell_range` gained an error message.** Item B2's ugly message originates there; the
   CLI guard catches the path case, this catches the rest.

Nothing in §3 of the spec is contradicted.

## Deferred

- **A4 — the §4 experiment in `deep_reasoner_beta`** (does this let it delete `stamp_docs.py`
  and reduce `execute_docs.py` to its fan-out?). It needs a juplit release and live API calls,
  so it is not a juplit change and is not measured here. Until it runs, "193 lines deleted" stays
  an inference, exactly as the spec marks it.
- **`start()`'s early-return-if-alive silently ignoring `cwd`** — untouched, as the spec defers.
  It only becomes a problem under shape (b), which was not chosen.
