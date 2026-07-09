"""Golden regression tests for the reMarkable -> PDF pipeline.

The renderer's output was validated ~2% against official Paper Pro exports;
these tests pin that behavior byte-for-byte at the drawing-op level so the
IR refactor cannot silently change fidelity.

reportlab PDFs are nondeterministic (CreationDate, doc ID), so goldens are
normalized per-page content-stream op dumps + MediaBoxes, not raw PDF bytes.
Regenerate deliberately with: uv run pytest --update-goldens
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pikepdf
import pytest

from inkterop.render import RenderConfig, render_notebook

FIXTURES = Path(__file__).parent / "fixtures" / "remarkable"
GOLDEN = Path(__file__).parent / "golden"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())


def _jsonable(obj):
    """pikepdf operand -> stable JSON value (floats rounded to 4 decimals)."""
    if isinstance(obj, pikepdf.Name):
        return str(obj)
    if isinstance(obj, pikepdf.String):
        return bytes(obj).decode("latin-1")
    if isinstance(obj, pikepdf.Array):
        return [_jsonable(o) for o in obj]
    if isinstance(obj, pikepdf.Dictionary):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (int, float)) or hasattr(obj, "__float__"):
        f = float(obj)
        return int(f) if f == int(f) else round(f, 4)
    return str(obj)


def pdf_ops(pdf_path: Path) -> list:
    """Normalized [per-page {mediabox, ops}] for a PDF file."""
    pages = []
    with pikepdf.open(pdf_path) as pdf:
        for page in pdf.pages:
            ops = [
                [str(op.operator), [_jsonable(o) for o in op.operands]]
                for op in pikepdf.parse_content_stream(page)
            ]
            pages.append({
                "mediabox": [round(float(v), 4) for v in page.mediabox],
                "ops": ops,
            })
    return pages


def render_fixture(slug: str, out_dir: Path) -> Path:
    info = MANIFEST[slug]
    out = out_dir / f"{slug}.pdf"
    render_notebook(
        [FIXTURES / info["file"]],
        out,
        landscape=(info["orientation"] == "landscape"),
        config=RenderConfig(),
        templates=[info["template"]],
    )
    return out


@pytest.mark.parametrize("slug", sorted(MANIFEST))
def test_golden(slug, tmp_path, request):
    golden_path = GOLDEN / f"{slug}.ops.json.gz"
    got = pdf_ops(render_fixture(slug, tmp_path))

    if request.config.getoption("--update-goldens"):
        GOLDEN.mkdir(exist_ok=True)
        with gzip.open(golden_path, "wt") as f:
            json.dump(got, f, separators=(",", ":"))
        pytest.skip(f"updated {golden_path.name}")

    assert golden_path.exists(), (
        f"missing golden {golden_path.name}; run pytest --update-goldens"
    )
    with gzip.open(golden_path, "rt") as f:
        want = json.load(f)

    assert len(got) == len(want), (
        f"page count changed: {len(got)} != {len(want)}"
    )
    for i, (gp, wp) in enumerate(zip(got, want)):
        assert gp["mediabox"] == wp["mediabox"], f"page {i + 1} MediaBox changed"
        for j, (gop, wop) in enumerate(zip(gp["ops"], wp["ops"])):
            assert gop == wop, (
                f"page {i + 1} op {j} changed: {gop!r} != {wop!r}"
            )
        assert len(gp["ops"]) == len(wp["ops"]), (
            f"page {i + 1} op count changed: {len(gp['ops'])} != {len(wp['ops'])}"
        )


def test_render_is_deterministic(tmp_path):
    """Two renders of the same fixture must produce identical op dumps."""
    a = pdf_ops(render_fixture("ballpoint-small", tmp_path / "a"))
    b = pdf_ops(render_fixture("ballpoint-small", tmp_path / "b"))
    assert a == b
