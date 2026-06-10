import os
import sys

from rlib import jit

_interp_type_str = os.getenv("SOM_TIER", None)

_TC = 1               # threaded code
_INLINER = 2          # stack-manipulation inliner
_BC = 3               # tracing JIT
_ADAPTIVE = 4         # adaptive hybrid controller
_HYBRID = 5           # threaded-code -> tracing tier shift
_TC_NO_IC = 6         # tier 1 without inline caching
_TC_NO_IC_NO_HO = 7   # tier 1 without IC and handler opt
_UNKNOWN = 8


def _get_tier_type():
    if _interp_type_str == "1":
        return _TC
    if _interp_type_str == "2":
        return _INLINER
    if _interp_type_str == "3":
        return _BC
    if _interp_type_str == "4":
        return _ADAPTIVE
    if _interp_type_str == "5":
        return _HYBRID
    if _interp_type_str == "6":
        return _TC_NO_IC
    if _interp_type_str == "7":
        return _TC_NO_IC_NO_HO
    return _UNKNOWN


_INTERP_TYPE = _get_tier_type()


@jit.elidable
def is_tier1():
    return _INTERP_TYPE == _TC or _INTERP_TYPE == _UNKNOWN


@jit.elidable
def is_inliner():
    return _INTERP_TYPE == _INLINER


@jit.elidable
def is_tier3():
    return _INTERP_TYPE == _BC


@jit.elidable
def is_tier4():
    return _INTERP_TYPE == _ADAPTIVE


@jit.elidable
def is_hybrid():
    return _INTERP_TYPE == _HYBRID


@jit.elidable
def is_tier1_no_ic():
    return _INTERP_TYPE == _TC_NO_IC


@jit.elidable
def is_tier1_no_ic_no_ho():
    return _INTERP_TYPE == _TC_NO_IC_NO_HO


# interpret_tier3 execution modes, threaded as the promoted red `hybrid`. The
# historical bools embed (False == MODE_INLINE, True == MODE_HYBRID).
MODE_INLINE = 0   # tier 3: inline every send into the trace
MODE_HYBRID = 1   # tier 4 hybrid: residualize the _poly/_mega sites
MODE_INLINER = 2  # tier 2 stack inliner: residualize every send (warm phase)
