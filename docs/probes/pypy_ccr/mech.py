# Mechanism-check workload: a hot loop that calls a small Python function
# (which calls another). Stock PyPy inlines inc->add1 into the loop trace;
# with PYPYTIER_PROMOTE=inf both must be residualized (CALL_ASSEMBLER), so the
# optimized loop trace goes shallow. Run under PYPYLOG=jit-log-opt to inspect.
import sys


def add1(x):
    return x + 1


def inc(x):
    return add1(x)


def main(n):
    s = 0
    i = 0
    while i < n:
        s = inc(s)
        i += 1
    return s


if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5000000
    print main(n)
