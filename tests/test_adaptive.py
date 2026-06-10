"""Untranslated unit tests for the adaptive tier-4 controller and residual helpers.

These validate the decision logic in isolation (the JIT is a no-op untranslated, so
profiling runs every invocation). Ported from the TLA reference test/test_tla.py
TestCBController. The real gate is a translated build; see NOTE_2SOM_adaptive_tier4_port.md.
"""
import os

# SOM_TIER/SOM_INTERP must be set for the package to import.
os.environ.setdefault("SOM_INTERP", "BC")
os.environ.setdefault("SOM_TIER", "4")

from som.interpreter.bc import adaptive
from som.interpreter.bc import residual
from som.interpreter.bc.residual import (
    SK_ADD, SK_SUB, SK_MUL, SK_IDIV, SK_LT, SK_LE, SK_EQ, SK_GENERIC, SK_UNSEEN,
)
from som.vmobjects.integer import Integer
from som.vmobjects.double import Double
from som.vm.globals import trueObject, falseObject


# --- a minimal stand-in for a compiled method (only the fields the controller reads)
class FakeMethod(object):
    def __init__(self, nbytes, site_kinds=None):
        self._n = nbytes
        z = lambda: [0] * nbytes
        self._cnt_a = z()
        self._cnt_b = z()
        self._cmp_last = z()
        self._cmp_switches = z()
        self._inl_runs = z()
        self._bails = z()
        self._redecided = z()
        self._site_kind = site_kinds if site_kinds is not None else z()
        self._poly = z()
        self._mega = z()
        self._mega_miss = z()
        self._mega_seen = None
        self._mega_prev_distinct = 0
        self._mega_cache = [None] * nbytes
        self.adaptive_tier = 0
        self.adaptive_invocations = 0
        self.warm_invocations = 0
        self.warm_ops = 0
        self.ab_round = 0
        self.t3_min = 0.0
        self.t4_min = 0.0
        self.t3_n = 0
        self.t4_n = 0
        self.runs = []          # records (hybrid) of each _run_tier3 call

    def get_number_of_bytecodes(self):
        return self._n

    def _run_tier3(self, frame, max_stack_size, hybrid):
        self.runs.append(hybrid)
        return "ok"


def _reset_cfg():
    # restore shipped defaults (tests mutate _t4cfg)
    adaptive._t4cfg.cbmodel = 1
    adaptive._t4cfg.cnt_base = 100
    adaptive._t4cfg.cnt_slope = 10
    adaptive._t4cfg.cnt_maxinv = 3
    adaptive._t4cfg.ab_warm = 1
    adaptive._t4cfg.ab_samples = 2
    adaptive._t4cfg.ab_t4_margin = 5
    adaptive._t4cfg.cmp_switch_floor = 8
    adaptive._t4cfg.ratio = 1
    adaptive._t4cfg.freeze = 512
    adaptive._t4cfg.minn = 50
    adaptive._t4cfg.bailfloor = 8
    # warm phase off by default in these tests: the controller-decision tests below
    # assert the LEGACY direct commits; the warm tests enable it explicitly.
    adaptive._t4cfg.warm_enabled = 0
    adaptive._t4cfg.promote_inv = 64
    adaptive._t4cfg.promote_ops = 8192


# --- selector classification ----------------------------------------------------
def test_classify_selector():
    assert residual.classify_selector("+") == SK_ADD
    assert residual.classify_selector("-") == SK_SUB
    assert residual.classify_selector("*") == SK_MUL
    assert residual.classify_selector("/") == SK_IDIV
    assert residual.classify_selector("<") == SK_LT
    assert residual.classify_selector("<=") == SK_LE
    assert residual.classify_selector("=") == SK_EQ
    assert residual.classify_selector("~=") == residual.SK_NE
    assert residual.classify_selector("<>") == residual.SK_NE
    assert residual.classify_selector("value:") == SK_GENERIC
    assert residual.is_arith_kind(SK_ADD)
    assert not residual.is_arith_kind(SK_LT)
    assert residual.is_predicate_kind(SK_LT)
    assert not residual.is_predicate_kind(SK_ADD)


class _FakeSig(object):
    def __init__(self, s):
        self._s = s

    def get_embedded_string(self):
        return self._s


def test_site_kind_memoizes():
    m = FakeMethod(3)
    assert m._site_kind[1] == SK_UNSEEN
    k = residual.site_kind(m, 1, _FakeSig("<"))
    assert k == SK_LT
    assert m._site_kind[1] == SK_LT  # cached
    # a later call returns the cached value without re-classifying
    assert residual.site_kind(m, 1, _FakeSig("ignored")) == SK_LT


