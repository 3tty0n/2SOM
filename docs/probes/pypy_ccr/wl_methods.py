# Second monomorphic-deep workload, structurally different from wl_many: K
# distinct classes, each with a chain of methods m0->m1->...->m{depth-1} on
# float fields. A driver loop calls obj.m0(x) just past the trace threshold, so
# the loop compiles and inlines the whole method chain (LOAD_METHOD / attribute
# dispatch, not CALL_FUNCTION) -- the common OOP warmup shape. Each class is a
# distinct set of code objects, so each driver loop is a distinct deep trace.
import sys


def build(k, depth):
    src = ["class C%d(object):\n" % k]
    src.append("    def __init__(self):\n        self.s = %d.0\n" % (k + 1))
    for d in range(depth):
        if d == depth - 1:
            body = "        return x * 1.5 - self.s\n"
        else:
            body = "        return self.m%d(x + 1.0) + x * 0.5\n" % (d + 1)
        src.append("    def m%d(self, x):\n%s" % (d, body))
    src.append(
        "def driver%d(n):\n"
        "    o = C%d()\n"
        "    s = 0.0\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        s += o.m0(i * 0.25)\n"
        "        i += 1\n"
        "    return s\n" % (k, k))
    ns = {}
    exec("".join(src), ns)
    return ns["driver%d" % k]


def main(K, depth, iters):
    drivers = [build(k, depth) for k in range(K)]
    total = 0.0
    for drv in drivers:
        total += drv(iters)
    return total


if __name__ == '__main__':
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    depth = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    iters = int(sys.argv[3]) if len(sys.argv) > 3 else 1300
    print "%.3f" % main(K, depth, iters)
