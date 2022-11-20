import sys
import math
from numpy.lib import type_check

import pandas as pd
import numpy as np

import matplotlib.pyplot as plt
import seaborn as sns

from pysom_benchmarks import *

my_palette = sns.color_palette("Set1")
sns.set_palette(my_palette)

offset = 30
invocations = 30

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

already_show_legend = False

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

    def plot_graph(df, ax, bench):
        ax.title.set_text(bench)
        ax.plot(df[bench]["RPySOM-bc-jit-tier1"], label="threaded code", lw=2)
        ax.plot(df[bench]["RPySOM-bc-jit-tier2"], label="tracing JIT", lw=2)
        ax.plot(df[bench]["RPySOM-bc-interp"], label="interpreter", lw=2)

    for typ_name, figsize, num in [
            ("tiny", (10, 6), 4),
            ("micro", (10, 8), 4),
            ("macro", (10, 5), 4),
    ]:
        fig = plt.figure(figsize=figsize, constrained_layout=True)
        fig.tight_layout()

        bench_name = typ_name.upper()
        benchs = locals().get(bench_name)

        axs = []
        for i, bench in enumerate(benchs):
            if len(benchs) > num:
                row = math.floor(len(benchs) / num) + 1
                col = num
            else:
                row = math.floor(len(benchs) / num)
                col = num
            ax = fig.add_subplot(row, col, i + 1)

            ax.set_box_aspect(1)
            ax.autoscale()
            ax.grid(axis='y', color='gray', lw=1, ls='--')
            ax.set_ylabel('ms', fontsize=11)
            ax.set_xlabel('#iteration', fontsize=11)

            axs.append(ax)
            plot_graph(df, ax, bench)


        if not already_show_legend:
            handles, labels = axs[0].get_legend_handles_labels()
            axs[0].legend(loc='lower left', bbox_to_anchor=(0,1.25), ncol=1)
            # plt.legend(handles, labels, ncol=3, loc='upper left', bbox_to_anchor=(0,3))
            already_show_legend = True
        plt.savefig(name + "_" + typ_name + ".pdf")
        plt.savefig(name + "_" + typ_name + ".png")
        # plt.show()
