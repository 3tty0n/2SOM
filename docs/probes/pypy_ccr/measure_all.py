#!/usr/bin/env python3
"""Measure per-run (warmup) curves for ALL runnable own/ benchmarks, stock vs
B1 (2048:130:L), and render a grid + dump raw JSON.

Each benchmark is run as `pypy-c <bench>.py -n N`, which prints N per-run times
(run 0 = cold, includes JIT tracing for benchmarks that do not pre-warm).  We
take the element-wise min across R cold-process repeats.  N/R are tuned per
benchmark by cost so the slow ones stay bounded.
"""
import json, os, subprocess, sys, math

PYPY = "/home/yusuke/src/github.com/pypy/pypy/pypy/goal/pypy-c"
OWN = "/home/yusuke/src/foss.heptapod.net/pypy/benchmarks/own"
OUT = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr/own_all"

CFGS = [("stock", {}),
        ("B1", {"PYPYTIER_PROMOTE": "2048", "PYPYTIER_MINSIZE": "130",
                "PYPYTIER_LOOPGATE": "1"})]

# (bench, N, R) tuned by per-run cost; pre-warm noted where it hides the cold run
FAST = "chaos crypto_pyaes deltablue float hof_mono meteor-contest nbody_modified " \
       "p5_micro p5_micro2 p5_micro3 p5_micro4 raytrace-simple spectral-norm telco".split()
MED = "bm_gzip fannkuch fib go json_bench p5_jsonruns pyflate-fast pypy_interp".split()
BIG = "gcbench sqlitesynth spitfire".split()
SLOW = "bm_mdp hexiom2 nqueens pidigits".split()
HUGE = ["bm_icbd"]

PLAN = ([(b, 15, 4) for b in FAST] + [(b, 12, 3) for b in MED] +
        [(b, 8, 3) for b in BIG] + [(b, 4, 2) for b in SLOW] +
        [(b, 3, 1) for b in HUGE])
PLAN.sort(key=lambda x: x[0])


