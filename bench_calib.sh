#!/usr/bin/env bash
# Print tier3 steady-state min for a benchmark at a given inner size (calibration).
set -u
CP=$(cat /tmp/bench_cp.txt)
H="Examples/Benchmarks/BenchmarkHarness.som"
for pair in "$@"; do
  b=${pair%%:*}; n=${pair##*:}
  t=$(./som-bc-jit-tier3 -cp "$CP" "$H" "$b" 8 "$n" 2>/dev/null \
      | grep runtime: | sed 's/.*runtime: //; s/us//' | sort -n | head -1)
  printf "%-22s inner=%-8s min=%sus\n" "$b" "$n" "$t"
done
