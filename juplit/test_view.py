"""Tests for the cheap reads: digests, the cell index, and bounded cell views."""

import base64
import random
import struct
import zlib
from pathlib import Path

import nbformat
import pytest
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook, new_output

from juplit.artifacts import stamp_cell
from juplit.tasks import generate_notebooks, html
from juplit.test_artifacts import _make_project
from juplit.view import (
    cells_table,
    markdown_heading,
    output_digest,
    parse_cell_range,
    truncate_output,
    view_cells,
)


def _png(width: int, height: int, noisy: bool = False) -> str:
    """A real PNG of the given size, base64-encoded, with no image library.

    `noisy` fills it with incompressible pixels, which is how a plot-heavy notebook
    actually reaches megabytes.
    """
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    size = (width * 3 + 1) * height
    pixels = random.Random(0).randbytes(size) if noisy else b"\x00" * size
    raw = zlib.compress(pixels)
    return base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
    ).decode()


# ── Digests ──────────────────────────────────────────────────────────────────

def test_output_digest_names_kind_and_size_per_output_type():
    stream = new_output("stream", name="stdout", text="fold 1\nfold 2\ndone\n")
    assert output_digest(stream).startswith("stream(stdout)")
    assert "3L" in output_digest(stream)

    error = new_output("error", ename="ZeroDivisionError", evalue="division by zero",
                       traceback=["a", "b", "c"])
    assert output_digest(error) == "error ZeroDivisionError: division by zero (3 frames)"

    table = new_output("execute_result", data={"text/plain": "[1200 rows x 8 columns]"})
    assert "text/plain 1L" in output_digest(table)

    html_out = new_output("display_data", data={"text/html": "<table>" + "x" * 4000})
    assert output_digest(html_out).startswith("text/html 4.0KB")


def test_image_digest_reads_dimensions_without_decoding_the_payload():
    b64 = _png(640, 480)
    digest = output_digest(new_output("display_data", data={"image/png": b64}))
    assert digest.startswith("image/png 640x480")
    assert b64[:64] not in digest


# ── The index ────────────────────────────────────────────────────────────────

def test_cells_table_shows_state_and_never_leaks_base64(tmp_path):
    b64 = _png(640, 480)
    clean = new_code_cell("plot()", outputs=[new_output("display_data", data={"image/png": b64})])
    stamp_cell(clean)
    stale = new_code_cell("summary()", outputs=[new_output("stream", name="stdout", text="ok\n")])
    stamp_cell(stale)
    stale.source = "summary(v2)"
    nb = new_notebook(cells=[new_markdown_cell("# Title"), clean, stale])
    ipynb = tmp_path / "n.ipynb"
    nbformat.write(nb, ipynb)

    table = cells_table(ipynb)

    assert "[00] md" in table
    assert "image/png 640x480" in table and "clean" in table
    assert b64[:64] not in table

    # the state sits in a fixed column, before the variable-length digest
    clean_row, stale_row = table.splitlines()[1], table.splitlines()[2]
    assert clean_row.index("clean") < clean_row.index("out:")
    assert stale_row.index("STALE") < stale_row.index("out:")
    assert clean_row.index("clean") == stale_row.index("STALE")


def test_a_four_megabyte_notebook_renders_in_a_few_hundred_tokens(tmp_path):
    """The token-budget acceptance criterion."""
    big = _png(600, 450, noisy=True)
    cells = []
    for i in range(40):
        cell = new_code_cell(f"step_{i}()",
                             outputs=[new_output("display_data", data={"image/png": big})])
        stamp_cell(cell)
        cells.append(cell)
    ipynb = tmp_path / "big.ipynb"
    nbformat.write(new_notebook(cells=cells), ipynb)
    assert ipynb.stat().st_size > 1_000_000

    table = cells_table(ipynb)

    assert len(table) < 4000
    assert big[:64] not in table
    assert max(len(line) for line in table.splitlines()) < 200


# ── Bounded reads ────────────────────────────────────────────────────────────

def test_truncate_output_elides_the_middle_of_a_huge_stream():
    text = "".join(f"line {i}\n" for i in range(10_000))
    rendered = truncate_output(new_output("stream", name="stdout", text=text))
    assert "lines elided" in rendered
    assert "line 0" in rendered and "line 9999" in rendered
    assert len(rendered.splitlines()) < 50


