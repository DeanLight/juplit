"""Reading a notebook without paying for it.

A plot-heavy `.ipynb` is megabytes of base64. An agent that reads one into context has
spent its budget on data it cannot interpret. These renderers never emit a payload: an
image output becomes `image/png 640x480 82KB`, a 10 000-line stream becomes its first
and last lines, and only text is ever shown in full.
"""

import base64
import re
import struct
from pathlib import Path

import nbformat
from nbformat import NotebookNode

MAX_LINES = 40
MAX_CHARS = 4000
BINARY_PREFIXES = ("image/", "application/pdf", "video/", "audio/")


def parse_cell_range(spec: str) -> list[int]:
    """`"3"`, `"3-7"`, `"1,4,9-11"` -> a sorted list of cell indices."""
    indices: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            if end < start:
                raise ValueError(f"invalid cell range {part!r}: {end} is before {start}")
            indices.update(range(start, end + 1))
        else:
            indices.add(int(part))
    return sorted(indices)


# ── Digests ──────────────────────────────────────────────────────────────────

def _human_bytes(count: int) -> str:
    for unit, size in (("MB", 1_000_000), ("KB", 1_000)):
        if count >= size:
            return f"{count / size:.1f}{unit}"
    return f"{count}B"


def _png_dimensions(b64: str) -> tuple[int, int] | None:
    """Width and height from a PNG's IHDR, read out of the first 64 base64 characters.

    48 decoded bytes is enough, so this costs no image library and never touches the
    payload.
    """
    try:
        head = base64.b64decode(b64[:64])
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">II", head[16:24])
    except Exception:
        return None


