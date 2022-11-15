#!/usr/bin/env python3
import sys

import pandas as pd
import numpy as np

import matplotlib.pyplot as plt
import seaborn as sns
from scipy.stats import gmean

from pprint import pprint

offset = 30
invocations = 5

def calc_gmean(elapsed_times, offset, n, i):
    times = [elapsed_times[i + offset * j][1] for j in range(n)]
    multiply = 1
    for t in times:
        multiply = multiply * t
    geometric_mean = multiply ** (1/n)
    return geometric_mean

def get_name(data):
    l = data.split(".")
    del l[-1]
    return '.'.join(l)

try:
    data = sys.argv[1]
except IndexError:
    raise Exception("argument is not specified")

name = get_name(data)

try:
    offset_ext = int(sys.argv[2])
    if offset_ext > offset:
        raise Exception("offset %d is out of range %d" % (offset_ext, offset))
    offset = offset_ext
except IndexError:
    pass

targets = set()
benchs = set()

with open(data, "r") as f:
    result = {}

    while True:
        line = f.readline().rstrip()

        if len(line) == 0:
            break

        if line.startswith("#"):
            continue

        line = line.split("\t")
        invocation, iteration, elapsed, bench, target = (
            float(line[0]),
            float(line[1]),
            float(line[2]),
            line[5],
            line[6],
        )

        targets.add(target)
        benchs.add(bench)

        if target not in result:
            result[target] = {bench: [(invocation, elapsed)]}
        else:
            if bench not in result[target]:
                result[target][bench] = [(invocation, elapsed)]
            else:
                result[target][bench].append((invocation, elapsed))

    targets = sorted(targets)
    benchs = sorted(benchs)


    # calculate the mean of each elapsed time
    result_shaped = {}
    for target in targets:
        for bench in benchs:
            elapsed_times = result[target][bench]
            means = []
            for i in range(offset):
                mean = calc_gmean(elapsed_times, offset, invocations, i)
                means.append(mean)

            l = []
            acc = 0.0
            for mean in means:
                acc += mean
                l.append(acc)

            if bench not in result_shaped:
                result_shaped[bench] = {target: l}
            else:
                result_shaped[bench][target] = l

    df = pd.DataFrame(result_shaped)

    fig = plt.figure(figsize=(20, 14))
    fig.tight_layout()

    def plot_graph(df, ax, bench):
        ax.title.set_text(bench)
        ax.plot(df[bench]["RPySOM-bc-jit-tier1"], label="threaded code")
        ax.plot(df[bench]["RPySOM-bc-jit-tier2"], label="tracing JIT")
        ax.plot(df[bench]["RPySOM-bc-interp"], label="interpreter")

    axs = []
    for i, bench in enumerate(benchs):
        import math

        ax = fig.add_subplot(math.floor(len(benchs) / 6) + 1, 6, i + 1)
        axs.append(ax)
        # ax.set_ylim(0, 180)
        plot_graph(df, ax, bench)

    handles, labels = axs[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc=(0.8, 0.1))

    plt.savefig(name + ".pdf")
    plt.savefig(name + ".png")
    plt.show()
