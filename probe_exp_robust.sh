#!/usr/bin/env bash
# Robust tier3-vs-tier4 comparison on the short Experiment workloads: many invocations,
# report MEDIAN (and min) seconds per tier. Short composite runs are ~50% JIT-compilation
# time with high variance, so a single min can mislead; the median is the honest center.
set -u
N="${1:-25}"
CP="core-lib/Smalltalk:Examples/Benchmarks:Examples/Benchmarks/NBody:Examples/Benchmarks/Json:Examples/Benchmarks/DeltaBlue:Examples/Benchmarks/CD"

stat () { # bin file -> "median min" over N invocations
  local f=/tmp/samples.txt; : > "$f"
  local i v
  for i in $(seq 1 "$N"); do
    v=$(./"$1" -cp "$CP" "$2" 2>/dev/null | tail -1)
    [ -n "$v" ] && echo "$v" >> "$f"
  done
  sort -n "$f" | awk '{a[NR]=$1} END{print a[int(NR/2)+1], a[1]}'
}

printf "%-16s %18s %18s %8s %8s\n" benchmark "tier3 (med/min)" "tier4 (med/min)" med_r min_r
printf -- "-----------------------------------------------------------------------------\n"
for b in Experiment1 Experiment5 Experiment14 Experiment17 Experiment19 ExperimentRandom; do
  f="Examples/Benchmarks/Experiment/$b.som"
  read t3med t3min < <(stat som-bc-jit-tier3 "$f")
  read t4med t4min < <(stat som-bc-jit-tier4 "$f")
  mr=$(awk -v a="$t4med" -v b="$t3med" 'BEGIN{if(b+0>0)printf "%.3f",a/b}')
  nr=$(awk -v a="$t4min" -v b="$t3min" 'BEGIN{if(b+0>0)printf "%.3f",a/b}')
  printf "%-16s %8s /%8s %8s /%8s %8s %8s\n" "$b" "$t3med" "$t3min" "$t4med" "$t4min" "$mr" "$nr"
done
