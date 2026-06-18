# Plan: Cold-Call Residualization (CCR) on PyPy — static result + dynamic controller

Reframed from the 2SOM tier2/3/4 plan to target **production PyPy** directly. The
amortization gate is abandoned (it traded go/chaos away to fix json — a non-local
trace-tree effect, not a per-callee property). CCR here means **loopgate-only**:
`PYPYTIER_PROMOTE` + `PYPYTIER_MINSIZE` + `PYPYTIER_LOOPGATE`.

## Problem

Meta-tracing inlines every portal-recursive callee into the caller's trace. That
wins steady state but makes warmup expensive: large traces are slow to record,
optimize, and assemble. For short / interactive / serverless / CI workloads the
steady state is never reached, so trace-compile cost *is* the runtime. PyPy's
`can_never_inline` heuristic is static (size/loop) and never adapts to how hot a
callee actually is.

## Idea (static CCR — DONE, validated)

At trace time, residualize a callee the interpreter has executed only a few times
("cold", `_tier_runs < PROMOTE`) as a `CALL_ASSEMBLER` to its own smaller trace,
instead of inlining it; inline once it crosses `PROMOTE`. Gated by `MINSIZE`
(never residualize tiny callees — boundary dwarfs the body) and `LOOPGATE`
(only residualize loop-bearing callees — the boundary amortizes over inner
iterations). Implemented entirely through the existing `can_never_inline` hook
plus one per-`PyCode` counter incremented on interpreted dispatch. No tracer /
optimizer / backend changes.

**Result (bench_e2e, 17 lib-free benchmarks, n=50, reps=5, same binary A/B):**
- cold (first iteration): **-13.7%**
- JIT tracing time: **-13.4%**  (the mechanistic driver; tracks cold per-benchmark)
- steady: -1.5% (parity / slight win)

Win is heavy-tailed: go -60%, richards -61% dominate; most benchmarks -2..-8%.

## The gap a dynamic controller must close

Static CCR leaves two structural residuals it *cannot* fix with static signals:
- **json_bench** (+3% cold/steady): residualizing the recursive encoder breaks
  escape analysis across the boundary (`nvirtuals` 2156->429) — forced
  allocations every iteration.
- **chaos** (+6% steady): residual boundary cost without enough amortization.

Why static gates fail to separate these from go's *beneficial* residualization:
the discriminator is **inner-loop trip count per residual entry** (go: hundreds;
json: ~5) — a *dynamic* quantity. The static call-density gate not only fails to
see it, it backfires: inlining go's minor call-dense helpers reshapes the whole
trace tree and *raises* total CALL_ASSEMBLER crossings (375->656 at K=18). The
decision is non-local; only runtime feedback separates the cases.

## PyPy vs 2SOM: why the controller is harder here

- **2SOM** residualizes via explicit interpreter-level barriers (`_residual_send_*`),
  trivially instrumented with a per-method op/activation counter, and promotes by
  writing the quasi-immutable `adaptive_tier` (OSR exit at the merge point). Full
  per-callee runtime feedback is cheap.
- **PyPy** residualizes via the JIT's `can_never_inline` -> `CALL_ASSEMBLER`.
  There is **no interpreter-level residual barrier**: once a callee is
  compiled-residual it produces no interpreter-observable signal, so `_tier_runs`
  freezes (KO-1). Post-compile per-callee feedback needs JIT-level instrumentation.
- **What PyPy does give us:** quasi-immutable `PyCode` fields. A write to a
  per-code decision field invalidates every trace that constant-folded it ->
  forced re-trace. That is the promotion/demotion *actuator*.

## Dynamic controller — design options

**Option 1 — observed-trip-count gate (profile-guided, pre-compile). RECOMMENDED.**
Accumulate, per `PyCode`, the inner-loop iteration count (backward-jump / FOR_ITER
backedges) during *interpreted* execution, alongside the activation count. At
trace time, `can_never_inline` residualizes a cold callee only when its observed
mean trip count per activation is high enough to amortize the boundary. This is
the dynamic analog of the failed static amortization gate, but keyed on **real
observed trip counts** instead of static call density — exactly the signal that
separated go (high) from json (low). Interpreter-level only; one rebuild then an
env-knob (`PYPYTIER_WORKMIN`) sweep, no per-iteration rebuilds.
  - Risk: the trace-tree interaction that defeated the static gate may persist;
    threshold needs tuning. If it does persist, that is a clean negative result
    isolating the cause to Option 2's territory.