# --- _t3_pure parity ------------------------------------------------------------
def test_t3_pure_parity_and_fallback():
    a, b = Integer(7), Integer(5)
    assert residual._t3_pure(SK_ADD, a, b).get_embedded_integer() == 12
    assert residual._t3_pure(SK_SUB, a, b).get_embedded_integer() == 2
    assert residual._t3_pure(SK_MUL, a, b).get_embedded_integer() == 35
    assert residual._t3_pure(SK_LT, a, b) is falseObject
    assert residual._t3_pure(SK_LE, Integer(5), Integer(5)) is trueObject
    assert residual._t3_pure(SK_EQ, Integer(5), Integer(5)) is trueObject
    da, db = Double(3.5), Double(2.0)
    assert residual._t3_pure(SK_ADD, da, db).get_embedded_double() == 5.5
    # None-sentinel: division, mixed types, non-numeric -> caller falls back
    assert residual._t3_pure(SK_IDIV, a, b) is None
    assert residual._t3_pure(SK_ADD, a, da) is None
    assert residual._t3_pure(SK_GENERIC, a, b) is None


# --- profiling ------------------------------------------------------------------
def test_profile_arith_sets_poly_on_mixed():
    _reset_cfg()
    adaptive._t4cfg.minn = 2  # decide quickly
    m = FakeMethod(2)
    # 2 int/int then 2 mixed -> minority fraction crosses ratio==1 threshold
    adaptive._profile(m, 0, SK_ADD, Integer(1), Integer(2))
    adaptive._profile(m, 0, SK_ADD, Integer(1), Integer(2))
    assert m._cnt_a[0] == 2 and m._cnt_b[0] == 0
    adaptive._profile(m, 0, SK_ADD, Integer(1), Double(2.0))
    adaptive._profile(m, 0, SK_ADD, Integer(1), Double(2.0))
    assert m._cnt_b[0] == 2
    # with ratio==1, any minority makes minority*1 >= total false until balanced;
    # here minority=2, total=4 -> 2 >= 4 is False, so poly stays 0 (still inline).
    assert m._poly[0] == 0


def test_profile_cmp_counts_switches_and_residualizes():
    _reset_cfg()
    m = FakeMethod(2)
    # interleaved int/float comparisons at a predicate site
    seq = [Integer(1), Double(1.0), Integer(1), Double(1.0), Integer(1)]
    for v in seq:
        adaptive._profile(m, 1, SK_LT, v, v)
    # 4 shape transitions among the 5 samples
    assert m._cmp_switches[1] == 4
    # both shapes seen -> predicate residualized (poly=1)
    assert m._poly[1] == 1


# --- quasi-immutable poly replace ----------------------------------------------
def test_set_poly_replaces_array():
    m = FakeMethod(3)
    old = m._poly
    adaptive._t4_set_poly(m, 1, 1)
    assert m._poly is not old        # whole-array replace (invalidates traces)
    assert m._poly[1] == 1 and m._poly[0] == 0
    # no-op when unchanged: same array object kept
    same = m._poly
    adaptive._t4_set_poly(m, 1, 1)
    assert m._poly is same


# --- controller decisions -------------------------------------------------------
def test_controller_commits_tier3_for_monomorphic():
    _reset_cfg()
    m = FakeMethod(5)
    m._cnt_a[0] = 1000   # well past the profile gate, no cnt_b -> monomorphic
    adaptive._adaptive_tier4(m, None, 0)
    assert m.adaptive_tier == 3
    assert m.runs[-1] == adaptive.MODE_INLINE   # ran inline (tier 3)


def test_controller_commits_tier4_on_high_cmp_switches():
    _reset_cfg()
    sk = [SK_GENERIC] * 5
    sk[2] = SK_LT
    m = FakeMethod(5, site_kinds=sk)
    m._cnt_a[2] = 100
    m._cnt_b[2] = 100                # mixed predicate
    m._cmp_switches[2] = 10          # >= cmp_switch_floor (8)
    adaptive._adaptive_tier4(m, None, 0)
    assert m.adaptive_tier == 4
    assert m.runs[-1] == adaptive.MODE_HYBRID   # ran hybrid (tier 4)


