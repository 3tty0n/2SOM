#!/usr/bin/env bash
# Discover every runnable benchmark in Examples/Benchmarks by probing each candidate
# class through BenchmarkHarness.som on tier3. A class is "runnable" if it produces an
# "average:" report line (i.e. it subclasses Benchmark and self-verifies).
set -u
CP=$(cat /tmp/bench_cp.txt)
H="Examples/Benchmarks/BenchmarkHarness.som"
BIN="${1:-som-bc-jit-tier3}"

cands=$(find Examples/Benchmarks -name "*.som" -exec basename {} .som \; | sort -u \
        | grep -vE "Test$|TestCase|TestRunner|TestGC|^Benchmark$|BenchmarkHarness|^Sort$")

: > /tmp/runnable.txt
n=0; ok=0
for b in $cands; do
  n=$((n+1))
  out=$(timeout 30 ./"$BIN" -cp "$CP" "$H" "$b" 1 2 2>&1)
  if echo "$out" | grep -q "average:"; then
    echo "$b" >> /tmp/runnable.txt
    ok=$((ok+1))
  fi
done
echo "probed $n candidates, $ok runnable"
sort -u /tmp/runnable.txt -o /tmp/runnable.txt
tr '\n' ' ' < /tmp/runnable.txt; echo
