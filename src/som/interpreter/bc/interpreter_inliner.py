"""Re-export shim: interpret_inliner (warm/tier-2) is produced by the unified
make_interp factory in interpreter_tier3.py (the P_WARM sibling)."""
from som.interpreter.bc.interpreter_tier3 import (  # noqa: F401
    interpret_inliner,
    inliner_jitdriver,
    inliner_configure,
    get_printable_location_inliner,
)
