"""Adaptive tier-4 controller and off-trace profiling for 2SOM.

Direct port of the TLA reference controller (rpython/jit/tl/threadedcode/tla.py
and frames.py). The mechanism decides, per compiled method, whether to run the
plain tracing tier (tier 3, inline every send) or the hybrid tier (tier 4,
residualise the polymorphic predicate/arithmetic sites recorded in
``method._poly``). A static operand-type profile cannot tell interleaved
polymorphism (residual wins) from phase-separated polymorphism (inline wins) --
the tracing JIT destroys that signal once it compiles -- so for a genuinely
polymorphic method the controller A/B-*times* both tiers off-trace and commits
to the faster one.

Everything here runs OUTSIDE compiled traces: ``_profile`` is called only when
``not we_are_jitted()``, and the controller is reached through a
``@jit.dont_look_inside`` warmup path, so none of it pollutes a trace.
"""
import os

from rlib import jit
from rlib.jit import we_are_blackholing

from som.interpreter.bc.residual import (
    site_kind,
    is_predicate_kind,
)
from som.tier_type import MODE_INLINE, MODE_HYBRID, MODE_INLINER

try:
    from rpython.rlib.rtime import time as _rtime
except ImportError:
    "NOT_RPYTHON"
    import time as _pytime

    def _rtime():
        return _pytime.time()


