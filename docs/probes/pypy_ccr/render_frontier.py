#!/usr/bin/env python2
"""Full-47 frontier figure: per-benchmark cold & steady ratios vs stock for
static CCR (wm0) and the dynamic controller (wm40), sorted by static cold."""
import json, os, subprocess, math
HERE = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr"
IN = HERE + "/warmup_all_wm0_wm40.json"
OUT = HERE + "/frontier_47"
DEVNULL = open(os.devnull, "w")
S, A = "#ea4335", "#1a73e8"   # static / adaptive


def geo(xs):
    xs = [x for x in xs if x == x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")


def col(ox, oy, w, h, title, key, rows):
    # rows: list of (bench, static_ratio, dyn_ratio); dot plot vs 1.0
    px0, py0 = ox + 150, oy + 26
    pw, ph = w - 160, h - 40
    vals = [r[1] for r in rows] + [r[2] for r in rows] + [1.0]
    lo, hi = min(vals) * 0.97, max(vals) * 1.02
    X = lambda v: px0 + pw * (v - lo) / (hi - lo)
    n = len(rows)
    rh = ph / float(n)
    s = ['<text x="%g" y="%g" font-size="14" font-weight="bold" fill="#111">%s</text>' % (ox + 6, oy + 16, title)]
    s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#888" stroke-width="1.3" stroke-dasharray="4,3"/>' % (X(1.0), py0, X(1.0), py0 + ph))
    s.append('<text x="%g" y="%g" font-size="9" fill="#888">1.0 (stock)</text>' % (X(1.0) - 14, py0 - 3))
    for grid in (0.6, 0.8, 1.2, 1.4):
        if lo < grid < hi:
            s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eee"/>' % (X(grid), py0, X(grid), py0 + ph))
            s.append('<text x="%g" y="%g" font-size="8" fill="#bbb">%.1f</text>' % (X(grid) - 6, py0 + ph + 10, grid))
    for i, (b, sv, av) in enumerate(rows):
        y = py0 + rh * (i + 0.5)
        s.append('<text x="%g" y="%g" font-size="9" fill="#333" text-anchor="end">%s</text>' % (px0 - 6, y + 3, b[:20]))
        s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#ddd"/>' % (min(X(sv), X(av)), y, max(X(sv), X(av)), y))
        s.append('<circle cx="%g" cy="%g" r="3" fill="%s"/>' % (X(sv), y, S))
        s.append('<circle cx="%g" cy="%g" r="3" fill="%s"/>' % (X(av), y, A))
    gs, ga = geo([r[1] for r in rows]), geo([r[2] for r in rows])
    s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="1.5"/>' % (X(gs), py0, X(gs), py0 + ph, S))
    s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="1.5"/>' % (X(ga), py0, X(ga), py0 + ph, A))
    s.append('<text x="%g" y="%g" font-size="10" font-weight="bold" fill="%s">geo %.3f (%+.1f%%)</text>' % (px0 + 4, py0 + ph + 22, S, gs, (gs - 1) * 100))
    s.append('<text x="%g" y="%g" font-size="10" font-weight="bold" fill="%s">geo %.3f (%+.1f%%)</text>' % (px0 + 4, py0 + ph + 36, A, ga, (ga - 1) * 100))
    return "\n".join(s)


def main():
    d = json.load(open(IN))["data"]
    rows = []
    for b, v in d.items():
        s0 = v["stock"]
        rows.append((b, v["wm0"]["curve"][0] / s0["curve"][0], v["wm40"]["curve"][0] / s0["curve"][0],
                     v["wm0"]["running"] / s0["running"], v["wm40"]["running"] / s0["running"]))
    rows.sort(key=lambda r: r[1])
    cold = [(r[0], r[1], r[2]) for r in rows]
    steady = [(r[0], r[3], r[4]) for r in sorted(rows, key=lambda r: r[3])]
    H = 40 + len(rows) * 17 + 50
    W = 1180
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="DejaVu Sans, Arial, sans-serif">' % (W, H)]
    s.append('<rect width="%d" height="%d" fill="#fafafa"/>' % (W, H))
    s.append('<text x="16" y="22" font-size="17" font-weight="bold" fill="#111">CCR on PyPy, full 47-benchmark suite: static (wm0) vs dynamic controller (wm40)</text>')
    lx = 16
    for c, nm in ((S, "static CCR (loopgate)"), (A, "dynamic (WORKMIN=40)")):
        s.append('<rect x="%d" y="30" width="14" height="10" fill="%s"/>' % (lx, c))
        s.append('<text x="%d" y="39" font-size="11" fill="#333">%s</text>' % (lx + 18, nm)); lx += 40 + 9 * len(nm)
    s.append(col(0, 48, W / 2, H - 56, "Cold (first-iteration) / stock", "cold", cold))
    s.append(col(W / 2, 48, W / 2, H - 56, "Steady (MAD) / stock", "steady", steady))
    s.append("</svg>")
    open(OUT + ".svg", "w").write("\n".join(s))
    try:
        subprocess.check_call(["inkscape", OUT + ".svg", "--export-type=pdf", "--export-filename=" + OUT + ".pdf"], stderr=DEVNULL, stdout=DEVNULL)
    except Exception:
        subprocess.check_call(["convert", "-density", "150", "-background", "white", OUT + ".svg", OUT + ".pdf"])
    subprocess.call(["inkscape", OUT + ".svg", "--export-type=png", "--export-dpi=110", "--export-filename=" + OUT + ".png"], stderr=DEVNULL, stdout=DEVNULL)
    print("wrote " + OUT + ".pdf")


if __name__ == "__main__":
    main()
