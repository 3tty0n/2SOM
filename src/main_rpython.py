#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys

from rpython.rlib.rsre import rsre_re as re

from som.compiler.parse_error import ParseError
from som.interp_type import is_ast_interpreter, is_bytecode_interpreter
from som.tier_type import is_hybrid, is_tier1, is_tier2, tier_manager
from som.vm.universe import main, Exit

try:
    import rpython.rlib  # pylint: disable=unused-import
    from rpython.rlib import jit
except ImportError:
    "NOT_RPYTHON"
    print("Failed to load RPython library. Please make sure it is on PYTHONPATH")
    sys.exit(1)

# __________  Entry points  __________

def entry_point(argv):
    i = 0
    while True:
        if not i < len(argv):
            break

        if argv[i] == "--jit":
            if len(argv) == i + 1:
                print("missing argument after --jit")
                return 2
            jitarg = argv[i + 1]
            jit.set_user_param(None, jitarg)
            del argv[i : i + 2]
            continue
        elif argv[i] == '--hybrid_threshold':
            jitvalue = argv[i + 1]
            tier_manager.set_threshold(int(jitvalue))
            del argv[i : i + 2]
            continue
        i += 1

    try:
        main(argv)
    except Exit as ex:
        return ex.code
    except ParseError as ex:
        os.write(2, str(ex))
        return 1
    except Exception as ex:  # pylint: disable=broad-except
        os.write(2, "ERROR: %s thrown during execution.\n" % ex)
        return 1
    return 1

# _____ Define and setup target ___


def target(driver, _args):
    exe_name = "som-"
    if is_ast_interpreter():
        exe_name += "ast-"
    elif is_bytecode_interpreter():
        exe_name += "bc-"

    if driver.config.translation.jit:
        exe_name += "jit-"
    else:
        exe_name += "interp-"

    if is_tier1():
        exe_name += "tier1"
    elif is_tier2():
        exe_name += "tier2"
    elif is_hybrid():
        exe_name += "hybrid"

    driver.exe_name = exe_name
    return entry_point, None


def jitpolicy(_driver):
    from rpython.jit.codewriter.policy import JitPolicy  # pylint: disable=import-error

    return JitPolicy()


if __name__ == "__main__":
    from rpython.translator.driver import TranslationDriver  # pylint: disable=E

    f, _ = target(TranslationDriver(), sys.argv)
    sys.exit(f(sys.argv))
