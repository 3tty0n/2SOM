#!/usr/bin/env python3
"""Warmup measurement for the two slowest own/ benchmarks, stock vs B1.

  bm_krakatau: a real cold warmup curve -- time each decompileClass() call from
               a cold process (the benchmark itself discards 30 warmup iters).
  bm_icbd:     a relaunched cold subprocess batch (analyze_all, ~33 s, no
               intra-process loop), so there is no warmup curve -- we report the
               cold-batch time stock vs B1 as two bars.
"""
import json, os, subprocess, sys, math

PYPY = "/home/yusuke/src/github.com/pypy/pypy/pypy/goal/pypy-c"
OWN = "/home/yusuke/src/foss.heptapod.net/pypy/benchmarks/own"
KDRV = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr/warmcurve_krakatau.py"
OUT = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr/slow_warmup"

KN, KR = 40, 2     # krakatau: 40 decompileClass calls, best of 2 cold processes
IR = 3             # icbd: min over 3 cold-batch launches
CFGS = [("stock", {}),
        ("B1", {"PYPYTIER_PROMOTE": "2048", "PYPYTIER_MINSIZE": "130",
                "PYPYTIER_LOOPGATE": "1"})]


def env_for(extra):
    env = dict(os.environ)
    for k in ("PYPYLOG", "PYPYTIER_PROMOTE", "PYPYTIER_MINSIZE", "PYPYTIER_LOOPGATE"):
        env.pop(k, None)
    env.update(extra)
    return env


def floats(out):
    r = []
    for line in out.split():
        try:
            r.append(float(line))
        except ValueError:
            pass
    return r


def krakatau_curve(extra):
    runs = []
    for _ in range(KR):
        out = subprocess.check_output([PYPY, KDRV, str(KN)], cwd=OWN, env=env_for(extra))
        runs.append(floats(out))
    m = min(len(x) for x in runs)
    return [min(run[i] for run in runs) for i in range(m)]


def icbd_batch(extra):
    best = None
    for _ in range(IR):
        out = subprocess.check_output([PYPY, "bm_icbd.py", "-n", "1"], cwd=OWN, env=env_for(extra))
        fs = floats(out)
        if fs:
            t = fs[0]
            best = t if best is None else min(best, t)
    return best


def collect():
    d = {"krakatau": {}, "icbd": {}}
    for cfg, extra in CFGS:
        d["krakatau"][cfg] = krakatau_curve(extra)
        sys.stderr.write("krakatau/%s cold=%.3f steady=%.3f\n"
                         % (cfg, d["krakatau"][cfg][0], min(d["krakatau"][cfg])))
        d["icbd"][cfg] = icbd_batch(extra)
        sys.stderr.write("icbd/%s = %.3f\n" % (cfg, d["icbd"][cfg]))
    return d


# ----------------------------- SVG -----------------------------
COL = {"stock": "#9aa0a6", "B1": "#1a73e8"}
PW, PH = 430, 300
ML, MB, MT, MR = 58, 46, 34, 14


def nice(v):
    if v <= 0:
        return 1.0
    e = math.floor(math.log10(v)); base = 10 ** e
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if m * base >= v:
            return m * base
    return 10 * base


def line_panel(ox, oy, title, curves):
    ms = {c: [t * 1000.0 for t in v] for c, v in curves.items()}
    nmax = max(len(v) for v in ms.values())
    ymax = nice(max(max(v) for v in ms.values()) * 1.05)
    px0, py0 = ox + ML, oy + MT
    pw, ph = PW - ML - MR, PH - MT - MB
    X = lambda i: px0 + pw * i / float(max(1, nmax - 1))
    Y = lambda v: py0 + ph - ph * v / ymax
    s = ['<rect x="%g" y="%g" width="%g" height="%g" fill="#fff" stroke="#ddd"/>' % (px0, py0, pw, ph)]
    for k in range(5):
        yv = ymax * k / 4.0; yy = Y(yv)
        s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eee"/>' % (px0, yy, px0 + pw, yy))
        s.append('<text x="%g" y="%g" font-size="10" fill="#666" text-anchor="end">%g</text>' % (px0 - 5, yy + 3, yv))
    step = 5 if nmax > 12 else 2
    for i in range(0, nmax, step):
        s.append('<text x="%g" y="%g" font-size="10" fill="#666" text-anchor="middle">%d</text>' % (X(i), py0 + ph + 14, i))
    s.append('<text x="%g" y="%g" font-size="11" fill="#444" text-anchor="middle">decompileClass() call index (0 = cold)</text>' % (px0 + pw / 2.0, py0 + ph + 32))
    s.append('<text x="%g" y="%g" font-size="11" fill="#444" text-anchor="middle" transform="rotate(-90 %g %g)">time / call (ms)</text>' % (ox + 14, py0 + ph / 2.0, ox + 14, py0 + ph / 2.0))
    s.append('<text x="%g" y="%g" font-size="13" font-weight="bold" fill="#222">%s</text>' % (px0, oy + 22, title))
    cold = ms["B1"][0] / ms["stock"][0]; steady = min(ms["B1"]) / min(ms["stock"])
    s.append('<text x="%g" y="%g" font-size="11" fill="#1a73e8" text-anchor="end">cold x%.2f  steady x%.2f</text>' % (px0 + pw - 2, oy + 22, cold, steady))
    for cfg in ("stock", "B1"):
        v = ms[cfg]
        pts = " ".join("%g,%g" % (X(i), Y(v[i])) for i in range(len(v)))
        s.append('<polyline fill="none" stroke="%s" stroke-width="2.2" points="%s"/>' % (COL[cfg], pts))
        for i in range(len(v)):
            s.append('<circle cx="%g" cy="%g" r="2.2" fill="%s"/>' % (X(i), Y(v[i]), COL[cfg]))
    return "\n".join(s)


