"""Type-invariant residual helpers for the stack-inliner (tier 2) and the adaptive
hybrid (tier 4) tiers.

Residualising a send computes it opaquely so the operand type never enters the trace,
keeping compiled code invariant to the type mix. Two mechanisms:

* _t3_pure -- @jit.elidable fast path for the pure primitive ops (+ - * and the
  comparisons) on Integer^2 / Double^2; returns None when it cannot prove the result
  so the caller falls back to the real send.
* _residual_send_* -- @jit.dont_look_inside opaque dispatch for every other send and
  for div/mod/rem (which can raise, so they stay behind the barrier and out of
  _t3_pure, which must never raise).
"""
from rlib import jit

from som.tier_type import is_adaptive, MODE_INLINE, MODE_HYBRID, MODE_INLINER
from som.vm.globals import trueObject, falseObject
from som.vmobjects.double import Double
from som.vmobjects.integer import Integer


# --- site classification codes (stored in BcAbstractMethod._site_kind) ----------
SK_UNSEEN = 0     # not yet classified
SK_GENERIC = 1    # a send that is not a fast arithmetic/comparison primitive
SK_ADD = 10       # '+'   -> prim_add
SK_SUB = 11       # '-'   -> prim_subtract
SK_MUL = 12       # '*'   -> prim_multiply
SK_IDIV = 13      # '/'   -> prim_int_div     (can raise)
SK_DDIV = 14      # '//'  -> prim_double_div  (can raise)
SK_MOD = 15       # '%'   -> prim_modulo      (can raise)
SK_REM = 16       # 'rem:'-> prim_remainder   (can raise)
SK_LT = 20        # '<'
SK_LE = 21        # '<='
SK_GT = 22        # '>'
SK_GE = 23        # '>='
SK_EQ = 24        # '='   -> prim_equals
SK_NE = 25        # '~=' / '<>' -> prim_unequals
SK_EQEQ = 26      # '=='  -> _equals_equals (Integer fast case only)


def is_arith_kind(kind):
    """Arithmetic/data site: frequency-aware residualization, never an A/B probe."""
    return SK_ADD <= kind <= SK_REM


def is_predicate_kind(kind):
    """Predicate/control-flow site: the cmp-switch interleaving policy applies."""
    return SK_LT <= kind <= SK_EQEQ


def classify_selector(selector):
    """Map a selector string to a site-kind code (SK_GENERIC if not a fast op)."""
    if selector == "+":
        return SK_ADD
    if selector == "-":
        return SK_SUB
    if selector == "*":
        return SK_MUL
    if selector == "/":
        return SK_IDIV
    if selector == "//":
        return SK_DDIV
    if selector == "%":
        return SK_MOD
    if selector == "rem:":
        return SK_REM
    if selector == "<":
        return SK_LT
    if selector == "<=":
        return SK_LE
    if selector == ">":
        return SK_GT
    if selector == ">=":
        return SK_GE
    if selector == "=":
        return SK_EQ
    if selector == "~=" or selector == "<>":
        return SK_NE
    if selector == "==":
        return SK_EQEQ
    return SK_GENERIC


def site_kind(method, site, signature):
    """Return the cached site-kind for `site`, classifying (off-trace) on first sight.

    `signature` is the selector Symbol already fetched by the send handler. The
    string comparison happens at most once per site; afterwards the hot loop only
    reads the memoized int from method._site_kind.
    """
    kind = method._site_kind[site]
    if kind == SK_UNSEEN:
        kind = classify_selector(signature.get_embedded_string())
        method._site_kind[site] = kind
    return kind


