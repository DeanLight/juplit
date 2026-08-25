# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -juplit
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
# # Artifact notebooks
#
# An **artifact notebook** is a pair where *both* halves are committed: the `.py` without
# outputs, as always, and the `.ipynb` **with** them.
#
# You want that when the outputs are the deliverable. An ablation run against a live
# endpoint takes twenty minutes and real money; the plots and tables it produced are what
# a reviewer reads on GitHub, because nobody is going to re-run it and nobody can read the
# raw logs. juplit's default — `.py` is truth, `.ipynb` is disposable — is exactly wrong
# for that one case.
#
# Declaring a pair changes three things:
#
# - its outputs survive `juplit nb`, `sync` and `clean`;
# - juplit records which version of each cell's source produced its outputs, so it can
#   tell you later when they stop matching;
# - the running state of each run — execution counts, timestamps, progress-bar spam — is
#   stripped on every write, so re-running with the same results produces *no diff*.
#
# This page runs an experiment end to end: declare it, produce the results, extend it the
# way an agent does, and then let juplit catch the code drifting away from the numbers.

# %%
import re
import subprocess
from pathlib import Path

REPO = next(d for d in [Path.cwd(), *Path.cwd().parents] if (d / "mkdocs.yml").exists())
DEMO = REPO / ".cache" / "artifact-demo"


def sh(command: str, cwd: Path = DEMO) -> None:
    """Run a shell command in the demo project and show what it printed.

    Returns nothing on purpose: a returned string would be echoed a second time as the
    cell's result, so every cell would carry its output twice. Pids and this machine's
    paths are masked, so re-running the page produces the same outputs — which is what
    lets it be committed as an artifact notebook.
    """
    result = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True)
    printed = (result.stdout + result.stderr).strip().replace(str(DEMO), "/tmp/ablation-demo")
    print(f"$ {command}\n" + re.sub(r"pid \d+", "pid <pid>", printed))


# %% [markdown]
# ## 1. Declare the pair
#
# One key in `pyproject.toml`, matched as a glob against the repo-relative path of the
# `.py`. Everything undeclared behaves exactly as it does today — this is an opt-in
# subset, not a change to the default.

# %%
DEMO.mkdir(parents=True, exist_ok=True)
(DEMO / "experiments").mkdir(exist_ok=True)
(DEMO / "pyproject.toml").write_text('''\
[tool.juplit]
notebook_src_dirs  = ["experiments"]
artifact_notebooks = ["experiments/**/*.py"]   # .ipynb committed WITH its outputs

[tool.jupytext]
formats = "ipynb,py:percent"
''')
print((DEMO / "pyproject.toml").read_text())

# %% [markdown]
# The `.ipynb` also has to be un-ignored. Preserved locally and never committed looks
# exactly like the feature not working, so juplit warns on every command while a declared
# artifact is still ignored, and `juplit check` fails on it.

# %%
(DEMO / ".gitignore").write_text("*.ipynb\n!experiments/ablation.ipynb\n")
sh("git init -q")
print((DEMO / ".gitignore").read_text())

# %% [markdown]
# ## 2. The experiment
#
# A three-arm ablation, small enough to read: the results, then a summary. In a real one
# these cells call a model endpoint; here they are a dict, so this page costs nothing to
# build.

# %%
(DEMO / "experiments" / "ablation.py").write_text('''\
# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
# ---

# %% [markdown]
# # Prompt ablation
#
# Three arms, three seeds each.

# %%
ARMS = {"baseline": [0.41, 0.39, 0.44], "prompted": [0.55, 0.58, 0.51], "tuned": [0.67, 0.64, 0.69]}
for arm, scores in ARMS.items():
    print(f"{arm:<10} {scores}")

# %% [markdown]
# ## Summary

# %%
for arm, scores in ARMS.items():
    print(f"{arm:<10} mean {sum(scores) / len(scores):.2f}")
''')
sh("juplit nb")

# %% [markdown]
# ## 3. Produce the results
#
# Start a kernel and run the notebook. The kernel outlives the command — every later
# `juplit` call in this page talks to the same one — and `juplit run` **saves** what it
# produced into the `.ipynb`.
#
# `--all` is the clean build: restart the kernel, run everything top to bottom. `run`
# always needs a selector, because the plausible default is the expensive one.

