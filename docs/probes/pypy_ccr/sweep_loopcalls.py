#!/usr/bin/env python3
"""Sweep PYPYTIER_LOOPCALLS=K on the decisive benchmarks to lock the threshold.

We need K to (a) remove the json_bench / bm_gzip cold+steady regression and
(b) keep go's warmup win (and not hurt the other current winners).  Prints, per
benchmark, cold and steady ratios vs stock for each K.
"""
import os, subprocess, sys, math

PYPY = "/home/yusuke/src/github.com/pypy/pypy/pypy/goal/pypy-c"
OWN = "/home/yusuke/src/foss.heptapod.net/pypy/benchmarks/own"

# decisive set: the two regressors + the main winner + a few sensitive winners
SPEC = [("json_bench", 15, 5), ("bm_gzip", 15, 5), ("go", 20, 5),
        ("chaos", 15, 5), ("raytrace-simple", 15, 5), ("float", 15, 5)]
KS = [0, 16, 18, 22]   # K=0 == current loopgate behaviour


def env(extra):
    e = dict(os.environ)
    for k in ("PYPYLOG", "PYPYTIER_PROMOTE", "PYPYTIER_MINSIZE",
              "PYPYTIER_LOOPGATE", "PYPYTIER_LOOPCALLS"):
        e.pop(k, None)
    e.update(extra)
    return e


def run(bench, n, extra):
    p = subprocess.Popen([PYPY, bench + ".py", "-n", str(n)], cwd=OWN,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env(extra))
    out, _ = p.communicate()
    ts = []
    for ln in out.split():
        try:
            ts.append(float(ln))
        except ValueError:
            pass
    return ts


def curve(bench, n, r, extra):
    runs = [x for x in (run(bench, n, extra) for _ in range(r)) if x]
    m = min(len(x) for x in runs)
    return [min(run[i] for run in runs) for i in range(m)]


def b1env(k):
    e = {"PYPYTIER_PROMOTE": "2048", "PYPYTIER_MINSIZE": "130", "PYPYTIER_LOOPGATE": "1"}
    if k > 0:
        e["PYPYTIER_LOOPCALLS"] = str(k)
    return e


def main():
    print("%-16s %8s | %s" % ("bench", "metric",
          "  ".join("K=%-2d" % k for k in KS)))
    cold_geo = {k: [] for k in KS}
    steady_geo = {k: [] for k in KS}
    for bench, n, r in SPEC:
        st = curve(bench, n, r, {})
        coldrow, steadyrow = [], []
        for k in KS:
            b1 = curve(bench, n, r, b1env(k))
            c = b1[0] / st[0]
            s = min(b1) / min(st)
            coldrow.append(c); steadyrow.append(s)
            cold_geo[k].append(c); steady_geo[k].append(s)
        print("%-16s %8s | %s" % (bench, "cold",
              "  ".join("%.3f" % x for x in coldrow)))
        print("%-16s %8s | %s" % ("", "steady",
              "  ".join("%.3f" % x for x in steadyrow)))
        sys.stdout.flush()
    geo = lambda xs: math.exp(sum(math.log(x) for x in xs) / len(xs))
    print("-" * 60)
    print("%-16s %8s | %s" % ("GEOMEAN", "cold",
          "  ".join("%.3f" % geo(cold_geo[k]) for k in KS)))
    print("%-16s %8s | %s" % ("", "steady",
          "  ".join("%.3f" % geo(steady_geo[k]) for k in KS)))


if __name__ == "__main__":
    main()