# --- runtime-mutable configuration (instance fields, env-overridable) -----------
# RPython constant-folds module globals / list-held constants in surprising ways,
# so the knobs live on a mutable instance and are read at runtime.
class _T4Cfg(object):
    def __init__(self):
        self.debug = 0            # SOM_T4_DEBUG: trace controller commit decisions
        self.cbmodel = 1          # master gate: 0 => legacy controller
        # profile-gate threshold: cnt_base + cnt_slope * num_bytecodes
        self.cnt_base = 100
        self.cnt_slope = 10
        # profile passes before the tier decision. >=4 so a 2-entry-IC overflow (3rd
        # distinct class) is recorded before the decision pass, else mega detection
        # never bootstraps.
        self.cnt_maxinv = 4
        self.ab_warm = 1          # A/B: per-tier samples discarded as compile warmup
        self.ab_samples = 2       # A/B: timed samples per tier before committing
        self.ab_t4_margin = 5     # A/B: tier 4 must win by this percent
        self.cmp_switch_floor = 8  # cmp switches/site: interleaved -> tier 4
        # frequency-aware arithmetic residualization knobs
        self.ratio = 1            # residualise when minority * ratio >= total
        self.freeze = 512         # freeze the decision after this many samples
        self.minn = 50            # min samples before an arithmetic decision
        self.bailfloor = 8        # DRR: min off-type bails before a re-decision
        # Megamorphic-send residualisation. tier 3 dispatches a megamorphic send via a
        # per-class bridge chain (O(N) per call); residualising it behind a barrier is
        # O(1) and wins past the crossover. The residual dispatch uses a per-site PIC
        # (BcMethod._mega_cache), which lowered the crossover to ~24 distinct classes;
        # measured >=20% win from ~30 (+22% @30 up to +72% @80). Residualise only when a
        # site's distinct receiver-class count reaches mega_floor.
        self.mega_enabled = 1     # SOM_CB_MEGA: 0 disables megamorphic residualisation
        # distinct receiver classes at a site before residualising (30 = clear >=20%
        # point, safely above the ~24 parity zone).
        self.mega_floor = 30
        self.mega_probe_max = 80  # max profiling invocations to count classes for a candidate
        # WARM phase: after profiling, a method first commits tier 2 (MODE_INLINER, cheap
        # residualizing traces), then promotes to its final tier (3/4) once hot -- by
        # promote_inv warm activations or promote_ops residual sends. The adaptive_tier
        # write invalidates the warm traces, so running loops deopt to the committed mode.
        self.warm_enabled = 1     # SOM_T4_WARM: 0 disables the warm phase (legacy decisions)
        # Promotion thresholds (grid on Experiment / micro-startup / DeltaBlue-steady):
        #   ops=2048/inv=16 -> exp 0.893, startup 0.992; ops=8192/inv=64 -> exp 0.827,
        #   startup 1.007; ops=32768/inv=256 -> exp ~0.79 but startup 1.03 / DeltaBlue 1.25.
        self.promote_inv = 64     # SOM_T4_PROMOTE_INV: warm activations before promotion
        self.promote_ops = 8192   # SOM_T4_PROMOTE_OPS: warm residual sends before promotion
        # Warm is a STARTUP tier: after warm_era seconds of process runtime every warm
        # entry promotes immediately and new decisions commit directly. Without the
        # deadline, low-traffic warm methods linger and steady-hot traces compiled
        # meanwhile bake in adaptive_tier==2 guards/barrier calls on non-constant
        # paths that never heal (DeltaBlue steady 1.2x at 100 iterations).
        self.warm_era = 0.4       # SOM_T4_WARM_ERA_MS / 1000.0
        # COLD phase: before profiling even starts, an undecided method runs its
        # first cold_inv activations on the THREADED-CODE interpreter (tier 1).
        # Cold activations do not consume the profile gate. A single-activation
        # hot loop escapes mid-method after cold_ops back-edges (cold_backedge ->
        # ContinueInTier2 -> lean tier-3 resume), mirroring warm's promote_ops.
        # DEFAULT OFF -- measured a net LOSS as a phase: the tier-1 loop
        # interprets ~3x slower than the lean graphs (shallow-handler indirection
        # is built for chain compilation, not interpretation), and threaded
        # chains never compile within any cold residence that doesn't strangle
        # warmup (Experiment19 0.22s -> 0.34s at inv=16/ops=4096; threshold
        # collisions abort recordings at small ops budgets; era-length residence
        # is ~5x slower and segfaults once chains compile). The cheap-compiled
        # warmup niche is already taken by the warm inliner tier. Threaded code
        # pays off as a long-residence STANDALONE tier (SOM_TIER=1), not a phase.
        self.cold_enabled = 0     # SOM_T4_COLD: 1 enables the experimental cold phase
        self.cold_inv = 16        # SOM_T4_COLD_INV: cold activations before profiling
        self.cold_ops = 4096      # SOM_T4_COLD_OPS: cold back-edges before the OSR escape
        # Straggler drain: a warm method older than this many controller DECISIONS
        # promotes at the next decision pass. Low-traffic methods otherwise stay
        # warm forever (their entries stop before promote_inv), and warm-history
        # exposure perturbs the shape of steady traces (DeltaBlue deep-steady 1.2x
        # at age 48 vs tier3 parity at 8; experiments unaffected). Decision count,
        # not wall clock, so it tracks workload progress.
        self.warm_drain_age = 8   # SOM_T4_DRAIN_AGE


_t4cfg = _T4Cfg()


# Runtime-mutable controller state on a prebuilt instance (RPython would drop the
# in-place writes of a module-global counter after translation -- the same reason
# _T4Cfg is an instance). `profiling` is the window the controller raises around a
# method's profile-gate activation so the send handlers gather the operand/layout
# profile while the method runs inline -- never traced hybrid=True, which would leave
# a stale loop that committed hybrid=False calls fail the top guard on (Fibonacci 3.2x).
class _T4State(object):
    def __init__(self):
        self.profiling = 0   # >0 while a profile-gate activation is running
        self.start_time = 0.0  # process start (set by _t4_configure); warm-era anchor
        self.decisions = 0   # controller decision passes (drain clock)
        self.warm_list = []  # methods currently committed warm (drain registry)
        self.d_layout = 0    # diag: _profile_layout calls
        self.d_overflow = 0  # diag: inline-cache overflows observed
        self.d_gate = 0      # diag: profile-gate activations
        self.d_gatejit = 0   # diag: gate activations seen under we_are_jitted()


_t4state = _T4State()


def _profiling_active():
    return _t4state.profiling > 0


