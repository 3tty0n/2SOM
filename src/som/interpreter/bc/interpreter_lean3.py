"""Re-export shim: the committed tier-3 interpreter (interpret_lean3) is now
derived from the shared factory in interpreter_lean.py (the COMMITTED sibling).

It used to be a standalone ~700-line clone of interpret_tier3; that body now lives
once in interpreter_lean.make_lean_interp(residualize=False, osr_exit=False). This
module keeps the historical import path working.
"""
from som.interpreter.bc.interpreter_lean import (  # noqa: F401
    interpret_lean3,
    lean3_jitdriver,
    get_printable_location_lean3,
)
