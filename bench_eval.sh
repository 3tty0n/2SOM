#!/usr/bin/env bash
# Comprehensive tier4-vs-tier3 evaluation: stable steady-state min over several process
# runs, plus the tier4 controller's per-benchmark commit decisions. Flags regressions
# (>1.05) and wins (<0.95); reports geomean and what tier4 committed.
set -u
OUTER="${1:-40}"; RUNS="${2:-4}"
CP=$(cat /tmp/bench_cp.txt)
H="Examples/Benchmarks/BenchmarkHarness.som"

BENCHES=(
  Bounce:700 BubbleSort:150 Queens:120 Permute:120 Sieve:200 Storage:5 Towers:1
  Mandelbrot:150 List:120 Fannkuch:8 NBody:50000 GraphSearch:7 QuickSort:3
  TreeSort:2 Richards:1 DeltaBlue:8 Json:1
  Dispatch:160 Fibonacci:350 FieldLoop:100 FieldWrite:400 IntegerLoop:400
  JsonSmall:80 Loop:200 LoopWhileTrue:200 NonLocalReturn:15000 PageRank:50
  Recurse:700 Sum:250 WhileLoop:250 WhileLoopPoly:120
  StressStartupFibonacci:5 StressStartupRecurse:1
)

steady_min () { # bin bench inner -> best steady-state min over RUNS process runs
  local bin=$1 b=$2 n=$3 best=99999999 r mm
  for r in $(seq 1 "$RUNS"); do
    mm=$(./"$bin" -cp "$CP" "$H" "$b" "$OUTER" "$n" 2>/dev/null \
         | grep "runtime:" | tail -$((OUTER*3/4)) | sed 's/.*runtime: //; s/us//' | sort -n | head -1)
    [ -n "$mm" ] && [ "$mm" -lt "$best" ] && best=$mm
  done
  echo "$best"
}

RES="/tmp/bench_eval.tsv"; : > "$RES"
printf "%-22s %9s %9s %7s  %-9s %s\n" benchmark tier3 tier4 t4/t3 commit flag
printf -- "---------------------------------------------------------------------\n"
for spec in "${BENCHES[@]}"; do
  b=${spec%%:*}; n=${spec##*:}
  t3=$(steady_min som-bc-jit-tier3 "$b" "$n")
  t4=$(steady_min som-bc-jit-tier4 "$b" "$n")
  # commit decisions from a short debug run
  dbg=$(SOM_T4_DEBUG=1 ./som-bc-jit-tier4 -cp "$CP" "$H" "$b" 3 "$n" 2>&1)
  mega=$(echo "$dbg" | grep -c "commit tier 4 (mega)")
  t4c=$(echo "$dbg" | grep "commit tier 4" | grep -vc mega)
  commit="t3"
  [ "$t4c" -gt 0 ] && commit="hybrid"
  [ "$mega" -gt 0 ] && commit="mega"
  r=$(awk -v a="$t4" -v b="$t3" 'BEGIN{if(b>0)printf "%.2f",a/b; else print "NA"}')
  flag=$(awk -v r="$r" 'BEGIN{if(r>1.05)print "<< SLOWER"; else if(r<0.95)print "(faster)"; else print ""}')
  printf "%-22s %9s %9s %7s  %-9s %s\n" "$b" "$t3" "$t4" "$r" "$commit" "$flag"
  printf "%s\t%s\t%s\t%s\t%s\n" "$b" "$t3" "$t4" "$r" "$commit" >> "$RES"
done
printf -- "---------------------------------------------------------------------\n"
awk -F'\t' '{if($4+0>0){s+=log($4);n++}} END{printf "geomean t4/t3 = %.4f over %d benchmarks\n",exp(s/n),n}' "$RES"
echo "slower (>1.05): $(awk -F'\t' '$4+0>1.05{print $1}' "$RES" | tr '\n' ' ')"
echo "faster (<0.95): $(awk -F'\t' '$4+0<0.95 && $4+0>0{print $1}' "$RES" | tr '\n' ' ')"
echo "committed hybrid/mega: $(awk -F'\t' '$5!="t3"{printf "%s(%s) ",$1,$5}' "$RES")"
