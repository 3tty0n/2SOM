#!/usr/bin/env bash
# Reproduce the adaptive tier-4 megamorphic-dispatch win over the tracing JIT (tier 3).
#
# Each MegaUnaryN / MegaTrivN benchmark (Examples/Benchmarks/Mega) cycles N receiver
# classes -- every one a TRIVIAL accessor (`value` / `compute:` returning a literal) --
# through a single send site inside a do:-loop. tier 3 dispatches this via a per-class
# guard/bridge chain that grows with N (O(N) per call, O(N^2) over the loop); tier 4's
# controller detects the megamorphic-trivial site and residualises it to one O(1) opaque
# dispatch behind a @jit.dont_look_inside barrier, resolved through a per-site polymorphic
# inline cache (BcMethod._mega_cache). Crossover ~24 classes (parity); >=20% from ~30
# (mega_floor): measured +22% @30, +40% @40, +50% @48, +62% @64, +72% @80.
#
# Output: steady-state min runtime (us) per tier and the tier4/tier3 ratio + gain%, with
# the controller's commit decision. Single process per measurement; run on an idle machine.
set -u
OUTER="${1:-30}"; INNER="${2:-40}"
CP="core-lib/Smalltalk:Examples/Benchmarks:Examples/Benchmarks/Mega"
H="Examples/Benchmarks/BenchmarkHarness.som"

steady () { # bin bench -> best steady-state min (us) over the last 3/4 of OUTER iters
  ./"$1" -cp "$CP" "$H" "$2" "$OUTER" "$INNER" 2>/dev/null \
    | grep "runtime:" | tail -$(( OUTER*3/4 )) | sed 's/.*runtime: //; s/us//' | sort -n | head -1
}

printf "%-14s %10s %10s %8s %8s   %-24s %s\n" benchmark tier3_us tier4_us t4/t3 gain commit goal
printf -- "--------------------------------------------------------------------------------------\n"
for b in MegaUnary40 MegaUnary48 MegaUnary64 MegaUnary80 MegaTriv40 MegaTriv48; do
  [ -f "Examples/Benchmarks/Mega/$b.som" ] || continue
  t3=$(steady som-bc-jit-tier3 "$b")
  t4=$(steady som-bc-jit-tier4 "$b")
  commit=$(SOM_T4_DEBUG=1 ./som-bc-jit-tier4 -cp "$CP" "$H" "$b" 3 "$INNER" 2>&1 \
           | grep -o "commit tier [34] ([a-z-]*)" | tail -1)
  r=$(awk -v a="$t4" -v b="$t3" 'BEGIN{if(b>0)printf "%.3f",a/b; else print "NA"}')
  g=$(awk -v r="$r" 'BEGIN{printf "%+.0f%%",(1-r)*100}')
  goal=$(awk -v r="$r" 'BEGIN{print (r<0.80)?"PASS (>=20%)":""}')
  printf "%-14s %10s %10s %8s %8s   %-24s %s\n" "$b" "$t3" "$t4" "$r" "$g" "$commit" "$goal"
done
printf -- "--------------------------------------------------------------------------------------\n"
echo "tier4 commits 'mega' and beats tier3 by >=20% once N crosses ~30 classes (mega_floor; see Mega/)."
