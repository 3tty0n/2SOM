# -*- coding: utf-8 -*-
# Cold warmup-curve driver (runs under pypy-c = Python 2.7).
# Prints N per-iteration wall times for the core workload of <bench>, starting
# from a COLD process with NO pre-warm, so iteration 0 includes JIT tracing.
import sys, os, time, imp

sys.path.insert(0, os.getcwd())   # benchmarks live in CWD (own/) and import each other

bench = sys.argv[1]
N = int(sys.argv[2])


def time_calls(fn, n):
    ts = []
    for _ in range(n):
        t0 = time.time()
        fn()
        ts.append(time.time() - t0)
    return ts


if bench == 'go':
    import go
    times = time_calls(go.versus_cpu, N)
elif bench == 'pyflate':
    mod = imp.load_source('pyflate_fast', 'pyflate-fast.py')
    times = time_calls(mod._main, N)
elif bench == 'chaos':
    import chaos
    times = chaos.main(N)            # no pre-warm; returns N cold per-iter times
elif bench == 'nbody':
    import nbody_modified
    times = nbody_modified.main(N)   # no pre-warm
elif bench == 'bm_mdp':
    import bm_mdp
    times = bm_mdp.main(N)           # no pre-warm
else:
    raise SystemExit('unknown bench %s' % bench)

for t in times:
    print(repr(t))
