#!/usr/bin/env python2
"""Sweep PYPYTIER_WORKMIN (dynamic trip-count gate) on the decisive subset.

Goal: a WORKMIN that keeps the warmup wins (go, chaos, telco, mako, pyxl,
spitfire) while removing the cold/steady regressions static CCR has on the full
suite (django, chameleon, genshi, scimark_fft, dulwich, richards, sqlalchemy).
WORKMIN=0 == loopgate-only (the static-CCR baseline).
"""
import sys, os, math
PYPYREPO = "/home/yusuke/src/github.com/pypy/pypy"
sys.path.insert(0, PYPYREPO)
import bench_e2e as B

PYPY = PYPYREPO + "/pypy/goal/pypy-c"
N, REPS, TIMEOUT = 40, 3, 150
WORKMINS = [0, 10, 20, 40, 80]

SUBSET = ["go", "chaos", "telco", "bm_mako", "pyxl_bench", "spitfire2",       # wins
          "richards",                                                          # cold-win/steady-loss
          "django", "bm_chameleon", "genshi_text", "genshi_xml",
          "scimark_fft", "bm_dulwich_log", "json_bench",                       # regressors
          "sqlalchemy_imperative", "deltablue"]                                # steady regressors
BY = dict((b[0], b) for b in B.BENCHMARKS)
TIERKEYS = ["PYPYTIER_PROMOTE", "PYPYTIER_MINSIZE", "PYPYTIER_LOOPGATE",
            "PYPYTIER_WORKMIN", "PYPYTIER_LOOPCALLS"]


def setenv(extra):
    for k in TIERKEYS:
        os.environ.pop(k, None)
    os.environ.update(extra)


def med(xs):
    xs = sorted(x for x in xs if x == x)
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2]) if n else float("nan")


def run(name, env):
    _, script, benv, extra = BY[name]
    setenv(env)
    cs, ss = [], []
    for _ in range(REPS):
        r = B.run_one(PYPY, script, benv, extra, N, 0, timeout=TIMEOUT)
        if "error" not in r and r.get("iter_times"):
            cs.append(r["iter_times"][0]); ss.append(r["running"])
    return (min(cs) if cs else None, med(ss) if ss else None)


def ccr_env(wm):
    e = {"PYPYTIER_PROMOTE": "2048", "PYPYTIER_MINSIZE": "130", "PYPYTIER_LOOPGATE": "1"}
    if wm > 0:
        e["PYPYTIER_WORKMIN"] = str(wm)
    return e


def geo(xs):
    xs = [x for x in xs if x and x == x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")


def main():
    cold = dict((w, []) for w in WORKMINS)
    steady = dict((w, []) for w in WORKMINS)
    hdr = "  ".join("wm%d" % w for w in WORKMINS)
    print("%-20s metric | %s" % ("bench", hdr)); sys.stdout.flush()
    for name in SUBSET:
        sc, ss = run(name, {})
        if sc is None:
            print("%-20s STOCK FAILED" % name); continue
        cr, sr = [], []
        for w in WORKMINS:
            c, s = run(name, ccr_env(w))
            cr.append(c / sc if c else float("nan"))
            sr.append(s / ss if s else float("nan"))
            cold[w].append(cr[-1]); steady[w].append(sr[-1])
        print("%-20s cold   | %s" % (name, "  ".join("%.2f" % x for x in cr)))
        print("%-20s steady | %s" % ("", "  ".join("%.2f" % x for x in sr)))
        sys.stdout.flush()
    print("-" * 60)
    print("%-20s cold   | %s" % ("GEOMEAN", "  ".join("%.3f" % geo(cold[w]) for w in WORKMINS)))
    print("%-20s steady | %s" % ("", "  ".join("%.3f" % geo(steady[w]) for w in WORKMINS)))


if __name__ == "__main__":
    main()
