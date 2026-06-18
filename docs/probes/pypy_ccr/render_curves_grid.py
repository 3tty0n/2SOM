#!/usr/bin/env python3
"""Render ALL per-benchmark warmup curves (N=50) from geomean_warmup.json:
stock vs CCR loopgate (K=0) vs CCR + loop-amortization gate (K=18).  No
re-measurement -- reads the saved data.  Also prints a per-benchmark cold/steady
table and the categorisation used in the analysis.
"""
import json, os, subprocess, sys, math

HERE = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr"
IN = HERE + "/geomean_warmup.json"
OUT = HERE + "/all_curves"
N = 50
COL = {"stock": "#9aa0a6", "B1old": "#ea4335", "B1new": "#1a73e8"}
NAME = {"stock": "stock", "B1old": "CCR loopgate (K=0)", "B1new": "CCR + amortization gate (K=18)"}
DEVNULL = open(os.devnull, "w")
PW, PH, ML, MB, MT, MR, COLS, GAP = 300, 196, 46, 28, 32, 10, 4, 16


def nice(v):
    if v <= 0:
        return 1.0
    e = math.floor(math.log10(v)); base = 10 ** e
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if m * base >= v:
            return m * base
    return 10 * base


def panel(ox, oy, bench, cur):
    ms = {c: [t * 1000 for t in cur[c]] for c in cur}
    px0, py0 = ox + ML, oy + MT
    pw, ph = PW - ML - MR, PH - MT - MB
    ymax = nice(max(max(v) for v in ms.values()) * 1.04)
    X = lambda i: px0 + pw * i / float(N - 1)
    Y = lambda v: py0 + ph - ph * v / ymax
    s = ['<rect x="%g" y="%g" width="%g" height="%g" fill="#fff" stroke="#ddd"/>' % (px0, py0, pw, ph)]
    for t in range(3):
        yv = ymax * t / 2.0; yy = Y(yv)
        s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eee"/>' % (px0, yy, px0 + pw, yy))
        s.append('<text x="%g" y="%g" font-size="8" fill="#999" text-anchor="end">%g</text>' % (px0 - 4, yy + 3, yv))
    for i in (0, 5, 10, 20, 49):
        s.append('<text x="%g" y="%g" font-size="8" fill="#999" text-anchor="middle">%d</text>' % (X(i), py0 + ph + 12, i))
    # ratios
    co = ms["B1old"][0] / ms["stock"][0]; cn = ms["B1new"][0] / ms["stock"][0]
    so = min(ms["B1old"]) / min(ms["stock"]); sn = min(ms["B1new"]) / min(ms["stock"])
    s.append('<text x="%g" y="%g" font-size="11" font-weight="bold" fill="#222">%s</text>' % (px0, oy + 13, bench))
    # color cold winner: red if loopgate clearly better, blue if amortization better
    cc = "#ea4335" if co < cn - 0.02 else ("#1a73e8" if cn < co - 0.02 else "#555")
    s.append('<text x="%g" y="%g" font-size="8.5" fill="%s" text-anchor="end">cold L%.2f A%.2f</text>' % (px0 + pw, oy + 8, cc, co, cn))
    sc = "#ea4335" if so < sn - 0.02 else ("#1a73e8" if sn < so - 0.02 else "#555")
    s.append('<text x="%g" y="%g" font-size="8.5" fill="%s" text-anchor="end">steady L%.2f A%.2f</text>' % (px0 + pw, oy + 18, sc, so, sn))
    for cfg in ("stock", "B1old", "B1new"):
        v = ms[cfg]
        pts = " ".join("%g,%g" % (X(i), Y(v[i])) for i in range(N))
        w = 1.4 if cfg == "stock" else 1.8
        s.append('<polyline fill="none" stroke="%s" stroke-width="%g" points="%s" opacity="%s"/>' % (COL[cfg], w, pts, "0.75" if cfg == "stock" else "1"))
    return "\n".join(s)


def main():
    data = json.load(open(IN))["data"]
    benches = sorted(data.keys())
    rows = (len(benches) + COLS - 1) // COLS
    W = COLS * PW + (COLS - 1) * GAP + 20
    H = rows * PH + (rows - 1) * GAP + 70
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="DejaVu Sans, Arial, sans-serif">' % (W, H)]
    s.append('<rect width="%d" height="%d" fill="#fafafa"/>' % (W, H))
    s.append('<text x="14" y="24" font-size="17" font-weight="bold" fill="#111">All warmup curves (N=50): stock vs CCR loopgate vs CCR + amortization gate (%d benchmarks)</text>' % len(benches))
    s.append('<text x="14" y="42" font-size="11" fill="#555">L = loopgate-only ratio,  A = +amortization-gate ratio (both vs stock; &lt;1 = faster). Per-panel y in ms, x = iteration (0 = cold).</text>')
    lx = 14
    for cfg in ("stock", "B1old", "B1new"):
        s.append('<rect x="%d" y="50" width="15" height="10" fill="%s"/>' % (lx, COL[cfg]))
        s.append('<text x="%d" y="59" font-size="11" fill="#333">%s</text>' % (lx + 19, NAME[cfg]))
        lx += 30 + 8 * len(NAME[cfg])
    for idx, b in enumerate(benches):
        rr, cc = idx // COLS, idx % COLS
        s.append(panel(10 + cc * (PW + GAP), 66 + rr * (PH + GAP), b, data[b]))
    s.append("</svg>")
    open(OUT + ".svg", "w").write("\n".join(s))
    try:
        subprocess.check_call(["inkscape", OUT + ".svg", "--export-type=pdf", "--export-filename=" + OUT + ".pdf"], stderr=DEVNULL, stdout=DEVNULL)
    except Exception:
        subprocess.check_call(["convert", "-density", "150", "-background", "white", OUT + ".svg", OUT + ".pdf"])
    subprocess.call(["inkscape", OUT + ".svg", "--export-type=png", "--export-dpi=110", "--export-filename=" + OUT + ".png"], stderr=DEVNULL, stdout=DEVNULL)

    # per-benchmark table + categorisation
    def r(b, cfg, idx0):
        st = data[b]["stock"]; v = data[b][cfg]
        return (v[0] / st[0]) if idx0 else (min(v) / min(st))
    print("%-16s | cold L  cold A | steady L steady A | verdict" % "bench")
    print("-" * 74)
    for b in benches:
        cL, cA = r(b, "B1old", 1), r(b, "B1new", 1)
        sL, sA = r(b, "B1old", 0), r(b, "B1new", 0)
        if cL < 0.95 and cA > cL + 0.05:
            v = "loopgate WINS cold; gate destroys it"
        elif cA < cL - 0.03 or sA < sL - 0.03:
            v = "amortization helps"
        else:
            v = "~neutral"
        print("%-16s | %.3f  %.3f | %.3f   %.3f | %s" % (b, cL, cA, sL, sA, v))
    print(OUT + ".pdf")


if __name__ == "__main__":
    main()