def run_once(bench, n, extra):
    env = dict(os.environ)
    for k in ("PYPYLOG", "PYPYTIER_PROMOTE", "PYPYTIER_MINSIZE", "PYPYTIER_LOOPGATE"):
        env.pop(k, None)
    env.update(extra)
    p = subprocess.Popen([PYPY, bench + ".py", "-n", str(n)], cwd=OWN,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    out, err = p.communicate()
    ts = []
    for line in out.split():
        try:
            ts.append(float(line))
        except ValueError:
            pass
    return ts


def measure(bench, n, r, extra):
    runs = [run_once(bench, n, extra) for _ in range(r)]
    runs = [x for x in runs if x]
    if not runs:
        return None
    m = min(len(x) for x in runs)
    return [min(run[i] for run in runs) for i in range(m)]


def collect():
    data = {}
    for bench, n, r in PLAN:
        d = {}
        ok = True
        for cfg, extra in CFGS:
            cur = measure(bench, n, r, extra)
            if cur is None:
                ok = False
                break
            d[cfg] = cur
        if ok:
            data[bench] = d
            cold = d["B1"][0] / d["stock"][0]
            steady = min(d["B1"]) / min(d["stock"])
            sys.stderr.write("%-16s cold x%.3f steady x%.3f\n" % (bench, cold, steady))
        else:
            sys.stderr.write("%-16s FAILED\n" % bench)
    return data


# ----------------------------- SVG grid -----------------------------
PW, PH = 300, 200
ML, MB, MT, MR = 48, 30, 30, 10
COLS = 5
GAP = 16
COL = {"stock": "#9aa0a6", "B1": "#1a73e8"}


def nice(v):
    if v <= 0:
        return 1.0
    e = math.floor(math.log10(v)); base = 10 ** e
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if m * base >= v:
            return m * base
    return 10 * base


def panel(ox, oy, bench, curves):
    ms = {c: [t * 1000.0 for t in v] for c, v in curves.items()}
    nmax = max(len(v) for v in ms.values())
    ymax = nice(max(max(v) for v in ms.values()) * 1.05)
    px0, py0 = ox + ML, oy + MT
    pw, ph = PW - ML - MR, PH - MT - MB
    X = lambda i: px0 + pw * i / float(max(1, nmax - 1))
    Y = lambda v: py0 + ph - ph * v / ymax
    s = ['<rect x="%g" y="%g" width="%g" height="%g" fill="#fff" stroke="#ddd"/>'
         % (px0, py0, pw, ph)]
    for k in range(3):
        yv = ymax * k / 2.0; yy = Y(yv)
        s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eee"/>' % (px0, yy, px0 + pw, yy))
        s.append('<text x="%g" y="%g" font-size="8" fill="#888" text-anchor="end">%g</text>'
                 % (px0 - 4, yy + 3, yv))
    cold = ms["B1"][0] / ms["stock"][0]
    steady = min(ms["B1"]) / min(ms["stock"])
    color = "#137333" if steady <= 1.03 and cold <= 0.98 else (
        "#c5221f" if (steady > 1.05 or cold > 1.05) else "#555")
    s.append('<text x="%g" y="%g" font-size="11" font-weight="bold" fill="#222">%s</text>'
             % (px0, oy + 14, bench))
    s.append('<text x="%g" y="%g" font-size="9" fill="%s" text-anchor="end">cold x%.2f  steady x%.2f</text>'
             % (px0 + pw, oy + 14, color, cold, steady))
    for cfg in ("stock", "B1"):
        v = ms[cfg]
        pts = " ".join("%g,%g" % (X(i), Y(v[i])) for i in range(len(v)))
        s.append('<polyline fill="none" stroke="%s" stroke-width="1.8" points="%s"/>' % (COL[cfg], pts))
        for i in range(len(v)):
            s.append('<circle cx="%g" cy="%g" r="1.8" fill="%s"/>' % (X(i), Y(v[i]), COL[cfg]))
    s.append('<text x="%g" y="%g" font-size="8" fill="#999" text-anchor="middle">iter (0=cold) / ms</text>'
             % (px0 + pw / 2.0, py0 + ph + 18))
    return "\n".join(s)


def render(data):
    benches = sorted(data.keys())
    rows = (len(benches) + COLS - 1) // COLS
    W = COLS * PW + (COLS - 1) * GAP + 20
    H = rows * PH + (rows - 1) * GAP + 64
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
         'font-family="DejaVu Sans, Arial, sans-serif">' % (W, H)]
    s.append('<rect width="%d" height="%d" fill="#fafafa"/>' % (W, H))
    s.append('<text x="14" y="24" font-size="16" font-weight="bold" fill="#111">'
             'PyPy per-run curves, all own/ benchmarks: stock vs B1 (2048:130:L)  '
             '(%d benchmarks)</text>' % len(benches))
    lx = 14
    for cfg, name in (("stock", "stock PyPy"), ("B1", "B1 2048:130:L")):
        s.append('<rect x="%d" y="34" width="13" height="9" fill="%s"/>' % (lx, COL[cfg]))
        s.append('<text x="%d" y="43" font-size="11" fill="#333">%s</text>' % (lx + 17, name))
        lx += 130
    for idx, b in enumerate(benches):
        rr, cc = idx // COLS, idx % COLS
        s.append(panel(10 + cc * (PW + GAP), 52 + rr * (PH + GAP), b, data[b]))
    s.append("</svg>")
    return "\n".join(s)


def main():
    data = collect()
    json.dump(data, open(OUT + ".json", "w"))
    open(OUT + ".svg", "w").write(render(data))
    try:
        subprocess.check_call(["inkscape", OUT + ".svg", "--export-type=pdf",
                               "--export-filename=" + OUT + ".pdf"],
                              stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    except Exception:
        subprocess.check_call(["convert", "-density", "150", "-background", "white",
                               OUT + ".svg", OUT + ".pdf"])
    print(OUT + ".pdf")


if __name__ == "__main__":
    main()
