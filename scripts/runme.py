#!/usr/bin/env python3
import os
import subprocess

INVOCATIONS = 10
ITERATIONS = 100

BENCHS = {
    "Bounce": (350, 57),
    "BubbleSort": (500, 23),
    "Fannkuch": (9, 23),
    "Fibonacci": (500, 23),
    "GraphSearch": (30, 23),
    "List": (500, 23),
    "Mandelbrot": (350, 2),
    "NBody": (350, 2),
    "PageRank": (350, 23),
    "Permute": (250, 23),
    "Queens": (500, 23),
    "Recurse": (500, 23),
    "Sieve": (1250, 2),
    "Storage": (350, 57),
    "Sum": (500, 23),
    "TreeSort": (350, 23),
    "Towers": (350, 23),
}

NICE = ["nice", "-n-20"]

BINS = ["./som-bc-jit-tier1", "./som-bc-jit-tier2", "./som-bc-interp-tier1"]
# BINS = ["./som-bc-jit-tier2", "./som-bc-interp-tier1"]

ARGS = [
    "-cp",
    "Smalltalk:Examples/Benchmarks/GraphSearch:Examples/Benchmarks/NBody:Examples/Benchmarks/LanguageFeatures:Examples/Benchmarks/TestSuite",
    "Examples/Benchmarks/BenchmarkHarness.som",
    "--gc",
]

def mkdir(path):
    if os.path.exists(path):
        return
    os.mkdir(path)


def jit_threshold(threshold):
    return [
        "--jit",
        "threshold=%d" % threshold,
        "--jit",
        "function_threshold=%d" % threshold,
    ]


def parse_bin(bin_name):
    if bin_name == "./som-bc-jit-tier1":
        return "threaded"
    elif bin_name == "./som-bc-jit-tier2":
        return "tracing"
    elif bin_name == "./somb-bc-interp-tier1":
        return "interp"
    else:
        raise Exception


def measure_rss():
    def gnu_time(bm, inv, bin_name, output_dir):
        gnu_time = ["/usr/bin/time", "-f", "'RSS:%M KB'", "-o", "%s/%s_%s_%d.txt" % (
            output_dir, bm.lower(), bin_name, inv)]
        return gnu_time


    output_dir = "logs-rss"
    mkdir(output_dir)

    for binary in BINS:
        for bm in BENCHS:
            for inv in range(INVOCATIONS):
                extra_args, threshold = BENCHS[bm]
                command = (
                    NICE +
                    gnu_time(bm, inv, parse_bin(binary), output_dir)
                    + [binary]
                    + jit_threshold(threshold)
                    + ARGS
                    + [bm, "100", str(extra_args)]
                )
                subprocess.run(command)


def measure_gc_time():

    output_dir = "logs-gc"
    mkdir(output_dir)

    for binary in BINS:
        for bm in BENCHS:
            for inv in range(INVOCATIONS):
                extra_args, threshold = BENCHS[bm]
                output_path = "%s/%s_%s_%d.txt" % (
                    output_dir, bm.lower(), parse_bin(binary), inv)
                command = (
                    NICE
                    + [binary]
                    + jit_threshold(threshold)
                    + ["--gc-stats"]
                    + ARGS
                    + [bm, "100", str(extra_args)]
                )
                with open(output_path, 'w') as outfile:
                    subprocess.run(command, stdout=outfile)


def main():
    # measure_rss()
    measure_gc_time()


if __name__ == "__main__":
    main()
