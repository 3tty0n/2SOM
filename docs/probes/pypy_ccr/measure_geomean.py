#!/usr/bin/env python3
"""N=50 per-iteration geomean warmup curves: stock vs B1-old (loopgate) vs
B1-new (loopgate + loop-amortization gate PYPYTIER_LOOPCALLS=K).

Each benchmark runs `pypy-c <b>.py -n 50` R times from cold; we take the
element-wise min across repeats to denoise.  Then, per iteration index i, we
take the geomean ACROSS benchmarks of (config_time[i] / stock_time[i]) -- the
aggregate "how much faster than stock at iteration i" curve.  Cold = iter 0,
steady = min over the curve.

Usage: measure_geomean.py <K>      (K = PYPYTIER_LOOPCALLS for B1-new)
"""
import json, os, subprocess, sys, math

PYPY = "/home/yusuke/src/github.com/pypy/pypy/pypy/goal/pypy-c"
OWN = "/home/yusuke/src/foss.heptapod.net/pypy/benchmarks/own"
OUT = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr/geomean_warmup"
N = 50
RENDER_ONLY = len(sys.argv) > 1 and sys.argv[1] == "render"
try:
    K = int(sys.argv[1]) if len(sys.argv) > 1 and not RENDER_ONLY else 18
except ValueError:
    K = 18

TIER_A = ("raytrace-simple spectral-norm deltablue nbody_modified p5_micro "
          "p5_micro2 p5_micro3 p5_micro4 float telco hof_mono crypto_pyaes "
          "chaos meteor-contest fannkuch").split()
TIER_B = "go fib pyflate-fast bm_gzip p5_jsonruns pypy_interp json_bench".split()
SPEC = [(b, 6) for b in TIER_A] + [(b, 4) for b in TIER_B]

CFGS = [("stock", {}),
        ("B1old", {"PYPYTIER_PROMOTE": "2048", "PYPYTIER_MINSIZE": "130",
                   "PYPYTIER_LOOPGATE": "1"}),
        ("B1new", {"PYPYTIER_PROMOTE": "2048", "PYPYTIER_MINSIZE": "130",
                   "PYPYTIER_LOOPGATE": "1", "PYPYTIER_LOOPCALLS": str(K)})]


def env(extra):
    e = dict(os.environ)
    for k in ("PYPYLOG", "PYPYTIER_PROMOTE", "PYPYTIER_MINSIZE",
              "PYPYTIER_LOOPGATE", "PYPYTIER_LOOPCALLS"):
        e.pop(k, None)
    e.update(extra)
    return e


def run(bench, extra):
    p = subprocess.Popen([PYPY, bench + ".py", "-n", str(N)], cwd=OWN,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env(extra))
    out, _ = p.communicate()
    ts = []
    for ln in out.split():
        try:
            ts.append(float(ln))
        except ValueError:
            pass
    return ts


def curve(bench, r, extra):
    runs = [x for x in (run(bench, extra) for _ in range(r)) if x]
    if not runs:
        return None
    m = min(len(x) for x in runs)
    return [min(run[i] for run in runs) for i in range(m)]


def collect():
    data = {}
    for bench, r in SPEC:
        d = {}
        ok = True
        for cfg, extra in CFGS:
            c = curve(bench, r, extra)
            if c is None or len(c) < N:
                ok = False
                break
            d[cfg] = c[:N]
        if ok:
            data[bench] = d
            sys.stderr.write("%-16s cold old %.3f new %.3f | steady old %.3f new %.3f\n" % (
                bench, d["B1old"][0] / d["stock"][0], d["B1new"][0] / d["stock"][0],
                min(d["B1old"]) / min(d["stock"]), min(d["B1new"]) / min(d["stock"])))
        else:
            sys.stderr.write("%-16s FAILED/short\n" % bench)
        sys.stderr.flush()
    return data


