"""Wacom Universal Ink Model (.uim) writer tests.

Round-trips go through our own reader (`read_uim`); the oracle tests
additionally parse our output with Wacom's Apache-2.0 reference
implementation (PyPI: universal-ink-library, imports as `uim`) - if the
reference parser rejects a file, that is a writer bug.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from inkterop import ir
from inkterop.formats.base import Fidelity
from inkterop.formats.uim import (
    UimReader, UimWriter, _uid, encode_uim, read_uim,
)

FIXTURE = Path(__file__).parent / "fixtures" / "uim" / \
    "two-strokes-pressure.uim"
RM_FIXTURE = Path(__file__).parent / "fixtures" / "remarkable" / \
    "fineliner-pencil-colors.rm"


# ----------------------------------------------------------------- builders

def _mk(x, y, *, family=ir.ToolFamily.PEN, color=(0.2, 0.4, 0.8),
        width=3.0, channels=None, opacity=1.0) -> ir.Stroke:
    c = ir.Color(*color)
    channels = dict(channels or {})
    variable = ir.Channel.WIDTH in channels
    return ir.Stroke(
        x=list(x), y=list(y), tool=ir.ToolRef(family=family), color=c,
        channels=channels,
        appearance=ir.StrokeAppearance(
            mode=(ir.GeometryMode.STROKED_VARIABLE if variable
                  else ir.GeometryMode.STROKED_CONSTANT),
            color=c, width=None if variable else width, opacity=opacity,
        ),
    )


def _doc(strokes, title="uim-writer-test") -> ir.Document:
    xs = [v for s in strokes for v in s.x] or [100.0]
    ys = [v for s in strokes for v in s.y] or [100.0]
    # point_scale 0.75 = the writer's own DIP scale, so coordinates
    # round-trip identically (modulo float32).
    return ir.Document(
        format_id="irjson", title=title,
        pages=[ir.Page(bounds=ir.Rect(0.0, 0.0, max(xs), max(ys)),
                       point_scale=0.75,
                       layers=[ir.Layer(strokes=strokes)])])


def _back(doc: ir.Document, **kw) -> list[ir.Stroke]:
    out = read_uim(encode_uim(doc, **kw))
    out.validate()
    return list(out.pages[0].strokes())


# --------------------------------------------------------------- round-trip

def test_round_trip_constant_width_pen():
    src = _mk([10.0, 50.0, 90.0, 120.0], [20.0, 25.0, 22.0, 30.0])
    (s,) = _back(_doc([src]))
    assert s.x == pytest.approx(src.x, abs=1e-3)
    assert s.y == pytest.approx(src.y, abs=1e-3)
    assert len(s) == len(src)
    assert s.appearance.mode is ir.GeometryMode.STROKED_CONSTANT
    assert s.appearance.width == pytest.approx(3.0, abs=1e-3)
    assert ir.Channel.WIDTH not in s.channels
    assert s.color.r == pytest.approx(0.2, abs=1 / 255)
    assert s.color.g == pytest.approx(0.4, abs=1 / 255)
    assert s.color.b == pytest.approx(0.8, abs=1 / 255)
    assert s.appearance.opacity == pytest.approx(1.0)
    assert s.tool.family is ir.ToolFamily.PEN


def test_round_trip_variable_width():
    widths = [1.0, 2.5, 4.0, 2.0, 0.5]
    src = _mk([0.0, 10.0, 20.0, 30.0, 40.0], [5.0] * 5,
              channels={ir.Channel.WIDTH: widths})
    (s,) = _back(_doc([src]))
    assert s.appearance.mode is ir.GeometryMode.STROKED_VARIABLE
    assert s.channels[ir.Channel.WIDTH] == pytest.approx(widths, abs=1e-4)


def test_round_trip_alpha_channel():
    alphas = [0.2, 0.4, 0.6, 0.8, 1.0]
    src = _mk([0.0, 10.0, 20.0, 30.0, 40.0], [5.0] * 5,
              channels={ir.Channel.ALPHA: alphas}, opacity=0.9)
    (s,) = _back(_doc([src]))
    assert s.channels[ir.Channel.ALPHA] == pytest.approx(alphas, abs=1 / 255)
    assert s.appearance.opacity == pytest.approx(0.9, abs=1 / 255)


def test_round_trip_sensor_channels():
    n = 8
    channels = {
        ir.Channel.PRESSURE: [0.1 + 0.1 * i for i in range(n)],
        ir.Channel.TIMESTAMP: [0.01 * i for i in range(n)],
        ir.Channel.TILT_AZIMUTH: [0.5 - 0.05 * i for i in range(n)],
        ir.Channel.TILT_ALTITUDE: [1.2] * n,
    }
    src = _mk([float(10 * i) for i in range(n)],
              [100.0 + i * i for i in range(n)], channels=channels)
    (s,) = _back(_doc([src]))
    assert s.channels[ir.Channel.PRESSURE] == pytest.approx(
        channels[ir.Channel.PRESSURE], abs=1e-4)
    assert s.channels[ir.Channel.TIMESTAMP] == pytest.approx(
        channels[ir.Channel.TIMESTAMP], abs=1e-3)
    assert s.channels[ir.Channel.TILT_AZIMUTH] == pytest.approx(
        channels[ir.Channel.TILT_AZIMUTH], abs=1e-4)
    assert s.channels[ir.Channel.TILT_ALTITUDE] == pytest.approx(
        channels[ir.Channel.TILT_ALTITUDE], abs=1e-4)


def test_round_trip_highlighter():
    src = _mk([0.0, 40.0, 80.0], [10.0, 10.0, 10.0],
              family=ir.ToolFamily.HIGHLIGHTER, color=(1.0, 0.8, 0.0),
              width=12.0, opacity=0.5)
    (s,) = _back(_doc([src]))
    assert s.tool.family is ir.ToolFamily.HIGHLIGHTER
    assert s.appearance.underlay
    assert s.appearance.blend is ir.BlendMode.MULTIPLY
    assert s.appearance.opacity == pytest.approx(0.5, abs=1 / 255)
    assert "highlight" in s.tool.native.params["brush_uri"]


def test_round_trip_tool_families():
    families = [ir.ToolFamily.PEN, ir.ToolFamily.PENCIL,
                ir.ToolFamily.BALLPOINT, ir.ToolFamily.MARKER,
                ir.ToolFamily.HIGHLIGHTER, ir.ToolFamily.CALLIGRAPHY]
    strokes = [_mk([0.0, 10.0], [10.0 * i, 10.0 * i], family=f)
               for i, f in enumerate(families)]
    back = _back(_doc(strokes))
    assert [s.tool.family for s in back] == families


def test_round_trip_multiple_strokes_and_single_point():
    strokes = [
        _mk([10.0, 20.0, 30.0], [1.0, 2.0, 3.0]),
        _mk([55.5], [66.25]),  # single point -> 3 written incl. phantoms
        _mk([5.0, 5.0], [7.0, 9.0], color=(1.0, 0.0, 0.0)),
    ]
    back = _back(_doc(strokes))
    assert [len(s) for s in back] == [3, 1, 2]
    assert back[1].x == pytest.approx([55.5]) and \
        back[1].y == pytest.approx([66.25])
    for a, b in zip(strokes, back):
        assert b.x == pytest.approx(a.x, abs=1e-3)
        assert b.y == pytest.approx(a.y, abs=1e-3)


def test_invisible_layers_are_kept():
    """Container contract: ALL layers in layer order, even invisible."""
    doc = _doc([_mk([0.0, 10.0], [0.0, 10.0])])
    doc.pages[0].layers.append(ir.Layer(
        strokes=[_mk([20.0, 30.0], [20.0, 30.0])], visible=False))
    back = _back(doc)
    assert len(back) == 2
    assert back[1].x == pytest.approx([20.0, 30.0], abs=1e-3)


def test_native_payload_property():
    import base64
    import json

    src = _mk([0.0, 10.0], [0.0, 10.0])
    src.tool = ir.ToolRef(
        family=ir.ToolFamily.PEN,
        native=ir.NativeTool("remarkable", 17, {"size": 2}))
    src.extra = {"remarkable": {"move_id": 4}}
    doc = _doc([src], title="native-carry")
    back = read_uim(encode_uim(doc))
    key = f"inkterop.native.{_uid('native-carry', 0, 0).hex()}"
    payload = json.loads(base64.b64decode(back.metadata["properties"][key]))
    assert payload["tool"] == {"format_id": "remarkable", "tool_id": 17,
                               "params": {"size": 2}}
    assert payload["extra"] == {"remarkable": {"move_id": 4}}


def test_deterministic_output():
    doc = _doc([_mk([0.0, 10.0, 20.0], [1.0, 2.0, 3.0],
                    channels={ir.Channel.PRESSURE: [0.1, 0.5, 0.9]})])
    assert encode_uim(doc) == encode_uim(doc)


def test_native_fidelity_restyles():
    src = _mk([0.0, 10.0], [0.0, 0.0], family=ir.ToolFamily.HIGHLIGHTER,
              width=8.0, opacity=0.123)
    (s,) = _back(_doc([src]), fidelity=Fidelity.NATIVE)
    # restyled() replaces the exact appearance with the family default
    assert s.appearance.opacity == pytest.approx(0.85, abs=1 / 255)
    assert s.tool.family is ir.ToolFamily.HIGHLIGHTER


# ------------------------------------------------------- fixture round-trip

def test_fixture_round_trip():
    src = UimReader().read(FIXTURE)
    back = read_uim(encode_uim(src))
    back.validate()
    assert back.metadata["properties"]["Title"] == src.title
    a_strokes = list(src.pages[0].strokes())
    b_strokes = list(back.pages[0].strokes())
    assert len(b_strokes) == len(a_strokes) == 2
    for a, b in zip(a_strokes, b_strokes):
        assert len(b) == len(a)
        # fixture point_scale is 0.75 == the writer's DIP scale: identity
        assert b.x == pytest.approx(a.x, abs=1e-3)
        assert b.y == pytest.approx(a.y, abs=1e-3)
        for ch in (ir.Channel.PRESSURE, ir.Channel.TIMESTAMP,
                   ir.Channel.TILT_AZIMUTH, ir.Channel.TILT_ALTITUDE,
                   ir.Channel.WIDTH):
            assert b.channels[ch] == pytest.approx(a.channels[ch], abs=1e-3)
        assert b.tool.family is a.tool.family
        assert b.tool.native.params["brush_uri"] == \
            a.tool.native.params["brush_uri"]  # same-format URI carry
        assert b.appearance.opacity == pytest.approx(
            a.appearance.opacity, abs=1 / 255)
        assert b.color.rgb() == pytest.approx(a.color.rgb(), abs=1 / 255)


# ------------------------------------------------------------------ corpus

def _find_corpus() -> Path:
    """corpus/ lives at the repo root, gitignored (see test_uim.py)."""
    for base in Path(__file__).resolve().parents[2:]:
        candidate = base / "corpus" / "third-party" / \
            "universal-ink-library" / "ink"
        if candidate.is_dir():
            return candidate
    return Path(__file__).parents[2] / "corpus" / "third-party" / \
        "universal-ink-library" / "ink"


def _corpus_files() -> list[Path]:
    corpus = _find_corpus()
    return [p for sub in ("uim_3.0.0", "uim_3.1.0")
            for p in sorted((corpus / sub).glob("*.uim"))
            if (corpus / sub).is_dir()]


@pytest.mark.skipif(not _corpus_files(), reason="corpus samples not present")
@pytest.mark.parametrize("path", _corpus_files(), ids=lambda p: p.stem)
def test_corpus_reencode_round_trip(path: Path):
    """Every corpus file (both versions) re-encodes to a v3.1.0 file our
    reader decodes back to the same geometry."""
    src = UimReader().read(path)
    back = read_uim(encode_uim(src))
    back.validate()
    a_strokes = list(src.pages[0].strokes())
    b_strokes = list(back.pages[0].strokes())
    assert len(b_strokes) == len(a_strokes) > 0
    for a, b in zip(a_strokes, b_strokes):
        assert len(b) == len(a)
        assert b.x == pytest.approx(a.x, abs=1e-3)
        assert b.y == pytest.approx(a.y, abs=1e-3)


# ---------------------------------------------------- cross-format (rM in)

def test_cross_format_remarkable():
    from inkterop.formats.remarkable.reader import RemarkableReader

    src = RemarkableReader().read(RM_FIXTURE)
    k = src.pages[0].point_scale / 0.75  # source units -> DIPs
    back = read_uim(encode_uim(src))
    back.validate()
    a_strokes = list(src.pages[0].strokes())
    b_strokes = list(back.pages[0].strokes())
    assert len(a_strokes) > 0
    assert len(b_strokes) == len(a_strokes)
    for a, b in zip(a_strokes, b_strokes):
        assert len(b) == len(a)
        assert b.x == pytest.approx([v * k for v in a.x], abs=1e-2)
        assert b.y == pytest.approx([v * k for v in a.y], abs=1e-2)
        if ir.Channel.WIDTH in a.channels:
            assert ir.Channel.WIDTH in b.channels or \
                b.appearance.width is not None
        widths = a.channels.get(ir.Channel.WIDTH)
        if widths and ir.Channel.WIDTH in b.channels:
            assert b.channels[ir.Channel.WIDTH] == pytest.approx(
                [w * k for w in widths], abs=1e-3)


# ----------------------------------------------------------------- registry

def test_writer_registered(tmp_path):
    from inkterop import formats

    writer = formats.writer_for(tmp_path / "out.uim")
    assert writer is not None and writer.format_id == "uim"
    assert writer.validated


def test_multi_page_naming(tmp_path):
    doc = _doc([_mk([0.0, 10.0], [0.0, 10.0])])
    doc.pages.append(ir.Page(
        bounds=ir.Rect(0.0, 0.0, 100.0, 100.0), point_scale=0.75,
        layers=[ir.Layer(strokes=[_mk([1.0, 2.0], [3.0, 4.0])])]))
    out = tmp_path / "multi.uim"
    UimWriter().write(doc, out, Fidelity.EXACT)
    assert out.is_file() and (tmp_path / "multi-2.uim").is_file()
    p2 = list(UimReader().read(tmp_path / "multi-2.uim")
              .pages[0].strokes())
    assert p2[0].x == pytest.approx([1.0, 2.0], abs=1e-3)


# ------------------------------------------------------------------- oracle

def test_oracle_wacom_parser_geometry():
    """Wacom's own parser must accept our bytes; a parse failure or a
    value mismatch is a WRITER bug."""
    uim_parser = pytest.importorskip(
        "uim.codec.parser.uim", reason="universal-ink-library not installed")
    xs = [10.0, 50.0, 90.0, 120.0]
    ys = [20.0, 25.0, 22.0, 30.0]
    pressures = [0.25, 0.5, 0.75, 1.0]
    doc = _doc([
        _mk(xs, ys, channels={ir.Channel.PRESSURE: pressures}),
        _mk([0.0, 5.0], [0.0, 5.0], family=ir.ToolFamily.HIGHLIGHTER),
    ], title="oracle")
    model = uim_parser.UIMParser().parse(encode_uim(doc))

    assert len(model.strokes) == 2
    s0 = model.strokes[0]
    lx, ly = list(s0.splines_x), list(s0.splines_y)
    assert len(lx) == len(xs) + 2  # duplicated phantom endpoints
    assert lx[0] == lx[1] == pytest.approx(xs[0], abs=1e-3)
    assert lx[-1] == lx[-2] == pytest.approx(xs[-1], abs=1e-3)
    assert ly[0] == pytest.approx(ys[0], abs=1e-3)
    assert ly[-1] == pytest.approx(ys[-1], abs=1e-3)
    assert lx[1:-1] == pytest.approx(xs, abs=1e-3)
    assert ly[1:-1] == pytest.approx(ys, abs=1e-3)

    # brush URIs resolve through the InkData brushURIs table
    assert model.strokes[0].style.brush_uri == "inkterop://brush/pen"
    assert model.strokes[1].style.brush_uri == "inkterop://brush/highlighter"

    # sensor channel values decode to what we encoded
    sd0 = model.sensor_data.sensor_data[0]
    assert sd0.id == uuid.UUID(bytes_le=_uid("oracle", 0, 0, "sensor"))
    pressure_id = uuid.UUID(bytes_le=_uid("channel", "Pressure"))
    values = next(d.values for d in sd0.data_channels if d.id == pressure_id)
    assert list(values) == pytest.approx(pressures, abs=1e-4)
    x_id = uuid.UUID(bytes_le=_uid("channel", "X"))
    x_m = next(d.values for d in sd0.data_channels if d.id == x_id)
    assert [v * 3779.5275590592 for v in x_m] == pytest.approx(xs, abs=0.01)


def test_oracle_wacom_parser_fixture_reencode():
    """Fixture -> IR -> our writer -> Wacom parser: stroke geometry must
    match the fixture's own Wacom parse (modulo phantom handling)."""
    uim_parser = pytest.importorskip(
        "uim.codec.parser.uim", reason="universal-ink-library not installed")
    src = UimReader().read(FIXTURE)
    model = uim_parser.UIMParser().parse(encode_uim(src))
    ours = list(src.pages[0].strokes())
    assert len(model.strokes) == len(ours)
    for s_ir, s_lib in zip(ours, model.strokes):
        assert list(s_lib.splines_x)[1:-1] == pytest.approx(s_ir.x, abs=1e-3)
        assert list(s_lib.splines_y)[1:-1] == pytest.approx(s_ir.y, abs=1e-3)
