# -*- coding: utf-8 -*-
# Cold warmup-curve driver for bm_krakatau (runs under pypy-c = Python 2.7).
# Times each decompileClass() call (100 decompiles) from a cold process, so
# iteration 0 includes JIT tracing.  bm_krakatau.main() discards 30 warmup
# iterations, hiding exactly this curve; we capture it directly.
import sys, os, time, cStringIO

sys.path.insert(0, os.getcwd())   # own/ dir, where bm_krakatau + krakatau/ live
import bm_krakatau as k

N = int(sys.argv[1])
old = sys.stdout
sys.stdout = cStringIO.StringIO()   # decompileClass prints the AST; suppress it
ts = []
try:
    for i in range(N):
        t0 = time.time()
        k.decompileClass()
        ts.append(time.time() - t0)
finally:
    sys.stdout = old
for t in ts:
    print repr(t)
