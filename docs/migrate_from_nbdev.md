# Migrating from nbdev to juplit

This guide describes how to migrate a project that uses nbdev (Jupyter-based literate programming with `#|export` directives) to juplit's percent-format workflow.

## Overview

nbdev notebooks use special directives inside cells to control what gets exported:

- `#|export` — cell is exported to the Python module
- `#|hide` — cell is hidden in docs (usually tests/setup)
- No directive — cell is shown in docs but not exported (usually examples/tests)

In juplit, **all cells are regular Python** and everything in the file is importable. Test/example code is gated with `if test():` instead of being in non-exported cells.

## Migration steps

### 1. Initialize a new juplit project

```bash
pip install cookiecutter juplit
cookiecutter gh:DeanLight/juplit_template
cd <new_project_slug>
uv sync
poe init
```

### 2. For each nbdev notebook, create a paired .py file

For each `.ipynb` in the nbdev `nbs/` directory, create a corresponding `.py` file in the new module directory using the conversion rules below.

**Markdown cells** → `# %% [markdown]` cell:

```python
# %% [markdown]
# # Module Title
#
# Cell content here.
```

**Code cells with `#|export`** → regular `# %%` cell (strip the directive):

```python
# nbdev:              →   # juplit:
# #|export                # %%
# def my_func(x):         def my_func(x):
#     return x                 return x
```

**Code cells without `#|export`** (examples, tests, `#|hide` cells) → `# %%` cell wrapped in `if test():`

```python
# nbdev (no #|export):    →   # juplit:
# assert my_func(1) == 1      # %%
#                              if test():
#                                  assert my_func(1) == 1
```

### 3. Add the jupytext header

Every converted file needs the header at the top, followed by the `test` import:

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

# %%
from juplit import test
# (other imports your module needs)
```

### 4. Generate notebooks and verify

```bash
poe sync       # generates .ipynb from .py files
poe test       # run tests to verify nothing broke
```

### 5. Update imports in the rest of the project

nbdev exports from a generated module path. After migration, the module is the `.py` files directly. Update any `from nbs.xx_module import ...` to `from your_module import ...`.

## Example conversion

**Before (nbdev `nbs/00_core.ipynb`):**

```python
# Cell 1 — markdown
# # Core module

# Cell 2 — #|export
# #|export
# def add(a, b):
#     return a + b

# Cell 3 — no directive (test shown in docs)
# assert add(1, 2) == 3

# Cell 4 — #|hide (hidden test)
# #|hide
# assert add(-1, 1) == 0
```

**After (juplit `your_module/core.py`):**

```python
# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
# ...
# ---

# %% [markdown]
# # Core module

# %%
from juplit import test

# %%
def add(a, b):
    return a + b

# %%
if test():
    assert add(1, 2) == 3

# %%
if test():
    assert add(-1, 1) == 0
```

## Install the migration skill for Claude Code

If you want Claude Code to assist with the migration automatically:

```bash
mkdir -p .claude/skills
juplit skill-migrate > .claude/skills/juplit-migrate.md
```
