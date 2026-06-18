#!/usr/bin/env python2
"""Warmup curves for the aggressive profile-guided CCR set: the benchmarks where
a one-shot A/B profile enables CCR (cold < 0.98 and steady < 1.10).  Stock vs CCR
per-iteration curves from warmup_all_ctrl250.json."""
import json, os, subprocess, math
HERE = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr"
d = json.load(open(HERE + "/warmup_all_ctrl250.json"))["data"]
OUT = HERE + "/pgo_warmup"
DEVNULL = open(os.devnull, "w")
SC, CC = "#9aa0a6", "#1a73e8"
PW, PH, ML, MB, MT, MR, COLS, GAP = 300, 190, 46, 26, 30, 10, 4, 16


def nice(v):
    if v <= 0:
        return 1.0
    e = math.floor(math.log10(v)); base = 10 ** e
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if m * base >= v:
            return m * base
    return 10 * base


def panel(ox, oy, b, stock, ccr):
    n = min(len(stock), len(ccr))
    sm = [t * 1000 for t in stock[:n]]; cm = [t * 1000 for t in ccr[:n]]
    px0, py0 = ox + ML, oy + MT
    pw, ph = PW - ML - MR, PH - MT - MB
    ymax = nice(max(max(sm), max(cm)) * 1.04)
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
    cold = cm[0] / sm[0]; steady = min(cm) / min(sm)
    s.append('<text x="%g" y="%g" font-size="11" font-weight="bold" fill="#222">%s</text>' % (px0, oy + 12, b))
    s.append('<text x="%g" y="%g" font-size="9" fill="#137333" text-anchor="end">cold %.2f steady %.2f</text>' % (px0 + pw, oy + 12, cold, steady))
    for v, c, w in ((sm, SC, 1.5), (cm, CC, 1.9)):
        pts = " ".join("%g,%g" % (X(i), Y(v[i])) for i in range(n))
        s.append('<polyline fill="none" stroke="%s" stroke-width="%g" points="%s"/>' % (c, w, pts))
    return "\n".join(s)


def main():
    rows = []
    for b, v in d.items():
        c = v["static"]["curve"][0] / v["stock"]["curve"][0]
        st = v["static"]["running"] / v["stock"]["running"]
        if c < 0.98 and st < 1.10:
            rows.append((b, c, v["stock"]["curve"], v["static"]["curve"]))
    rows.sort(key=lambda r: r[1])
    nr = (len(rows) + COLS - 1) // COLS
    W = COLS * PW + (COLS - 1) * GAP + 20
    H = nr * PH + (nr - 1) * GAP + 64
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="DejaVu Sans, Arial, sans-serif">' % (W, H)]
    s.append('<rect width="%d" height="%d" fill="#fafafa"/>' % (W, H))
    s.append('<text x="14" y="24" font-size="17" font-weight="bold" fill="#111">Profile-guided CCR (aggressive): warmup curves of the %d enabled benchmarks</text>' % len(rows))
    lx = 14
    for nm, col in (("stock", SC), ("CCR (2048:130:L)", CC)):
        s.append('<rect x="%d" y="34" width="15" height="10" fill="%s"/>' % (lx, col))
        s.append('<text x="%d" y="43" font-size="11" fill="#333">%s</text>' % (lx + 19, nm)); lx += 40 + 8 * len(nm)
    for idx, (b, c, sc, cc) in enumerate(rows):
        rr, ccol = idx // COLS, idx % COLS
        s.append(panel(10 + ccol * (PW + GAP), 52 + rr * (PH + GAP), b, sc, cc))
    s.append("</svg>")
    open(OUT + ".svg", "w").write("\n".join(s))
    try:
        subprocess.check_call(["inkscape", OUT + ".svg", "--export-type=pdf", "--export-filename=" + OUT + ".pdf"], stderr=DEVNULL, stdout=DEVNULL)
    except Exception:
        subprocess.check_call(["convert", "-density", "150", "-background", "white", OUT + ".svg", OUT + ".pdf"])
    subprocess.call(["inkscape", OUT + ".svg", "--export-type=png", "--export-dpi=110", "--export-filename=" + OUT + ".png"], stderr=DEVNULL, stdout=DEVNULL)
    print("enabled (%d): %s" % (len(rows), ", ".join(r[0] for r in rows)))


if __name__ == "__main__":
    main()
