#!/usr/bin/env python3
"""Measure cold warmup curves (stock vs B1 2048:130:L) and render an SVG/PNG.

For each benchmark we run warmcurve_driver.py under pypy-c R times and take the
element-wise minimum across repeats (best cold time at each iteration index),
then draw time-per-iteration vs iteration index for stock and B1 side by side.
"""
import os, subprocess, sys, math

PYPY = "/home/yusuke/src/github.com/pypy/pypy/pypy/goal/pypy-c"
OWN = "/home/yusuke/src/foss.heptapod.net/pypy/benchmarks/own"
DRV = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr/warmcurve_driver.py"
OUT = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr/warmup_curve"

# bench -> (label, N iterations, R repeats)
BENCHES = [
    ("go",      "go (UCT search / move)  — warmup win",     20, 7),
    ("chaos",   "chaos (5000 transform pts)  — cold win / steady cost", 25, 7),
    ("bm_mdp",  "bm_mdp (MDP solve)  — control (parity)", 12, 5),
    ("nbody",   "nbody_modified  — control (parity)",   30, 7),
]
CFGS = [
    ("stock", {}),
    ("B1",    {"PYPYTIER_PROMOTE": "2048", "PYPYTIER_MINSIZE": "130",
               "PYPYTIER_LOOPGATE": "1"}),
]


def run_once(bench, n, extra):
    env = dict(os.environ)
    for k in ("PYPYLOG", "PYPYTIER_PROMOTE", "PYPYTIER_MINSIZE", "PYPYTIER_LOOPGATE"):
        env.pop(k, None)
    env.update(extra)
    out = subprocess.check_output([PYPY, DRV, bench, str(n)], cwd=OWN, env=env)
    return [float(x) for x in out.split()]


def measure(bench, n, r, extra):
    runs = [run_once(bench, n, extra) for _ in range(r)]
    m = min(len(x) for x in runs)
    return [min(run[i] for run in runs) for i in range(m)]


def collect():
    data = {}
    for bench, label, n, r in BENCHES:
        data[bench] = {}
        for cfg, extra in CFGS:
            data[bench][cfg] = measure(bench, n, r, extra)
            sys.stderr.write("%s/%s done\n" % (bench, cfg))
    return data


# ----------------------------- SVG rendering -----------------------------
PANEL_W, PANEL_H = 430, 280
MARGIN_L, MARGIN_B, MARGIN_T, MARGIN_R = 62, 44, 34, 16
COLS = 2
GAP = 28
COL = {"stock": "#9aa0a6", "B1": "#1a73e8"}


def nice_ceiling(v):
    if v <= 0:
        return 1.0
    e = math.floor(math.log10(v))
    base = 10 ** e
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if m * base >= v:
            return m * base
    return 10 * base


