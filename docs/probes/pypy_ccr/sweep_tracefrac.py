#!/usr/bin/env python2
"""Sweep PYPYTIER_TRACEFRAC (global tracing-fraction controller) on the decisive
subset.  Goal: a threshold that keeps the compile-bound wins (go, richards,
chaos) while inlining for the execution-bound regressors (django, chameleon,
scimark_fft, genshi).  TRACEFRAC=0 == static CCR (loopgate).

Residualize iff traced_ops >= K * interp_work (once traced_ops > 4096).
Wide log range because the traced/interp magnitude is unknown a priori.
"""
import sys, os, math
PYPYREPO = "/home/yusuke/src/github.com/pypy/pypy"
sys.path.insert(0, PYPYREPO)
import bench_e2e as B

PYPY = PYPYREPO + "/pypy/goal/pypy-c"
N, REPS, TIMEOUT = 40, 3, 150
# per-mille tracing-fraction thresholds; measured gap is [0.23 django, 0.32 chaos]
KS = [0, 200, 250, 280, 310, 400]
SUBSET = ["go", "richards", "chaos", "telco", "bm_mako",            # wins
          "django", "bm_chameleon", "scimark_fft", "genshi_xml",
          "bm_dulwich_log", "json_bench"]                            # regressors
BY = dict((b[0], b) for b in B.BENCHMARKS)
TIERKEYS = ["PYPYTIER_PROMOTE", "PYPYTIER_MINSIZE", "PYPYTIER_LOOPGATE",
            "PYPYTIER_WORKMIN", "PYPYTIER_TRACEFRAC", "PYPYTIER_LOOPCALLS"]


def setenv(extra):
    for k in TIERKEYS:
        os.environ.pop(k, None)
    os.environ.update(extra)


def med(xs):
    xs = sorted(x for x in xs if x == x)
    n = len(xs)
    return (xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])) if n else float("nan")


def run(name, env):
    _, script, benv, extra = BY[name]
    setenv(env)
    cs, ss = [], []
    for _ in range(REPS):
        r = B.run_one(PYPY, script, benv, extra, N, 0, timeout=TIMEOUT)
        if "error" not in r and r.get("iter_times"):
            cs.append(r["iter_times"][0]); ss.append(r["running"])
    return (min(cs) if cs else None, med(ss) if ss else None)


def ccr_env(k):
    e = {"PYPYTIER_PROMOTE": "2048", "PYPYTIER_MINSIZE": "130", "PYPYTIER_LOOPGATE": "1"}
    if k > 0:
        e["PYPYTIER_TRACEFRAC"] = str(k)
    return e


def geo(xs):
    xs = [x for x in xs if x and x == x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")


def main():
    cold = dict((k, []) for k in KS)
    steady = dict((k, []) for k in KS)
    print("%-16s metric | %s" % ("bench", "  ".join("K%-4d" % k for k in KS)))
    for name in SUBSET:
        sc, ss = run(name, {})
        if sc is None:
            print("%-16s STOCK FAILED" % name); continue
        cr, sr = [], []
        for k in KS:
            c, s = run(name, ccr_env(k))
            cr.append(c / sc if c else float("nan"))
            sr.append(s / ss if s else float("nan"))
            cold[k].append(cr[-1]); steady[k].append(sr[-1])
        print("%-16s cold   | %s" % (name, "  ".join("%.2f " % x for x in cr)))
        print("%-16s steady | %s" % ("", "  ".join("%.2f " % x for x in sr)))
        sys.stdout.flush()
    print("-" * 64)
    print("%-16s cold   | %s" % ("GEOMEAN", "  ".join("%.3f" % geo(cold[k]) for k in KS)))
    print("%-16s steady | %s" % ("", "  ".join("%.3f" % geo(steady[k]) for k in KS)))


if __name__ == "__main__":
    main()
