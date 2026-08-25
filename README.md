# juplit

Literate programming for Python — write in notebooks, commit clean Python, keep AI agents fast.

## Why juplit

Jupyter notebooks are great for development: you can write prose next to code, run cells incrementally, and explore interactively. But `.ipynb` files are JSON blobs that create real problems:

- **Git history is cluttered** — every output change, cell execution count, or metadata tweak shows up as a diff
- **AI agents struggle** — JSON notebooks are token-heavy and hard to reason over compared to plain Python
- **Code review is painful** — notebooks don't diff cleanly in pull requests

**juplit gives you the best of both worlds.** You write in jupytext percent-format `.py` files — plain Python that AI agents can read and reason over efficiently. You generate `.ipynb` files locally for interactive Jupyter sessions, but keep them out of git. The `.py` file is always the source of truth.

## Installation

```bash
pip install juplit
```

## CLI usage

```bash
juplit nb      # generate .ipynb from .py files (run after cloning)
juplit sync    # sync .py <-> .ipynb after editing
juplit clean   # sync then delete all .ipynb files (before AI agent sessions)
juplit skill   # print the Claude Code skill file for juplit
```

## Artifact notebooks

Some notebooks exist for their outputs — an analysis run against a live endpoint, where
the plots and tables are the deliverable and nobody will re-run it to review it. Declare
those pairs and juplit stops treating the `.ipynb` as disposable:

```toml
[tool.juplit]
artifact_notebooks = ["experiments/**/*.py"]   # .ipynb committed WITH its outputs
```

```gitignore
!experiments/ablation.ipynb   # un-ignore it, or the outputs are never committed
```

For a declared pair, `nb` and `sync` keep the outputs, and `clean` keeps the file
(`--force` deletes it anyway). Every cell juplit executes records which version of its
source produced its outputs, so a cell whose code has moved on is reported and blocked
rather than passing silently:

```bash
juplit check          # pre-commit / CI: reads committed files only, no kernel needed
juplit run nb.py --stale    # re-execute just the cells that drifted
```

Committed does not mean untouched: execution counts, per-run timestamps and progress-bar
spam are stripped on every write, so re-running with the same results produces no diff.

## The execution loop

juplit can hold a Jupyter kernel alive *between* CLI calls, with no Jupyter server — so
an agent (or you) can try a cell, look at the result, and commit only the version that
worked:

```bash
juplit cells experiments/ablation.py     # index: one line per cell, digests, no base64
juplit view  experiments/ablation.py 3-7 # just those cells, with their outputs

juplit kernel start
juplit try 'df.groupby("arm").score.mean()'      # prints; writes nothing
juplit add-cell experiments/ablation.py --from-last   # the snippet + the outputs you saw

juplit html experiments/ablation.py      # standalone page for a non-Python reader
```

**`try` never writes. `run` always writes.** That is the whole safety rule.

See the [artifact notebooks tutorial](https://deanlight.github.io/juplit/artifact_notebooks/)
for the full walkthrough.

## Project setup (pyproject.toml)

For a new project, use the [cookiecutter template](https://github.com/DeanLight/juplit_template)

```toml
[project]
dependencies = ["juplit>=0.1.0"]

[dependency-groups]
dev = ["poethepoet>=0.25.0", "pytest>=8.0.0", "ipykernel>=6.0.0", "pre-commit>=3.0.0"]

[tool.poe.tasks]
init  = {cmd = "pre-commit install"}
sync  = {cmd = "juplit sync"}
nb    = {cmd = "juplit nb"}
clean = {cmd = "juplit clean"}
test  = {cmd = "pytest"}

[tool.juplit]
notebook_src_dir = "your_module"   # directory juplit scans for paired .py files

[tool.jupytext]
formats = "ipynb,py:percent"

[tool.pytest.ini_options]
python_files = ["*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
```

juplit finds the nearest `pyproject.toml` by walking up from the current directory, so the CLI works from any subdirectory.

## Separating logic from tests with `test()`

Use `test()` to gate inline test code so it runs interactively in Jupyter and under pytest, but **never on import**:

```python
from juplit import test

# %%
def add(a: int, b: int) -> int:
    return a + b

# %%
if test():
    assert add(1, 2) == 3
    assert add(-1, 1) == 0
    print("add() tests pass")
```

pytest picks up these blocks automatically when you configure:

```toml
[tool.pytest.ini_options]
python_files = ["*.py"]
```

No `def test_*` functions required — just `if test():` blocks next to the code they test.

You can also mix standard pytest functions with `if test():` scaffolding blocks.  Because `if test():` runs at module scope during pytest collection, variables it sets up are available to `def test_*` functions:

```python
from juplit import test

# %%
def compute(x: int) -> int:
    return x * 2 + 1

# %%
if test():
    inputs   = [1,  3,  -1]
    expected = [3,  7,  -1]

def test_compute():
    for x, e in zip(inputs, expected):
        assert compute(x) == e
```

## Paired notebook format

A `.py` file is recognized as a paired notebook when its header contains:

```python
# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
# ...
# ---
```

Cells are delimited with `# %%` (code) and `# %% [markdown]` (prose).

## Claude Code integration

Generate a skill file for Claude Code so it understands the juplit workflow:

```bash
juplit skill > .claude/skills/juplit-programming.md
```

For a skill on how to migrate nbdev repos to juplit:
```bash
juplit skill_migrate > .claude/skills/juplit-programming-nbdev-migrate.md
```
