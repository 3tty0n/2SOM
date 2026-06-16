'''Send-dispatch support for the bytecode interpreter: polymorphic inline-cache
lookup (`_lookup`), megamorphic PIC dispatch (`_residual_mega_send_*`), super
sends, doesNotUnderstand, and the slow-path invoker. Shared by all interpreter
siblings produced by `interpreter_tier3.make_interp`.
'''
from som.interpreter.bc.bytecodes import Bytecodes, bytecode_as_str
from som.interpreter.ast.frame import read_frame, FRAME_AND_INNER_RCVR_IDX
from som.interpreter.bc.frame import get_block_at
from som.interpreter.send import lookup_and_send_3_tier3
from som.vmobjects.array import Array
from rlib import jit
from rlib.jit import we_are_jitted, elidable_promote


def _invoke_invokable_slow_path_tier3(invokable, num_args, receiver, stack, stack_ptr):
    if num_args == 1:
        stack[stack_ptr] = invokable.invoke_1(receiver)

    elif num_args == 2:
        arg = stack[stack_ptr]
        if we_are_jitted():
            stack[stack_ptr] = None
        stack_ptr -= 1
        stack[stack_ptr] = invokable.invoke_2(receiver, arg)

    elif num_args == 3:
        arg2 = stack[stack_ptr]
        arg1 = stack[stack_ptr - 1]

        if we_are_jitted():
            stack[stack_ptr] = None
            stack[stack_ptr - 1] = None

        stack_ptr -= 2

        stack[stack_ptr] = invokable.invoke_3(receiver, arg1, arg2)

    else:
        stack_ptr = invokable.invoke_n(stack, stack_ptr)
    return stack_ptr


def _do_super_send_tier3(bytecode_index, method, stack, stack_ptr):
    signature = method.get_constant(bytecode_index)

    receiver_class = method.get_holder().get_super_class()
    invokable = receiver_class.lookup_invokable(signature)

    num_args = invokable.get_number_of_signature_arguments()
    receiver = stack[stack_ptr - (num_args - 1)]

    if invokable:
        method.set_inline_cache(
            bytecode_index, receiver_class.get_layout_for_instances(), invokable
        )
        if num_args == 1:
            bc = Bytecodes.q_super_send_1
        elif num_args == 2:
            bc = Bytecodes.q_super_send_2
        elif num_args == 3:
            bc = Bytecodes.q_super_send_3
        elif num_args == 4:
            bc = Bytecodes.q_super_send_4
        else:
            bc = Bytecodes.q_super_send_n
        method.set_bytecode(bytecode_index, bc)
        stack_ptr = _invoke_invokable_slow_path_tier3(
            invokable, num_args, receiver, stack, stack_ptr
        )
    else:
        stack_ptr = _send_does_not_understand_tier3(
            receiver, invokable.get_signature(), stack, stack_ptr
        )
    return stack_ptr


def _not_yet_implemented():
    raise Exception("Not yet implemented")


def _unknown_bytecode(bytecode, bytecode_idx, method):
    from som.compiler.bc.disassembler import dump_method

    dump_method(method, "")
    raise Exception(
        "Unknown bytecode: "
        + str(bytecode)
        + " "
        + bytecode_as_str(bytecode)
        + " at bci: "
        + str(bytecode_idx)
    )


def get_self(frame, ctx_level):
    # Get the self object from the interpreter
    if ctx_level == 0:
        return read_frame(frame, FRAME_AND_INNER_RCVR_IDX)
    return get_block_at(frame, ctx_level).get_from_outer(FRAME_AND_INNER_RCVR_IDX)


@elidable_promote("all")
def _lookup(layout, selector, method, bytecode_index):
    # First try of inline cache
    cached_layout1 = method.get_inline_cache_layout(bytecode_index)
    if cached_layout1 is layout:
        invokable = method.get_inline_cache_invokable(bytecode_index)
    elif cached_layout1 is None:
        invokable = layout.lookup_invokable(selector)
        method.set_inline_cache(bytecode_index, layout, invokable)
    else:
        # second try
        # the bytecode index after the send is used by the selector constant,
        # and can be used safely as another cache item
        cached_layout2 = method.get_inline_cache_layout(bytecode_index + 1)
        if cached_layout2 == layout:
            invokable = method.get_inline_cache_invokable(bytecode_index + 1)
        else:
            invokable = layout.lookup_invokable(selector)
            if cached_layout2 is None:
                method.set_inline_cache(bytecode_index + 1, layout, invokable)
    return invokable


