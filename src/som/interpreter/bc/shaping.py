'''Tier-5 "shaping" policy: adaptive trace shaping for the tracing JIT.

The `_shaping_can_never_inline` hook is consulted by the tracer on each recursive
portal-call to decide inline-vs-residualize; the warmup-phase machinery
(`_ShapingWarmup`, `shaping_gc_minor`, `_shaping_warmup_check`) governs when that
decision flips. Enabled at runtime by SOM_SHAPING (see `shaping_configure`).
'''
# Tier 5 (adaptive trace shaping): env-gated (SOM_SHAPING=1), default OFF.
# The ladder: 1 threaded code, 2 stack inliner, 3 tracing, 4 adaptive execution
# placement (which graph runs each method), 5 adaptive trace shaping (what each
# trace inlines). Unlike tiers 1-4, tier 5 is not a translation-time SOM_TIER --
# it is a runtime mode of the tier-3 binary, decided inside the tracer via the
# can_never_inline hook, which residualizes a portal call (CALL_ASSEMBLER)
# instead of inlining the callee.
# Default policy: a per-method invocation counter -- residualize a callee until
# it has been entered promote_inv times, inline after. The alternative global
# quiescence-phase policy (SOM_SHAPING_QUIESCE > 0) residualizes everything until the
# JIT goes silent, then purges; it wins a few transient-heavy composites but
# regresses long-running recursion behind an uncompilable driver loop, so it is
# opt-in. See _ShapingCfg.
import os as _os

from rlib import jit
from rlib.jit import promote


class _ShapingCfg(object):
    _immutable_fields_ = ["enabled?", "quiesce?"]

    def __init__(self):
        self.enabled = 0       # SOM_SHAPING: 1 enables adaptive portal inlining (tier 5)
        # The DEFAULT policy is the per-method invocation counter: a callee is
        # residualized while method.shaping_invocations < promote_inv and inlined
        # after. It is a natural warmup filter -- a short-lived method stays
        # cheap, while a hot loop crosses the ~1039 hotness threshold long after
        # passing promote_inv activations, so it traces fully inlined. No global
        # phase, no purge: nothing a long-running driver loop can lose.
        self.promote_inv = 64  # SOM_SHAPING_PROMOTE_INV: activations before a callee inlines
        # SOM_SHAPING_QUIESCE > 0 switches to the GLOBAL quiescence-phase policy: the
        # warmup phase residualizes every callee until the JIT goes silent for
        # this many interpreted activations, then purges. The phase mode wins on a
        # few transient-heavy composites (Mandelbrot, Experiment7/18) but loses
        # badly on a long-running recursive method behind a driver loop the
        # purge cannot recompile (Fibonacci 15x) -- hence default off.
        self.quiesce = 0       # SOM_SHAPING_QUIESCE: activations of silence; 0 = counter mode
        self.quiesce_gc = 32   # SOM_SHAPING_QUIESCE_GC: minor GCs of silence (phase mode only)
        # SOM_SHAPING_PURGE (phase mode only): 1 = plain purge (loops recount from zero);
        # 2 = purge with reheat (fork API: compiled cells keep near-bound
        # hotness so retraces fire on re-entry).
        self.purge = 1


_shaping_cfg = _ShapingCfg()


try:
    from rpython.rlib.nonconst import NonConstant as _nonconst
except ImportError:
    "NOT_RPYTHON"

    def _nonconst(x):
        return x


class _ShapingWarmup(object):
    # `on` is deliberately a PLAIN mutable field: the warmup lever is a promoted
    # in-trace guard, not quasi-immutable invalidation (an empty marker call
    # gets dead-code-eliminated and the folded read never registers traces).
    def __init__(self):
        self.on = False
        self.tick = 0        # interpreted activation entries (merge-point, bc 0)
        self.last_trace = 0  # tick value at the last tracer consultation
        # GC clock: interpreted-activation ticks stall once cheap traces cover
        # the program (fully-compiled execution is invisible to interpreter
        # code), so the phase end is ALSO clocked by minor collections -- the one
        # heartbeat that never stalls while the program allocates. Advanced
        # from the GC hook (main_rpython.MyHooks); plain int stores only.
        self.gc_tick = 0
        self.last_trace_gc = 0
        self.purge_pending = 0  # set by the GC-context phase end; purge runs lazily


_shaping_warmup = _ShapingWarmup()


def _shaping_warmup_mark():
    # Promote the warmup flag at the invocation chokepoint. DO NOT gate this on
    # quiesce > 0: the promote is load-bearing for BOTH policies. For the phase mode
    # it is the in-trace warmup guard whose failure evicts cheap code at phase
    # end. For the counter (quiesce == 0) it keeps _interpret_tier3_mode a
    # non-trivial graph so the codewriter preserves the interpret_tier3 ->
    # interpret_tier3 recursive_call that can_never_inline hooks; gating it out
    # lets the call be restructured and the counter stops residualizing
    # entirely (measured: Exp17 0.21 -> 0.31, Fibonacci 80ms -> tier3 parity,
    # i.e. tier 5 silently becomes a no-op). The guard_value on the flag is
    # heap-cached after the first send, so the steady cost is negligible.
    if _shaping_cfg.enabled:
        promote(_shaping_warmup.on)


