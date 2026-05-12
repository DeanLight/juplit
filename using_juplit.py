# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.1
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Using juplit
#
# juplit lets you do **literate programming** in Python — writing code, tests, and
# explanations together in Jupyter notebooks — while keeping your repository clean
# and readable for both humans and AI agents.
#
# This notebook walks through the core workflow end to end.

# %% [markdown]
# ## Installation
#
# Add juplit to your project with uv (recommended) or pip:
#
# ```bash
# uv add juplit
# # or
# pip install juplit
# ```
#
# For a new project, use the cookiecutter template to get the full scaffolding:
#
# ```bash
# pip install cookiecutter juplit
# cookiecutter gh:DeanLight/juplit_template
# cd <new_project_slug>
# uv sync
# poe init   # install git pre-commit hooks
# poe sync   # generate .ipynb files from .py sources
# ```

# %% [markdown]
# ## The paired file format
#
# Every juplit source file is a `.py` file in **jupytext percent format** that pairs
# with a `.ipynb` notebook.  The `.py` file is committed; the `.ipynb` is generated
# on demand and gitignored.
#
# A minimal paired file looks like this:
#
# ```python
# # ---
# # jupyter:
# #   jupytext:
# #     formats: ipynb,py:percent
# # ---
#
# # %% [markdown]
# # # My Module
#
# # %%
# from juplit import test
#
# # %%
# def greet(name: str) -> str:
#     return f"Hello, {name}!"
#
# # %%
# if test():
#     assert greet("world") == "Hello, world!"
# ```
#
# The key line is `formats: ipynb,py:percent` — it marks the file as paired.

# %% [markdown]
# ## The `test()` guard
#
# Import `test` from juplit to gate code that should run interactively and under
# pytest, but **never on plain import**:
#
# ```python
# from juplit import test
#
# # %%
# def add(a: int, b: int) -> int:
#     return a + b
#
# # %%
# if test():
#     assert add(1, 2) == 3
#     assert add(-1, 1) == 0
#     print("add() tests passed")
# ```
#
# `test()` returns `True` when:
# - Running as `__main__` (interactive Jupyter cell execution)
# - `pytest` is active
#
# It returns `False` on normal import — so test assertions never run in production.
#
# ### Using `def test_*` functions with `test()` scaffolding
#
# You can also use standard pytest-style functions.  Use `if test():` blocks to
# set up shared fixtures at module level — they run during pytest collection,
# so the variables are in scope when the test functions execute:
#
# ```python
# from juplit import test
#
# # %%
# def compute(x: int) -> int:
#     return x * 2 + 1
#
# # %%
# if test():
#     inputs   = [1,  3,  -1]
#     expected = [3,  7,  -1]
#
# def test_compute():
#     for x, e in zip(inputs, expected):
#         assert compute(x) == e
# ```
#
# The `if test():` block scaffolds the test data; `test_compute` is a normal
# pytest-collected function that uses it.

# %% [markdown]
# ## poe commands
#
# | Command | What it does |
# |---|---|
# | `poe sync` | Sync `.py` ↔ `.ipynb` — run after editing `.py` files |
# | `poe nb` | Generate `.ipynb` from `.py` — run after cloning |
# | `poe clean` | Sync then delete all `.ipynb` files — use before AI sessions |
# | `poe test` | Run pytest across all `.py` files |
# | `poe docs` | Sync notebooks then serve docs locally |
# | `poe docs-deploy` | Sync notebooks then deploy to GitHub Pages |

# %% [markdown]
# ## Day-to-day workflow
#
# ### After cloning
#
# ```bash
# uv sync        # install dependencies
# poe init       # install git pre-commit hooks
# poe nb         # generate .ipynb from .py sources
# ```
#
# ### Editing code
#
# 1. Open the `.py` file in your editor (or open the paired `.ipynb` in Jupyter)
# 2. Make your changes
# 3. Run `poe sync` to keep `.py` and `.ipynb` in step
# 4. Commit the `.py` file — `.ipynb` is gitignored
#
# ### Before handing off to an AI agent
#
# ```bash
# poe clean   # removes .ipynb so the agent only sees .py files
# ```
#
# AI agents work with `.py` files directly — they're plain text, diff cleanly,
# and don't carry notebook metadata noise.

# %% [markdown]
# ## pyproject.toml configuration
#
# ```toml
# [tool.juplit]
# notebook_src_dirs = ["your_module", "docs"]  # dirs scanned for paired .py files
#
# [tool.jupytext]
# formats = "ipynb,py:percent"
#
# [tool.pytest.ini_options]
# python_files = ["*.py"]
# python_classes = ["Test*"]
# python_functions = ["test_*"]
# ```

# %% [markdown]
# ## Using the skill with Claude Code
#
# juplit ships skill files that teach Claude Code the workflow.  Install them once
# per project:
#
# ```bash
# mkdir -p .claude/skills
# juplit skill        > .claude/skills/juplit-programming.md
# juplit skill-migrate > .claude/skills/juplit-migrate.md
# ```
#
# After that, Claude Code will automatically follow the juplit conventions when
# creating or editing paired notebooks in your project.
