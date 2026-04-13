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

## Separating logic from tests with `testing()`

Use `testing()` to gate inline test code so it runs interactively in Jupyter and under pytest, but **never on import**:

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