# --- pure fast residual (elidable: no side effects, never raises) ---------------
@jit.elidable
def _t3_pure(op, w_rcvr, w_arg):
    """Compute a pure primitive op type-invariantly for Integer^2 / Double^2.

    Returns the result wrapper, or None when the fast path does not apply so the
    caller falls back to the real (opaque) send. Handles only ops that cannot
    raise -- division/modulo are excluded by construction.
    """
    if isinstance(w_rcvr, Integer) and isinstance(w_arg, Integer):
        if op == SK_ADD:
            return w_rcvr.prim_add(w_arg)
        if op == SK_SUB:
            return w_rcvr.prim_subtract(w_arg)
        if op == SK_MUL:
            return w_rcvr.prim_multiply(w_arg)
        if op == SK_LT:
            return trueObject if w_rcvr.prim_less_than(w_arg) else falseObject
        if op == SK_LE:
            return w_rcvr.prim_less_than_or_equal(w_arg)
        if op == SK_GT:
            return w_rcvr.prim_greater_than(w_arg)
        if op == SK_GE:
            return w_rcvr.prim_greater_than_or_equal(w_arg)
        if op == SK_EQ or op == SK_EQEQ:
            # '=' and Integer '==' both reduce to prim_equals for Integer x Integer.
            return w_rcvr.prim_equals(w_arg)
        if op == SK_NE:
            return w_rcvr.prim_unequals(w_arg)
        return None
    if isinstance(w_rcvr, Double) and isinstance(w_arg, Double):
        if op == SK_ADD:
            return w_rcvr.prim_add(w_arg)
        if op == SK_SUB:
            return w_rcvr.prim_subtract(w_arg)
        if op == SK_MUL:
            return w_rcvr.prim_multiply(w_arg)
        if op == SK_LT:
            return trueObject if w_rcvr.prim_less_than(w_arg) else falseObject
        if op == SK_LE:
            return w_rcvr.prim_less_than_or_equal(w_arg)
        if op == SK_GT:
            return w_rcvr.prim_greater_than(w_arg)
        if op == SK_GE:
            return w_rcvr.prim_greater_than_or_equal(w_arg)
        if op == SK_EQ:
            return w_rcvr.prim_equals(w_arg)
        if op == SK_NE:
            return w_rcvr.prim_unequals(w_arg)
        # Double '==' (SK_EQEQ) is identity-flavoured; not the Integer fast case.
        return None
    return None


# Generic opaque residual dispatch: the callee body never enters the caller's trace.
# `method` is the caller. In the tier-4 binary the warm phase runs here, so the
# dispatch must be as cheap as the tier-2 inliner -- one invoke_*_tier3 call, NOT a
# route through the dont_look_inside controller. _callee_mode reads the callee's
# committed tier directly (adaptive_tier is quasi-immutable).
def _warm_op(method):
    from som.interpreter.bc.adaptive import warm_residual_op

    warm_residual_op(method)


def _callee_mode(invokable):
    # Mode a residual callee runs in: a leaf (primitive/trivial, no adaptive_tier)
    # runs MODE_INLINE; a BcAbstractMethod runs its committed mode, or MODE_INLINER
    # while warm/undecided. A warm callee also counts this activation toward
    # promotion -- residual sends bypass the controller, so without it a method only
    # ever called from warm code would never promote.
    from som.vmobjects.method_bc import BcAbstractMethod

    if isinstance(invokable, BcAbstractMethod):
        at = invokable.adaptive_tier
        if at == 3:
            return MODE_INLINE
        if at == 4:
            return MODE_HYBRID
        if at == 2:
            from som.interpreter.bc.adaptive import warm_callee_invocation

            warm_callee_invocation(invokable)
            at = invokable.adaptive_tier  # may have just promoted
            if at == 3:
                return MODE_INLINE
            if at == 4:
                return MODE_HYBRID
        return MODE_INLINER
    return MODE_INLINE


@jit.dont_look_inside
def _residual_send_1(method, invokable, rcvr):
    if is_adaptive():
        _warm_op(method)
        return invokable.invoke_1_tier3(rcvr, _callee_mode(invokable))
    return invokable.invoke_1_tier3(rcvr, MODE_INLINE)


@jit.dont_look_inside
def _residual_send_2(method, invokable, rcvr, arg):
    if is_adaptive():
        _warm_op(method)
        return invokable.invoke_2_tier3(rcvr, arg, _callee_mode(invokable))
    return invokable.invoke_2_tier3(rcvr, arg, MODE_INLINE)


@jit.dont_look_inside
def _residual_send_3(method, invokable, rcvr, arg1, arg2):
    if is_adaptive():
        _warm_op(method)
        return invokable.invoke_3_tier3(rcvr, arg1, arg2, _callee_mode(invokable))
    return invokable.invoke_3_tier3(rcvr, arg1, arg2, MODE_INLINE)


@jit.dont_look_inside
def _residual_send_4(method, invokable, rcvr, arg1, arg2, arg3):
    if is_adaptive():
        _warm_op(method)
        return invokable.invoke_4_tier3(rcvr, arg1, arg2, arg3, _callee_mode(invokable))
    return invokable.invoke_4_tier3(rcvr, arg1, arg2, arg3, MODE_INLINE)


@jit.dont_look_inside
def _residual_send_n(method, invokable, stack, stack_ptr):
    if is_adaptive():
        _warm_op(method)
        return invokable.invoke_n_tier3(stack, stack_ptr, _callee_mode(invokable))
    return invokable.invoke_n_tier3(stack, stack_ptr, MODE_INLINE)
