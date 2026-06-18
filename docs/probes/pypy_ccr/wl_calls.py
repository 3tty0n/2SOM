# Warmup-bound, call-heavy workload: a deep call chain (top->mid->leaf) driven
# by a loop that runs just past PyPy's trace threshold, then exits. Stock PyPy
# inlines the whole chain into the loop trace -- expensive to compile, barely
# amortized on a short run. This is the shape the B1 "residualize-while-cold"
# policy is meant to help: keep early traces shallow and cheap.
import sys


def leaf(x):
    return (x * 2 + 1) ^ (x >> 1)


def mid(x):
    return leaf(x) + leaf(x + 1) - leaf(x + 2)


def top(x):
    return mid(x) + mid(x + 3) - mid(x + 5)


def work(rounds, iters):
    total = 0
    r = 0
    while r < rounds:
        s = 0
        i = 0
        while i < iters:
            s += top(i)
            i += 1
        total += s & 0xffff
        r += 1
    return total


if __name__ == '__main__':
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    iters = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
    print work(rounds, iters)
