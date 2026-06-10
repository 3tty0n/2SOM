#!/usr/bin/env bash
# Measure tier4 vs tier3 (the adaptive hybrid vs the tracing baseline) across ALL
# runnable benchmarks in Examples/Benchmarks. Reports steady-state min (us) and the
# tier4/tier3 ratio; flags any benchmark where tier4 is >10% slower (degradation).
set -u
OUTER="${1:-40}"
CP=$(cat /tmp/bench_cp.txt)
H="Examples/Benchmarks/BenchmarkHarness.som"

# benchmark:inner  (inner tuned for ~5-40ms steady-state on tier3)
BENCHES=(
  Bounce:700 BubbleSort:150 Queens:120 Permute:120 Sieve:200 Storage:5 Towers:1
  Mandelbrot:150 List:120 Fannkuch:8 NBody:50000 GraphSearch:7 QuickSort:3
  TreeSort:2 Richards:1 DeltaBlue:8 Json:1
  Dispatch:160 Fibonacci:350 FieldLoop:100 FieldWrite:400 IntegerLoop:400
  JsonSmall:80 Loop:200 LoopWhileTrue:200 NonLocalReturn:15000 PageRank:50
  Recurse:700 Sum:250 WhileLoop:250 WhileLoopPoly:120
  StressStartupFibonacci:5 StressStartupRecurse:1
)

run_one () { # bin bench inner
  ./"$1" -cp "$CP" "$H" "$2" "$OUTER" "$3" 2>/dev/null \
    | grep "runtime:" | sed 's/.*runtime: //; s/us//' | sort -n | head -1
}

RES="/tmp/bench_all.tsv"; : > "$RES"
printf "%-22s %11s %11s %8s  %s\n" "benchmark" "tier3(us)" "tier4(us)" "t4/t3" "flag"
printf -- "-----------------------------------------------------------------\n"
for spec in "${BENCHES[@]}"; do
  b=${spec%%:*}; n=${spec##*:}
  t3=$(run_one som-bc-jit-tier3 "$b" "$n"); t4=$(run_one som-bc-jit-tier4 "$b" "$n")
  [ -z "$t3" ] && t3=0; [ -z "$t4" ] && t4=0
  r=$(awk -v a="$t4" -v b="$t3" 'BEGIN{ if(b>0) printf "%.2f", a/b; else print "NA" }')
  flag=$(awk -v r="$r" 'BEGIN{ if(r>1.10) print "<< DEGRADED"; else if(r<0.97) print "(faster)"; else print "" }')
  printf "%-22s %11s %11s %8s  %s\n" "$b" "$t3" "$t4" "$r" "$flag"
  printf "%s\t%s\t%s\t%s\n" "$b" "$t3" "$t4" "$r" >> "$RES"
done
printf -- "-----------------------------------------------------------------\n"
awk -F'\t' '{ if($4+0>0){s+=log($4); n++} } END{ printf "geomean t4/t3 = %.3f over %d benchmarks\n", exp(s/n), n }' "$RES"
echo "degraded (>1.10): $(awk -F'\t' '$4+0>1.10{c++} END{print c+0}' "$RES")   raw: $RES"