def bar_panel(ox, oy, title, vals):
    ymax = nice(max(vals.values()) * 1.15)
    px0, py0 = ox + ML, oy + MT
    pw, ph = PW - ML - MR, PH - MT - MB
    Y = lambda v: py0 + ph - ph * v / ymax
    s = ['<rect x="%g" y="%g" width="%g" height="%g" fill="#fff" stroke="#ddd"/>' % (px0, py0, pw, ph)]
    for k in range(5):
        yv = ymax * k / 4.0; yy = Y(yv)
        s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eee"/>' % (px0, yy, px0 + pw, yy))
        s.append('<text x="%g" y="%g" font-size="10" fill="#666" text-anchor="end">%g</text>' % (px0 - 5, yy + 3, yv))
    s.append('<text x="%g" y="%g" font-size="13" font-weight="bold" fill="#222">%s</text>' % (px0, oy + 22, title))
    ratio = vals["B1"] / vals["stock"]
    s.append('<text x="%g" y="%g" font-size="11" fill="#1a73e8" text-anchor="end">x%.3f</text>' % (px0 + pw - 2, oy + 22, ratio))
    bw = 70
    for j, cfg in enumerate(("stock", "B1")):
        cx = px0 + pw * (0.32 + 0.36 * j)
        h = ph - (Y(vals[cfg]) - py0)
        s.append('<rect x="%g" y="%g" width="%g" height="%g" fill="%s"/>' % (cx - bw / 2, Y(vals[cfg]), bw, h, COL[cfg]))
        s.append('<text x="%g" y="%g" font-size="11" fill="#222" text-anchor="middle">%s</text>' % (cx, py0 + ph + 16, cfg))
        s.append('<text x="%g" y="%g" font-size="11" font-weight="bold" fill="#222" text-anchor="middle">%.1f s</text>' % (cx, Y(vals[cfg]) - 5, vals[cfg]))
    s.append('<text x="%g" y="%g" font-size="11" fill="#444" text-anchor="middle">cold-batch wall time (single subprocess)</text>' % (px0 + pw / 2.0, py0 + ph + 34))
    s.append('<text x="%g" y="%g" font-size="11" fill="#444" text-anchor="middle" transform="rotate(-90 %g %g)">seconds</text>' % (ox + 14, py0 + ph / 2.0, ox + 14, py0 + ph / 2.0))
    return "\n".join(s)


def render(d):
    W = 2 * PW + 28 + 20
    H = PH + 70
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="DejaVu Sans, Arial, sans-serif">' % (W, H)]
    s.append('<rect width="%d" height="%d" fill="#fafafa"/>' % (W, H))
    s.append('<text x="14" y="26" font-size="16" font-weight="bold" fill="#111">Slowest own/ benchmarks: stock vs B1 (2048:130:L)</text>')
    lx = 14
    for cfg, name in (("stock", "stock PyPy"), ("B1", "B1 2048:130:L")):
        s.append('<rect x="%d" y="36" width="14" height="10" fill="%s"/>' % (lx, COL[cfg]))
        s.append('<text x="%d" y="45" font-size="11" fill="#333">%s</text>' % (lx + 18, name)); lx += 130
    s.append(line_panel(10, 52, "bm_krakatau  (warmup curve)", d["krakatau"]))
    s.append(bar_panel(10 + PW + 28, 52, "bm_icbd  (cold batch, no curve)", d["icbd"]))
    s.append("</svg>")
    return "\n".join(s)


def main():
    d = collect()
    json.dump(d, open(OUT + ".json", "w"))
    open(OUT + ".svg", "w").write(render(d))
    try:
        subprocess.check_call(["inkscape", OUT + ".svg", "--export-type=pdf",
                               "--export-filename=" + OUT + ".pdf"],
                              stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    except Exception:
        subprocess.check_call(["convert", "-density", "150", "-background", "white", OUT + ".svg", OUT + ".pdf"])
    # also a PNG for quick preview
    subprocess.call(["inkscape", OUT + ".svg", "--export-type=png", "--export-dpi=120",
                     "--export-filename=" + OUT + ".png"], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    print(OUT + ".pdf")


if __name__ == "__main__":
    main()
