#!/usr/bin/env bash
# Probe whether the tier4 controller's profiling-pass count (SOM_CB_CNT_MAXINV) is the
# warmup overhead on the short composite Experiment workloads -- and whether lowering it
# breaks the megamorphic win. Deterministic-ish: min elapsed over N invocations.
set -u
N="${1:-8}"
ECP="core-lib/Smalltalk:Examples/Benchmarks:Examples/Benchmarks/NBody:Examples/Benchmarks/Json:Examples/Benchmarks/DeltaBlue:Examples/Benchmarks/CD"
MCP="core-lib/Smalltalk:Examples/Benchmarks:Examples/Benchmarks/Mega"
H="Examples/Benchmarks/BenchmarkHarness.som"

min_exp () { # bin file [env...] -> min seconds (single-shot composite)
  local best=99 v i
  for i in $(seq 1 "$N"); do
    v=$(env "${@:3}" ./"$1" -cp "$ECP" "$2" 2>/dev/null | tail -1)
    best=$(awk -v a="$v" -v b="$best" 'BEGIN{print (a+0>0 && a+0<b+0)?a:b}')
  done
  echo "$best"
}
min_mega () { # bin bench [env...] -> min steady us
  local best=99999999 v i
  for i in 1 2 3; do
    v=$(env "${@:3}" ./"$1" -cp "$MCP" "$H" "$2" 25 40 2>/dev/null | grep runtime: | tail -18 | sed 's/.*runtime: //; s/us//' | sort -n | head -1)
    [ -n "$v" ] && [ "$v" -lt "$best" ] 2>/dev/null && best=$v
  done
  echo "$best"
}

echo "=== Experiment: tier3 baseline vs tier4 at maxinv {4,2,1} (ratio t4/t3, lower=better) ==="
printf "%-14s %9s %9s %9s %9s\n" benchmark tier3 t4_inv4 t4_inv2 t4_inv1
for b in Experiment1 Experiment5 Experiment14 Experiment17; do
  f="Examples/Benchmarks/Experiment/$b.som"
  t3=$(min_exp som-bc-jit-tier3 "$f")
  i4=$(min_exp som-bc-jit-tier4 "$f")
  i2=$(min_exp som-bc-jit-tier4 "$f" SOM_CB_CNT_MAXINV=2)
  i1=$(min_exp som-bc-jit-tier4 "$f" SOM_CB_CNT_MAXINV=1)
  r4=$(awk -v a="$i4" -v b="$t3" 'BEGIN{printf "%.3f",a/b}')
  r2=$(awk -v a="$i2" -v b="$t3" 'BEGIN{printf "%.3f",a/b}')
  r1=$(awk -v a="$i1" -v b="$t3" 'BEGIN{printf "%.3f",a/b}')
  printf "%-14s %9s %9s %9s %9s\n" "$b" "$t3" "$r4" "$r2" "$r1"
done
echo
echo "=== Mega win must survive: MegaUnary40 tier4 commit + ratio at maxinv {4,2,1} ==="
t3m=$(min_mega som-bc-jit-tier3 MegaUnary40)
for inv in 4 2 1; do
  t4m=$(min_mega som-bc-jit-tier4 MegaUnary40 SOM_CB_CNT_MAXINV=$inv)
  commit=$(SOM_CB_CNT_MAXINV=$inv SOM_T4_DEBUG=1 ./som-bc-jit-tier4 -cp "$MCP" "$H" MegaUnary40 3 40 2>&1 | grep -oE "commit tier [34] \([a-z-]*\)" | sort | uniq -c | tr '\n' ' ')
  r=$(awk -v a="$t4m" -v b="$t3m" 'BEGIN{printf "%.3f",a/b}')
  printf "maxinv=%s  MegaUnary40 t4/t3=%s  [%s]\n" "$inv" "$r" "$commit"
done