def _profiling_active_for(method):
    """True when the profiling window is open and `method` is still uncommitted. The
    window is a global flag, so the per-method guard stops an already-committed method
    from paying profiling instrumentation while some other method is being profiled."""
    return _t4state.profiling > 0 and method.adaptive_tier == 0


def _env_int(name, current):
    v = os.environ.get(name)
    if v:
        return int(v)
    return current


def _t4_configure():
    """Read the SOM_* knobs once at startup. Call from the VM bootstrap."""
    _t4cfg.cbmodel = _env_int("SOM_ADAPTIVE_MODEL", _t4cfg.cbmodel)
    _t4cfg.cnt_base = _env_int("SOM_CB_CNT_BASE", _t4cfg.cnt_base)
    _t4cfg.cnt_slope = _env_int("SOM_CB_CNT_SLOPE", _t4cfg.cnt_slope)
    _t4cfg.cnt_maxinv = _env_int("SOM_CB_CNT_MAXINV", _t4cfg.cnt_maxinv)
    _t4cfg.ab_warm = _env_int("SOM_CB_AB_WARM", _t4cfg.ab_warm)
    _t4cfg.ab_samples = _env_int("SOM_CB_AB_SAMPLES", _t4cfg.ab_samples)
    _t4cfg.ab_t4_margin = _env_int("SOM_CB_AB_HYBRID_MARGIN", _t4cfg.ab_t4_margin)
    _t4cfg.cmp_switch_floor = _env_int("SOM_CB_SWITCH_FLOOR", _t4cfg.cmp_switch_floor)
    _t4cfg.ratio = _env_int("SOM_POLY_RATIO", _t4cfg.ratio)
    _t4cfg.freeze = _env_int("SOM_FREEZE", _t4cfg.freeze)
    _t4cfg.minn = _env_int("SOM_PROFILE_MIN", _t4cfg.minn)
    _t4cfg.bailfloor = _env_int("SOM_BAIL_FLOOR", _t4cfg.bailfloor)
    _t4cfg.mega_enabled = _env_int("SOM_CB_MEGA", _t4cfg.mega_enabled)
    _t4cfg.mega_floor = _env_int("SOM_CB_MEGA_FLOOR", _t4cfg.mega_floor)
    _t4cfg.mega_probe_max = _env_int("SOM_CB_MEGA_PROBE", _t4cfg.mega_probe_max)
    _t4cfg.warm_enabled = _env_int("SOM_T4_WARM", _t4cfg.warm_enabled)
    _t4cfg.cold_enabled = _env_int("SOM_T4_COLD", _t4cfg.cold_enabled)
    _t4cfg.cold_inv = _env_int("SOM_T4_COLD_INV", _t4cfg.cold_inv)
    _t4cfg.cold_ops = _env_int("SOM_T4_COLD_OPS", _t4cfg.cold_ops)
    _t4cfg.promote_inv = _env_int("SOM_T4_PROMOTE_INV", _t4cfg.promote_inv)
    _t4cfg.promote_ops = _env_int("SOM_T4_PROMOTE_OPS", _t4cfg.promote_ops)
    _t4cfg.warm_era = _env_int("SOM_T4_WARM_ERA_MS", int(_t4cfg.warm_era * 1000)) / 1000.0
    _t4cfg.warm_drain_age = _env_int("SOM_T4_DRAIN_AGE", _t4cfg.warm_drain_age)
    _t4cfg.debug = _env_int("SOM_T4_DEBUG", _t4cfg.debug)
    _t4state.start_time = _rtime()


def _warm_era_over():
    # Warm is a startup tier; past the era every warm entry promotes immediately
    # and new decisions commit directly. Called only off-trace on warm/decision
    # paths, so the clock read costs nothing on committed steady state.
    return _rtime() - _t4state.start_time > _t4cfg.warm_era


