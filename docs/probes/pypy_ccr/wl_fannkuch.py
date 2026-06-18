# Short fannkuch-redux (pure Python, classic warmup-sensitive microbench).
# Small n keeps the run short so JIT warmup dominates the total.
# Reference results: fannkuch(7)=16, fannkuch(8)=22, fannkuch(9)=30.
import sys


def fannkuch(n):
    perm1 = list(range(n))
    count = list(range(n))
    max_flips = 0
    r = n
    while True:
        while r != 1:
            count[r - 1] = r
            r -= 1
        perm = perm1[:]
        flips = 0
        k = perm[0]
        while k:
            perm[:k + 1] = perm[k::-1]
            flips += 1
            k = perm[0]
        if flips > max_flips:
            max_flips = flips
        while True:
            if r == n:
                return max_flips
            perm1.insert(r, perm1.pop(0))
            count[r] -= 1
            if count[r] > 0:
                break
            r += 1


if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    print fannkuch(n)
