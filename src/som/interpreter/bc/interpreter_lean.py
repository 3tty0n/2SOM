"""Re-export shim: the warm and committed lean interpreters are now produced,
together with the hybrid interpret_tier3, by the unified make_interp factory in
interpreter_tier3.py. This module keeps the historical import path working."""
from som.interpreter.bc.interpreter_tier3 import (  # noqa: F401
    interpret_inliner,
    inliner_jitdriver,
    inliner_configure,
    get_printable_location_inliner,
    interpret_committed,
    committed_jitdriver,
    get_printable_location_committed,
)