def _t4_dbg(method, tier, reason):
    """Env-gated (SOM_T4_DEBUG) controller-decision trace to stderr. Off-trace only."""
    if _t4cfg.debug:
        os.write(2, "[t4] commit tier " + str(tier) + " (" + reason + ") "
                 + method.merge_point_string() + "\n")


# --- quasi-immutable poly update (array-replace-on-change) -----------------------
def _t4_set_poly(method, site, value):
    """Set method._poly[site]; replaces the whole array on change so the JIT
    invalidates the affected hybrid traces (see BcAbstractMethod._immutable_fields_).
    NEVER store into _poly in place."""
    if method._poly[site] == value:
        return
    new = method._poly[:]
    new[site] = value
    method._poly = new


# --- megamorphic-dispatch detection & decision ----------------------------------
def _set_mega(method, site, value):
    """Set method._mega[site]; whole-array replace so the JIT invalidates traces that
    folded the old value (quasi-immutable, like _poly). NEVER store in place."""
    if method._mega[site] == value:
        return
    new = method._mega[:]
    new[site] = value
    method._mega = new


def _layout_callee_trivial(layout, signature):
    from som.vmobjects.method_trivial import AbstractTrivialMethod

    if layout is None:
        return False
    invokable = layout.lookup_invokable(signature)
    return invokable is not None and isinstance(invokable, AbstractTrivialMethod)


def _all_callees_trivial(method, site, sset, signature):
    """True if every receiver class seen at the site resolves `signature` to a TRIVIAL method
    (literal return / field read / global read / field write). Such callees have an O(1)
    invoke that bypasses the interpreter loop, so residualising the megamorphic send stays
    cheap (a flat per-call cost that beats tier 3's O(N) guard chain past the crossover). A
    NON-trivial callee runs the interpreter loop behind the @dont_look_inside barrier, which
    is far costlier than tier 3's compiled inline dispatch -- residualising those LOSES
    (MegaArith40 measured 3.6x slower), so we must not mark the site megamorphic. Checks both
    the two classes held in the inline cache and the overflow classes tracked in `sset`."""
    if not _layout_callee_trivial(method.get_inline_cache_layout(site), signature):
        return False
    if not _layout_callee_trivial(method.get_inline_cache_layout(site + 1), signature):
        return False
    for layout in sset:
        if not _layout_callee_trivial(layout, signature):
            return False
    return True


def _profile_layout(method, site, signature, layout):
    """Count the DISTINCT receiver layouts seen at `site`, off-trace during the profiling
    window. The caller only invokes this once the 2-entry inline cache has OVERFLOWED, so
    `layout` is always a 3rd-or-later class; the two classes still held in the IC are added
    back as the `+ 2` below to recover the true distinct total. When that total reaches
    mega_floor AND every callee is trivial, the site is deeply megamorphic with cheap callees
    -- tier 3's per-class guard/bridge chain is O(N) per call -- so it is marked for opaque
    O(1) residualisation. The total is cached in _mega_miss[site] for the controller's growth
    check. Tracking only post-overflow classes keeps the monomorphic/2-class majority free."""
    if not _t4cfg.mega_enabled:
        return
    _t4state.d_layout += 1
    seen = method._mega_seen
    if seen is None:
        seen = {}
        method._mega_seen = seen
    sset = seen.get(site, None)
    if sset is None:
        sset = {}
        seen[site] = sset
    if layout not in sset:
        sset[layout] = True
        _t4state.d_overflow += 1
        n = len(sset) + 2   # + the two distinct classes still held in the inline cache
        method._mega_miss[site] = n
        if n >= _t4cfg.mega_floor and _all_callees_trivial(method, site, sset, signature):
            _set_mega(method, site, 1)


def _mega_max_distinct(method):
    """Largest distinct-receiver-class count over all send sites (0 if none profiled)."""
    if method._mega_seen is None:
        return 0
    best = 0
    n = method.get_number_of_bytecodes()
    i = 0
    while i < n:
        c = method._mega_miss[i]
        if c > best:
            best = c
        i += 1
    return best


