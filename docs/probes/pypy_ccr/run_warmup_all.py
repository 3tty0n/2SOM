#!/usr/bin/env python2
"""Full-suite warmup measurement built on bench_e2e.run_one.

Runs every benchmark in bench_e2e.BENCHMARKS under two configs on the SAME
binary -- stock (PYPYTIER unset) and CCR -- interleaved per benchmark, REPS cold
processes each, n iterations.  Stores per-rep warmup curve + JIT tracing seconds
+ run_one's MAD-detected steady ('running').  Slow benchmarks are capped by a
per-run timeout.

Usage: run_warmup_all.py [n] [reps] [timeout_s] [workmin]
  workmin > 0 sets PYPYTIER_WORKMIN on the CCR config (dynamic controller).
"""
import sys, os, json, math

PYPYREPO = "/home/yusuke/src/github.com/pypy/pypy"
sys.path.insert(0, PYPYREPO)
import bench_e2e as B  # noqa: E402

PYPY = PYPYREPO + "/pypy/goal/pypy-c"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
TIMEOUT = int(sys.argv[3]) if len(sys.argv) > 3 else 150
# arg4: TRACEFRAC (per-mille) for the global controller config
TF = int(sys.argv[4]) if len(sys.argv) > 4 else 250
OUT = "/home/yusuke/src/github.com/3tty0n/2SOM/docs/probes/pypy_ccr/warmup_all_ctrl%d" % TF

TIERKEYS = ["PYPYTIER_PROMOTE", "PYPYTIER_MINSIZE", "PYPYTIER_LOOPGATE",
            "PYPYTIER_WORKMIN", "PYPYTIER_TRACEFRAC", "PYPYTIER_LOOPCALLS"]

_LG = {"PYPYTIER_PROMOTE": "2048", "PYPYTIER_MINSIZE": "130", "PYPYTIER_LOOPGATE": "1"}


def _ctrl(tf):
    e = dict(_LG)
    e["PYPYTIER_TRACEFRAC"] = str(tf)
    return e


# stock (CCR off) vs static CCR (loopgate) vs global tracing-fraction controller
CFGS = [("stock", {}), ("static", dict(_LG)), ("ctrl", _ctrl(TF))]


def setenv(extra):
    for k in TIERKEYS:
        os.environ.pop(k, None)
    os.environ.update(extra)


def median(xs):
    xs = sorted(x for x in xs if x == x)
    n = len(xs)
    if not n:
        return float("nan")
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def emin(reps):
    series = [r["iter_times"] for r in reps if r.get("iter_times")]
    if not series:
        return None
    m = min(len(s) for s in series)
    if m < 6:
        return None
    return [min(s[i] for s in series) for i in range(m)]


def save(data):
    f = open(OUT + ".json", "w")
    json.dump({"n": N, "reps": REPS, "tracefrac": TF, "data": data}, f)
    f.close()


def main():
    data = {}
    for name, script, env, extra in B.BENCHMARKS:
        d = {}
        ok = True
        for cfg, cfgenv in CFGS:
            setenv(cfgenv)
            reps = []
            for i in range(REPS):
                r = B.run_one(PYPY, script, env, extra, N, 0, timeout=TIMEOUT)
                if "error" not in r:
                    reps.append(r)
                else:
                    sys.stderr.write("  %-20s %-5s rep%d ERR %s\n" % (name, cfg, i, r["error"]))
            c = emin(reps)
            if not c:
                ok = False
                break
            d[cfg] = {"curve": c,
                      "tracing": median([r["tracing"] for r in reps]),
                      "jit_total": median([r["jit_total"] for r in reps]),
                      "running": median([r["running"] for r in reps])}  # MAD steady
        if ok:
            data[name] = d
            s = d["stock"]
            parts = []
            for cfg, _ in CFGS[1:]:
                cold = d[cfg]["curve"][0] / s["curve"][0]
                steady = d[cfg]["running"] / s["running"]
                parts.append("%s cold %.3f steady %.3f" % (cfg, cold, steady))
            sys.stderr.write("%-20s %s\n" % (name, " | ".join(parts)))
        else:
            sys.stderr.write("%-20s DROPPED\n" % name)
        sys.stderr.flush()
        save(data)            # written once per benchmark, handle closed each time
    print("wrote %s.json (%d benchmarks)" % (OUT, len(data)))


if __name__ == "__main__":
    main()