def geo(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def per_iter_geomean(data, cfg):
    # ratio cfg/stock per benchmark per iteration, geomean across benchmarks
    out = []
    for i in range(N):
        ratios = [data[b][cfg][i] / data[b]["stock"][i] for b in data]
        out.append(geo(ratios))
    return out


# ------------------------------ SVG ------------------------------
COL = {"stock": "#9aa0a6", "B1old": "#ea4335", "B1new": "#1a73e8"}
NAME = {"stock": "stock (=1.0)", "B1old": "CCR (loopgate)",
        "B1new": "CCR + loop-amortization gate (K=%d)" % K}
DEVNULL = open(os.devnull, "w")


def nice(v):
    if v <= 0:
        return 1.0
    e = math.floor(math.log10(v)); base = 10 ** e
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if m * base >= v:
            return m * base
    return 10 * base


def main_panel(ox, oy, w, h, geos):
    ML, MB, MT, MR = 64, 48, 30, 16
    px0, py0 = ox + ML, oy + MT
    pw, ph = w - ML - MR, h - MT - MB
    ys = [1.0] + [v for g in geos.values() for v in g]
    ymin = min(ys) * 0.98
    ymax = max(ys) * 1.02
    X = lambda i: px0 + pw * i / float(N - 1)
    Y = lambda v: py0 + ph - ph * (v - ymin) / (ymax - ymin)
    s = ['<rect x="%g" y="%g" width="%g" height="%g" fill="#fff" stroke="#ccc"/>' % (px0, py0, pw, ph)]
    # y gridlines
    k = 6
    for t in range(k + 1):
        yv = ymin + (ymax - ymin) * t / k
        yy = Y(yv)
        s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eee"/>' % (px0, yy, px0 + pw, yy))
        s.append('<text x="%g" y="%g" font-size="11" fill="#666" text-anchor="end">%.2f</text>' % (px0 - 6, yy + 4, yv))
    # baseline 1.0
    yy1 = Y(1.0)
    s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="1.5" stroke-dasharray="5,4"/>' % (px0, yy1, px0 + pw, yy1, COL["stock"]))
    # x ticks
    for i in (0, 1, 2, 3, 5, 10, 15, 20, 30, 40, 49):
        if i < N:
            s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eee"/>' % (X(i), py0, X(i), py0 + ph))
            s.append('<text x="%g" y="%g" font-size="10" fill="#666" text-anchor="middle">%d</text>' % (X(i), py0 + ph + 16, i))
    s.append('<text x="%g" y="%g" font-size="12" fill="#444" text-anchor="middle">iteration index (0 = cold, includes JIT tracing)</text>' % (px0 + pw / 2.0, py0 + ph + 36))
    s.append('<text x="%g" y="%g" font-size="12" fill="#444" text-anchor="middle" transform="rotate(-90 %g %g)">geomean time ratio vs stock (lower = faster)</text>' % (ox + 16, py0 + ph / 2.0, ox + 16, py0 + ph / 2.0))
    s.append('<text x="%g" y="%g" font-size="15" font-weight="bold" fill="#111">Aggregate warmup: per-iteration geomean ratio vs stock (N=50)</text>' % (px0, oy + 20))
    for cfg in ("B1old", "B1new"):
        g = geos[cfg]
        pts = " ".join("%g,%g" % (X(i), Y(g[i])) for i in range(N))
        s.append('<polyline fill="none" stroke="%s" stroke-width="2.4" points="%s"/>' % (COL[cfg], pts))
        for i in range(N):
            s.append('<circle cx="%g" cy="%g" r="1.7" fill="%s"/>' % (X(i), Y(g[i]), COL[cfg]))
    return "\n".join(s)


def inset(ox, oy, w, h, title, curves):
    ML, MB, MT, MR = 52, 40, 26, 12
    ms = {c: [t * 1000 for t in v] for c, v in curves.items()}
    px0, py0 = ox + ML, oy + MT
    pw, ph = w - ML - MR, h - MT - MB
    ymax = nice(max(max(v) for v in ms.values()) * 1.05)
    X = lambda i: px0 + pw * i / float(N - 1)
    Y = lambda v: py0 + ph - ph * v / ymax
    s = ['<rect x="%g" y="%g" width="%g" height="%g" fill="#fff" stroke="#ccc"/>' % (px0, py0, pw, ph)]
    for t in range(5):
        yv = ymax * t / 4.0; yy = Y(yv)
        s.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#eee"/>' % (px0, yy, px0 + pw, yy))
        s.append('<text x="%g" y="%g" font-size="9" fill="#888" text-anchor="end">%g</text>' % (px0 - 4, yy + 3, yv))
    for i in (0, 5, 10, 20, 30, 49):
        s.append('<text x="%g" y="%g" font-size="9" fill="#888" text-anchor="middle">%d</text>' % (X(i), py0 + ph + 13, i))
    s.append('<text x="%g" y="%g" font-size="12" font-weight="bold" fill="#222">%s</text>' % (px0, oy + 18, title))
    oldc = ms["B1old"][0] / ms["stock"][0]; newc = ms["B1new"][0] / ms["stock"][0]
    olds = min(ms["B1old"]) / min(ms["stock"]); news = min(ms["B1new"]) / min(ms["stock"])
    s.append('<text x="%g" y="%g" font-size="9" fill="#555" text-anchor="end">cold %.2f&#8594;%.2f  steady %.2f&#8594;%.2f</text>' % (px0 + pw, oy + 18, oldc, newc, olds, news))
    s.append('<text x="%g" y="%g" font-size="10" fill="#444" text-anchor="middle">iteration / ms per run</text>' % (px0 + pw / 2.0, py0 + ph + 30))
    for cfg in ("stock", "B1old", "B1new"):
        v = ms[cfg]
        pts = " ".join("%g,%g" % (X(i), Y(v[i])) for i in range(N))
        s.append('<polyline fill="none" stroke="%s" stroke-width="1.8" points="%s"/>' % (COL[cfg], pts))
    return "\n".join(s)


def render(data):
    geos = {c: per_iter_geomean(data, c) for c in ("stock", "B1old", "B1new")}
    # overall scalar geomeans
    cold = {c: geo([data[b][c][0] / data[b]["stock"][0] for b in data]) for c in CFGS_NAMES}
    steady = {c: geo([min(data[b][c]) / min(data[b]["stock"]) for b in data]) for c in CFGS_NAMES}
    W, H = 1180, 760
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="DejaVu Sans, Arial, sans-serif">' % (W, H)]
    s.append('<rect width="%d" height="%d" fill="#fafafa"/>' % (W, H))
    s.append('<text x="20" y="30" font-size="19" font-weight="bold" fill="#111">CCR loop-amortization gate: fixes json_bench/bm_gzip, but trades away the warmup win</text>')
    # legend
    lx = 20
    for cfg in ("stock", "B1old", "B1new"):
        s.append('<rect x="%d" y="42" width="16" height="11" fill="%s"/>' % (lx, COL[cfg]))
        s.append('<text x="%d" y="52" font-size="12" fill="#333">%s</text>' % (lx + 20, NAME[cfg]))
        lx += 26 + 9 * len(NAME[cfg])
    s.append(main_panel(20, 64, W - 40, 430, geos))
    # scalar geomean annotation box
    bx, by = W - 360, 86
    s.append('<rect x="%d" y="%d" width="340" height="86" fill="#fff" stroke="#ccc"/>' % (bx, by))
    s.append('<text x="%d" y="%d" font-size="12" font-weight="bold" fill="#111">all-benchmark geomean (CCR/stock)</text>' % (bx + 10, by + 18))
    s.append('<text x="%d" y="%d" font-size="12" fill="#ea4335">old: cold %.3f (%+.1f%%)   steady %.3f (%+.1f%%)</text>' % (bx + 10, by + 40, cold["B1old"], (cold["B1old"]-1)*100, steady["B1old"], (steady["B1old"]-1)*100))
    s.append('<text x="%d" y="%d" font-size="12" fill="#1a73e8">new: cold %.3f (%+.1f%%)   steady %.3f (%+.1f%%)</text>' % (bx + 10, by + 60, cold["B1new"], (cold["B1new"]-1)*100, steady["B1new"], (steady["B1new"]-1)*100))
    s.append('<text x="%d" y="%d" font-size="11" fill="#555">%d benchmarks</text>' % (bx + 10, by + 78, len(data)))
    # insets for the two regressors
    s.append(inset(20, 510, 560, 230, "json_bench (was the regressor)", data["json_bench"]))
    s.append(inset(600, 510, 560, 230, "bm_gzip (was the regressor)", data["bm_gzip"]))
    s.append("</svg>")
    return "\n".join(s), geos, cold, steady


CFGS_NAMES = ["stock", "B1old", "B1new"]


def main():
    if RENDER_ONLY:
        data = json.load(open(OUT + ".json"))["data"]
    else:
        data = collect()
    svg, geos, cold, steady = render(data)
    out = {"K": K, "data": data, "geo_per_iter": geos,
           "cold_geomean": cold, "steady_geomean": steady}
    json.dump(out, open(OUT + ".json", "w"))
    open(OUT + ".svg", "w").write(svg)
    try:
        subprocess.check_call(["inkscape", OUT + ".svg", "--export-type=pdf",
                               "--export-filename=" + OUT + ".pdf"],
                              stderr=DEVNULL, stdout=DEVNULL)
    except Exception:
        subprocess.check_call(["convert", "-density", "150", "-background", "white", OUT + ".svg", OUT + ".pdf"])
    subprocess.call(["inkscape", OUT + ".svg", "--export-type=png", "--export-dpi=120",
                     "--export-filename=" + OUT + ".png"], stderr=DEVNULL, stdout=DEVNULL)
    sys.stderr.write("cold geomean   old %.3f new %.3f\n" % (cold["B1old"], cold["B1new"]))
    sys.stderr.write("steady geomean old %.3f new %.3f\n" % (steady["B1old"], steady["B1new"]))
    print(OUT + ".pdf")


if __name__ == "__main__":
    main()
