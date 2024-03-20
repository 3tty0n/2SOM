#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys

from rpython.rlib.nonconst import NonConstant
from rpython.rlib.rsre import rsre_re as re
from rpython.memory.gc.base import GCBase
from rpython.memory.gc.hook import GcHooks

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


class GcHooksStats(object):
    minors = 0
    steps = 0
    collects = 0
    duration_major = 0.0
    duration_minor = 0.0

    def reset(self):
        # the NonConstant are needed so that the annotator annotates the
        # fields as a generic SomeInteger(), instead of a constant 0. A call
        # to this method MUST be seen during normal annotation, else the class
        # is annotated only during GC transform, when it's too late
        self.minors = NonConstant(0)
        self.steps = NonConstant(0)
        self.collects = NonConstant(0)
        self.duration_major = NonConstant(0.0)
        self.duration_minor = NonConstant(0.0)


class MyHooks(GcHooks):

    def __init__(self, stats=None):
        self.stats = stats or GcHooksStats()

    def is_gc_minor_enabled(self):
        return True

    def is_gc_collect_step_enabled(self):
        return True

    def is_gc_collect_enabled(self):
        return True

    def on_gc_minor(self, duration, total_memory_used, pinned_objects):
        self.stats.minors += 1
        self.stats.duration_minor += duration

    def on_gc_collect_step(self, duration, oldstate, newstate):
        self.stats.steps += 1
        self.stats.duration_major += duration

    def on_gc_collect(self, num_major_collects,
                      arenas_count_before, arenas_count_after,
                      arenas_bytes, rawmalloc_bytes_before,
                      rawmalloc_bytes_after, pinned_objects):
        self.stats.collects += 1


GC_HOOKS_STATS = GcHooksStats()


def get_gchooks():
    return MyHooks(GC_HOOKS_STATS)


# __________  Entry points  __________


def report_gc_stats():
    minors = GC_HOOKS_STATS.minors
    steps = GC_HOOKS_STATS.steps
    collects = GC_HOOKS_STATS.collects
    duration_minor = GC_HOOKS_STATS.duration_minor
    duration_major = GC_HOOKS_STATS.duration_major
    print('GC hooks statistics')
    print('    gc-minor:          %d' % minors)
    print('    gc-collect-step:   %d' % steps)
    print('    gc-collect:        %d' % collects)
    print('    gc-duration-minor: %f us' % (duration_minor * 100000))
    print('    gc-duration-major: %f us' % (duration_major * 100000))


def entry_point(argv):
    is_gc_stats = False

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
        elif argv[i] == '--gc-stats':
            is_gc_stats = True
            del argv[i]
        i += 1

    try:
        GC_HOOKS_STATS.reset()
        main(argv)
    except Exit as ex:
        return ex.code
    except ParseError as ex:
        os.write(2, str(ex))
        return 1
    except Exception as ex:  # pylint: disable=broad-except
        os.write(2, "ERROR: %s thrown during execution.\n" % ex)
        return 1
    finally:
        if is_gc_stats:
            report_gc_stats()

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

    exe_name += "-no-ic-no-handler-opt"
    driver.exe_name = exe_name
    return entry_point, None


def jitpolicy(_driver):
    from rpython.jit.codewriter.policy import JitPolicy  # pylint: disable=import-error

    return JitPolicy()


if __name__ == "__main__":
    from rpython.translator.driver import TranslationDriver  # pylint: disable=E

    f, _ = target(TranslationDriver(), sys.argv)
    sys.exit(f(sys.argv))
