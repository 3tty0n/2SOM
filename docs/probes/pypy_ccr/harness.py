#!/usr/bin/env python
"""Phase-0 harness for the PyPy tieredjit B1 backend.

Measures, for a workload, stock PyPy (PYPYTIER_PROMOTE unset) vs the tiered
backend at a sweep of promotion thresholds:
  - cold-run wall time (min + median over K repeats: every run is a fresh
    process, so this IS the warmup/cold-start metric);
  - JIT tracing + backend seconds and loop/bridge counts (PYPYLOG=jit-summary).

Usage:
  python harness.py <pypy-c> <workload.py> [args...] -- [promote values...]
Defaults to thresholds: off(stock) inf 256 2048 8192
"""
import os
import re
import subprocess
import sys
import time

K = 7  # wall-time repeats per config


def run_once(pypy, env, script_args):
    t0 = time.time()
    p = subprocess.Popen([pypy] + script_args,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=env)
    out, err = p.communicate()
    dt = time.time() - t0
    return dt, p.returncode, out, err


def wall(pypy, promote, script_args):
    env = dict(os.environ)
    env.pop('PYPYLOG', None)
    if promote is None:
        env.pop('PYPYTIER_PROMOTE', None)
    else:
        env['PYPYTIER_PROMOTE'] = str(promote)
    times = []
    rc = 0
    last_out = b''
    for _ in range(K):
        dt, rc, out, err = run_once(pypy, env, script_args)
        if rc != 0:
            sys.stderr.write("FAILED rc=%d\n%s\n" % (rc, err.decode('utf-8', 'replace')))
            return None, rc, out
        times.append(dt)
        last_out = out
    times.sort()
    mn = times[0]
    md = times[len(times) // 2]
    return (mn, md), rc, last_out


SUMRE = {
    'tracing': re.compile(r'Tracing:\s+(\d+)\s+([\d.]+)'),
    'backend': re.compile(r'Backend:\s+(\d+)\s+([\d.]+)'),
    'loops': re.compile(r'Total # of loops:\s+(\d+)'),
    'bridges': re.compile(r'Total # of bridges:\s+(\d+)'),
    'ops': re.compile(r'^ops:\s+(\d+)', re.M),
}


def jitsummary(pypy, promote, script_args):
    env = dict(os.environ)
    logf = '/tmp/pypy_ccr_jitsum.log'
    env['PYPYLOG'] = 'jit-summary:' + logf
    if promote is None:
        env.pop('PYPYTIER_PROMOTE', None)
    else:
        env['PYPYTIER_PROMOTE'] = str(promote)
    if os.path.exists(logf):
        os.remove(logf)
    run_once(pypy, env, script_args)
    text = ''
    if os.path.exists(logf):
        with open(logf) as f:
            text = f.read()
    res = {}
    m = SUMRE['tracing'].search(text)
    res['trace_n'], res['trace_s'] = (int(m.group(1)), float(m.group(2))) if m else (0, 0.0)
    m = SUMRE['backend'].search(text)
    res['back_n'], res['back_s'] = (int(m.group(1)), float(m.group(2))) if m else (0, 0.0)
    m = SUMRE['loops'].search(text)
    res['loops'] = int(m.group(1)) if m else 0
    m = SUMRE['bridges'].search(text)
    res['bridges'] = int(m.group(1)) if m else 0
    return res


def main():
    argv = sys.argv[1:]
    if '--' in argv:
        i = argv.index('--')
        head, promotes = argv[:i], argv[i + 1:]
    else:
        head, promotes = argv, ['off', 'inf', '256', '2048', '8192']
    pypy = head[0]
    script_args = head[1:]

    def to_promote(p):
        return None if p == 'off' else p

    print("workload: %s" % ' '.join(script_args))
    print("pypy:     %s" % pypy)
    print("repeats:  %d" % K)
    print("")
    print("%-8s %10s %10s %8s %8s %8s %8s %8s %7s %7s" % (
        'promote', 'wall_min', 'wall_med', 'jit_s', 'trace_s', 'back_s',
        'trace_n', 'back_n', 'loops', 'bridg'))
    base_min = None
    for p in promotes:
        pr = to_promote(p)
        w, rc, out = wall(pypy, pr, script_args)
        if w is None:
            print("%-8s  FAILED rc=%d" % (p, rc))
            continue
        js = jitsummary(pypy, pr, script_args)
        jit_s = js['trace_s'] + js['back_s']
        if p == 'off':
            base_min = w[0]
        ratio = (w[0] / base_min) if base_min else 1.0
        tag = ''
        if p != 'off' and base_min:
            tag = '  x%.3f' % ratio
        print("%-8s %10.4f %10.4f %8.3f %8.3f %8.3f %8d %8d %7d %7d%s" % (
            p, w[0], w[1], jit_s, js['trace_s'], js['back_s'],
            js['trace_n'], js['back_n'], js['loops'], js['bridges'], tag))
    sys.stdout.flush()


if __name__ == '__main__':
    main()