# %%
sh("juplit kernel start")
sh("juplit run experiments/ablation.py --all")

# %% [markdown]
# Those outputs are now committed evidence. The `.py` stays clean — no outputs, no
# metadata — so it still reviews and diffs like source code.

# %%
sh("head -20 experiments/ablation.py")

# %% [markdown]
# ## 4. Read it without paying for it
#
# `juplit cells` is the index: markdown headings, and a digest of every output — kind,
# size, dimensions, first and last line. Never base64. A forty-cell notebook full of
# plots costs a few hundred tokens here instead of megabytes.

# %%
sh("juplit cells experiments/ablation.py")

# %% [markdown]
# The headings make it navigable: an agent reads the index, decides it wants the summary,
# and pulls only that cell with `juplit view`.

# %%
sh("juplit view experiments/ablation.py 3")

# %% [markdown]
# ## 5. Extend it the way an agent does
#
# The loop is *try, look, then add once happy*. `juplit try` runs code on the live kernel
# and prints the result — it **writes nothing**, so a wrong guess leaves no trace in the
# notebook and costs nothing.

# %%
sh("""juplit try 'print(spread)'""")

# %% [markdown]
# That failed, and the notebook on disk never knew. Fix it and try again:

# %%
SNIPPET = 'spread = max(ARMS[\"tuned\"]) - min(ARMS[\"baseline\"]); print(f\"spread {spread:.2f}\")'
sh(f"juplit try {SNIPPET!r}")

# %% [markdown]
# Now keep it. `--from-last` writes that snippet **and the outputs you just looked at**
# into the pair — nothing is re-executed, so what lands is exactly what you saw.

# %%
sh("juplit add-cell experiments/ablation.py --from-last")
sh("juplit cells experiments/ablation.py")

# %% [markdown]
# The `.py` gained a cell and the `.ipynb` gained the same cell with its output, so the
# commit is one local insertion in both halves — not a rewritten JSON blob.

# %% [markdown]
# ## 6. When the code drifts away from the numbers
#
# Committed outputs can go stale: you edit a cell weeks later, and the notebook now shows
# new code beside old results. juplit stamps every cell it executes with the version of
# the source that produced its outputs, so it can see that happen.
#
# Change the summary from a mean to a max:

# %%
path = DEMO / "experiments" / "ablation.py"
path.write_text(path.read_text().replace("mean {sum(scores) / len(scores):.2f}",
                                         "max  {max(scores):.2f}"))
sh("juplit sync")

# %% [markdown]
# `juplit check` is the same verdict as a pre-commit hook or a CI job. It reads only
# committed files — no kernel, no local state — so it works on a fresh clone.

# %%
sh("juplit check")

# %% [markdown]
# Note what it did **not** do: delete anything. Those outputs cost real money, so the
# choice between re-running and reverting stays yours. Here, re-run just the cell that
# drifted — the kernel from step 3 is still alive and still holds `ARMS`:

# %%
sh("juplit run experiments/ablation.py --stale")
sh("juplit check")

# %% [markdown]
# ## 7. Housekeeping
#
# `juplit clean` deletes generated notebooks before an agent session — but it keeps
# artifact notebooks, because those are deliverables rather than build products, and it
# reaps the kernels nothing else was going to reap. `--force` deletes them anyway.

# %%
sh("juplit clean")

# %% [markdown]
# And for someone who will not open a notebook at all, `juplit html` renders the whole
# thing — outputs included — as a standalone page.

# %%
sh("juplit html experiments/ablation.py")

# %% [markdown]
# ## What to remember
#
# - Declare the pair, and un-ignore its `.ipynb`.
# - **`try` never writes. `run` always writes.** That is the whole safety rule.
# - `cells` before `view`, and never read a raw `.ipynb` into a context window.
# - Stale outputs are named and block; they are never deleted for you.
# - Re-running and getting the same results produces no diff, so a committed notebook is
#   something you can actually review.
