"""Grouped per-tool channel summary across the calibration corpus."""
from pathlib import Path
from statistics import median, mean
from collections import defaultdict
from inkterop.formats import reader_for
from inkterop import ir

CAL = Path('../corpus/calibration')
FILES = {
    'remarkable': CAL / 'remarkable-calibration/6edcf8a3-b8fb-448b-bb5d-8438237d2253/bb22b72b-64cf-41f0-a395-f43c523e7094.rm',
    'saber': CAL / 'saber-calibration.sbn2',
    'notability': CAL / 'notability-calibration.ntb',
    'goodnotes': CAL / 'goodnotes-calibration.goodnotes',
    'nebo': CAL / 'nebo-calibration.nebo',
}

for name, path in FILES.items():
    doc = reader_for(path).read(path)
    groups = defaultdict(list)
    for page in doc.pages:
        for s in page.strokes():
            groups[s.tool.family.value].append(s)
    print(f'\n=== {name} ===')
    for fam, ss in sorted(groups.items()):
        ws, ps, aws, corr = [], [], [], []
        for s in ss:
            ch = s.channels or {}
            w = ch.get(ir.Channel.WIDTH); p = ch.get(ir.Channel.PRESSURE)
            if w: ws.extend(w)
            if p: ps.extend(p)
            if w and p and len(w) == len(p) and len(w) > 4:
                mw, mp = mean(w), mean(p)
                num = sum((a-mw)*(b-mp) for a, b in zip(w, p))
                dw = sum((a-mw)**2 for a in w) ** .5
                dp = sum((b-mp)**2 for b in p) ** .5
                if dw > 1e-9 and dp > 1e-9:
                    corr.append(num/(dw*dp))
            if getattr(s.appearance, 'width', None):
                aws.append(s.appearance.width)
        line = f'  {fam:18s} n={len(ss):3d}'
        if ws: line += f'  w[{min(ws):.2f},{median(ws):.2f},{max(ws):.2f}]'
        if aws: line += f'  const_w={sorted(set(round(a,2) for a in aws))}'
        if ps: line += f'  p[{min(ps):.2f},{max(ps):.2f}]'
        if corr: line += f'  corr(w,p)~{median(corr):+.2f} (n={len(corr)})'
        print(line)
