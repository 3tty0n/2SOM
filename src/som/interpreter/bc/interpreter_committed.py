"""Re-export shim: interpret_committed (committed tier-3) is produced by the unified
make_interp factory in interpreter_tier3.py (the P_COMMITTED sibling)."""
from som.interpreter.bc.interpreter_tier3 import (  # noqa: F401
    interpret_committed,
    committed_jitdriver,
    get_printable_location_committed,
)
