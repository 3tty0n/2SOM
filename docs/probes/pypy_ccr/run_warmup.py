#!/usr/bin/env python
"""Run the official PyPy warmup benchmarks under stock vs B1 thresholds.

For benchmarks that print a trailing Python list of per-round times (the
warmup suite's convention), we sum it -> the *internal* warmup time, which
excludes VM startup + import noise. We also record total process wall.

Usage: python run_warmup.py <pypy-c> <bench.py> [-- thresholds...]
"""
import os
import subprocess
import sys
import time

K = 5
THRESHOLDS = ['off', '2048', '8192', 'inf']


def parse_trailing_list(out):
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith('[') and line.endswith(']'):
            try:
                vals = eval(line)
                if isinstance(vals, list) and vals and all(
                        isinstance(v, (int, float)) for v in vals):
                    return vals
            except Exception:
                pass
    return None


def run(pypy, promote, script, env_extra):
    env = dict(os.environ)
    env.pop('PYPYLOG', None)
    env.update(env_extra)
    if promote == 'off':
        env.pop('PYPYTIER_PROMOTE', None)
    else:
        env['PYPYTIER_PROMOTE'] = promote
    t0 = time.time()
    p = subprocess.Popen([pypy, script], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env)
    out, err = p.communicate()
    wall = time.time() - t0
    return wall, p.returncode, out, err


def main():
    argv = sys.argv[1:]
    thresholds = THRESHOLDS
    if '--' in argv:
        i = argv.index('--')
        thresholds = argv[i + 1:]
        argv = argv[:i]
    pypy, script = argv[0], argv[1]
    pp = os.path.dirname(os.path.dirname(os.path.abspath(pypy)))  # pypy repo root for imports
    env_extra = {'PYTHONPATH': pp}

    print("bench: %s" % script)
    print("%-6s %12s %12s %10s %10s" % (
        'promote', 'wall_min', 'internal', 'w_ratio', 'i_ratio'))
    base_w = base_i = None
    for p in thresholds:
        walls = []
        internal = None
        rc = 0
        for _ in range(K):
            w, rc, out, err = run(pypy, p, script, env_extra)
            if rc != 0:
                sys.stderr.write("FAIL %s rc=%d: %s\n" % (p, rc, err[-400:]))
                break
            walls.append(w)
            lst = parse_trailing_list(out)
            if lst is not None:
                s = sum(lst)
                internal = s if internal is None else min(internal, s)
        if rc != 0 or not walls:
            print("%-6s  FAILED" % p)
            continue
        wmin = min(walls)
        if p == 'off':
            base_w = wmin
            base_i = internal
        wr = wmin / base_w if base_w else 1.0
        ir = (internal / base_i) if (internal and base_i) else float('nan')
        istr = ('%.4f' % internal) if internal is not None else 'n/a'
        print("%-6s %12.4f %12s %10.3f %10.3f" % (p, wmin, istr, wr, ir))
    sys.stdout.flush()


if __name__ == '__main__':
    main()
