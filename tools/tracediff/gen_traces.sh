#!/bin/sh
# gen_traces.sh -- generate curated jit-log-opt dumps into traces/
# for use by the tracediff dashboard (server.py).
#
# Files are named <bench>.<tierPolicy>.opt so the dashboard can group
# by the part before the first .tierXXX.
set -e
cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"
T2="$ROOT/som-bc-jit-tier2"
T5="$ROOT/som-bc-jit-tier5"
[ -x "$T2" ] || { echo "binary not found: $T2"; exit 1; }
[ -x "$T5" ] || { echo "binary not found: $T5"; exit 1; }

mkdir -p traces
TRACES="$(pwd)/traces"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Tiny program: hot loop whose body sends `self inc: i` (non-recursive).
cat > "$WORK/TraceDemo.som" <<'SOM'
TraceDemo = (
    inc: x = ( ^ x + 1 )
    run = ( |sum| sum := 0.
        1 to: 100000 do: [:i | sum := sum + (self inc: i) ]. ^ sum )
)
SOM

CPD="$ROOT/Smalltalk:$WORK"
CPB="$ROOT/Smalltalk:$ROOT/Examples/Benchmarks:$ROOT/Examples/Benchmarks/NBody:$ROOT/Examples/Benchmarks/LanguageFeatures:$ROOT/Examples/Benchmarks/TestSuite"
HARNESS="$ROOT/Examples/Benchmarks/BenchmarkHarness.som"

CA() { grep -c "call_assembler" "$TRACES/$1" 2>/dev/null || true; }
note() { printf "  %-36s  call_assembler=%s\n" "$1" "$(CA "$1")"; }

# ---- TraceDemo: tier2 (stack inliner) ----
PYPYLOG=jit-log-opt:"$TRACES/tracedemo.tier2.opt" \
    "$T2" -cp "$CPD" "$WORK/TraceDemo.som" >/dev/null 2>&1
note tracedemo.tier2.opt

# ---- TraceDemo: tier3 (tier5 binary, no SOM_T5) ----
PYPYLOG=jit-log-opt:"$TRACES/tracedemo.tier3.opt" \
    "$T5" -cp "$CPD" "$WORK/TraceDemo.som" >/dev/null 2>&1
note tracedemo.tier3.opt

# ---- TraceDemo: tier5 counter mode ----
PYPYLOG=jit-log-opt:"$TRACES/tracedemo.tier5counter.opt" \
    SOM_T5=1 "$T5" -cp "$CPD" "$WORK/TraceDemo.som" >/dev/null 2>&1
note tracedemo.tier5counter.opt

# ---- TraceDemo: tier5 phase mode, warmup-only (quiesce never fires) ----
PYPYLOG=jit-log-opt:"$TRACES/tracedemo.tier5phase.opt" \
    SOM_T5=1 SOM_T5_QUIESCE=100000000 SOM_T5_QUIESCE_GC=100000000 \
    "$T5" -cp "$CPD" "$WORK/TraceDemo.som" >/dev/null 2>&1
note tracedemo.tier5phase.opt

# ---- Towers: tier3 ----
PYPYLOG=jit-log-opt:"$TRACES/towers.tier3.opt" \
    "$T5" -cp "$CPB" "$HARNESS" --gc Towers 30 1 >/dev/null 2>&1
note towers.tier3.opt

# ---- Towers: tier5 phase mode (quiesce fires mid-run -> recompile) ----
PYPYLOG=jit-log-opt:"$TRACES/towers.tier5phase.opt" \
    SOM_T5=1 SOM_T5_QUIESCE=4000 SOM_T5_QUIESCE_GC=8 SOM_T5_PURGE=2 \
    "$T5" -cp "$CPB" "$HARNESS" --gc Towers 30 1 >/dev/null 2>&1
note towers.tier5phase.opt

# ---- Fibonacci: tier3 (deep recursion -> residualized by PyPy bound) ----
PYPYLOG=jit-log-opt:"$TRACES/fibonacci.tier3.opt" \
    "$T5" -cp "$CPB" "$HARNESS" --gc Fibonacci 5 1 >/dev/null 2>&1
note fibonacci.tier3.opt

# ---- Fibonacci: tier5 counter mode (same structural residualization) ----
PYPYLOG=jit-log-opt:"$TRACES/fibonacci.tier5counter.opt" \
    SOM_T5=1 \
    "$T5" -cp "$CPB" "$HARNESS" --gc Fibonacci 5 1 >/dev/null 2>&1
note fibonacci.tier5counter.opt

echo
ls -lh "$TRACES"/*.opt
echo
echo "Now run:  python3 tools/tracediff/server.py   and open the printed URL"
