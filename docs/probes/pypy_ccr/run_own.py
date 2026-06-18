#!/usr/bin/env python
"""Run pypy/benchmarks own/ benchmarks under stock vs B1 thresholds.

Each benchmark prints N per-run times (-n N): the first run is cold (includes
JIT tracing), later runs are steady. We separate:
  - warmup  = run[0]                 (cold first run, best over R repeats)
  - steady  = min over all timings   (fully warmed)
  - total   = sum of timings         (best over R repeats)
and report B1/stock ratios for warmup and steady -- the whole question is
whether B1 trims warmup without wrecking steady.

Usage: python run_own.py <pypy-c> <own_dir> <N> <R> -- bench1 bench2 ... [@ thresholds...]
"""
import os
import subprocess
import sys
import time

DEF_THRESH = ['off', '2048', 'inf']


def run(pypy, promote, script, cwd):
    env = dict(os.environ)
    env.pop('PYPYLOG', None)
    env.pop('PYPYTIER_PROMOTE', None)
    env.pop('PYPYTIER_MINSIZE', None)
    env.pop('PYPYTIER_LOOPGATE', None)
    if promote != 'off':
        # config token: "PROMOTE[:MINSIZE[:L]]"; trailing ":L" enables the loop-gate
        parts = promote.split(':')
        env['PYPYTIER_PROMOTE'] = parts[0]
        if len(parts) >= 2 and parts[1]:
            env['PYPYTIER_MINSIZE'] = parts[1]
        if len(parts) >= 3 and parts[2].upper() == 'L':
            env['PYPYTIER_LOOPGATE'] = '1'
    t0 = time.time()
    p = subprocess.Popen([pypy, script + '.py', '-n', str(N)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         cwd=cwd, env=env)
    out, err = p.communicate()
    wall = time.time() - t0
    times = []
    for line in out.strip().splitlines():
        line = line.strip()
        try:
            times.append(float(line))
        except ValueError:
            pass
    return wall, p.returncode, times, err


def measure(pypy, promote, script, cwd, R):
    warm = []
    allt = []
    totals = []
    rc = 0
    for _ in range(R):
        wall, rc, times, err = run(pypy, promote, script, cwd)
        if rc != 0 or not times:
            sys.stderr.write("FAIL %s/%s rc=%d %s\n" % (script, promote, rc, err[-200:]))
            return None
        warm.append(times[0])
        totals.append(sum(times))
        allt.extend(times)
    return {'warm': min(warm), 'steady': min(allt), 'total': min(totals)}


def main():
    global N
    argv = sys.argv[1:]
    pypy, own, N, R = argv[0], argv[1], int(argv[2]), int(argv[3])
    rest = argv[4:]
    assert rest and rest[0] == '--'
    rest = rest[1:]
    if '@' in rest:
        i = rest.index('@')
        benches, thresholds = rest[:i], rest[i + 1:]
    else:
        benches, thresholds = rest, DEF_THRESH

    print("%-16s %8s | %-22s | %-22s" % ('bench (n=%d r=%d)' % (N, R), 'cfg',
                                         'warmup(run0)', 'steady(min)'))
    for b in benches:
        base = {}
        for p in thresholds:
            m = measure(pypy, p, b, own, R)
            if m is None:
                print("%-16s %8s   FAILED" % (b, p))
                continue
            if p == 'off':
                base = m
            wr = m['warm'] / base['warm'] if base else 1.0
            sr = m['steady'] / base['steady'] if base else 1.0
            wtag = '' if p == 'off' else ('  x%.3f' % wr)
            stag = '' if p == 'off' else ('  x%.3f' % sr)
            print("%-16s %8s | %10.5f%-12s | %10.6f%-12s" % (
                b, p, m['warm'], wtag, m['steady'], stag))
        print("")
    sys.stdout.flush()


if __name__ == '__main__':
    main()