**Option 2 — quasi-immut promotion with a JIT-level residual counter.**
Instrument the `CALL_ASSEMBLER` residual entry to bump a per-callee counter; a
residual that is hot-but-cheap (high entry count, low work per entry) is demoted
to inline by writing the quasi-immut decision field -> re-trace. This is the true
analog of 2SOM's op-count promotion and the only design that observes
post-compile residual behavior. Bigger JIT change, higher risk; staged as the
fallback if Option 1's pre-compile profile proves insufficient.

**Option 3 — phase-gated global residualization.**
Residualize during warmup, promote-to-inline at global trace quiescence.
Guarantees steady safety but does not fix json's *cold* and would give back CCR's
current slight steady win; low marginal value here. Rejected.

## Build sequence

1. **Baseline (in progress):** full 47-benchmark warmup curves, static CCR vs
   stock, on the clean gate-removed binary -> `warmup_all.json`. Establishes the
   complete regression/win map the controller must improve on.
2. **Implement Option 1:** add per-`PyCode` `_tier_work` (backedge counter,
   incremented on the interpreted backward-jump path only, never inside JIT
   code); extend `can_never_inline` with a `PYPYTIER_WORKMIN` mean-trip-count
   gate (default 0 = today's loopgate). One rebuild.
3. **Sweep `WORKMIN`** on the decisive set (go, chaos, json_bench, richards,
   pyflate, telco) for a setting that keeps go/richards/chaos wins while removing
   json's regression. Confirm on the full 47.
4. **If Option 1 separates them:** re-measure full suite, report the closed gap.
   **If not:** report the negative result (PyPy needs Option 2's post-compile
   feedback) and scope Option 2.

## Evaluation criteria

- Warmup: keep cold geomean <= today's 0.863 (-13.7%); **no benchmark regresses
  cold by >3%** (fix json's +3%).
- Steady: geomean within 1% of today's 0.985; no benchmark worse than today.
- Mechanism: `nvirtuals` for json recovers toward stock when its encoder is
  inlined; go's residual sites unchanged.

## Files

- `pypy/module/pypyjit/interp_jit.py` — `_tier_work`, `WORKMIN` gate in
  `_ccr_can_never_inline`, backedge increment.
- `pypy/interpreter/pycode.py` — `_tier_work` field (tri-state cache style).
- `docs/probes/pypy_ccr/run_warmup_all.py` — full-suite driver (done).

---
## RESULT — dynamic controller built and evaluated (Option 1)

Implemented `PYPYTIER_WORKMIN` (per-`PyCode` `_tier_work` backedge counter; residualize
a cold callee only when observed mean trip count `_tier_work/_tier_runs >= WORKMIN`).
Swept WORKMIN in {0,10,20,40,80} on a 16-benchmark decisive subset (wins + regressors),
cold = min-of-reps iter0, steady = run_one MAD detector.

GEOMEAN (subset):
| WORKMIN | 0 | 10 | 20 | 40 | 80 |
|---|---|---|---|---|---|
| cold   | **0.891** | 0.977 | 0.964 | 0.939 | 0.980 |
| steady | 1.009 | 0.989 | 0.961 | **0.957** | 0.999 |

**Outcome: a tradeoff frontier, not a warmup win.**
- **Warmup:** static CCR (WORKMIN=0) is unbeatable on cold; every WORKMIN>0 erodes it
  (the killed wins -- go 0.75->0.99, chaos 0.73->1.0, richards 0.39->1.01 -- outweigh
  the fixed cold regressors -- django 1.64->1.00, dulwich 1.08->0.98). The dynamic
  per-callee trip-count signal **cannot improve warmup** over static CCR.
- **Steady:** WORKMIN 20-40 genuinely helps -- turns the +0.9% steady regression into
  -4%, and removes catastrophic outliers (genshi steady 1.35->1.03, django cold
  1.64->1.00). So WORKMIN is a **steady-safety / robustness knob**, not a warmup tool.

**Why Option 1 cannot win warmup (the core negative result, now twice-confirmed):**
go's benefit is **non-local** -- residualizing its recursive game-tree cluster shrinks
traces collectively. go's dominant residual `remove` iterates tiny stone-groups (low
trip count), structurally identical to json's small-dict encoder. No per-callee signal
(static call-density OR dynamic trip-count) can separate a beneficial low-trip residual
from a harmful one, because the cost/benefit lives in the call graph, not the callee.
Both gates fail the same way; a working discriminator must observe **global** trace cost
(Option 2, a JIT-level change) -- out of reach for interpreter-level hooks.

**Recommendation:** ship WORKMIN=0 (static CCR) for the warmup goal; expose WORKMIN as
an optional steady-safety knob. Option 2 (global trace-cost feedback) is the only design
that could fix warmup regressors without losing wins, and is scoped as future work.

### Full-47 confirmation (definitive, proper steady)

stock vs static CCR (wm0) vs dynamic (wm40), 45 benchmarks completing (krakatau +
1 lib drop), n=40 reps=3, steady = run_one MAD detector. `frontier_47.pdf`.

| GEOMEAN(45) | cold | steady | JIT tracing |
|---|---|---|---|
| static CCR (wm0)  | 0.983 (-1.7%) | **1.031 (+3.1%)** | **0.917 (-8.3%)** |
| dynamic   (wm40)  | 1.006 (+0.6%) | 1.001 (+0.1%) | 1.005 (+0.5%) |

- The bench_e2e **-13.7% cold headline was a selection artifact** of the lib-free
  set. On the representative full suite static CCR is **-1.7% cold / -8.3% tracing
  / +3.1% steady**, with 15 cold-reg (django 1.61, scimark_fft 1.13, genshi_xml
  1.12) and 13 steady-reg (sqlalchemy 1.52, genshi 1.29, spambayes 1.20).
- The dynamic controller at the regression-removing setting (wm40) **collapses to
  near-stock** (cold +0.6%, tracing +0.5%): removing the harmful low-trip residuals
  also removes the beneficial ones, so it suppresses residualization to ~zero and
  gives back BOTH the warmup and the tracing win. Confirmed: no per-callee setting
  keeps the wins (richards 0.38, go 0.73, chaos 0.75) while removing the
  regressions.

**Bottom line.** CCR's mechanism (cheaper traces, -8.3% tracing) is real and
universal-ish; the runtime warmup payoff is heavy-tailed (huge on recursive/loop
workloads, marginal in geomean) and carries a steady cost on call-heavy code that
**no per-callee controller can excise without disabling the technique** -- the
cost/benefit is graph-global. A working fix requires global trace-cost feedback
(JIT-level, future work).

---
## RESULT — profile-guided deployment (the project's positive headline)

The discriminator (compile- vs execution-boundedness) is global per-workload, so
CCR is best deployed per-workload: a one-shot A/B profile (run stock + CCR once,
keep the winner) enables CCR only where it helps.  This converts static CCR's
"wash with regressions" into a clean, regression-free win on the full 47.

GEOMEAN (45 benchmarks completing):
| policy | cold | steady | regressions |
|---|---|---|---|
| static CCR everywhere | -2.5% | +2.7% | django 1.69, genshi 1.24, ... |
| profile-guided (conservative: cold<0.98 & steady<1.05) | -2.4% | -0.6% | none |
| profile-guided (aggressive: cold<0.98 & steady<1.10) | **-5.0%** | -0.3% | none |

On the 12 benchmarks the aggressive policy enables (richards, go, chaos, bm_mako,
meteor-contest, raytrace-simple, pyxl_bench, eparse, sympy_sum, ai, bm_mdp,
crypto_pyaes): **cold -10.3%, JIT-tracing -21.5%, steady -2.6%** -- a real
double-digit warmup win with zero regressions anywhere. (`pgo_warmup.pdf`,
`render_pgo_curves.py`.)

Caveat: the enable decision needs an A/B run, not a single stock-side signal --
the stock tracing-fraction does NOT predict where CCR helps (mako 0.065 helps,
django 0.063 hurts).  Deployable for a fixed app/suite (profile once, set the
flag); not automatic for arbitrary short scripts.

## Signal ladder (why per-workload, not per-call)
| signal | separates wins/regressors | usable at decision time |
|---|---|---|
| call-density / trip-count / trace-length / avg-trace-size | no (non-local / interleaved) | - |
| tracing-fraction (TRACEFRAC) | yes (aggregate) | no (lags warmup) |

Only the global tracing fraction separates the cases, and it can't be read at the
irreversible inline decision (it accumulates only after traces complete) -- so the
control must be per-workload (profile-guided), not per-call.
