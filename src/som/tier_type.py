import os
import sys

from rlib import jit

_interp_type_str = os.getenv("SOM_TIER", None)

_TC = 1
_BC = 2
_HYBRID = 3
_TC_NO_IC = 4
_TC_NO_IC_NO_HO = 5
_UNKNOWN = 6


def _get_tier_type():
    if _interp_type_str == "1":
        return _TC
    if _interp_type_str == "2":
        return _BC
    if _interp_type_str == "3":
        return _HYBRID
    if _interp_type_str == "4":
        return _TC_NO_IC
    if _interp_type_str == "5":
        return _TC_NO_IC_NO_HO
    return _UNKNOWN


_INTERP_TYPE = _get_tier_type()


@jit.elidable
def is_tier1():
    return _INTERP_TYPE == _TC or _INTERP_TYPE == _UNKNOWN


@jit.elidable
def is_tier2():
    return _INTERP_TYPE == _BC


@jit.elidable
def is_hybrid():
    return _INTERP_TYPE == _HYBRID


@jit.elidable
def is_tier1_no_ic():
    return _INTERP_TYPE == _TC_NO_IC


@jit.elidable
def is_tier1_no_ic_no_ho():
    return _INTERP_TYPE == _TC_NO_IC_NO_HO
