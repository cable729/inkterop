"""Measure rendered stroke widths in app vector exports and fit width laws.

Reproduces the round-1 numbers in docs/calibration-results.md (fitted
constants live in ir/renderrule.py). Run from core/:

    uv run python scripts/fit_render_laws.py

Method: match export paths to native strokes by centroid, then measure
the rendered ribbon width by ray-casting perpendicular to the local
stroke direction through the outline polygons (even-odd parity).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

from inkterop import ir
from inkterop.formats import reader_for

CAL = Path("../corpus/calibration")


def svg_paths(path: Path) -> list[dict]:
    out = []
    for a in re.findall(r"<path ([^>]*?)/?>", path.read_text()):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', a))
        subs = []
        for sub in attrs.get("d", "").split("M"):
            pts = [(float(x), float(y)) for x, y in
                   re.findall(r"(-?[\d.]+)[ ,]+(-?[\d.]+)", sub)]
            if len(pts) > 1:
                subs.append(pts)
        if subs:
            out.append({"attrs": attrs, "subs": subs})
    return out


def centroid(pts):
    return (sum(a for a, _ in pts) / len(pts), sum(b for _, b in pts) / len(pts))


def match_paths(paths, strokes):
    """path elements grouped by nearest native stroke (centroid)."""
    ncent = [centroid(list(zip(s.x, s.y))) for s in strokes]
    grouped = defaultdict(list)
    for pa in paths:
        c = centroid([pt for sub in pa["subs"] for pt in sub])
        si = min(range(len(strokes)),
                 key=lambda i: (ncent[i][0] - c[0]) ** 2 + (ncent[i][1] - c[1]) ** 2)
        grouped[si].append(pa)
    return grouped


def parity_intervals(qx, qy, ux, uy, segs, lim):
    """Inside intervals (even-odd) along the ray q + t*(ux,uy)."""
    ts = []
    for (ax, ay), (bx, by) in segs:
        dx, dy = bx - ax, by - ay
        den = ux * dy - uy * dx
        if abs(den) < 1e-12:
            continue
        t = ((ax - qx) * dy - (ay - qy) * dx) / den
        if abs(t) > lim:
            continue
        s = ((qx + t * ux - ax) / dx) if abs(dx) > abs(dy) else \
            ((qy + t * uy - ay) / dy)
        if 0.0 <= s < 1.0:
            ts.append(t)
    ts.sort()
    return [(ts[k], ts[k + 1]) for k in range(0, len(ts) - 1, 2)]


def ribbon_widths(stroke, pas, lim):
    """(point_index, rendered_width) at interior points of a stroke."""
    segs = [(sub[k], sub[k + 1]) for pa in pas for sub in pa["subs"]
            for k in range(len(sub) - 1)]
    segs += [(sub[-1], sub[0]) for pa in pas for sub in pa["subs"]]
    out = []
    for j in range(3, len(stroke.x) - 3):
        dx = stroke.x[j + 1] - stroke.x[j - 1]
        dy = stroke.y[j + 1] - stroke.y[j - 1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        ivs = parity_intervals(stroke.x[j], stroke.y[j],
                               -dy / length, dx / length, segs, lim)
        cont = next((b - a for a, b in ivs if a <= 0 <= b), None)
        if cont:
            out.append((j, cont))
    return out


def remarkable():
    print("== reMarkable: official SVG export vs stored WIDTH ==")
    rm = CAL / ("remarkable-calibration/6edcf8a3-b8fb-448b-bb5d-8438237d2253/"
                "bb22b72b-64cf-41f0-a395-f43c523e7094.rm")
    strokes = list(reader_for(rm).read(rm).pages[0].strokes())
    paths = svg_paths(CAL / "remarkable-calibration-svg/Calibration - page 1.svg")

    stroked = [pa for pa in paths if "stroke-width" in pa["attrs"]]
    grouped = match_paths(stroked, strokes)
    exact = total = 0
    for si, pas in sorted(grouped.items()):
        w = strokes[si].channels[ir.Channel.WIDTH]
        for pa in pas:
            total += 1
            exact += float(pa["attrs"]["stroke-width"]) == median(w)
    fams = {strokes[si].tool.family.value for si in grouped}
    print(f"  stroked paths ({', '.join(sorted(fams))}): "
          f"stroke-width == stored WIDTH exactly on {exact}/{total}")

    filled = [pa for pa in paths if "stroke-width" not in pa["attrs"]]
    pairs = defaultdict(list)
    for si, pas in match_paths(filled, strokes).items():
        s = strokes[si]
        w = s.channels.get(ir.Channel.WIDTH)
        if not w:
            continue
        for j, rw in ribbon_widths(s, pas, lim=60):
            if w[j] > 0.1:
                pairs[s.tool.family.value].append(rw / w[j])
    for fam, rs in sorted(pairs.items()):
        rs.sort()
        print(f"  {fam:18s} n={len(rs):4d} rendered/stored "
              f"median={median(rs):.3f} IQR[{rs[len(rs)//4]:.3f},"
              f"{rs[3*len(rs)//4]:.3f}]")

    # calligraphy direction fit
    rows = []
    for s in strokes:
        if s.tool.family.value != "calligraphy":
            continue
        w = s.channels[ir.Channel.WIDTH]
        p = s.channels[ir.Channel.PRESSURE]
        ts = s.tool.native.params.get("thickness_scale", 1.0)
        for j in range(1, len(s.x) - 1):
            dx, dy = s.x[j + 1] - s.x[j - 1], s.y[j + 1] - s.y[j - 1]
            if math.hypot(dx, dy) < 1e-6:
                continue
            rows.append((w[j] / ts, math.atan2(dy, dx), p[j]))
    best = None
    for deg in range(0, 180, 2):
        t0 = math.radians(deg)
        X = [(1.0, abs(math.sin(a - t0)), pp) for _, a, pp in rows]
        Y = [wv for wv, _, _ in rows]
        coef = _lstsq(X, Y)
        pred = [sum(c * x[i] for i, c in enumerate(coef)) for x in X]
        ssr = sum((a - b) ** 2 for a, b in zip(Y, pred))
        my = mean(Y)
        r2 = 1 - ssr / sum((y - my) ** 2 for y in Y)
        if best is None or r2 > best[0]:
            best = (r2, deg, coef)
    r2, deg, coef = best
    print(f"  calligraphy fit: w/ts = {coef[0]:.3f} "
          f"{coef[1]:+.3f}*|sin(theta-{deg}deg)| {coef[2]:+.3f}*p  "
          f"R2={r2:.3f}  (n={len(rows)})")


def _lstsq(X, Y):
    k = len(X[0])
    A = [[sum(x[i] * x[j] for x in X) for j in range(k)] for i in range(k)]
    B = [sum(x[i] * y for x, y in zip(X, Y)) for i in range(k)]
    for i in range(k):
        piv = A[i][i]
        for j in range(i + 1, k):
            f = A[j][i] / piv
            A[j] = [a - f * b for a, b in zip(A[j], A[i])]
            B[j] -= f * B[i]
    coef = [0.0] * k
    for i in reversed(range(k)):
        coef[i] = (B[i] - sum(A[i][j] * coef[j] for j in range(i + 1, k))) / A[i][i]
    return coef


def nebo():
    print("== Nebo: app SVG export vs force channel ==")
    doc = reader_for(CAL / "nebo-calibration.nebo").read(CAL / "nebo-calibration.nebo")
    strokes = list(doc.pages[0].strokes())
    paths = svg_paths(CAL / "nebo-calibration.app-export.svg")
    grouped = match_paths(paths, strokes)
    samples = []
    for si, pas in grouped.items():
        s = strokes[si]
        f = s.channels.get(ir.Channel.PRESSURE)
        if not f:
            continue
        meas = ribbon_widths(s, pas, lim=12)
        # thin-pen rows only: drop the ~5 mm brush/highlighter strokes
        # entirely (their thin end-sections would contaminate the fit)
        if not meas or median(rw for _, rw in meas) > 2.0:
            continue
        samples.extend((f[j], rw) for j, rw in meas if rw < 2.0)
    bins = defaultdict(list)
    for ff, rw in samples:
        bins[round(ff * 5) / 5].append(rw / 0.25)
    print("  rendered/base (0.25 mm pens) by force bin:")
    for b in sorted(bins):
        print(f"    f~{b:.1f}: n={len(bins[b]):4d} median={median(bins[b]):.2f}")
    fs = [f for f, _ in samples]
    rs = [r for _, r in samples]
    mf, mr = mean(fs), mean(rs)
    slope = sum((a - mf) * (b - mr) for a, b in zip(fs, rs)) / \
        sum((a - mf) ** 2 for a in fs)
    a0 = mr - slope * mf
    print(f"  linear fit: rendered = {a0:.4f} + {slope:.4f}*force (mm) "
          f"-> rendered/base = {a0/0.25:.2f} + {slope/0.25:.2f}*force; "
          f"pivot at force {(0.25 - a0)/slope:.2f}")


if __name__ == "__main__":
    remarkable()
    nebo()
