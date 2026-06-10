#!/usr/bin/env bash
# Benchmark tier2 (stack inliner), tier3 (tracing JIT) and tier4 (adaptive hybrid)
# on the Examples/Benchmarks suite via BenchmarkHarness.som.
#
# For each (tier, benchmark) it runs OUTER activations of INNER inner-iterations and
# records the *minimum* per-iteration runtime (microseconds) as the steady-state
# number -- the min naturally drops JIT warmup and, for tier4, the controller's A/B
# probing phase. It then prints a table with the tier2/tier3 and tier4/tier3 ratios
# so regressions are obvious (>1.00 = slower than the tracing baseline).
#
# Usage: ./bench_tiers.sh [OUTER] [extra som args...]
set -u

OUTER="${1:-40}"; shift || true
EXTRA_JIT="${*:-}"

CP="core-lib/Smalltalk:Examples/Benchmarks:Examples/Benchmarks/NBody:Examples/Benchmarks/LanguageFeatures:Examples/Benchmarks/GraphSearch:Examples/Benchmarks/Json:Examples/Benchmarks/DeltaBlue:Examples/Benchmarks/Richards:Examples/Benchmarks/CD"
HARNESS="Examples/Benchmarks/BenchmarkHarness.som"

# benchmark  inner   (tuned so steady-state is ~5-35ms on tier3)
BENCHES=(
  "Bounce 700"
  "BubbleSort 150"
  "Queens 120"
  "Permute 120"
  "Sieve 200"
  "Storage 5"
  "Towers 1"
  "Mandelbrot 150"
  "List 120"
  "Fannkuch 8"
  "NBody 50000"
  "GraphSearch 7"
  "QuickSort 3"
  "TreeSort 2"
  "Richards 1"
  "DeltaBlue 8"
  "Json 1"
)

# steady-state minimum runtime (us) for one tier+bench; empty on failure
run_one () {
  local bin="$1" bench="$2" inner="$3"
  ./"$bin" $EXTRA_JIT -cp "$CP" "$HARNESS" "$bench" "$OUTER" "$inner" 2>/dev/null \
    | grep "runtime:" | sed 's/.*runtime: //; s/us//' | sort -n | head -1
}

RESULTS="/tmp/bench_results.tsv"
: > "$RESULTS"
printf "%-12s %12s %12s %12s %9s %9s\n" "benchmark" "tier2(us)" "tier3(us)" "tier4(us)" "t2/t3" "t4/t3"
printf -- "------------------------------------------------------------------------------\n"

for spec in "${BENCHES[@]}"; do
  set -- $spec; bench="$1"; inner="$2"
  t2=$(run_one som-bc-jit-tier2 "$bench" "$inner")
  t3=$(run_one som-bc-jit-tier3 "$bench" "$inner")
  t4=$(run_one som-bc-jit-tier4 "$bench" "$inner")
  [ -z "$t2" ] && t2=0; [ -z "$t3" ] && t3=0; [ -z "$t4" ] && t4=0
  r23=$(awk -v a="$t2" -v b="$t3" 'BEGIN{ if(b>0) printf "%.2f", a/b; else print "-" }')
  r43=$(awk -v a="$t4" -v b="$t3" 'BEGIN{ if(b>0) printf "%.2f", a/b; else print "-" }')
  printf "%-12s %12s %12s %12s %9s %9s\n" "$bench" "$t2" "$t3" "$t4" "$r23" "$r43"
  printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$bench" "$t2" "$t3" "$t4" "$r23" "$r43" >> "$RESULTS"
done

printf -- "------------------------------------------------------------------------------\n"
# geometric means of the ratios (overall picture)
awk -F'\t' '{ if($5+0>0){s2+=log($5); n2++}; if($6+0>0){s4+=log($6); n4++} }
  END{ printf "geomean   t2/t3 = %.3f   t4/t3 = %.3f\n", exp(s2/n2), exp(s4/n4) }' "$RESULTS"
echo "(ratio >1.00 = slower than tier3; raw TSV at $RESULTS)"
