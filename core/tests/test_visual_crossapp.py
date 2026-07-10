"""Cross-app visual fidelity: our pinned renderer vs real apps' own exports.

Two groups:

1. Xournal++ loop (needs the Mac app; skipped elsewhere) — the reference
   proof of the interchange architecture, both directions THROUGH the
   .inkz container:
     forward:  [xopp] -> [.inkz] -> [pinned render]  vs  xournalpp's PDF
     reverse:  [.inkz] -> [xopp] -> xournalpp PDF    vs  our render
   Xournal++ 1.3.5 exports headlessly (--create-pdf), so this runs
   without any UI automation.

2. Corpus scorecard (needs gitignored corpus/; skipped on CI) — RATCHET
   tests against app-made exports of our own fixtures. The scores are low
   today for known, itemized reasons (paper templates our readers don't
   emit; unmeasured per-app rendering rules — see visual/README.md); the
   assertions only guard against regressing below the measured baseline.
   RAISE the baselines as rendering rules land; never lower them.

Comparisons use registered mode (foreign rasterizer) with the calibrated
defaults; our renders use normalize="native" (cross-app comparisons must
honor the source page size — the uniform mirror page is a different job).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from inkterop import formats
from inkterop.formats.base import Fidelity
from inkterop.formats.inkz import InkzReader, InkzWriter
from inkterop.render.pdf import PdfWriter, RenderConfig
from inkterop.visual.diff import compare, compare_pdfs
from inkterop.visual.raster import pdf_pages_to_images

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = Path(__file__).resolve().parents[2] / "corpus"
XPP = Path("/Applications/Xournal++.app/Contents/MacOS/xournalpp")

#: Same-content-different-rasterizer floor measured at 96.6% ink-match
#: (visual/README.md); Xournal++ renders our vector geometry near-exactly,
#: measured 100% — leave margin for app/version drift.
XPP_MIN_INK_MATCH = 0.98

NATIVE = {"render_config": RenderConfig(normalize="native")}


def _render_native_pdf(doc, out: Path) -> Path:
    PdfWriter().write(doc, out, Fidelity.EXACT, NATIVE)
    return out


def _xpp_export(xopp: Path, out: Path) -> Path:
    subprocess.run([str(XPP), str(xopp), "-p", str(out)],
                   check=True, capture_output=True, timeout=120)
    return out


def _via_inkz(doc, tmp: Path):
    InkzWriter().write(doc, tmp / "doc.inkz", Fidelity.EXACT)
    return InkzReader().read(tmp / "doc.inkz")


@pytest.mark.skipif(not XPP.exists(), reason="Xournal++ app not installed")
def test_xournalpp_forward_through_container(tmp_path: Path) -> None:
    """[xopp] -> [.inkz] -> [pinned render] must match the app's export."""
    src = FIXTURES / "remarkable" / "fineliner-pencil-colors.rm"
    doc = formats.reader_for(src).read(src)
    xopp = tmp_path / "doc.xopp"
    formats.writer_for(xopp).write(doc, xopp, Fidelity.EXACT)

    xopp_doc = formats.reader_for(xopp).read(xopp)
    via = _via_inkz(xopp_doc, tmp_path)

    theirs = _xpp_export(xopp, tmp_path / "theirs.pdf")
    ours = _render_native_pdf(via, tmp_path / "ours.pdf")
    results = compare_pdfs(theirs, ours, mode="registered", dpi=96)
    assert results, "no pages compared"
    worst = min(r.ink_match_ratio for r in results)
    assert worst >= XPP_MIN_INK_MATCH, (
        f"forward loop drifted: worst ink-match {worst:.4%}")


@pytest.mark.skipif(not XPP.exists(), reason="Xournal++ app not installed")
def test_xournalpp_reverse_from_container(tmp_path: Path) -> None:
    """[.inkz] -> [xopp] -> the app must render the file we wrote the way
    we predict (our render of that same .xopp read back).

    Note this deliberately compares against our render of the WRITTEN
    .xopp, not of the source .inkz: the difference between those two is
    the *target format's* expressiveness loss (xopp holds no per-point
    alpha or underlay blend — reMarkable pencil texture flattens), which
    is a conversion-fidelity question, not an app-agreement question.
    Measured: render(inkz) vs xournalpp(xopp) scores ~21% on this pencil-
    heavy fixture; render(written xopp) vs xournalpp(xopp) is the loop
    check and must stay near-perfect."""
    src = FIXTURES / "remarkable" / "fineliner-pencil-colors.rm"
    doc = formats.reader_for(src).read(src)
    via = _via_inkz(doc, tmp_path)

    xopp = tmp_path / "from-inkz.xopp"
    formats.writer_for(xopp).write(via, xopp, Fidelity.EXACT)
    theirs = _xpp_export(xopp, tmp_path / "theirs.pdf")
    written = formats.reader_for(xopp).read(xopp)
    ours = _render_native_pdf(written, tmp_path / "ours.pdf")
    results = compare_pdfs(theirs, ours, mode="registered", dpi=96)
    worst = min(r.ink_match_ratio for r in results)
    assert worst >= XPP_MIN_INK_MATCH, (
        f"reverse loop drifted: worst ink-match {worst:.4%}")


# --- corpus scorecard (ratchet) ---------------------------------------------

SCORECARD = [
    # (fixture, app export in corpus/, measured baseline ink-match)
    ("goodnotes/gn-mac-mixed-pens.goodnotes",
     "gn-mac-mixed-pens.app-export.pdf", 0.03),
    ("saber/saber-mac-pens-text.sba",
     "saber-mac-pens-text.app-export.pdf", 0.02),
]


@pytest.mark.parametrize("rel,export,baseline", SCORECARD)
def test_corpus_scorecard(rel: str, export: str, baseline: float,
                          tmp_path: Path) -> None:
    app_pdf = CORPUS / export
    if not app_pdf.exists():
        pytest.skip(f"corpus export not present: {export}")
    fx = FIXTURES / rel
    doc = formats.reader_for(fx).read(fx)
    ours = _render_native_pdf(_via_inkz(doc, tmp_path), tmp_path / "ours.pdf")
    results = compare_pdfs(app_pdf, ours, mode="registered", dpi=96)
    worst = min(r.ink_match_ratio for r in results)
    assert worst >= baseline, (
        f"scorecard regressed below measured baseline: {worst:.4%} < "
        f"{baseline:.2%} — a fidelity change made things WORSE")