def _update_object_and_invalidate_old_caches(obj, method, bytecode_index, universe):
    obj.update_layout_to_match_class()
    obj.get_object_layout(universe)

    cached_layout1 = method.get_inline_cache_layout(bytecode_index)
    if cached_layout1 is not None and not cached_layout1.is_latest:
        method.set_inline_cache(bytecode_index, None, None)

    cached_layout2 = method.get_inline_cache_layout(bytecode_index + 1)
    if cached_layout2 is not None and not cached_layout2.is_latest:
        method.set_inline_cache(bytecode_index + 1, None, None)


def _send_does_not_understand_tier3(receiver, selector, stack, stack_ptr):
    # ignore self
    number_of_arguments = selector.get_number_of_signature_arguments() - 1
    arguments_array = Array.from_size(number_of_arguments)

    # Remove all arguments and put them in the freshly allocated array
    i = number_of_arguments - 1
    while i >= 0:
        value = stack[stack_ptr]
        if we_are_jitted():
            stack[stack_ptr] = None
        stack_ptr -= 1

        arguments_array.set_indexable_field(i, value)
        i -= 1

    stack[stack_ptr] = lookup_and_send_3_tier3(
        receiver, selector, arguments_array, "doesNotUnderstand:arguments:"
    )

    return stack_ptr


@jit.dont_look_inside
def _residual_mega_send_1(method, bytecode_index, signature, receiver, universe):
    # Opaque megamorphic dispatch for a unary send, behind the barrier so the trace
    # never specialises on the receiver class. Same layout-invalidation / dNU semantics
    # as the send_1 handler, but resolved through the per-site PIC instead of _lookup
    # (whose 2 slots always miss at a committed mega site).
    layout = receiver.get_object_layout(universe)
    invokable = method.mega_cache_lookup(bytecode_index, layout)
    if invokable is not None:
        return invokable.invoke_1_tier3(receiver, False)
    invokable = layout.lookup_invokable(signature)
    if invokable is not None:
        method.mega_cache_store(bytecode_index, layout, invokable)
        return invokable.invoke_1_tier3(receiver, False)
    if not layout.is_latest:
        _update_object_and_invalidate_old_caches(
            receiver, method, bytecode_index, universe
        )
        layout = receiver.get_object_layout(universe)
        invokable = layout.lookup_invokable(signature)
        if invokable is not None:
            method.mega_cache_store(bytecode_index, layout, invokable)
            return invokable.invoke_1_tier3(receiver, False)
    # doesNotUnderstand: (a unary send has no real arguments)
    arguments_array = Array.from_size(0)
    return lookup_and_send_3_tier3(
        receiver, signature, arguments_array, "doesNotUnderstand:arguments:"
    )


@jit.dont_look_inside
def _residual_mega_send_2(method, bytecode_index, signature, receiver, arg, universe):
    # Opaque megamorphic dispatch for a binary send (see _residual_mega_send_1). The
    # arg has already been popped off the operand stack by the caller.
    layout = receiver.get_object_layout(universe)
    invokable = method.mega_cache_lookup(bytecode_index, layout)
    if invokable is not None:
        return invokable.invoke_2_tier3(receiver, arg, False)
    invokable = layout.lookup_invokable(signature)
    if invokable is not None:
        method.mega_cache_store(bytecode_index, layout, invokable)
        return invokable.invoke_2_tier3(receiver, arg, False)
    if not layout.is_latest:
        _update_object_and_invalidate_old_caches(
            receiver, method, bytecode_index, universe
        )
        layout = receiver.get_object_layout(universe)
        invokable = layout.lookup_invokable(signature)
        if invokable is not None:
            method.mega_cache_store(bytecode_index, layout, invokable)
            return invokable.invoke_2_tier3(receiver, arg, False)
    # doesNotUnderstand: (a send_2 selector always has exactly one real argument)
    arguments_array = Array.from_size(1)
    arguments_array.set_indexable_field(0, arg)
    return lookup_and_send_3_tier3(
        receiver, signature, arguments_array, "doesNotUnderstand:arguments:"
    )
