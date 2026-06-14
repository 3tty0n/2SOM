# 2SOM TraceDiff

A small, dependency-free dashboard for **diffing `jit-log-opt` traces** across the
2SOM tier ladder — inspired by PyPy's jitviewer, but built to answer one question:

> *How does the same program get compiled differently under tier2 / tier3 / tier4 / tier5,
> at the trace level?*

It loads two trace dumps, aligns their compiled loops by SOM method, and shows — per loop
and per operation — what each tier **inlined** vs **residualized**.

## What it surfaces

The distinguishing signal between the tiers lives entirely in the op stream:

| op in the trace | meaning |
|---|---|
| `enter_portal_frame` / `debug_merge_point(.., depth>0, ..)` | a send was **inlined** (its body is in this loop) |
| `call_assembler_*` | a send was **residualized** into a separate compiled loop (tier3 recursion bound, or tier5's profile hook) |
| `call_may_force(ConstClass(_residual_send…))` | the **tier2 stack inliner's** residual send |

So tier3 vs tier5 on the same hot send shows up as: the inlined body (portal frame, depth 1)
on one side becomes a `call_assembler` + a separate callee loop on the other.

## Run

```sh
# (optional) generate a curated, clearly-named comparison set into traces/
sh tools/tracediff/gen_traces.sh

# start the dashboard (scans the project root and tools/tracediff/traces/
# for *.opt and *.log)
python3 tools/tracediff/server.py            # default http://127.0.0.1:8077/
python3 tools/tracediff/server.py -d /some/dir -p 8099   # extra dir / port
python3 tools/tracediff/server.py --tmp      # also scan /tmp
python3 tools/tracediff/server.py --no-root  # skip the project root
```

Drop a `PYPYLOG=jit-log-opt:NAME.opt` (or `.log`) dump in the project root and it is
picked up automatically (hit ⟳ to rescan).

Open the printed URL. Pick **Trace A** (baseline) and **Trace B** (compare).

## The views (top to bottom)

1. **Companion logs + compiler badge** — for each side, a badge naming the compiler that
   produced it — **tracing JIT** (residualises sends via `call_assembler` portal calls,
   tier3/tier5) vs **stack inliner** (residualises via `call_may_force(_residual_send)` at
   the interpreter level, tier2) vs **mixed** — plus links to the captured `jit-log-opt`,
   `jit-summary` and `jit-tracing` files (opened raw in a new tab). A trace `NAME.opt`
   auto-associates its siblings `NAME.summary` / `NAME.tracing`.
2. **Compilation cost** (needs `jit-summary`) — Tracing/Backend seconds, loops & bridges
   compiled, recorded ops, and **trace aborts**, A vs B with the ratio. This is the *payoff*
   of residualizing: a tier that residualizes traces less and aborts far less. (e.g. Towers
   tier3→tier5phase: Tracing 0.14 s→0.03 s, aborts 11→1.)
3. **Inline ⇔ Residualize map** — the core view. Every send site (caller method @ bytecode),
   with a pill for how it compiled in A vs B:
   - **B residualized · A inlined** — tier5's profile-driven lever (e.g. Bounce:
     `Ball>>initialize` inlines `Random class>>next` under tier3, residualizes it under tier5).
   - **both residualize (shared / recursion)** — recursive sends both tiers residualize at the
     PyPy recursion bound; *not* tier5's lever. Honestly separated from the above.
   - **B inlined · A residualized**, **only in A/B**, **both inline**.
   When a side compiled a send *both ways*, a compile-order strip (R = residualized,
   I = inlined; one cell per compiled trace) shows the progression.
   - The **→ became loop** column links each residualized send to the *separate compiled
     loop it turned into* — the callee's entry bridge — resolved from the `call_assembler`
     descr (`<LoopN>`) or, for portal calls, from the callee name the inlined side supplies
     (e.g. `Ball>>initialize` residualizes `Random class>>next` → click through to *Loop 9,
     29 ops*). The residual pill is tagged `trace`/`inliner` with the mechanism. This is how
     you see a residual *become a loop*.
4. **Traced source path** (needs `jit-tracing`) — *which bytecodes of the source program the
   tracer actually walked.* `jit-tracing` records every source bytecode the recorder stepped
   through (before optimization), so the method changing mid-trail is an **inline descent**.
   - A **coverage table** (A vs B): per source method, how many trail steps and distinct
     bytecodes each tier's tracer walked. tier3 walks *deep* (descends into inlined callees);
     tier5 walks *shallow* and traces residualized methods as their own attempts — e.g.
     `Random>>next`: tier3 50 steps, tier5 10. This is *why* tier5's tracing is cheaper.
   - Per side, the list of **trace attempts** (the loop being recorded → `compiled loop /
     bridge / entry bridge`, or **aborted** — a runaway recursion shows as a giant step count
     with no outcome). Expand one to see the full source trail, indented by inferred inline
     depth, with `SEND` bytecodes and method-entry descents highlighted. Note the headline
     contrast: tier3 *max inline depth 4*, tier5 *0*.
6. **Summary band** + **Loop/bridge table** — per-metric A→B counts and every compiled loop
   aligned by method (rows in only one trace are flagged *only A/B*).
7. **Loop view** (click a table row) — the two op streams aligned with `difflib`,
   gutter-coloured `+`/`−`/`~`. `debug_merge_point` rows render as foldable inline-frame
   headers (jitviewer-style); `call_assembler`/residual rows are highlighted. Filters:
   *only differences*, *residual sends*, *fold inlined frames*.

## The curated example set

`gen_examples.sh` captures all three log types (jit-log-opt / jit-summary / jit-tracing) for
three real benchmarks under tier3 and two tier5 policies:

| stem | what it shows |
|---|---|
| `bounce.*`  | non-recursive OO sends — the **clean lever** (tier3 inlines, `tier5resid` residualizes) |
| `queens.*`  | recursion + polymorphism |
| `towers.*`  | recursion; `towers.tier5phase` adds the quiesce→purge→re-trace policy |

Variants: `.tier2` (stack inliner), `.tier3` (tracing baseline, all inlined), `.tier5`
(counter default), `.tier5resid` (warmup-only quiesce — residualize everything, max
contrast), `.tier5phase` (Towers only). Compare `.tier2` vs `.tier5resid` to see the two
residualization *mechanisms* side by side (stack inliner vs tracing JIT).

## Make your own traces

```sh
PYPYLOG=jit-log-opt:mine.tier3.opt            ./som-bc-jit-tier5 -cp … prog.som
PYPYLOG=jit-log-opt:mine.tier5.opt SOM_T5=1   ./som-bc-jit-tier5 -cp … prog.som
```

These land in the project root and are picked up on reload. Files are grouped in
the picker by the name before the first `.tier…`, so `prog.tier3.opt` and `prog.tier5.opt`
are offered as a natural default pair.

## Files

- `jitlog.py` — the `jit-log-opt` parser (loops/bridges/ops, inline-frame reconstruction).
- `diff.py` — block alignment + `difflib` op-level diff.
- `server.py` — stdlib `http.server`; JSON API + static host. No third-party deps.
- `static/` — the single-page front-end (vanilla JS/CSS).
- `gen_traces.sh` — produces the curated comparison set under `traces/`.
