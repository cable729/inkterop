"""Format-agnostic drawing primitives shared by output backends.

`split_runs` is a quirk-exact port of the legacy renderer's stroke
splitting (validated ~2% against official Paper Pro exports):
- consecutive points share one constant-width polyline while their widths
  stay within WIDTH_RUN_TOLERANCE;
- adjacent runs share the split point;
- color/alpha are sampled only at run starts (NOT per point) — quirk kept
  deliberately so output is byte-identical to the validated renderer;
- a run is only closed once it has >= 2 points.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import ir

# Width tolerance (source units) within which consecutive points share a run.
WIDTH_RUN_TOLERANCE = 0.35


@dataclass
class Run:
    points: list  # [(x, y), ...]
    width: float  # source units
    rgb: tuple  # (r, g, b) 0-1
    alpha: float
    cap: str  # "round" | "square"


def stroke_runs(stroke: ir.Stroke) -> list[Run]:
    """Split an IR stroke into constant-width runs (legacy-exact)."""
    app = stroke.appearance
    n = len(stroke.x)
    if n == 0:
        return []

    constant = app is not None and app.mode is ir.GeometryMode.STROKED_CONSTANT
    if constant:
        widths = [app.width] * n
    else:
        widths = stroke.channels.get(ir.Channel.WIDTH) or [1.0] * n

    alphas = stroke.channels.get(ir.Channel.ALPHA)
    if alphas is None:
        alphas = [app.opacity if app else 1.0] * n

    point_rgb = stroke.extra.get("inkterop", {}).get("point_rgb")
    base_rgb = tuple((app.color if app else stroke.color).rgb())
    rgbs = ([tuple(c) for c in point_rgb] if point_rgb else [base_rgb] * n)

    cap = "square" if app and app.cap is ir.LineCap.SQUARE else "round"

    if alphas[0] <= 0:
        return []

    runs: list[Run] = []
    run = Run([(stroke.x[0], stroke.y[0])], widths[0], rgbs[0], alphas[0], cap)
    for i in range(1, n):
        w = run.width if constant else widths[i]
        if abs(w - run.width) > WIDTH_RUN_TOLERANCE and len(run.points) > 1:
            runs.append(run)
            last = run.points[-1]
            run = Run([last], w, rgbs[i], alphas[i], cap)
        run.points.append((stroke.x[i], stroke.y[i]))
    runs.append(run)
    return runs
