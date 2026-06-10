#!/usr/bin/env python3
"""Visualize 2SOM ReBench results (tier3 baseline vs tier4 adaptive).

Reads one or more ReBench `.data` files (the tab-separated format written by
runbench_experiment.conf) and produces:

  1. a per-benchmark speedup summary (contender/baseline ratio) with the geomean,
  2. for single-shot suites, an invocation-time distribution plot, and
  3. for multi-iteration (steady) suites, BREAK-EVEN curves: cumulative execution
     time per iteration for both tiers, with the iteration at which the adaptive
     tier's total time (warmup included) drops below the baseline's marked.

The break-even view models *continuous* execution: running the benchmark loop
iteration after iteration, at which point has the adaptive engine paid back its
warmup/compilation investment? Single-shot suites (iterations=1, e.g. the
Experiment composites) have no within-process iteration axis, so they get the
ratio + distribution views only.

Usage:
    python3 visualize_results.py [DATA ...] [options]

    DATA              one or more ReBench .data files (default: runbench_experiment.data)
    --baseline NAME   executor used as the denominator (default: auto, *tier3*)
    --contender NAME  executor used as the numerator   (default: auto, *tier4*)
    --criterion NAME  measurement criterion to keep    (default: total)
    --warmup-frac F   fraction of leading iterations treated as warmup when
                      computing the steady-state ratio  (default: 0.5)
    --out-dir DIR     where to write the PNGs           (default: bench_plots)
    --show            also open an interactive window
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

import numpy as np

import matplotlib
# default to a headless backend; --show flips it back
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# --- parsing ---------------------------------------------------------------

# unit -> milliseconds
_UNIT_TO_MS = {"s": 1000.0, "ms": 1.0, "us": 0.001, "ns": 1e-6}


class Series:
    """All measurements for one (suite, benchmark, executor) cell."""

    __slots__ = ("suite", "benchmark", "executor", "by_iter")

    def __init__(self, suite, benchmark, executor):
        self.suite = suite
        self.benchmark = benchmark
        self.executor = executor
        # iteration index (1-based) -> list of values (ms), one per invocation
        self.by_iter = defaultdict(list)

    @property
    def n_iter(self):
        return max(self.by_iter) if self.by_iter else 0

    @property
    def is_continuous(self):
        """True when the suite ran many iterations per process (a warmup curve)."""
        return self.n_iter > 1

    def warmup_curve(self):
        """Mean time at each iteration index, averaged over invocations (ms)."""
        iters = sorted(self.by_iter)
        return np.array(iters), np.array([np.mean(self.by_iter[i]) for i in iters])

    def flat_values(self):
        """Every measurement, regardless of iteration (ms)."""
        out = []
        for vals in self.by_iter.values():
            out.extend(vals)
        return np.array(out)

    def steady_stat(self, warmup_frac):
        """Representative steady-state time (ms).

        Single-shot: median over invocations. Continuous: median of the
        per-iteration means after dropping the leading `warmup_frac`.
        """
        if not self.is_continuous:
            return float(np.median(self.flat_values()))
        _, curve = self.warmup_curve()
        start = int(len(curve) * warmup_frac)
        steady = curve[start:] if start < len(curve) else curve
        return float(np.median(steady))


def parse_data(path, criterion):
    """Parse a ReBench .data file into {(suite, benchmark, executor): Series}."""
    series = {}
    header = None
    with open(path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            row = line.rstrip("\n").split("\t")
            if header is None:
                # the first non-comment line is the column header
                header = {name: idx for idx, name in enumerate(row)}
                missing = {"value", "unit", "criterion", "benchmark",
                           "executor", "suite", "iteration"} - set(header)
                if missing:
                    raise ValueError(
                        "%s: missing expected columns %s" % (path, sorted(missing)))
                continue
            if len(row) <= header["runId" if "runId" in header else "suite"]:
                continue
            if row[header["criterion"]] != criterion:
                continue
            try:
                value = float(row[header["value"]])
                it = int(row[header["iteration"]])
            except (ValueError, IndexError):
                continue
            unit = row[header["unit"]]
            value_ms = value * _UNIT_TO_MS.get(unit, 1.0)
            key = (row[header["suite"]], row[header["benchmark"]],
                   row[header["executor"]])
            s = series.get(key)
            if s is None:
                s = Series(*key)
                series[key] = s
            s.by_iter[it].append(value_ms)
    return series


# --- model -----------------------------------------------------------------

def pick_executors(executors, baseline, contender):
    """Resolve baseline/contender names, auto-detecting tier3/tier4 if unset."""
    def auto(substr):
        hits = [e for e in executors if substr in e]
        return hits[0] if hits else None

    baseline = baseline or auto("tier3")
    contender = contender or auto("tier4")
    return baseline, contender


def geomean(xs):
    xs = [x for x in xs if x > 0]
    if not xs:
        return float("nan")
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def break_even_iteration(base_curve, cont_curve):
    """First iteration k (1-based) at which the contender's CUMULATIVE time is
    <= the baseline's. Returns (k, cum_base, cum_cont) or (None, ...) if the
    contender never catches up within the measured window."""
    n = min(len(base_curve), len(cont_curve))
    cum_b = np.cumsum(base_curve[:n])
    cum_c = np.cumsum(cont_curve[:n])
    hit = np.where(cum_c <= cum_b)[0]
    k = int(hit[0]) + 1 if len(hit) else None
    return k, cum_b, cum_c


# --- plots -----------------------------------------------------------------

def plot_summary(pairs, baseline, contender, out_path):
    """Horizontal speedup bars per benchmark + geomean line(s)."""
    if not pairs:
        return None
    # pairs: list of (suite, benchmark, ratio); ratio = contender/baseline
    pairs = sorted(pairs, key=lambda p: p[2])
    labels = ["%s/%s" % (s, b) for (s, b, _r) in pairs]
    ratios = [r for (_s, _b, r) in pairs]
    gm = geomean(ratios)

    fig, ax = plt.subplots(figsize=(9, max(3, 0.34 * len(pairs) + 1.5)))
    colors = ["#2a9d8f" if r < 1.0 else "#e76f51" for r in ratios]
    y = np.arange(len(pairs))
    ax.barh(y, ratios, color=colors, edgecolor="black", linewidth=0.4)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.axvline(1.0, color="black", lw=1.0)
    ax.axvline(gm, color="#264653", lw=1.6, ls="--",
               label="geomean = %.3f (%.1f%% %s)" % (
                   gm, abs(1 - gm) * 100, "faster" if gm < 1 else "slower"))
    for yi, r in zip(y, ratios):
        ax.text(r + 0.01, yi, "%.3f" % r, va="center", fontsize=7)
    ax.set_xlabel("%s / %s  (<1 = adaptive faster)" % (contender, baseline))
    ax.set_title("Speedup vs baseline  [%d benchmarks]" % len(pairs))
    ax.legend(loc="lower right", fontsize=9)
    ax.margins(y=0.01)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return gm


def plot_break_even(benches, baseline, contender, out_path):
    """Cumulative-time curves with the break-even iteration marked, one panel
    per benchmark. `benches` is a list of (suite, benchmark, base, cont) Series."""
    if not benches:
        return []
    n = len(benches)
    cols = min(3, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(5.2 * cols, 3.6 * rows),
                             squeeze=False)
    results = []
    for idx, (suite, bench, base, cont) in enumerate(benches):
        ax = axes[idx // cols][idx % cols]
        _, base_curve = base.warmup_curve()
        _, cont_curve = cont.warmup_curve()
        k, cum_b, cum_c = break_even_iteration(base_curve, cont_curve)
        x = np.arange(1, len(cum_b) + 1)
        ax.plot(x, cum_b / 1000.0, color="#e76f51", lw=1.6, label=baseline)
        ax.plot(x, cum_c / 1000.0, color="#2a9d8f", lw=1.6, label=contender)
        if k is not None:
            ax.axvline(k, color="#264653", ls="--", lw=1.2)
            ax.plot([k], [cum_c[k - 1] / 1000.0], "o", color="#264653", ms=5)
            ax.annotate("break-even\n@ iter %d" % k,
                        xy=(k, cum_c[k - 1] / 1000.0),
                        xytext=(0.5, 0.12), textcoords="axes fraction",
                        fontsize=8, ha="center",
                        arrowprops=dict(arrowstyle="->", color="#264653", lw=0.8))
        else:
            ax.text(0.5, 0.5, "no break-even\nwithin %d iters" % len(cum_b),
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=9, color="#7a0c0c")
        ax.set_title("%s / %s" % (suite, bench), fontsize=9)
        ax.set_xlabel("iteration (continuous run)")
        ax.set_ylabel("cumulative time (s)")
        ax.legend(fontsize=8, loc="upper left")
        results.append((suite, bench, k, len(cum_b)))
    # blank any unused panels
    for j in range(n, rows * cols):
        axes[j // cols][j % cols].axis("off")
    fig.suptitle("Break-even: cumulative time vs iteration (adaptive pays back warmup)",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return results


def plot_warmup(benches, baseline, contender, out_path, head=None):
    """Per-iteration time vs iteration (the warmup ramp), one panel per benchmark.
    Marks the per-iteration crossover (first iteration where the contender's
    instantaneous time drops below the baseline's). Complements the cumulative
    break-even view: this shows WHERE the warmup cost is, not just when it pays back."""
    if not benches:
        return None
    n = len(benches)
    cols = min(3, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(5.2 * cols, 3.4 * rows),
                             squeeze=False)
    for idx, (suite, bench, base, cont) in enumerate(benches):
        ax = axes[idx // cols][idx % cols]
        _, bc = base.warmup_curve()
        _, cc = cont.warmup_curve()
        m = min(len(bc), len(cc))
        bc, cc = bc[:m], cc[:m]
        x = np.arange(1, m + 1)
        if head:
            x, bc, cc = x[:head], bc[:head], cc[:head]
        ax.plot(x, bc, color="#e76f51", lw=1.2, label=baseline)
        ax.plot(x, cc, color="#2a9d8f", lw=1.2, label=contender)
        cross = np.where(cc <= bc)[0]
        if len(cross):
            k = int(cross[0]) + 1
            ax.axvline(k, color="#264653", ls=":", lw=1.0)
            ax.text(0.97, 0.06, "contender faster from iter %d" % k,
                    transform=ax.transAxes, ha="right", va="bottom", fontsize=8,
                    bbox=dict(boxstyle="round", fc="white", ec="#264653", alpha=0.8))
        ax.set_title("%s / %s" % (suite, bench), fontsize=9)
        ax.set_xlabel("iteration")
        ax.set_ylabel("time per iteration (ms)")
        ax.legend(fontsize=8, loc="upper right")
    for j in range(n, rows * cols):
        axes[j // cols][j % cols].axis("off")
    fig.suptitle("Warmup: per-iteration time" + (
        " (first %d iterations)" % head if head else ""), fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


def plot_distributions(benches, baseline, contender, out_path):
    """Per-invocation time spread for single-shot benchmarks (box plots)."""
    if not benches:
        return None
    labels, data, colors = [], [], []
    for suite, bench, base, cont in benches:
        labels.append("%s\n%s" % (bench, baseline.split("-")[-1]))
        data.append(base.flat_values())
        colors.append("#e76f51")
        labels.append("%s\n%s" % (bench, contender.split("-")[-1]))
        data.append(cont.flat_values())
        colors.append("#2a9d8f")
    fig, ax = plt.subplots(figsize=(max(6, 1.1 * len(data)), 4.5))
    bp = ax.boxplot(data, patch_artist=True, showfliers=False,
                    medianprops=dict(color="black"))
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(c)
        patch.set_alpha(0.8)
    ax.set_xticklabels(labels, fontsize=7)
    ax.set_ylabel("invocation time (ms)")
    ax.set_title("Single-shot invocation-time distribution")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


# --- driver ----------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data", nargs="*", default=["runbench_experiment.data"])
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--contender", default=None)
    ap.add_argument("--criterion", default="total")
    ap.add_argument("--warmup-frac", type=float, default=0.5)
    ap.add_argument("--warmup-head", type=int, default=None,
                    help="zoom the warmup plot to the first N iterations")
    ap.add_argument("--out-dir", default="bench_plots")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args(argv)

    if args.show:
        matplotlib.use("TkAgg", force=True)

    series = {}
    for path in (args.data or ["runbench_experiment.data"]):
        if not os.path.exists(path):
            print("warning: %s not found, skipping" % path, file=sys.stderr)
            continue
        series.update(parse_data(path, args.criterion))
    if not series:
        print("No usable data rows found. Did the ReBench run produce results?")
        return 1

    executors = sorted({k[2] for k in series})
    baseline, contender = pick_executors(executors, args.baseline, args.contender)
    print("executors present : %s" % ", ".join(executors))
    print("baseline          : %s" % baseline)
    print("contender         : %s" % contender)
    if not baseline or not contender:
        print("\nNeed both a baseline and a contender executor to compare.\n"
              "Pass --baseline/--contender explicitly.")
        return 1

    # pair up benchmarks present for BOTH executors
    benchmarks = sorted({(k[0], k[1]) for k in series})
    summary, cont_benches, shot_benches, unpaired = [], [], [], []
    for suite, bench in benchmarks:
        base = series.get((suite, bench, baseline))
        cont = series.get((suite, bench, contender))
        if base is None or cont is None:
            unpaired.append((suite, bench,
                             "only " + (baseline if base else contender)))
            continue
        ratio = cont.steady_stat(args.warmup_frac) / base.steady_stat(args.warmup_frac)
        summary.append((suite, bench, ratio))
        if base.is_continuous and cont.is_continuous:
            cont_benches.append((suite, bench, base, cont))
        else:
            shot_benches.append((suite, bench, base, cont))

    os.makedirs(args.out_dir, exist_ok=True)

    # ---- text report ----
    print("\n=== speedup (%s / %s), steady-state ===" % (contender, baseline))
    by_suite = defaultdict(list)
    for suite, bench, ratio in sorted(summary, key=lambda x: x[2]):
        flag = "faster" if ratio < 1 else "slower"
        print("  %-22s %-26s %.3f  (%s)" % (suite, bench, ratio, flag))
        by_suite[suite].append(ratio)
    if summary:
        for suite in sorted(by_suite):
            rs = by_suite[suite]
            print("  -- %-19s geomean = %.4f over %d" %
                  (suite, geomean(rs), len(rs)))
        print("  == OVERALL geomean = %.4f over %d ==" %
              (geomean([r for _s, _b, r in summary]), len(summary)))
    if unpaired:
        print("\nunpaired (need both executors, excluded):")
        for suite, bench, why in unpaired:
            print("  %-22s %-26s %s" % (suite, bench, why))

    # ---- figures ----
    written = []
    gm = plot_summary(summary, baseline, contender,
                      os.path.join(args.out_dir, "speedup_summary.pdf"))
    if gm is not None:
        written.append("speedup_summary.pdf")

    wu = plot_warmup(cont_benches, baseline, contender,
                     os.path.join(args.out_dir, "warmup.pdf"),
                     head=args.warmup_head)
    if wu:
        written.append("warmup.pdf")

    be = plot_break_even(cont_benches, baseline, contender,
                         os.path.join(args.out_dir, "break_even.pdf"))
    if be:
        written.append("break_even.pdf")
        print("\n=== break-even iterations (continuous execution) ===")
        for suite, bench, k, n in be:
            where = ("iter %d / %d" % (k, n)) if k else "none within %d" % n
            print("  %-22s %-26s %s" % (suite, bench, where))
    elif cont_benches:
        pass
    else:
        print("\n(no multi-iteration data -> no break-even curves; run the "
              "micro-steady / macro-steady suites to populate them)")

    dist = plot_distributions(shot_benches, baseline, contender,
                              os.path.join(args.out_dir, "distributions.pdf"))
    if dist:
        written.append("distributions.pdf")

    if written:
        print("\nwrote: %s" % ", ".join(
            os.path.join(args.out_dir, w) for w in written))
    if args.show:
        plt.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
