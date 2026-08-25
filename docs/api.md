# API Reference

## juplit

::: juplit.tasks
    options:
      members:
        - sync_notebooks
        - generate_notebooks
        - clean_notebooks

::: juplit.testing
    options:
      members:
        - test

## Artifact notebooks

::: juplit.artifacts
    options:
      members:
        - is_artifact
        - artifact_py_files
        - cell_state
        - scan
        - stamp
        - normalize_notebook
        - check_artifacts
        - add_cell
        - run_cells

::: juplit.kernel
    options:
      members:
        - start
        - stop
        - status
        - execute

::: juplit.view
    options:
      members:
        - cells_table
        - view_cells
        - output_digest