def test_full_still_refuses_to_print_an_image():
    b64 = _png(64, 64)
    output = new_output("display_data", data={"image/png": b64})
    assert truncate_output(output, max_lines=10_000, max_chars=200_000).startswith("image/png")


def test_view_cells_shows_only_the_named_cells_with_their_outputs(tmp_path, monkeypatch):
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    py = exp / "e.py"
    py.write_text(py.read_text() + "\n# %%\nsecond = 2\n\n# %%\nthird = 3\n")
    generate_notebooks()
    ipynb = exp / "e.ipynb"
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells[1].outputs = [new_output("stream", name="stdout", text="two\n")]
    nbformat.write(nb, ipynb)

    rendered = view_cells(py, cells=[1])

    assert "second = 2" in rendered and "two" in rendered
    assert "third = 3" not in rendered
    with pytest.raises(ValueError, match="out of range"):
        view_cells(py, cells=[99])


def test_view_cells_works_without_a_generated_notebook(tmp_path, monkeypatch):
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    rendered = view_cells(exp / "e.py")

    assert 'val = "seed"' in rendered
    assert "no e.ipynb on disk" in rendered


def test_parse_cell_range_accepts_singles_lists_and_spans():
    assert parse_cell_range("1,4,9-11") == [1, 4, 9, 10, 11]
    assert parse_cell_range("3") == [3]
    with pytest.raises(ValueError, match="before"):
        parse_cell_range("7-3")


# ── html ─────────────────────────────────────────────────────────────────────

def test_html_renders_a_standalone_page(tmp_path, monkeypatch, capsys):
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    generate_notebooks()

    target = html(exp / "e.py")

    assert target.exists() and target.suffix == ".html"
    assert "<html" in target.read_text()[:2000].lower()


def test_html_reports_a_missing_notebook(tmp_path, monkeypatch):
    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="run `juplit nb`"):
        html(exp / "e.py")


# ── Headings in the index ────────────────────────────────────────────────────

def test_the_index_carries_markdown_headings_so_it_reads_as_a_contents(tmp_path):
    nb = new_notebook(cells=[
        new_markdown_cell("# Ablation study\n\nThree arms, one seed each."),
        new_code_cell("load()"),
        new_markdown_cell("## Results"),
        new_markdown_cell("Prose with no heading at all."),
        new_markdown_cell("Intro line\n\n### Caveats\n\nmore prose"),
    ])
    ipynb = tmp_path / "n.ipynb"
    nbformat.write(nb, ipynb)

    lines = cells_table(ipynb).splitlines()

    assert lines[0].endswith("# Ablation study")          # level is kept, so depth shows
    assert lines[2].endswith("## Results")
    assert lines[3].endswith("1L")                        # no heading: nothing appended
    assert lines[4].endswith("### Caveats")               # a heading below the first line
    assert "Three arms" not in lines[0]                   # the body is still not printed


def test_markdown_heading_ignores_comments_and_normalises_spacing():
    assert markdown_heading("#no space is not a heading") is None
    assert markdown_heading("   ##   Padded   ") == "## Padded"
    assert markdown_heading("plain prose") is None
    assert markdown_heading("####### seven hashes is not a heading") is None


def test_source_and_outputs_are_introduced_by_the_same_banner(tmp_path, monkeypatch):
    """Code and output blocks share one delimiter, so a long view stays scannable."""
    from juplit.view import BANNER_WIDTH, banner

    _, exp = _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    py = exp / "e.py"
    py.write_text(py.read_text() + "\n# %%\nprint('two')\n")
    generate_notebooks()
    ipynb = exp / "e.ipynb"
    nb = nbformat.read(ipynb, as_version=4)
    nb.cells[1].outputs = [new_output("stream", name="stdout", text="two\n"),
                           new_output("stream", name="stderr", text="a warning\n")]
    nbformat.write(nb, ipynb)

    rendered = view_cells(py, cells=[1])
    lines = rendered.splitlines()

    assert lines[0] == banner(1, "code")
    assert lines.count(banner(1, "output")) == 2          # one per output, same rule
    assert all(len(line) == BANNER_WIDTH for line in lines if line.startswith("── "))
    assert "→" not in rendered                            # the old asymmetric prefix is gone
