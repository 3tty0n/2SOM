# Session summary — Cold-Call Residualization (CCR) on PyPy

Project is now **PyPy-native**. 2SOM remains the design origin (where the
technique was the "B1" tier backend), but all work below targets production PyPy.

## 1. What CCR is

An adaptive inlining policy for the meta-tracing JIT, realized entirely at the
interpreter level. At trace time, a callee the interpreter has executed only a
few times ("cold") is **residualized** — left as a `CALL_ASSEMBLER` to its own
smaller trace — instead of inlined into the caller's trace. Once it crosses a
maturity threshold ("hot") it becomes inlinable. Smaller traces ⇒ cheaper to
record/optimize/assemble ⇒ faster warmup. The residual call boundary is the cost,
amortized when the callee has a hot inner loop.

Implemented through the existing `can_never_inline` jitdriver hook + one
per-`PyCode` counter. **No tracer / optimizer / backend changes.**

Knobs (env, read once at VM startup; all unset ⇒ exactly stock PyPy):
- `PYPYTIER_PROMOTE` — maturity threshold (we use **2048**).
- `PYPYTIER_MINSIZE` — size floor; never residualize callees smaller than this
  (**130** B). Tiny callees: the boundary dwarfs the body.
- `PYPYTIER_LOOPGATE=1` — only residualize loop-bearing callees (the boundary
  amortizes over inner iterations). The validated config is **`2048:130:L`**.

## 2. Implementation

- `pypy/module/pypyjit/interp_jit.py`: `_ccr_can_never_inline` (the hook),
  `_scan_has_loop`, `_TierConfig` + `tier_startup` (env parsing), `_tier_runs`
  increment on interpreted dispatch.
- `pypy/interpreter/pycode.py`: `_tier_runs`, `_tier_has_loop` (loop-gate cache),
  `_tier_work` (new, see §6).
- Build: from `./pypy`, `CFLAGS="-Wno-incompatible-pointer-types
  -Wno-implicit-function-declaration" pypy rpython/bin/rpython --opt=jit
  pypy/goal/targetpypystandalone.py` (~12 min; GCC 15). Binary lands in
  `/tmp/usession-*/testing_1/` and is copied to `pypy/goal/`. Backups kept as
  `pypy/goal/*.{stock,b1minsize,loopgate,ccr-gate}-bak`.

## 3. Headline result (static CCR, loopgate)

`bench_e2e.py`, single-binary A/B (stock = env unset, CCR = `2048:130:L`),
17 lib-free benchmarks, n=50, reps=5, element-wise min:

| metric | CCR/stock | win |
|---|---|---|
| cold (first iteration) | 0.863 | **−13.7%** |
| JIT tracing time | 0.866 | **−13.4%** |
| steady (tail median) | 0.985 | −1.5% (parity) |

Mechanistically coherent: tracing −13.4% drives cold −13.7%, and the two track
per-benchmark. Win is **heavy-tailed** — go −60%, richards −61% dominate; most
benchmarks −2..−8%. (An earlier own/-only N=50 run gave cold −8.2%, steady −2.3%.)

## 4. Negative result — the amortization gate (removed)

Attempt to fix the json_bench/bm_gzip residuals with a static **loop-amortization
gate** (`PYPYTIER_LOOPCALLS=K`: residualize only if `ops_in_loop ≥ K·calls_in_loop`).
At K=18 it removed json/gzip but **destroyed go (cold 0.53→0.75) and chaos
(0.69→1.04)**. Cause: non-local trace-tree interaction — inlining go's call-dense
helpers reshaped the tree and *raised* total `CALL_ASSEMBLER` crossings 375→656.
The same call-dense-loop structure is harmful in json but load-bearing in go;
static structure cannot separate them. All-benchmark geomean got worse
(cold −8.2%→−1.2%). **Gate fully removed from source.**

## 5. Why the residuals happen (trace-level evidence)