def _has_mega_site(method):
    n = method.get_number_of_bytecodes()
    i = 0
    while i < n:
        if method._mega[i] != 0:
            return True
        i += 1
    return False


# --- off-trace operand-shape profiling (port of frames.py:_profile) -------------
def _profile(method, site, kind, w_rcvr, w_arg):
    """Update the per-site operand-shape profile for `site`. cnt_a counts the
    dominant Integer x Integer shape, cnt_b every other shape. Predicate sites
    additionally count interpreted shape *switches* (the interleaving signal the
    JIT would later erase). Must be called only when ``not we_are_jitted()``."""
    from som.vmobjects.integer import Integer

    if isinstance(w_rcvr, Integer) and isinstance(w_arg, Integer):
        shape = 1
        a = method._cnt_a[site] + 1
        method._cnt_a[site] = a
        b = method._cnt_b[site]
    else:
        shape = 2
        a = method._cnt_a[site]
        b = method._cnt_b[site] + 1
        method._cnt_b[site] = b

    if is_predicate_kind(kind):
        if not we_are_blackholing():
            last = method._cmp_last[site]
            if last != 0 and last != shape:
                method._cmp_switches[site] += 1
            method._cmp_last[site] = shape
        if a != 0 and b != 0:
            # residualise a polymorphic predicate; whether tier 3 or tier 4 actually
            # runs is the controller's A/B choice.
            _t4_set_poly(method, site, 1)
    else:
        total = a + b
        if _t4cfg.minn <= total <= _t4cfg.freeze:
            minority = a if a < b else b
            if minority * _t4cfg.ratio >= total:
                _t4_set_poly(method, site, 1)
            else:
                _t4_set_poly(method, site, 0)
        # DRR (deopt-rate re-decision): inert at ratio==1 (the shipped default).
        if we_are_blackholing():
            nb = method._bails[site] + 1
            method._bails[site] = nb
            if (method._redecided[site] == 0 and
                    nb >= _t4cfg.bailfloor and
                    nb * _t4cfg.ratio >= method._inl_runs[site] + nb):
                _t4_set_poly(method, site, 1)
                method._redecided[site] = 1


# --- profile aggregation helpers (port of tla.py) -------------------------------
def _cb_observed_ops(method):
    s = 0
    n = method.get_number_of_bytecodes()
    i = 0
    while i < n:
        s += method._cnt_a[i] + method._cnt_b[i]
        i += 1
    return s


def _has_mixed_operand_profile(method):
    n = method.get_number_of_bytecodes()
    i = 0
    while i < n:
        if method._cnt_a[i] != 0 and method._cnt_b[i] != 0:
            return True
        if method._bails[i] != 0:
            return True
        i += 1
    return False


def _has_mixed_cmp_profile(method):
    n = method.get_number_of_bytecodes()
    i = 0
    while i < n:
        if is_predicate_kind(method._site_kind[i]):
            if method._cnt_a[i] != 0 and method._cnt_b[i] != 0:
                return True
            if method._bails[i] != 0:
                return True
        i += 1
    return False


def _cb_cmp_switch_decision(method):
    # Fast path for predicate polymorphism: interleaved predicates switch operand
    # shape repeatedly during interpreted warmup. High switch count => tier 4
    # without paying an A/B probe. Low switch count is ambiguous (a hybrid probe
    # can still expose interleaving), so it falls through to A/B.
    best = 0
    n = method.get_number_of_bytecodes()
    i = 0
    while i < n:
        if is_predicate_kind(method._site_kind[i]):
            if method._cnt_a[i] != 0 and method._cnt_b[i] != 0:
                s = method._cmp_switches[i]
                if s > best:
                    best = s
        i += 1
    if _t4cfg.cmp_switch_floor > 0 and best >= _t4cfg.cmp_switch_floor:
        return 4
    return 0


