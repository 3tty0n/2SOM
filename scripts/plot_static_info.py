import sys
import math
from os import path

import pandas as pd
import numpy as np
from scipy import stats

import matplotlib.pyplot as plt
import seaborn as sns

def remove_suffix(string, suffix):
    if string.endswith(suffix):
        return string[:-len(suffix)]
    return string

my_palette = sns.color_palette("Set1")
sns.set_palette(my_palette)

data = ["code_size.csv", "comp_time.csv"]

def plot_data(path):
    df = pd.read_csv(path, index_col=0)
    name = remove_suffix(path, 'csv')

    headers = list(df.columns)
    geo_means = {}
    for head in headers:
        geo_means[head] = stats.gmean(df[head])
    df.loc['geo_mean'] = geo_means

    ax = df.plot.bar(figsize=(8, 6))

    if name.startswith('code_size'):
        ax.set_ylabel('#ops', fontsize=12)
    elif name.startswith('comp_time'):
        ax.set_ylabel('compilation time (s)', fontsize=12)
    ax.set_xlabel('Benchmarks', fontsize=12)

    plt.tight_layout()
    ax.autoscale()
    plt.savefig(name + 'png')
    plt.show()

for d in data:
    if not path.exists(d):
        raise Exception("%s does not exist" % d)
    plot_data(d)
