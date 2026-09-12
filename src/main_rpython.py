#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys

from som.compiler.parse_error import ParseError
from som.interp_type import is_ast_interpreter, is_bytecode_interpreter
from som.placement import send_place
from som.vm.universe import main, Exit

try:
    import rpython.rlib  # pylint: disable=unused-import
except ImportError:
    "NOT_RPYTHON"
    print("Failed to load RPython library. Please make sure it is on PYTHONPATH")
    sys.exit(1)

# __________  Entry points  __________


def entry_point(argv):
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


def _referenced_globals(clazz, universe, out):
    """NOT_RPYTHON: collect the names a class may resolve as a global."""
    from som.vmobjects.method_bc import BcAbstractMethod
    from som.vmobjects.symbol import Symbol

    def walk(method):
        if not isinstance(method, BcAbstractMethod):
            return
        for lit in method.get_literals():
            if lit.is_invokable():
                walk(lit)
            elif isinstance(lit, Symbol):
                name = lit.get_embedded_string()
                if name and name[0].isupper() and name.isalnum():
                    out.append(name)

    for holder in (clazz, clazz.get_class(universe)):
        for inv in holder.get_instance_invokables_for_disassembler():
            walk(inv)


def _prebuild():
    """NOT_RPYTHON: load and bind the guest classes at translation time."""
    classpath = os.getenv("SOM_PREBUILT_CP", "")
    if not classpath:
        return ""
    from som.vm.current import current_universe
    from som.vm.symbols import symbol_for

    current_universe.setup_classpath(classpath)
    current_universe._initialize_object_system()  # pylint: disable=protected-access

    pending = []
    for name in os.getenv("SOM_PREBUILT_CLASSES", "").split(","):
        name = name.strip()
        if name:
            if current_universe.load_class(symbol_for(name)) is None:
                raise Exception("SOM_PREBUILT_CLASSES: cannot load " + name)
            pending.append(name)

    tried = set(pending)
    while pending:
        clazz = current_universe.get_global(symbol_for(pending.pop()))
        if clazz is None:
            continue
        refs = []
        _referenced_globals(clazz, current_universe, refs)
        for name in refs:
            if name in tried:
                continue
            tried.add(name)
            if current_universe.load_class(symbol_for(name)) is not None:
                pending.append(name)

    current_universe.prebuilt_classes = len(current_universe.static_sends.classes) // 2

    from som.vm import boundary, pgo, send_inline

    send_inline._state.checked = False
    boundary._state.loaded = False
    pgo._state.loaded = False
    return "-prebuilt"


def target(driver, _args):
    exe_name = "som-"
    if is_ast_interpreter():
        exe_name += "ast-"
    elif is_bytecode_interpreter():
        exe_name += "bc-"

    if driver.config.translation.jit:
        exe_name += "jit"
    else:
        exe_name += "interp"

    if send_place() != "interp":
        exe_name += "-send-" + send_place()

    exe_name += _prebuild()

    driver.exe_name = exe_name
    return entry_point, None


def jitpolicy(_driver):
    from rpython.jit.codewriter.policy import JitPolicy  # pylint: disable=import-error

    return JitPolicy()


if __name__ == "__main__":
    from rpython.translator.driver import TranslationDriver  # pylint: disable=E

    f, _ = target(TranslationDriver(), sys.argv)
    sys.exit(f(sys.argv))
