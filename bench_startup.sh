#!/usr/bin/env bash
# Micro-startup eval: SUM of the first-K iteration runtimes (warmup cost), best
# (lowest) sum over R process runs, tier3 vs tier4. Mirrors the runbench
# micro-startup configs (few iterations, small problem sizes).
set -u
K="${1:-10}"; R="${2:-5}"
CP="core-lib/Smalltalk:Examples/Benchmarks:Examples/Benchmarks/LanguageFeatures:Examples/Benchmarks/Json:Examples/Benchmarks/DeltaBlue:Examples/Benchmarks/NBody:Examples/Benchmarks/Richards:Examples/Benchmarks/GraphSearch"
H="Examples/Benchmarks/BenchmarkHarness.som"

BENCHES=(
  Bounce:10 List:2 Towers:2 Queens:5 Sieve:10 Permute:5 Storage:2
  Mandelbrot:30 Richards:1 Json:1 DeltaBlue:2 NBody:5000
  Fibonacci:10 Dispatch:10 Recurse:10 GraphSearch:4 PageRank:30
)

startup_sum () { # bin bench inner -> best sum-of-first-K runtimes over R runs
  local bin=$1 b=$2 n=$3 best=99999999999 r s
  for r in $(seq 1 "$R"); do
    s=$(./"$bin" -cp "$CP" "$H" "$b" "$K" "$n" 2>/dev/null \
        | grep "runtime:" | sed 's/.*runtime: //; s/us//' | awk '{t+=$1} END{print t+0}')
    [ -n "$s" ] && [ "$s" -gt 0 ] && [ "$s" -lt "$best" ] && best=$s
  done
  echo "$best"
}

RES="${3:-/tmp/bench_startup.tsv}"; : > "$RES"
printf "%-16s %12s %12s %7s\n" benchmark tier3_us tier4_us t4/t3
printf -- "----------------------------------------------------\n"
for spec in "${BENCHES[@]}"; do
  b=${spec%%:*}; n=${spec##*:}
  t3=$(startup_sum som-bc-jit-tier3 "$b" "$n")
  t4=$(startup_sum som-bc-jit-tier4 "$b" "$n")
  ratio=$(awk -v a="$t4" -v b="$t3" 'BEGIN{if(b>0)printf "%.3f",a/b; else print "NA"}')
  printf "%-16s %12s %12s %7s\n" "$b" "$t3" "$t4" "$ratio"
  printf "%s\t%s\t%s\t%s\n" "$b" "$t3" "$t4" "$ratio" >> "$RES"
done
printf -- "----------------------------------------------------\n"
awk -F'\t' '{if($4+0>0){s+=log($4);n++}} END{if(n>0)printf "geomean t4/t3 = %.4f over %d\n",exp(s/n),n}' "$RES"
