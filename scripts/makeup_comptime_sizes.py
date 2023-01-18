#!/usr/bin/env python3

import csv
import os
import re

import progressbar

BENCHS = [
    "Dispatch",
    "Fibonacci",
    "Sum",
    "Recurse",
    "Loop",
    "Sieve",
    "List",
    "Storage",
    "Queens",
    'Mandelbrot',
    'Fannkuch',
    'Bounce',
    'BubbleSort',
    'TreeSort',
    'QuickSort',
]

N = 10

THRESHOLD = {'Dispatch': 23}

INNER_ITER = {'Fannkuch': 9}

OUTER_ITER = {'Mandelbrot': 1000}

bar = progressbar.ProgressBar(maxval=len(BENCHS), \
    widgets=[progressbar.Bar('=', '[', ']'), ' ', progressbar.Percentage()])

def collect_trace_info():
    pypylog = "PYPYLOG=jit-backend-count,jit-summary:data/%s_%d.trace"

    bar.start()
    progress = 0
    for bm in BENCHS:
        threshold = 157
        outerIter = 1
        innerIter = 1000
        if bm in INNER_ITER:
            innerIter = INNER_ITER[bm]

        if bm in THRESHOLD:
            threshold = THRESHOLD[bm]

        if bm in OUTER_ITER:
            innerIter = 1
            outerIter = OUTER_ITER[bm]

        tier1 = "./som-bc-jit-tier1 --jit function_threshold=%d --jit threshold=%d" % (
            threshold,
            threshold,
        )
        tier2 = "./som-bc-jit-tier2 --jit threshold=%d" % (threshold)

        for i in range(1, N + 1):
            pypylog_with_output = pypylog % (bm + "_tier1", i)
            command_tier1 = (
                "%s %s -cp Smalltalk:Examples/Benchmarks/LanguageFeatures Examples/Benchmarks/BenchmarkHarness.som %s %d %d >/dev/null"
                % (pypylog_with_output, tier1, bm, outerIter, innerIter)
            )
            os.system(command_tier1)
            pypylog_with_output = pypylog % (bm + "_tier2", i)
            command_tier2 = (
                "%s %s -cp Smalltalk:Examples/Benchmarks/LanguageFeatures Examples/Benchmarks/BenchmarkHarness.som %s %d %d >/dev/null"
                % (pypylog_with_output, tier2, bm, outerIter, innerIter)
            )
            os.system(command_tier2)
        bar.update(progress)
        progress += 1
    bar.finish()


def _calc_compilation_time(path):
    backend_time = 0.0
    with open(path) as f:
        for line in f:
            line = line.strip("\n")
            if line.startswith("Tracing:"):
                backend_time += float(re.split(r"\t+", line.rstrip("\t"))[-1])

            if line.startswith("Backend:"):
                backend_time += float(re.split(r"\t+", line.rstrip("\t"))[-1])

        return backend_time * 1e3


def _aggregate_compilation_time(bm, times, tier):
    for i in range(1, N + 1):
        path = "data/%s_%s_%d.trace" % (bm, tier, i)
        times.append(_calc_compilation_time(path))


def calc_total_compilation_time():
    f = open('compilation_time.csv', 'w')
    writer = csv.writer(f)
    header = ["Benchmark", "Threaded code", "Tracing JIT"]
    writer.writerow(header)
    for bm  in BENCHS:
        backend_times_tier1 = []
        backend_times_tier2 = []
        _aggregate_compilation_time(bm, backend_times_tier1, "tier1")
        _aggregate_compilation_time(bm, backend_times_tier2, "tier2")
        ave_tier1 = sum(backend_times_tier1) / len(backend_times_tier1)
        ave_tier2 = sum(backend_times_tier2) / len(backend_times_tier2)
        data = [bm, round(ave_tier1, 3), round(ave_tier2, 3)]
        writer.writerow(data)
    f.close()


def _get_trace_ops(path):
    with open(path) as f:
        for line in f:
            line = line.strip("\n")
            if line.startswith('ops'):
                return int(re.split(r"\t+", line.rstrip("\t"))[-1])


def calc_trace_ops():
    result_tracing = {}
    result_threaded = {}
    for bm  in BENCHS:
        for tier, name in [('tier1', 'threaded code'), ('tier2', 'tracing')]:
            l = []
            for i in range(1, N + 1):
                path = "data/%s_%s_%d.trace" % (bm, tier, i)
                l.append(_get_trace_ops(path))
            if tier == 'tier1':
                result_threaded[bm] = int(sum(l) / len(l))
            elif tier == 'tier2':
                result_tracing[bm] = int(sum(l) / len(l))
            else:
                assert False, "unsupported tier " + tier

    f = open('code_size.csv', 'w')
    writer = csv.writer(f)
    header = ["Benchmark", "Threaded code", "Tracing JIT"]
    writer.writerow(header)
    for bm in BENCHS:
        data = [bm, result_threaded[bm], result_tracing[bm]]
        writer.writerow(data)
    f.close()


def main():
    if not os.path.isdir("data"):
        os.mkdir("data")

    collect_trace_info()
    calc_total_compilation_time()
    calc_trace_ops()


if __name__ == "__main__":
    main()