def _first_and_last(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return f"{lines[0][:60]!r}"
    return f"{lines[0][:40]!r} … {lines[-1][:40]!r}"


def output_digest(output: NotebookNode) -> str:
    """A one-line, base64-free summary of a single output."""
    kind = output.get("output_type")
    if kind == "stream":
        text = output.get("text", "")
        return (f"stream({output.get('name', 'stdout')}) {_human_bytes(len(text))} "
                f"{len(text.splitlines())}L {_first_and_last(text)}").strip()
    if kind == "error":
        frames = len(output.get("traceback", []))
        return f"error {output.get('ename')}: {output.get('evalue')} ({frames} frames)"

    parts = []
    for mime, payload in output.get("data", {}).items():
        text = payload if isinstance(payload, str) else str(payload)
        if mime.startswith(BINARY_PREFIXES):
            size = _human_bytes(len(text) * 3 // 4)
            dims = _png_dimensions(text) if mime == "image/png" else None
            parts.append(f"{mime} {dims[0]}x{dims[1]} {size}" if dims else f"{mime} {size}")
        elif mime == "text/plain":
            parts.append(f"text/plain {len(text.splitlines())}L {_first_and_last(text)}")
        else:
            parts.append(f"{mime} {_human_bytes(len(text))}")
    return ", ".join(parts) or kind


def truncate_output(output: NotebookNode, max_lines: int = MAX_LINES,
                    max_chars: int = MAX_CHARS) -> str:
    """Render one output for a context window: head and tail, with an elision marker.

    Binary mime types are always their digest, even here — "the whole output" means the
    whole text, never the base64.
    """
    kind = output.get("output_type")
    if kind == "stream":
        text = output.get("text", "")
    elif kind == "error":
        text = "\n".join(output.get("traceback", [])) or output_digest(output)
    else:
        data = output.get("data", {})
        if any(mime.startswith(BINARY_PREFIXES) for mime in data):
            return output_digest(output)
        text = data.get("text/plain") or next(iter(data.values()), "")
        if not isinstance(text, str):
            text = str(text)

    lines = text.splitlines()
    if len(lines) > max_lines:
        head, tail = lines[: max_lines // 2], lines[-(max_lines // 2):]
        elided = len(lines) - len(head) - len(tail)
        lines = head + [f"… {elided} lines elided …"] + tail
    rendered = "\n".join(lines)
    if len(rendered) > max_chars:
        rendered = f"{rendered[:max_chars]}\n… truncated at {max_chars} characters …"
    return rendered


# ── Whole-notebook views ─────────────────────────────────────────────────────

HEADING = re.compile(r"^(#{1,6})\s+(.+)")


def markdown_heading(source: str) -> str | None:
    """The first ATX heading in a markdown cell, normalised to `## Title`, or None.

    Carrying it into the index is what lets an agent navigate a notebook by its
    structure — "the section on ablations starts at cell 12" — instead of reading every
    cell to find out where it is.
    """
    for line in source.splitlines():
        match = HEADING.match(line.strip())
        if match:
            hashes, title = match.groups()
            return f"{hashes} {title.strip()}"
    return None


def cells_table(ipynb: Path) -> str:
    """The index: one line per cell, headings and digests, never any source or base64.

        [00] md     6L   # Ablation study
        [05] md     2L   ## Results
        [06] code  12L   clean        out: stream 1.2KB 14L 'Fitting fold 3…'
        [07] code   4L   STALE        out: text/plain 3L '[1200 rows x 8 columns]'

    Every column left of the digest is fixed-width, and the digest — the one field with
    no common structure — comes last, so `clean` / `STALE` land in the same place on
    every line instead of after a string whose length depends on the output.
    """
    from juplit.artifacts import cell_state

    nb = nbformat.read(ipynb, as_version=4)
    rows = []
    for index, cell in enumerate(nb.cells):
        kind = "code" if cell.cell_type == "code" else "md"
        length = f"{len(cell.source.splitlines())}L"
        row = f"[{index:02d}] {kind:<4} {length:>4}"
        if cell.cell_type == "markdown":
            heading = markdown_heading(cell.source)
            if heading:
                row = f"{row}   {heading[:80]}"
        if cell.get("outputs"):
            state = cell_state(cell)
            label = state.upper() if state == "stale" else state
            digests = ", ".join(output_digest(o) for o in cell.outputs)
            row = f"{row}   {label:<11}out: {digests}"
        rows.append(row)
    return "\n".join(rows)


BANNER_WIDTH = 72


def banner(index: int, label: str) -> str:
    """`── [03] code ─────…` — the same rule above a cell's source and above its outputs.

    Matching delimiters are what make a long `view` scannable: every block starts with
    its cell number and what it is, so you always know which cell you are looking at and
    whether you are reading code or what the code produced.
    """
    prefix = f"── [{index:02d}] {label} "
    return prefix + "─" * max(4, BANNER_WIDTH - len(prefix))


def view_cells(py_file: Path, cells: list[int] | None = None, full: bool = False) -> str:
    """Source of the named cells, each followed by its outputs. No range means all.

    Both halves are introduced by the same banner rule::

        ── [03] code ───────────────────────────────────────────────────
        for arm, scores in ARMS.items():
            print(f"{arm:<10} mean {sum(scores) / len(scores):.2f}")
        ── [03] output ─────────────────────────────────────────────────
        baseline   mean 0.41

    Source comes from the `.py` — the source of truth — and outputs from the paired
    `.ipynb`, so this works on an ordinary pair with no notebook on disk: it prints the
    source and says the notebook has not been generated.
    """
    import jupytext

    nb = jupytext.reads(py_file.read_text(), fmt="py:percent")
    ipynb = py_file.with_suffix(".ipynb")
    outputs: list[list[NotebookNode]] = []
    if ipynb.exists():
        executed = nbformat.read(ipynb, as_version=4)
        outputs = [cell.get("outputs", []) for cell in executed.cells]

    wanted = cells if cells is not None else range(len(nb.cells))
    blocks = []
    for index in wanted:
        if index >= len(nb.cells):
            raise ValueError(f"cell {index} is out of range (0..{len(nb.cells) - 1})")
        cell = nb.cells[index]
        kind = "code" if cell.cell_type == "code" else "markdown"
        block = [banner(index, kind), cell.source.rstrip()]
        for output in outputs[index] if index < len(outputs) else []:
            rendered = (truncate_output(output, max_lines=10_000, max_chars=200_000)
                        if full else truncate_output(output))
            block += [banner(index, "output"), rendered]
        blocks.append("\n".join(block))
    if not ipynb.exists():
        blocks.append(f"(no {ipynb.name} on disk — source only; run `juplit nb` to generate it)")
    return "\n\n".join(blocks)