def _cb_ab_pick(method):
    # Decide once both tiers have enough timed samples: faster best-of-min wins,
    # ties -> tier 3 (no per-op hybrid tax). Tier 4 must clear a small margin.
    # Returns 0 (undecided) until both tiers have ab_samples timed runs.
    if method.t3_n < _t4cfg.ab_samples or method.t4_n < _t4cfg.ab_samples:
        return 0
    t4_limit = method.t3_min * (100.0 - _t4cfg.ab_t4_margin)
    if method.t4_min * 100.0 < t4_limit:
        return 4
    return 3


# --- warm-phase promotion (adaptive tier2 -> tier3/4) ----------------------------
def _promote_warm(method, reason):
    """Commit the final tier for a method leaving its warm (MODE_INLINER) phase.
    The operand/layout profile gathered while warm feeds the same decision logic as
    the initial profiling window: megamorphic-trivial site -> tier 4 (mega residual),
    interleaved predicate polymorphism -> tier 4 (hybrid), else tier 3 (inline).
    Writing adaptive_tier (quasi-immutable) invalidates every compiled warm trace,
    so running warm loops deopt and continue at the committed mode."""
    if _t4cfg.mega_enabled and _has_mega_site(method):
        method.adaptive_tier = 4
        method._mega_seen = None
        _t4_dbg(method, 4, "warm-mega-" + reason)
        return
    if _has_mixed_cmp_profile(method) and _cb_cmp_switch_decision(method) == 4:
        method.adaptive_tier = 4
        _t4_dbg(method, 4, "warm-cmp-" + reason)
        return
    method.adaptive_tier = 3
    _t4_dbg(method, 3, "warm-" + reason)


def _tick_decision():
    """One controller decision pass: advance the drain clock and promote warm
    STRAGGLERS -- methods whose warm commit is older than warm_drain_age
    decisions. Their own entries stop before promote_inv, so without the drain
    they stay warm forever and steady-hot traces keep inlined-warm copies of
    them. Runs off-trace in the controller; the registry stays small."""
    _t4state.decisions += 1
    if len(_t4state.warm_list) == 0:
        return
    kept = []
    for m in _t4state.warm_list:
        if m.adaptive_tier != 2:
            continue  # promoted by count/era meanwhile
        if _t4state.decisions - m.warm_epoch > _t4cfg.warm_drain_age:
            _promote_warm(m, "drain")
        else:
            kept.append(m)
    _t4state.warm_list = kept


def _enter_warm(method):
    """Commit `method` to the warm tier and register it for the straggler drain."""
    method.adaptive_tier = 2
    method.warm_epoch = _t4state.decisions
    _t4state.warm_list.append(method)


@jit.dont_look_inside
def warm_callee_invocation(method):
    """Count one activation of a warm method reached outside the controller (residual
    sends, and inline-path invokes from committed code). Both bypass the controller,
    so without this a method only ever called there would never promote. Opaque so
    the counter never pollutes a trace. Shares warm_invocations/promote_inv."""
    n = method.warm_invocations + 1
    method.warm_invocations = n
    if n >= _t4cfg.promote_inv or _warm_era_over():
        _promote_warm(method, "inv")


def warm_residual_op(method):
    """Count one residual send executed from `method`'s warm code. Called behind the
    residual barrier (also from inside compiled warm traces), so a single-activation
    hot loop reaches the threshold and promotes mid-loop. No-op for non-warm methods."""
    if method.adaptive_tier != 2:
        return
    n = method.warm_ops + 1
    method.warm_ops = n
    if n >= _t4cfg.promote_ops:
        _promote_warm(method, "ops")


# --- cold-phase (threaded code, tier 1) ------------------------------------------
@jit.dont_look_inside
def cold_backedge(method):
    """Count one back-edge of a COLD (threaded-code) activation. True once the
    activation has looped past the cold budget: the tier-1 loop then escapes to
    the lean tier-3 graph mid-activation (ContinueInTier2, caught in
    BcMethod._run_tier1), so a single-activation hot loop never sticks on
    threaded code. The call is residualised into compiled threaded chains
    (@dont_look_inside), so chains exit by the same guard."""
    n = method.cold_ops + 1
    method.cold_ops = n
    return n >= _t4cfg.cold_ops


