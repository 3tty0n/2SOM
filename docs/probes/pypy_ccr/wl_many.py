# Maximally warmup-bound workload (B1 best case): K distinct driver loops, each
# with its own distinct deep call chain (f0..f{depth-1}), each loop run just
# past PyPy's trace threshold (~1039) so it compiles -- inlining the whole deep
# chain -- but barely amortizes. Maximises deep-inline tracing/backend cost
# relative to steady execution, which is exactly what the B1 residualize-while-
# cold policy is supposed to help.
import sys


def build(k, depth):
    src = []
    for d in range(depth):
        if d == depth - 1:
            body = "    return (x * 2 + %d) ^ (x >> 1)" % d
        else:
            body = "    return f%d_%d(x) + (x %% 7) - %d" % (d + 1, k, d)
        src.append("def f%d_%d(x):\n%s\n" % (d, k, body))
    src.append(
        "def driver_%d(n):\n"
        "    s = 0\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        s += f0_%d(i)\n"
        "        i += 1\n"
        "    return s\n" % (k, k))
    ns = {}
    exec("".join(src), ns)
    return ns["driver_%d" % k]


def main(K, depth, iters):
    drivers = [build(k, depth) for k in range(K)]
    total = 0
    for drv in drivers:
        total += drv(iters) & 0xffff
    return total


if __name__ == '__main__':
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    depth = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    iters = int(sys.argv[3]) if len(sys.argv) > 3 else 1300
    print main(K, depth, iters)
