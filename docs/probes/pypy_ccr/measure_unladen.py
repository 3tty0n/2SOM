#!/usr/bin/env python3
"""Per-run (warmup) curves for the runnable unladen_swallow benchmarks
(the workloads run_local.py / runner.py drive), stock vs B1 (2048:130:L).

Same method as measure_all.py: run `pypy-c <bench>.py -n N [posarg]` R times
from a cold process and take the element-wise min across repeats.
"""
import json, os, subprocess, sys, math

PYPY = "/home/yusuke/src/github.com/pypy/pypy/pypy/goal/pypy-c"
B = "/home/yusuke/src/foss.heptapod.net/pypy/benchmarks"
PERF = B + "/unladen_swallow/performance"
SPAMBAYES = (B + "/unladen_swallow/lib/spambayes" + os.pathsep +
             B + "/unladen_swallow/lib/lockfile")
OUT = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr/unladen_all"

CFGS = [("stock", {}),
        ("B1", {"PYPYTIER_PROMOTE": "2048", "PYPYTIER_MINSIZE": "130",
                "PYPYTIER_LOOPGATE": "1"})]

# (label, file, posargs, N, R)
SPEC = [
    ("bm_ai",             "bm_ai.py",             [],                15, 4),
    ("bm_call_simple",    "bm_call_simple.py",    [],                15, 4),
    ("bm_nbody",          "bm_nbody.py",          [],                15, 4),
    ("bm_pickle",         "bm_pickle.py",         ["pickle"],        12, 3),
    ("bm_unpickle",       "bm_pickle.py",         ["unpickle"],      12, 3),
    ("bm_regex_effbot",   "bm_regex_effbot.py",   [],                15, 4),
    ("bm_regex_v8",       "bm_regex_v8.py",       [],                15, 4),
    ("bm_richards",       "bm_richards.py",       [],                18, 4),
    ("bm_spambayes",      "bm_spambayes.py",      [],                10, 3),
    ("bm_threading",      "bm_threading.py",      ["threaded_count"],15, 4),
    ("bm_unpack_seq",     "bm_unpack_sequence.py",[],                15, 4),
]


def run_once(fname, posargs, n, extra):
    env = dict(os.environ)
    for k in ("PYPYLOG", "PYPYTIER_PROMOTE", "PYPYTIER_MINSIZE", "PYPYTIER_LOOPGATE"):
        env.pop(k, None)
    env["PYTHONPATH"] = SPAMBAYES + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra)
    argv = [PYPY, fname, "-n", str(n)] + posargs   # -n before positional!
    p = subprocess.Popen(argv, cwd=PERF, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env)
    out, _ = p.communicate()
    ts = []
    for line in out.split():
        try:
            ts.append(float(line))
        except ValueError:
            pass
    return ts


def measure(fname, posargs, n, r, extra):
    runs = [x for x in (run_once(fname, posargs, n, extra) for _ in range(r)) if x]
    if not runs:
        return None
    m = min(len(x) for x in runs)
    return [min(run[i] for run in runs) for i in range(m)]


def collect():
    data = {}
    for label, fname, posargs, n, r in SPEC:
        d = {}
        ok = True
        for cfg, extra in CFGS:
            cur = measure(fname, posargs, n, r, extra)
            if cur is None or len(cur) < 2:
                ok = False; break
            d[cfg] = cur
        if ok:
            data[label] = d
            cold = d["B1"][0] / d["stock"][0]
            st = min(d["B1"]) / min(d["stock"])
            sys.stderr.write("%-16s cold x%.3f steady x%.3f\n" % (label, cold, st))
        else:
            sys.stderr.write("%-16s FAILED\n" % label)
    return data


# ----------------------------- SVG grid (shared style with measure_all) ---
PW, PH = 300, 200
ML, MB, MT, MR = 48, 30, 30, 10
COLS = 4
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
        s.append('<text x="%g" y="%g" font-size="8" fill="#888" text-anchor="end">%g</text>' % (px0 - 4, yy + 3, yv))
    cold = ms["B1"][0] / ms["stock"][0]; steady = min(ms["B1"]) / min(ms["stock"])
    color = "#137333" if steady <= 1.03 and cold <= 0.98 else ("#c5221f" if (steady > 1.05 or cold > 1.05) else "#555")
    s.append('<text x="%g" y="%g" font-size="11" font-weight="bold" fill="#222">%s</text>' % (px0, oy + 14, bench))
    s.append('<text x="%g" y="%g" font-size="9" fill="%s" text-anchor="end">cold x%.2f  steady x%.2f</text>' % (px0 + pw, oy + 14, color, cold, steady))
    for cfg in ("stock", "B1"):
        v = ms[cfg]
        pts = " ".join("%g,%g" % (X(i), Y(v[i])) for i in range(len(v)))
        s.append('<polyline fill="none" stroke="%s" stroke-width="1.8" points="%s"/>' % (COL[cfg], pts))
        for i in range(len(v)):
            s.append('<circle cx="%g" cy="%g" r="1.8" fill="%s"/>' % (X(i), Y(v[i]), COL[cfg]))
    s.append('<text x="%g" y="%g" font-size="8" fill="#999" text-anchor="middle">iter (0=cold) / ms</text>' % (px0 + pw / 2.0, py0 + ph + 18))
    return "\n".join(s)


def render(data):
    benches = sorted(data.keys())
    rows = (len(benches) + COLS - 1) // COLS
    W = COLS * PW + (COLS - 1) * GAP + 20
    H = rows * PH + (rows - 1) * GAP + 64
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="DejaVu Sans, Arial, sans-serif">' % (W, H)]
    s.append('<rect width="%d" height="%d" fill="#fafafa"/>' % (W, H))
    s.append('<text x="14" y="24" font-size="16" font-weight="bold" fill="#111">unladen_swallow per-run curves: stock vs B1 (2048:130:L)  (%d benchmarks)</text>' % len(benches))
    lx = 14
    for cfg, name in (("stock", "stock PyPy"), ("B1", "B1 2048:130:L")):
        s.append('<rect x="%d" y="34" width="13" height="9" fill="%s"/>' % (lx, COL[cfg]))
        s.append('<text x="%d" y="43" font-size="11" fill="#333">%s</text>' % (lx + 17, name)); lx += 130
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
        subprocess.check_call(["convert", "-density", "150", "-background", "white", OUT + ".svg", OUT + ".pdf"])
    print(OUT + ".pdf")


if __name__ == "__main__":
    main()
