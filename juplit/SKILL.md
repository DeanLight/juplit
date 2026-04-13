# Juplit: Literate Programming Workflow for Python Projects

## What juplit is for

juplit lets you do **literate programming** — writing code alongside explanations, examples, and tests in Jupyter notebook cells — while keeping your repository clean and AI-agent-friendly.

The problem it solves:
- Jupyter `.ipynb` files are JSON blobs: hard to diff in git, noisy in PRs, and token-heavy when AI agents need to read them
- But interactive notebook development is genuinely useful: you can run cells incrementally, see outputs inline, and mix prose with code

**juplit's solution**: `.py` files in jupytext percent format are the source of truth. `.ipynb` files are generated on demand for interactive use and are gitignored. AI agents and humans read `.py` files; Jupyter reads `.ipynb` files.

## File format

Every paired notebook `.py` file starts with a jupytext header:

```python
# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.16.0
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---
```

The key line is `formats: ipynb,py:percent` — this is what marks a file as a paired notebook.

### Cell delimiters

| Syntax | Meaning |
|---|---|
| `# %%` | Code cell |
| `# %% [markdown]` | Markdown cell (content is `#`-prefixed comments) |

### Markdown cells

```python
# %% [markdown]
# # Module Title
#
# Description of what this module does.
```

## Separating logic from tests with `test()`

Import `test` from juplit to gate test code so it runs interactively and under pytest, but **never on import**:

```python
from juplit import test

# %%
def add(a: int, b: int) -> int:
    return a + b

# %%
if test():
    assert add(1, 2) == 3
    assert add(-1, 1) == 0
```

`test()` returns `True` when:
- The module is run as `__main__` (interactive Jupyter cell execution)
- `pytest` is active

It returns `False` on normal import, so test code never runs in production.

## Poe commands

| Command | What it does |
|---|---|
| `poe sync` | Sync `.py` ↔ `.ipynb` — run after cloning and after editing `.py` files |
| `poe clean` | Sync then delete all `.ipynb` files — use before AI agent sessions |
| `poe init` | Install git pre-commit hooks |
| `poe test` | Run pytest across all `.py` files |
| `poe docs` | Serve docs locally for preview |
| `poe docs-deploy` | Deploy docs to GitHub Pages |

## Workflow

### First-time setup after cloning

```bash
uv sync        # install dependencies
poe init       # install git hooks (includes juplit sync on commit)
poe sync       # generate .ipynb notebooks from .py files
```

### Editing code (as an AI agent or in an editor)

1. Edit the `.py` file directly
2. Run `poe sync` to propagate changes to `.ipynb`
3. Commit the `.py` file — `.ipynb` is gitignored

### Before handing off to an AI agent

```bash
poe clean      # removes all .ipynb files so agents only see .py files
```

## Creating a new paired notebook file

1. Create a `.py` file with the jupytext header (copy from an existing file)
2. Add cells using `# %%` and `# %% [markdown]` delimiters
3. Run `poe sync` to generate the paired `.ipynb`

Minimal template:

```python
# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.16.0
# ---

# %% [markdown]
# # Module Name
#
# Brief description.

# %%
from juplit import test

# %%
def my_function(x):
    return x

# %%
if test():
    assert my_function(1) == 1
```

## Configuration in pyproject.toml

```toml
[project]
dependencies = ["juplit>=0.1.0"]

[dependency-groups]
dev = ["poethepoet>=0.25.0", "pytest>=8.0.0", "ipykernel>=6.0.0", "pre-commit>=3.0.0"]

[tool.poe.tasks]
init         = {cmd = "pre-commit install"}
sync         = {cmd = "juplit sync"}
clean        = {cmd = "juplit clean"}
test         = {cmd = "pytest"}
docs         = {cmd = "mkdocs serve"}
docs-deploy  = {cmd = "mkdocs gh-deploy --force"}

[tool.juplit]
notebook_src_dir = "your_module_name"   # directory scanned for paired .py files

[tool.jupytext]
formats = "ipynb,py:percent"

[tool.pytest.ini_options]
python_files = ["*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
```

## Key conventions

- **Edit `.py` files only** — `.ipynb` is generated, never manually edited
- **One logical idea per cell** — keep cells small and focused
- **Gate all test code with `if test():`** — never let test side effects run on import
- **Markdown goes in `# %% [markdown]` cells** using `#`-prefixed comment lines
- **`_tasks.py` itself** uses `formats: py:percent` (no `ipynb` pairing) since it is a pure utility, not a notebook

