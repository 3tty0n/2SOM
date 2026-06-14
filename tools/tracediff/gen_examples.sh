#!/bin/sh
# gen_examples.sh -- capture jit-log-opt / jit-summary / jit-tracing for
# three benchmarks under tier3, tier5, tier5resid, plus towers tier5phase.
# Output: traces/<bench>.<variant>.<ext>  (30 files total)
set -e
cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"
BIN="$ROOT/som-bc-jit-tier5"
[ -x "$BIN" ] || { echo "binary not found: $BIN"; exit 1; }

TRACES="$(pwd)/traces"
mkdir -p "$TRACES"

CPB="$ROOT/Smalltalk:$ROOT/Examples/Benchmarks:$ROOT/Examples/Benchmarks/NBody:$ROOT/Examples/Benchmarks/LanguageFeatures:$ROOT/Examples/Benchmarks/TestSuite"
HARNESS="$ROOT/Examples/Benchmarks/BenchmarkHarness.som"

MAX_TRACING_LINES=200000

# cap BENCH STEM VARIANT_ENV A1 A2
# Three runs: one per log category -> .opt, .summary, .tracing
cap() {
    BENCH="$1"; STEM="$2"; VENV="$3"; A1="$4"; A2="$5"
    env $VENV PYPYLOG="jit-log-opt:$TRACES/$STEM.opt" \
        "$BIN" -cp "$CPB" "$HARNESS" "$BENCH" "$A1" "$A2" >/dev/null 2>&1
    env $VENV PYPYLOG="jit-summary:$TRACES/$STEM.summary" \
        "$BIN" -cp "$CPB" "$HARNESS" "$BENCH" "$A1" "$A2" >/dev/null 2>&1
    env $VENV PYPYLOG="jit-tracing:$TRACES/$STEM.tracing" \
        "$BIN" -cp "$CPB" "$HARNESS" "$BENCH" "$A1" "$A2" >/dev/null 2>&1
    # truncate oversized tracing files
    TR="$TRACES/$STEM.tracing"
    if [ -f "$TR" ] && [ "$(wc -l < "$TR")" -gt "$MAX_TRACING_LINES" ]; then
        tmp="$TR.tmp"
        head -n "$MAX_TRACING_LINES" "$TR" > "$tmp" && mv "$tmp" "$TR"
        echo "  [truncated $STEM.tracing to $MAX_TRACING_LINES lines]"
    fi
    CA=$(grep -c "call_assembler" "$TRACES/$STEM.opt" 2>/dev/null || true)
    printf "  %-36s  call_assembler=%s\n" "$STEM" "$CA"
}

echo "=== tier3 (no SOM_T5) ==="
cap Bounce  bounce.tier3  ""                                              20 1
cap Queens  queens.tier3  ""                                              20 1
cap Towers  towers.tier3  ""                                              30 1

echo "=== tier5 counter (SOM_T5=1) ==="
cap Bounce  bounce.tier5  "SOM_T5=1"                                     20 1
cap Queens  queens.tier5  "SOM_T5=1"                                     20 1
cap Towers  towers.tier5  "SOM_T5=1"                                     30 1

echo "=== tier5resid (warmup-only, max contrast) ==="
cap Bounce  bounce.tier5resid  "SOM_T5=1 SOM_T5_QUIESCE=100000000 SOM_T5_QUIESCE_GC=100000000"  20 1
cap Queens  queens.tier5resid  "SOM_T5=1 SOM_T5_QUIESCE=100000000 SOM_T5_QUIESCE_GC=100000000"  20 1
cap Towers  towers.tier5resid  "SOM_T5=1 SOM_T5_QUIESCE=100000000 SOM_T5_QUIESCE_GC=100000000"  30 1

echo "=== tier5phase (Towers only: residualize->inline transition) ==="
cap Towers  towers.tier5phase  "SOM_T5=1 SOM_T5_QUIESCE=4000 SOM_T5_QUIESCE_GC=8 SOM_T5_PURGE=2"  30 1

echo
echo "=== traces/ listing ==="
ls -la "$TRACES/"

echo
echo "=== call_assembler counts (all .opt files) ==="
for f in "$TRACES/"*.opt; do
    stem="$(basename "$f" .opt)"
    CA=$(grep -c "call_assembler" "$f" 2>/dev/null || true)
    printf "  %-36s  call_assembler=%s\n" "$stem" "$CA"
done

echo
echo "restart: python3 tools/tracediff/server.py"
