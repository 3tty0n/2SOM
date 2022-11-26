import os
import sys

from rlib import jit

_interp_type_str = os.getenv("SOM_TIER", None)

_TC = 1
_BC = 2
_HYBRID = 3
_UNKNOWN = 4


def _get_tier_type():
    if _interp_type_str == "1":
        return _TC
    if _interp_type_str == "2":
        return _BC
    if _interp_type_str == "3":
        return _HYBRID
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


class _TierManager(object):

    _CURRENT_TIER = 1

    def set_tier(self, tier):
        self._CURRENT_TIER = tier


    def tier_gt(self, val):
        return self._CURRENT_TIER > val


tier_manager = _TierManager()
