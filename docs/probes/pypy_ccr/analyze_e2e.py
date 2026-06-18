#!/usr/bin/env python2
"""Analyze bench_e2e.py output: stock vs CCR loopgate. Computes warmup-win
metrics (geomean of CCR/stock ratios) and renders all warmup curves.

Reads e2e_stock.json + e2e_ccr.json (raw[bench] = list of per-rep dicts with
iter_times / tracing / jit_total / wall_to_steady / running).
"""
import json, os, subprocess, math

HERE = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr"
STOCK = HERE + "/e2e_stock.json"
CCR = HERE + "/e2e_ccr.json"
OUT = HERE + "/e2e_warmup"
DEVNULL = open(os.devnull, "w")


def geo(xs):
    xs = [x for x in xs if x and x == x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")


def median(xs):
    xs = sorted(x for x in xs if x == x)
    n = len(xs)
    if not n:
        return float("nan")
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def emin_curve(reps):
    """element-wise min of iter_times across reps (denoise)."""
    series = [r["iter_times"] for r in reps if r.get("iter_times")]
    if not series:
        return None
    m = min(len(s) for s in series)
    return [min(s[i] for s in series) for i in range(m)]


def med_field(reps, key):
    return median([r[key] for r in reps if r.get(key) is not None and r.get(key) == r.get(key)])


def load(path):
    return json.load(open(path))["raw"]


def main():
    rs, rc = load(STOCK), load(CCR)
    benches = sorted(set(rs) & set(rc))
    rows = []
    for b in benches:
        cs, cc = emin_curve(rs[b]), emin_curve(rc[b])
        if not cs or not cc or len(cs) < 12 or len(cc) < 12:
            continue
        m = min(len(cs), len(cc))
        cs, cc = cs[:m], cc[:m]
        tail = max(4, m // 3)
        row = {
            "b": b, "stock": cs, "ccr": cc,
            "cold": cc[0] / cs[0],
            "w5": sum(cc[:5]) / sum(cs[:5]),
            "w10": sum(cc[:10]) / sum(cs[:10]),
            "steady": median(cc[-tail:]) / median(cs[-tail:]),
            "trace": med_field(rc[b], "tracing") / med_field(rs[b], "tracing"),
            "jit": med_field(rc[b], "jit_total") / med_field(rs[b], "jit_total"),
        }
        rows.append(row)

    print("%-16s %6s %6s %6s %7s %7s %7s" % ("bench", "cold", "w5", "w10", "steady", "trace", "jit"))
    for r in rows:
        print("%-16s %6.3f %6.3f %6.3f %7.3f %7.3f %7.3f" % (
            r["b"], r["cold"], r["w5"], r["w10"], r["steady"], r["trace"], r["jit"]))
    print("-" * 64)
    G = {}
    for k in ("cold", "w5", "w10", "steady", "trace", "jit"):
        G[k] = geo([r[k] for r in rows])
        print("%-16s %s = %.3f  (%+.1f%%)" % ("GEOMEAN", k, G[k], (G[k] - 1) * 100))
    render(rows, G)
    return rows, G


# ------------------------------ SVG curve grid ------------------------------
PW, PH, ML, MB, MT, MR, COLS, GAP = 300, 188, 46, 26, 30, 10, 4, 16
SC, CC = "#9aa0a6", "#1a73e8"


def nice(v):
    if v <= 0:
        return 1.0
    e = math.floor(math.log10(v)); base = 10 ** e
    for mm in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if mm * base >= v:
            return mm * base
    return 10 * base


def panel(ox, oy, r):
    n = len(r["stock"])
    s_ms = [t * 1000 for t in r["stock"]]
    c_ms = [t * 1000 for t in r["ccr"]]
    px0, py0 = ox + ML, oy + MT
    pw, ph = PW - ML - MR, PH - MT - MB
    ymax = nice(max(max(s_ms), max(c_ms)) * 1.04)
    X = lambda i: px0 + pw * i / float(n - 1)
    Y = lambda v: py0 + ph - ph * v / ymax
    s = ['<rect x="%g" y="%g" width="%g" height="%g" fill="#fff" stroke="#ddd"/>' % (px0, py0, pw, ph)]
    for t in range(3):
        yv = ymax * t / 2.0; yy = Y(yv)
        s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eee"/>' % (px0, yy, px0 + pw, yy))
        s.append('<text x="%g" y="%g" font-size="8" fill="#999" text-anchor="end">%g</text>' % (px0 - 4, yy + 3, yv))
    for i in (0, 5, 10, 20, n - 1):
        if 0 <= i < n:
            s.append('<text x="%g" y="%g" font-size="8" fill="#999" text-anchor="middle">%d</text>' % (X(i), py0 + ph + 11, i))
    s.append('<text x="%g" y="%g" font-size="11" font-weight="bold" fill="#222">%s</text>' % (px0, oy + 12, r["b"]))
    col = "#137333" if r["w10"] < 0.97 else ("#c5221f" if r["w10"] > 1.03 else "#555")
    s.append('<text x="%g" y="%g" font-size="9" fill="%s" text-anchor="end">w10 %.2f cold %.2f trace %.2f</text>' % (px0 + pw, oy + 12, col, r["w10"], r["cold"], r["trace"]))
    for v, c, w in ((s_ms, SC, 1.5), (c_ms, CC, 1.9)):
        pts = " ".join("%g,%g" % (X(i), Y(v[i])) for i in range(n))
        s.append('<polyline fill="none" stroke="%s" stroke-width="%g" points="%s"/>' % (c, w, pts))
    return "\n".join(s)


def render(rows, G):
    rows = sorted(rows, key=lambda r: r["w10"])
    nrows = (len(rows) + COLS - 1) // COLS
    W = COLS * PW + (COLS - 1) * GAP + 20
    H = nrows * PH + (nrows - 1) * GAP + 86
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="DejaVu Sans, Arial, sans-serif">' % (W, H)]
    s.append('<rect width="%d" height="%d" fill="#fafafa"/>' % (W, H))
    s.append('<text x="16" y="26" font-size="18" font-weight="bold" fill="#111">CCR loopgate warmup curves vs stock (bench_e2e, %d benchmarks, n=50, reps=5)</text>' % len(rows))
    s.append('<text x="16" y="48" font-size="13" fill="#137333" font-weight="bold">warmup geomean (CCR/stock): cold %.3f (%+.1f%%)  first-10 %.3f (%+.1f%%)  JIT-tracing %.3f (%+.1f%%)  |  steady %.3f (%+.1f%%)</text>' % (
        G["cold"], (G["cold"]-1)*100, G["w10"], (G["w10"]-1)*100, G["trace"], (G["trace"]-1)*100, G["steady"], (G["steady"]-1)*100))
    lx = 16
    for nm, c in (("stock", SC), ("CCR loopgate (2048:130:L)", CC)):
        s.append('<rect x="%d" y="58" width="15" height="10" fill="%s"/>' % (lx, c))
        s.append('<text x="%d" y="67" font-size="11" fill="#333">%s</text>' % (lx + 19, nm)); lx += 40 + 8 * len(nm)
    for idx, r in enumerate(rows):
        rr, cc = idx // COLS, idx % COLS
        s.append(panel(10 + cc * (PW + GAP), 76 + rr * (PH + GAP), r))
    s.append("</svg>")
    open(OUT + ".svg", "w").write("\n".join(s))
    try:
        subprocess.check_call(["inkscape", OUT + ".svg", "--export-type=pdf", "--export-filename=" + OUT + ".pdf"], stderr=DEVNULL, stdout=DEVNULL)
    except Exception:
        subprocess.check_call(["convert", "-density", "150", "-background", "white", OUT + ".svg", OUT + ".pdf"])
    subprocess.call(["inkscape", OUT + ".svg", "--export-type=png", "--export-dpi=110", "--export-filename=" + OUT + ".png"], stderr=DEVNULL, stdout=DEVNULL)


if __name__ == "__main__":
    main()
