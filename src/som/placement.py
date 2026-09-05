import os

from rlib import jit

_PLACE = os.getenv("SOM_SEND_PLACE", "interp")
assert _PLACE in ("aot", "interp", "jit"), "SOM_SEND_PLACE must be aot|interp|jit"


@jit.elidable
def send_place():
    return _PLACE


@jit.elidable
def send_place_is_aot():
    return _PLACE == "aot"


@jit.elidable
def send_place_is_jit():
    return _PLACE == "jit"
