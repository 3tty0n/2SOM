#!/usr/bin/env python3
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

BINS = ["./som-bc-jit-tier1", "./som-bc-jit-tier2", "./som-bc-jit-interp-tier1"]

ARGS = [
    "-cp",
    "Smalltalk:Examples/Benchmarks/GraphSearch:Examples/Benchmarks/NBody:Examples/Benchmarks/LanguageFeatures:Examples/Benchmarks/TestSuite",
    "Examples/Benchmarks/BenchmarkHarness.som",
    "--gc",
]


def jit_threshold(threshold):
    return [
        "--jit",
        "threshold=%d" % threshold,
        "--jit",
        "function_threshold=%d" % threshold,
    ]


def measure_rss():
    def gnu_time(bm, inv):
        gnu_time = ["/usr/bin/time", "-f 'RSS:%M KB'", "-o %s_%d.txt" % (bm.lower(), inv)]

    for binary in BINS:
        for bm in BENCHS:
            for inv in range(INVOCATIONS):
                extra_args, threshold = BENCHS[bm]
                command = (
                    gnu_time(bm, inv)
                    + [binary]
                    + jit_threshold(threshold)
                    + ARGS
                    + [bm, "100", str(extra_args)]
                )
                subprocess.run(command)


def measure_gc_time():
    for binary in BINS:
        for bm in BENCHS:
            for inv in range(INVOCATIONS):
                extra_args, threshold = BENCHS[bm]
                command = (
                    [binary, "--gc-stats"]
                    + jit_threshold(threshold)
                    + ARGS
                    + [bm, "100", str(extra_args)]
                )
                subprocess.run(command)


def main():
    measure_rss()


if __name__ == "__main__":
    main()
