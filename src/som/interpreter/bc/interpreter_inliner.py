"""Re-export shim: the warm stack-inliner interpreter (interpret_inliner) is now
derived from the shared factory in interpreter_lean.py (the WARM sibling).

It used to be a standalone ~780-line clone of interpret_tier3; that body now lives
once in interpreter_lean.make_lean_interp(residualize=True, osr_exit=True). This
module keeps the historical import path working.
"""
from som.interpreter.bc.interpreter_lean import (  # noqa: F401
    interpret_inliner,
    inliner_jitdriver,
    inliner_configure,
    get_printable_location_inliner,
)