- **json_bench** (+3% cold/steady): residualizing the recursive encoder breaks
  escape analysis across the boundary — `nvirtuals` 2156→429, forcing real
  allocations every iteration. CCR's tracing still drops (0.70) but json is
  allocation-bound, not trace-bound.
- **bm_gzip**: thin Python wrapper over zlib (C); no Python inner loop to amortize
  the boundary. At n=50 it's actually ~parity (the earlier "regression" was
  low-iteration noise).
- **pypy_interp**: the apparent cold regression is noise (cold variance 0.14–0.33
  across reps; min-of-9 is parity, and it wins steady).

## 6. Dynamic controller (built, default-off, awaiting evaluation)

The discriminator static structure can't see is **inner-loop trip count per call**
(go: hundreds; json: ~5) — a runtime quantity. PyPy (unlike 2SOM) has no
interpreter-level residual barrier, so post-compile per-callee feedback is out of
reach; but the trip count is observable *before* compile. So:

- `PyCode._tier_work` — counts interpreted inner-loop backedges per code object
  (incremented only on the interpreted `jump_absolute` path; dead-code-eliminated
  inside traces ⇒ zero steady overhead).
- `_ccr_can_never_inline` now gated by `PYPYTIER_WORKMIN=K`: residualize a cold
  callee only when observed mean trip count `_tier_work/_tier_runs ≥ K`. Default
  0 ⇒ byte-identical to the validated loopgate.

This is the PyPy-feasible analog of 2SOM's op-count promotion, keyed on measured
trip counts rather than static call density. **Needs one rebuild + a `WORKMIN`
sweep to evaluate** (does the runtime signal close the json/chameleon gap without
losing go, where static structure failed).

## 7. Naming

"B1" → **Cold-Call Residualization (CCR)** everywhere: code identifiers
(`_ccr_can_never_inline`), comments, `docs/probes/pypy_b1/` → `docs/probes/pypy_ccr/`,
`pypy_b1_port.md` → `pypy_ccr_port.md`, and the beamer slides. Env vars stay
`PYPYTIER_*` (they name the tier experiment, not the technique).

## 8. Current state

- **In progress:** full **47-benchmark** warmup measurement (stock vs CCR,
  `run_warmup_all.py`, n=30, reps=3) on the clean gate-removed binary. Early data
  surfaces full-suite regressors the 17-set hid: **bm_chameleon cold +12%,
  bm_dulwich +5%**. (krakatau drops — missing `lib/krakatau`.)
- Dynamic-controller rebuild is **held** until the baseline finishes (avoid CPU
  contention on timing-sensitive warmup numbers).

## 9. Artifacts

- Code: `pypy/module/pypyjit/interp_jit.py`, `pypy/interpreter/pycode.py` (PyPy repo).
- Probes/figures: `docs/probes/pypy_ccr/` — `run_warmup_all.py`, `analyze_e2e.py`,
  `measure_geomean.py`, `render_curves_grid.py`, plus figures
  `e2e_warmup.pdf` (headline), `geomean_warmup.pdf`, `all_curves.pdf`, and JSON
  data (`e2e_{stock,ccr}.json`, `geomean_warmup.json`, `warmup_all.json`).
- Docs: `pypy_ccr_port.md` (port writeup), `pypy_ccr_plan.md` (PyPy plan +
  controller design), `can_never_inline_beamer.tex` (slides),
  `pypy_ccr_session_summary.md` (this file).

## 10. Next steps

1. Finish baseline → render 47-benchmark warmup-curve grid + full regression map.
2. Rebuild with the dynamic controller; sweep `WORKMIN` on the decisive set
   (go, richards, chaos, json, chameleon, dulwich).
3. Re-measure full 47 at the chosen `WORKMIN`; report whether the runtime
   trip-count signal closes the gap — or a clean negative result pinning it to
   PyPy's missing post-compile feedback (→ a deeper JIT-level counter).