@jit.dont_look_inside
def _shaping_end_phase():
    # dont_look_inside: set_param(None, ...) is a jit_marker the codewriter
    # cannot rewrite inside a jit-visible graph (driver is None).
    _shaping_warmup.on = False   # one-way: fails all warmup guards
    _shaping_warmup.purge_pending = 0
    # Discard ALL cheap-phase compiled code (fork set_param): every JitCell
    # forgets its procedure token, so hot loops retrace fresh with full
    # inlining. Bridge recovery alone cannot replace existing loop tokens
    # in nested call-loop structures (measured: DeltaBlue/Towers stuck at
    # 6-14x); the warmup guards evict running code, the purge makes the
    # re-entries compile clean. See _ShapingCfg.purge for the value-2 trade-off.
    jit.set_param(None, "purge", _shaping_cfg.purge)


def _shaping_warmup_check():
    # Off-trace; called once per INTERPRETED activation entry at the merge
    # point (bc 0), which stays live on trampoline-routed residual calls --
    # the portal graph starts AT the merge point, so a prologue-side counter
    # would freeze once callers compile. Checks amortized to every 256th call.
    if _shaping_warmup.purge_pending:
        # The GC-clocked phase end fired (in GC-hook context, where set_param is
        # off-limits); finish the phase end here, on the first interpreted
        # activation -- which the failing warmup guards guarantee promptly.
        _shaping_end_phase()
        return
    _shaping_warmup.tick += 1
    if (_shaping_warmup.tick & 255) == 0:
        if _shaping_warmup.on:
            # Structural phase end: the tracer has been silent for `quiesce`
            # interpreted activations -> the startup compile storm is over.
            if _shaping_warmup.tick - _shaping_warmup.last_trace > _shaping_cfg.quiesce:
                _shaping_end_phase()


def _shaping_stamp():
    # Tracing-activity stamp (both clocks). Also called from shaping_configure
    # (main program) so the attribute annotations are fully established there:
    # the can_never_inline hook graph is annotated separately after the main
    # graphs are fixed, and a write appearing only in the hook would
    # re-generalize the attribute and abort translation.
    _shaping_warmup.last_trace = _shaping_warmup.tick
    _shaping_warmup.last_trace_gc = _shaping_warmup.gc_tick


def shaping_gc_minor():
    # GC-clocked phase end, called from MyHooks.on_gc_minor in main_rpython. Runs
    # in GC-hook context: plain int/bool stores only, NO allocation, and no
    # set_param -- ending the phase here only flips the flags; the failing
    # warmup guards then force interpreted re-entry, where the purge runs.
    if _shaping_cfg.enabled and _shaping_cfg.quiesce > 0 and _shaping_warmup.on:
        _shaping_warmup.gc_tick += 1
        if _shaping_warmup.gc_tick - _shaping_warmup.last_trace_gc > _shaping_cfg.quiesce_gc:
            _shaping_warmup.on = False
            _shaping_warmup.purge_pending = 1


def shaping_configure():
    v = _os.environ.get("SOM_SHAPING")
    _shaping_cfg.enabled = int(v) if v else _shaping_cfg.enabled
    v = _os.environ.get("SOM_SHAPING_PROMOTE_INV")
    _shaping_cfg.promote_inv = int(v) if v else _shaping_cfg.promote_inv
    v = _os.environ.get("SOM_SHAPING_QUIESCE")
    _shaping_cfg.quiesce = int(v) if v else _shaping_cfg.quiesce
    v = _os.environ.get("SOM_SHAPING_QUIESCE_GC")
    _shaping_cfg.quiesce_gc = int(v) if v else _shaping_cfg.quiesce_gc
    v = _os.environ.get("SOM_SHAPING_PURGE")
    _shaping_cfg.purge = int(v) if v else _shaping_cfg.purge
    # Establish generic annotations for the fields written from the GC hook
    # and the tracer hook (both annotated outside the main program); the
    # GcHooksStats.reset pattern in main_rpython documents the same need.
    _shaping_warmup.gc_tick = _nonconst(0)
    _shaping_warmup.last_trace_gc = _nonconst(0)
    _shaping_warmup.purge_pending = _nonconst(0)
    _shaping_warmup.tick = _nonconst(0)
    _shaping_warmup.last_trace = _nonconst(0)
    if _shaping_cfg.enabled and _shaping_cfg.quiesce > 0:
        _shaping_stamp()
        _shaping_warmup.on = True


def _shaping_can_never_inline(current_bc_idx, method):
    # Consulted by the tracer on every recursive portal-call decision -- which
    # makes it the tracing-activity signal itself: stamping the tick here is
    # what arms the quiescence phase end.
    if not _shaping_cfg.enabled:
        return False
    if _shaping_warmup.on:
        _shaping_stamp()
        return True
    if _shaping_cfg.quiesce > 0:
        # Phase mode, phase over: inline everything -- post-purge retraces
        # must reach tier-3 shape; the counter would residualize callees
        # whose interpreted-activation count happens to sit below the bar.
        return False
    return method.shaping_invocations < _shaping_cfg.promote_inv
