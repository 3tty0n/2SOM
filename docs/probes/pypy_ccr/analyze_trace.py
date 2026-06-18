#!/usr/bin/env python
"""Mechanism-check analyzer: parse a PYPYLOG=jit-log-opt:FILE dump and report
the signals that prove the can_never_inline hook is obeyed:

  - max inline depth, from the trailing '~N' recursion marker on
    debug_merge_point lines (stock inlines callees -> depth >= 1; with
    PYPYTIER_PROMOTE=inf callees are residualized -> depth ~0);
  - number of residualizing call ops (call_assembler / call to the portal);
  - total optimized-trace op lines (a proxy for trace size).

Usage: python analyze_trace.py <jit-log-opt-file>
"""
import re
import sys

DMP = re.compile(r'debug_merge_point\(.*?,\s*(\d+),')  # the depth field
CALL_ASM = re.compile(r'\bcall_assembler\w*\(')
CALL_MAY = re.compile(r'\bcall_may_force\w*\(')


def main():
    path = sys.argv[1]
    with open(path) as f:
        lines = f.readlines()
    max_depth = 0
    n_dmp = 0
    n_callasm = 0
    n_callmay = 0
    n_ops = 0
    in_loop = False
    for ln in lines:
        if ln.startswith('[') and 'jit-log-opt-' in ln:
            in_loop = True
            continue
        if not in_loop:
            continue
        m = DMP.search(ln)
        if m:
            n_dmp += 1
            d = int(m.group(1))
            if d > max_depth:
                max_depth = d
        if CALL_ASM.search(ln):
            n_callasm += 1
        if CALL_MAY.search(ln):
            n_callmay += 1
        s = ln.strip()
        if s and not s.startswith('[') and not s.startswith('#') and '(' in s:
            n_ops += 1
    print("file:            %s" % path)
    print("max inline depth %d   (debug_merge_point depth field)" % max_depth)
    print("debug_merge_pts  %d" % n_dmp)
    print("call_assembler   %d" % n_callasm)
    print("call_may_force   %d" % n_callmay)
    print("opt-trace lines  %d" % n_ops)


if __name__ == '__main__':
    main()
