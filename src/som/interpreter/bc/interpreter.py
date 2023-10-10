from som.interpreter.ast.frame import (
    read_frame,
    write_frame,
    write_inner,
    read_inner,
    FRAME_AND_INNER_RCVR_IDX,
    get_inner_as_context,
    create_frame_1,
    create_frame_2,
    mark_as_no_longer_on_stack,
)
from som.interpreter.bc.frame import create_frame_3, create_frame
from som.interpreter.bc.bytecodes import bytecode_length, Bytecodes, bytecode_as_str
from som.interpreter.bc.frame import (
    get_block_at,
    get_self_dynamically,
)
from som.interpreter.bc.tier_shifting import ContinueInTier1, ContinueInTier2
from som.interpreter.bc.traverse_stack import t_empty, t_dump, t_push
from som.interpreter.control_flow import ReturnException
from som.interpreter.send import lookup_and_send_2, lookup_and_send_3, lookup_and_send_2_tier2, lookup_and_send_3_tier2
from som.tier_type import is_hybrid, is_tier1, is_tier2, tier_manager
from som.vm.globals import nilObject, trueObject, falseObject
from som.vmobjects.array import Array
from som.vmobjects.block_bc import BcBlock
from som.vmobjects.double import Double
from som.vmobjects.integer import Integer, int_0, int_1

from rlib import jit
from rlib.objectmodel import r_dict, compute_hash, we_are_translated, always_inline
from rlib.jit import (
    promote,
    elidable_promote,
    we_are_jitted,
    dont_look_inside
)


@jit.unroll_safe
def interpret(method, frame, max_stack_size, dummy=False):
    """
    Each interpreter represents copmilation tier.
    e.g,
      - interpret_tier1: threaded code
      - interpret_tier2: tracing JIT
    In the whle loop we can define the rule to shift the compilation timer.
    Movement from interpreter to interpreter is implemented using exceptions.
    """
    from som.interpreter.bc.interpreter_tier1 import interpret_tier1
    from som.interpreter.bc.interpreter_tier1_tracing import interpret_tier1_tj
    from som.interpreter.bc.interpreter_tier2 import interpret_tier2

    if dummy:
        return

    if is_tier1():
        w_result = interpret_tier1(method, frame, max_stack_size)
        return w_result
    elif is_tier2():
        result = interpret_tier2(method, frame, max_stack_size)
        return result
    elif is_hybrid():
        current_bc_idx = 0
        while True:
            try:
                w_result = interpret_tier1_tj(
                    method, frame, max_stack_size, current_bc_idx
                )
                return w_result
            except ContinueInTier2 as e:
                assert e.method is not None
                method = e.method
                frame = e.frame
                stack = e.stack
                current_bc_idx = e.bytecode_index

            w_result = interpret_tier2(
                method,
                frame,
                max_stack_size,
                current_bc_idx,
                stack.items,
                stack.stack_ptr,
            )
            return w_result
    else:
        assert False, "unreached tier"


def jitpolicy(_driver):
    from rpython.jit.codewriter.policy import JitPolicy  # pylint: disable=import-error

    return JitPolicy()
