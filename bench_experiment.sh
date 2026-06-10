#!/usr/bin/env bash
# Large-scale composite-workload eval: each ExperimentN / ExperimentStableN subclasses
# BenchmarkHarness and runs a SEQUENCE of many benchmark classes (NBody, PageRank, Bounce,
# Json, DeltaBlue, CD, ...) in ONE process -- a realistic mixed workload that makes the
# shared harness/dispatch sites see ~10-15 receiver classes. Reports tier3 vs tier4 (min
# elapsed seconds over N single-shot invocations) and the ratio.
set -u
N="${1:-10}"
CP="core-lib/Smalltalk:Examples/Benchmarks:Examples/Benchmarks/NBody:Examples/Benchmarks/Json:Examples/Benchmarks/DeltaBlue:Examples/Benchmarks/CD"
OUT="${2:-/tmp/exp_res.tsv}"; : > "$OUT"

BL=""
for k in $(seq 1 20); do BL="$BL Experiment/Experiment${k}"; done
BL="$BL Experiment/ExperimentRandom"
for k in $(seq 1 20); do BL="$BL ExperimentStable/ExperimentStable${k}"; done
BL="$BL ExperimentStable/ExperimentStable"

minrun () { # bin file -> min seconds over N invocations
  local best=99 v i
  for i in $(seq 1 "$N"); do
    v=$(./"$1" -cp "$CP" "$2" 2>/dev/null | tail -1)
    best=$(awk -v a="$v" -v b="$best" 'BEGIN{print (a+0>0 && a+0<b+0)?a:b}')
  done
  echo "$best"
}

printf "%-26s %9s %9s %7s\n" benchmark tier3_s tier4_s t4/t3
printf -- "------------------------------------------------------------\n"
for path in $BL; do
  f="Examples/Benchmarks/${path}.som"; b=${path##*/}
  [ -f "$f" ] || continue
  b3=$(minrun som-bc-jit-tier3 "$f")
  b4=$(minrun som-bc-jit-tier4 "$f")
  r=$(awk -v a="$b4" -v b="$b3" 'BEGIN{if(b+0>0)printf "%.3f",a/b; else print "NA"}')
  printf "%-26s %9s %9s %7s\n" "$b" "$b3" "$b4" "$r"
  printf "%s\t%s\t%s\t%s\n" "$b" "$b3" "$b4" "$r" >> "$OUT"
done
printf -- "------------------------------------------------------------\n"
awk -F'\t' '{if($4+0>0){s+=log($4);n++}} END{if(n>0)printf "geomean t4/t3 = %.4f over %d benchmarks\n",exp(s/n),n}' "$OUT"
echo "slower(>1.05): $(awk -F'\t' '$4+0>1.05{printf "%s(%s) ",$1,$4}' "$OUT")"
echo "faster(<0.95): $(awk -F'\t' '$4+0<0.95 && $4+0>0{printf "%s(%s) ",$1,$4}' "$OUT")"