def panel_svg(ox, oy, label, curves):
    ms = {c: [t * 1000.0 for t in v] for c, v in curves.items()}
    nmax = max(len(v) for v in ms.values())
    ymax = nice_ceiling(max(max(v) for v in ms.values()) * 1.05)
    px0, py0 = ox + MARGIN_L, oy + MARGIN_T
    pw = PANEL_W - MARGIN_L - MARGIN_R
    ph = PANEL_H - MARGIN_T - MARGIN_B

    def X(i):
        return px0 + (pw * i / float(max(1, nmax - 1)))

    def Y(v):
        return py0 + ph - (ph * v / ymax)

    s = []
    s.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#fff" stroke="#ddd"/>'
             % (px0, py0, pw, ph))
    # y gridlines + labels
    for k in range(5):
        yv = ymax * k / 4.0
        yy = Y(yv)
        s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eee"/>'
                 % (px0, yy, px0 + pw, yy))
        s.append('<text x="%g" y="%g" font-size="10" fill="#666" text-anchor="end">%s</text>'
                 % (px0 - 6, yy + 3, ("%g" % yv)))
    # x ticks
    step = 5 if nmax > 12 else 2
    for i in range(0, nmax, step):
        xx = X(i)
        s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#f0f0f0"/>'
                 % (xx, py0, xx, py0 + ph))
        s.append('<text x="%g" y="%g" font-size="10" fill="#666" text-anchor="middle">%d</text>'
                 % (xx, py0 + ph + 14, i))
    # axis titles
    s.append('<text x="%g" y="%g" font-size="11" fill="#444" text-anchor="middle">iteration (0 = cold)</text>'
             % (px0 + pw / 2.0, py0 + ph + 32))
    s.append('<text x="%g" y="%g" font-size="11" fill="#444" text-anchor="middle" transform="rotate(-90 %g %g)">time / iter (ms)</text>'
             % (ox + 14, py0 + ph / 2.0, ox + 14, py0 + ph / 2.0))
    s.append('<text x="%g" y="%g" font-size="13" font-weight="bold" fill="#222">%s</text>'
             % (px0, oy + 22, label))
    # curves
    for cfg in ("stock", "B1"):
        v = ms[cfg]
        pts = " ".join("%g,%g" % (X(i), Y(v[i])) for i in range(len(v)))
        s.append('<polyline fill="none" stroke="%s" stroke-width="2.2" points="%s"/>'
                 % (COL[cfg], pts))
        for i in range(len(v)):
            s.append('<circle cx="%g" cy="%g" r="2.4" fill="%s"/>'
                     % (X(i), Y(v[i]), COL[cfg]))
    # cold + steady ratio annotation
    cold = ms["B1"][0] / ms["stock"][0]
    st_stock = min(ms["stock"]); st_b1 = min(ms["B1"])
    steady = st_b1 / st_stock
    s.append('<text x="%g" y="%g" font-size="10.5" fill="#1a73e8" text-anchor="end">cold x%.2f  steady x%.2f</text>'
             % (px0 + pw - 4, py0 + 14, cold, steady))
    return "\n".join(s)


def render(data):
    rows = (len(BENCHES) + COLS - 1) // COLS
    W = COLS * PANEL_W + (COLS - 1) * GAP + 20
    H = rows * PANEL_H + (rows - 1) * GAP + 70
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
         'font-family="DejaVu Sans, Arial, sans-serif">' % (W, H)]
    s.append('<rect width="%d" height="%d" fill="#fafafa"/>' % (W, H))
    s.append('<text x="14" y="26" font-size="16" font-weight="bold" fill="#111">'
             'PyPy cold warmup curves: stock vs B1 (PROMOTE=2048, MINSIZE=130, LOOPGATE)</text>')
    # legend
    lx = 14
    for cfg, name in (("stock", "stock PyPy"), ("B1", "B1 2048:130:L")):
        s.append('<rect x="%d" y="36" width="14" height="10" fill="%s"/>' % (lx, COL[cfg]))
        s.append('<text x="%d" y="45" font-size="11" fill="#333">%s</text>' % (lx + 18, name))
        lx += 130
    for idx, (bench, label, n, r) in enumerate(BENCHES):
        rr, cc = idx // COLS, idx % COLS
        ox = 10 + cc * (PANEL_W + GAP)
        oy = 56 + rr * (PANEL_H + GAP)
        s.append(panel_svg(ox, oy, label, data[bench]))
    s.append("</svg>")
    return "\n".join(s)


def main():
    data = collect()
    svg = render(data)
    with open(OUT + ".svg", "w") as f:
        f.write(svg)
    # SVG -> PNG
    try:
        subprocess.check_call(["inkscape", OUT + ".svg",
                               "--export-type=pdf",
                               "--export-filename=" + OUT + ".pdf"],
                              stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    except Exception:
        subprocess.check_call(["convert", "-density", "150", "-background", "white",
                               OUT + ".svg", OUT + ".pdf"])
    print(OUT + ".pdf")


if __name__ == "__main__":
    main()