def test_controller_profiles_then_decides():
    _reset_cfg()
    # empty profile, below gate -> stays profiling for the first invocations. The profile
    # gate now runs INLINE (hybrid=False) with the profiling window raised, so a
    # monomorphic method is never traced in hybrid mode.
    m = FakeMethod(5)
    adaptive._adaptive_tier4(m, None, 0)
    assert m.adaptive_tier == 0
    assert m.runs == [adaptive.MODE_INLINE]  # profiling pass runs inline
    assert adaptive._t4state.profiling == 0   # window balanced (entered then exited)


def test_legacy_controller():
    _reset_cfg()
    adaptive._t4cfg.cbmodel = 0      # legacy policy
    m = FakeMethod(3)
    # first two invocations profile (hybrid); monomorphic -> commit tier 3
    m._cnt_a[0] = 5
    adaptive._adaptive_tier4(m, None, 0)
    adaptive._adaptive_tier4(m, None, 0)
    adaptive._adaptive_tier4(m, None, 0)
    assert m.adaptive_tier == 3
    _reset_cfg()


# --- warm phase (adaptive tier2 -> tier3/4) --------------------------------------
def test_warm_phase_entered_for_monomorphic():
    _reset_cfg()
    adaptive._t4cfg.warm_enabled = 1
    m = FakeMethod(5)
    m._cnt_a[0] = 1000               # past the profile gate, monomorphic
    adaptive._adaptive_tier4(m, None, 0)
    assert m.adaptive_tier == 2                  # WARM, not a direct tier-3 commit
    assert m.runs[-1] == adaptive.MODE_INLINER   # ran in stack-inliner mode


def test_warm_promotes_by_invocations():
    _reset_cfg()
    adaptive._t4cfg.warm_enabled = 1
    adaptive._t4cfg.promote_inv = 3
    m = FakeMethod(5)
    m._cnt_a[0] = 1000
    adaptive._adaptive_tier4(m, None, 0)         # enters warm
    assert m.adaptive_tier == 2
    adaptive._adaptive_tier4(m, None, 0)         # warm_invocations 1
    adaptive._adaptive_tier4(m, None, 0)         # warm_invocations 2
    assert m.adaptive_tier == 2
    assert m.runs[-1] == adaptive.MODE_INLINER
    adaptive._adaptive_tier4(m, None, 0)         # warm_invocations 3 -> promote
    assert m.adaptive_tier == 3                  # monomorphic profile -> inline
    assert m.runs[-1] == adaptive.MODE_INLINE    # promoted activation runs committed


def test_warm_promotes_by_residual_ops():
    _reset_cfg()
    adaptive._t4cfg.warm_enabled = 1
    adaptive._t4cfg.promote_ops = 5
    m = FakeMethod(5)
    m.adaptive_tier = 2                          # already warm
    for _ in range(4):
        adaptive.warm_residual_op(m)
    assert m.adaptive_tier == 2                  # below the op threshold
    adaptive.warm_residual_op(m)                 # 5th residual send -> promote
    assert m.adaptive_tier == 3
    # no-op on a committed method (counter stops moving)
    ops = m.warm_ops
    adaptive.warm_residual_op(m)
    assert m.warm_ops == ops


def test_warm_promotes_by_callee_invocations():
    # A warm method reached only through residual sends (never the controller)
    # promotes via warm_callee_invocation (called by residual._callee_mode); it
    # shares warm_invocations/promote_inv with the controller's own count.
    _reset_cfg()
    adaptive._t4cfg.warm_enabled = 1
    adaptive._t4cfg.promote_inv = 3
    m = FakeMethod(5)
    m.adaptive_tier = 2                          # already warm
    adaptive.warm_callee_invocation(m)
    adaptive.warm_callee_invocation(m)
    assert m.adaptive_tier == 2                  # below promote_inv
    adaptive.warm_callee_invocation(m)           # 3rd callee activation -> promote
    assert m.adaptive_tier == 3                  # monomorphic profile -> inline


def test_warm_promotion_honours_mega_profile():
    _reset_cfg()
    adaptive._t4cfg.warm_enabled = 1
    adaptive._t4cfg.promote_ops = 1
    m = FakeMethod(5)
    m.adaptive_tier = 2
    m._mega = [0, 1, 0, 0, 0]                    # a mega site found while warm
    adaptive.warm_residual_op(m)                 # promote
    assert m.adaptive_tier == 4                  # mega -> hybrid, not plain tier 3


def test_warm_disabled_keeps_legacy_commit():
    _reset_cfg()                                  # warm_enabled = 0
    m = FakeMethod(5)
    m._cnt_a[0] = 1000
    adaptive._adaptive_tier4(m, None, 0)
    assert m.adaptive_tier == 3                  # direct legacy commit
    assert m.runs[-1] == adaptive.MODE_INLINE