# The controller (port of tla.py:_adaptive_tier4). @dont_look_inside: it runs once
# per outer activation, off any trace, so time() and the A/B logic never pollute one.
@jit.dont_look_inside
def _adaptive_tier4(method, frame, max_stack_size):
    if not _t4cfg.cbmodel:
        return _adaptive_tier4_legacy(method, frame, max_stack_size)

    method.adaptive_invocations += 1

    committed = method.adaptive_tier
    if committed == 4:
        return method._run_tier3(frame, max_stack_size, MODE_HYBRID)
    if committed == 3:
        return method._run_tier3(frame, max_stack_size, MODE_INLINE)
    if committed == 2:
        # WARM: run the stack-inliner mode and count the activation toward promotion
        # (warm_residual_op promotes single-activation hot loops independently).
        # Past the warm era, promote immediately (warm is a startup tier).
        method.warm_invocations += 1
        if method.warm_invocations >= _t4cfg.promote_inv or _warm_era_over():
            _promote_warm(method, "inv")
            at = method.adaptive_tier
            return method._run_tier3(
                frame, max_stack_size, MODE_HYBRID if at == 4 else MODE_INLINE
            )
        return method._run_tier3(frame, max_stack_size, MODE_INLINER)

    # 0) COLD: run the first activations of an undecided method on the threaded-code
    #    interpreter (tier 1) -- the cheapest tier while the method may yet be
    #    cold-forever. Cold activations are rewound off the profile gate so the
    #    profiling window stays full-length afterwards. Era-bounded like warm; a
    #    method that spent its back-edge budget escaped mid-loop and skips cold
    #    for good (its loops are hot -- get them to profiling/commit).
    if (_t4cfg.cold_enabled and not _warm_era_over()
            and method.cold_invocations < _t4cfg.cold_inv
            and method.cold_ops < _t4cfg.cold_ops):
        method.cold_invocations += 1
        method.adaptive_invocations -= 1
        return method._run_tier1(frame, max_stack_size)

    # 1) Profile a little, running inline with the profiling window raised so the send
    #    handlers gather the operand-type mix. A site whose distinct receiver-class count
    #    is still climbing (and below mega_floor) extends the window up to mega_probe_max
    #    so a deeply megamorphic site is fully observed; low-poly sites plateau and stop.
    thr = _t4cfg.cnt_base + _t4cfg.cnt_slope * method.get_number_of_bytecodes()
    keep_profiling = (_cb_observed_ops(method) < thr and
                      method.adaptive_invocations < _t4cfg.cnt_maxinv)
    max_d = _mega_max_distinct(method)
    if (not keep_profiling and _t4cfg.mega_enabled and
            method.adaptive_invocations < _t4cfg.mega_probe_max and
            max_d < _t4cfg.mega_floor and max_d > method._mega_prev_distinct):
        keep_profiling = True
    if keep_profiling:
        method._mega_prev_distinct = max_d
        _t4state.d_gate += 1
        if jit.we_are_jitted():
            _t4state.d_gatejit += 1
        _t4state.profiling += 1
        try:
            # _run_profiling forces the SHARED interpreter (profiling hooks live
            # there); plain MODE_INLINE now routes to the lean tier-3 graph.
            return method._run_profiling(frame, max_stack_size)
        finally:
            _t4state.profiling -= 1

    if _t4cfg.debug:
        os.write(2, "[t4dbg] decide " + method.merge_point_string()
                 + " obs=" + str(_cb_observed_ops(method))
                 + " inv=" + str(method.adaptive_invocations)
                 + " maxinv=" + str(_t4cfg.cnt_maxinv)
                 + " thr=" + str(thr)
                 + " hasMega=" + str(_has_mega_site(method))
                 + " layoutCalls=" + str(_t4state.d_layout)
                 + " overflows=" + str(_t4state.d_overflow) + " gate=" + str(_t4state.d_gate) + " gateJitted=" + str(_t4state.d_gatejit) + "\n")

    _tick_decision()

    # 1b) deeply-megamorphic site (>= mega_floor distinct classes) -> commit tier 4 so the
    #     site is residualised opaquely (O(1) dispatch vs tier 3's per-class bridge chain).
    if _t4cfg.mega_enabled and _has_mega_site(method):
        method.adaptive_tier = 4
        method._mega_seen = None   # profiling done; free the per-site layout sets
        _t4_dbg(method, 4, "mega")
        return method._run_tier3(frame, max_stack_size, MODE_HYBRID)

    # 2) monomorphic -> nothing to residualise. With the warm phase on, commit tier 2
    #    first and let _promote_warm re-decide 3-vs-4 once hot; off, commit tier 3 directly.
    if not _has_mixed_operand_profile(method):
        if _t4cfg.warm_enabled and not _warm_era_over():
            _enter_warm(method)
            _t4_dbg(method, 2, "warm-mono-operand")
            return method._run_tier3(frame, max_stack_size, MODE_INLINER)
        method.adaptive_tier = 3
        _t4_dbg(method, 3, "mono-operand")
        return method._run_tier3(frame, max_stack_size, MODE_INLINE)
    if not _has_mixed_cmp_profile(method):
        if _t4cfg.warm_enabled and not _warm_era_over():
            _enter_warm(method)
            _t4_dbg(method, 2, "warm-mono-cmp")
            return method._run_tier3(frame, max_stack_size, MODE_INLINER)
        method.adaptive_tier = 3
        _t4_dbg(method, 3, "mono-cmp")
        return method._run_tier3(frame, max_stack_size, MODE_INLINE)

    # 3) interleaved predicate polymorphism -> tier 4 immediately
    if _cb_cmp_switch_decision(method) == 4:
        method.adaptive_tier = 4
        _t4_dbg(method, 4, "cmp-switch")
        return method._run_tier3(frame, max_stack_size, MODE_HYBRID)

    # 4) otherwise A/B-time tier 3 (even rounds) vs tier 4 (odd rounds) and commit
    #    the faster. The first ab_warm samples of each tier are discarded.
    rnd = method.ab_round
    method.ab_round = rnd + 1
    use_t4 = (rnd & 1) == 1
    t0 = _rtime()
    res = method._run_tier3(
        frame, max_stack_size, MODE_HYBRID if use_t4 else MODE_INLINE
    )
    dt = _rtime() - t0
    # a hybrid probe can expose interleaving the static profile missed
    if use_t4 and _cb_cmp_switch_decision(method) == 4:
        method.adaptive_tier = 4
        _t4_dbg(method, 4, "ab-cmp-switch")
        return res
    if rnd // 2 >= _t4cfg.ab_warm:
        if use_t4:
            if method.t4_n == 0 or dt < method.t4_min:
                method.t4_min = dt
            method.t4_n += 1
        else:
            if method.t3_n == 0 or dt < method.t3_min:
                method.t3_min = dt
            method.t3_n += 1
        pick = _cb_ab_pick(method)
        if pick != 0:
            method.adaptive_tier = pick
            _t4_dbg(method, pick, "ab-pick")
    return res


@jit.dont_look_inside
def _adaptive_tier4_legacy(method, frame, max_stack_size):
    """Legacy controller (SOM_ADAPTIVE_MODEL=0): profile a few invocations, then
    commit tier 4 if any mixed operand profile exists, else tier 3."""
    method.adaptive_invocations += 1
    if method.adaptive_tier == 0:
        if method.adaptive_invocations <= 2:
            return method._run_tier3(frame, max_stack_size, True)
        if _has_mixed_operand_profile(method):
            method.adaptive_tier = 4
        else:
            method.adaptive_tier = 3
    elif method.adaptive_tier == 3 and _has_mixed_operand_profile(method):
        method.adaptive_tier = 4

    if method.adaptive_tier == 4:
        return method._run_tier3(frame, max_stack_size, True)
    return method._run_tier3(frame, max_stack_size, False)
