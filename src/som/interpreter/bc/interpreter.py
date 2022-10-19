from som.interpreter.ast.frame import (
    read_frame,
    write_frame,
    write_inner,
    read_inner,
    FRAME_AND_INNER_RCVR_IDX,
    get_inner_as_context,
)
from som.interpreter.bc.bytecodes import bytecode_length, Bytecodes, bytecode_as_str
from som.interpreter.bc.frame import (
    get_block_at,
    get_self_dynamically,
)
from som.interpreter.bc.traverse_stack import t_empty, t_dump, t_push
from som.interpreter.control_flow import ReturnException
from som.interpreter.send import lookup_and_send_2, lookup_and_send_3
from som.vm.globals import nilObject, trueObject, falseObject
from som.vmobjects.array import Array
from som.vmobjects.block_bc import BcBlock
from som.vmobjects.double import Double
from som.vmobjects.integer import Integer, int_0, int_1

from rlib import jit
from rlib.jit import (
    promote,
    elidable_promote,
    we_are_jitted,
    enable_shallow_tracing,
    enable_shallow_tracing_argn,
)

from som.vmobjects.method import AbstractMethod
# from som.vmobjects.method_bc import BcMethod


class Stack(object):
    def __init__(self, max_stack_size):
        self.stack = [None] * max_stack_size
        self.max_stack_size = max_stack_size
        self.stack_ptr = -1

    @enable_shallow_tracing
    def push(self, w_x):
        self.stack_ptr += 1
        assert self.stack_ptr < len(self.stack)
        self.stack[self.stack_ptr] = w_x

    @jit.dont_look_inside
    def pop(self):
        w_x = self.stack[self.stack_ptr]
        if we_are_jitted():
            self.stack[self.stack_ptr] = None
        self.stack_ptr -= 1
        return w_x

    @jit.dont_look_inside
    def top(self):
        return self.stack[self.stack_ptr]

    @jit.dont_look_inside
    def take(self, n):
        assert self.stack_ptr - n >= 0
        return self.stack[self.stack_ptr - n]

    @jit.not_in_trace
    def dump(self):
        print(self.stack, self.stack_ptr)


class StackPtr(object):
    def __init__(self, value=-1):
        self.value = value


@enable_shallow_tracing
def _do_super_send(stack, stack_ptr, bytecode_index, method):
    signature = method.get_constant(bytecode_index)

    receiver_class = method.get_holder().get_super_class()
    invokable = receiver_class.lookup_invokable(signature)

    stack_ptr_value = stack_ptr.value
    num_args = invokable.get_number_of_signature_arguments()
    receiver = stack[stack_ptr_value - (num_args - 1)]

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
        else:
            bc = Bytecodes.q_super_send_n
        method.set_bytecode(bytecode_index, bc)
        stack_ptr_value = _invoke_invokable_slow_path(
            invokable, num_args, receiver, stack, stack_ptr_value
        )
    else:
        stack_ptr_value = _send_does_not_understand(
            receiver, invokable.get_signature(), stack, stack_ptr_value
        )

    stack_ptr.value = stack_ptr_value


@enable_shallow_tracing_argn(0)
def _do_return_non_local(result, frame, ctx_level):
    # Compute the context for the non-local return
    block = get_block_at(frame, ctx_level)

    # Make sure the block context is still on the stack
    if not block.is_outer_on_stack():
        # Try to recover by sending 'escapedBlock:' to the self object.
        # That is the most outer self object, not the blockSelf.
        self_block = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)
        outer_self = get_self_dynamically(frame)
        return lookup_and_send_2(outer_self, self_block, "escapedBlock:")

    raise ReturnException(result, block.get_on_stack_marker())


def _invoke_invokable_slow_path(invokable, num_args, receiver, stack, stack_ptr):
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


@jit.dont_look_inside
def _halt(stack, stack_ptr):
    return stack[stack_ptr.value]


@enable_shallow_tracing
def _dup(stack, stack_ptr):
    val = stack[stack_ptr.value]
    stack_ptr.value += 1
    stack[stack_ptr.value] = val


@enable_shallow_tracing
def _dup_second(stack, stack_ptr):
    val = stack[stack_ptr.value - 1]
    stack_ptr.value += 1
    stack[stack_ptr.value] = val


@enable_shallow_tracing
def _push_frame(stack, stack_ptr, method, current_bc_idx, frame):
    stack_ptr.value += 1
    stack[stack_ptr.value] = read_frame(frame, method.get_bytecode(current_bc_idx + 1))


@enable_shallow_tracing
def _push_frame_0(stack, stack_ptr, frame):
    stack_ptr.value += 1
    stack[stack_ptr.value] = read_frame(frame, FRAME_AND_INNER_RCVR_IDX + 0)


@enable_shallow_tracing
def _push_frame_1(stack, stack_ptr, frame):
    stack_ptr.value += 1
    stack[stack_ptr.value] = read_frame(frame, FRAME_AND_INNER_RCVR_IDX + 1)


@enable_shallow_tracing
def _push_frame_2(stack, stack_ptr, frame):
    stack_ptr.value += 1
    stack[stack_ptr.value] = read_frame(frame, FRAME_AND_INNER_RCVR_IDX + 2)


@enable_shallow_tracing
def _push_inner(stack, stack_ptr, method, current_bc_idx, frame):
    idx = method.get_bytecode(current_bc_idx + 1)
    ctx_level = method.get_bytecode(current_bc_idx + 2)

    stack_ptr.value += 1
    if ctx_level == 0:
        stack[stack_ptr.value] = read_inner(frame, idx)
    else:
        block = get_block_at(frame, ctx_level)
        stack[stack_ptr.value] = block.get_from_outer(idx)


@enable_shallow_tracing
def _push_inner_0(stack, stack_ptr, frame):
    stack_ptr.value += 1
    stack[stack_ptr.value] = read_inner(frame, FRAME_AND_INNER_RCVR_IDX + 0)


@enable_shallow_tracing
def _push_inner_1(stack, stack_ptr, frame):
    stack_ptr.value += 1
    stack[stack_ptr.value] = read_inner(frame, FRAME_AND_INNER_RCVR_IDX + 1)


@enable_shallow_tracing
def _push_inner_2(stack, stack_ptr, frame):
    stack_ptr.value += 1
    stack[stack_ptr.value] = read_inner(frame, FRAME_AND_INNER_RCVR_IDX + 2)


@enable_shallow_tracing
def _push_field(stack, stack_ptr, method, current_bc_idx, frame):
    field_idx = method.get_bytecode(current_bc_idx + 1)
    ctx_level = method.get_bytecode(current_bc_idx + 2)
    self_obj = get_self(frame, ctx_level)
    stack_ptr.value += 1
    stack[stack_ptr.value] = self_obj.get_field(field_idx)


@enable_shallow_tracing
def _push_field_0(stack, stack_ptr, frame):
    self_obj = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)
    stack_ptr.value += 1
    stack[stack_ptr.value] = self_obj.get_field(0)


@enable_shallow_tracing
def _push_field_1(stack, stack_ptr, frame):
    self_obj = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)
    stack_ptr.value += 1
    stack[stack_ptr.value] = self_obj.get_field(1)


@enable_shallow_tracing
def _push_block(stack, stack_ptr, method, current_bc_idx, frame):
    block_method = method.get_constant(current_bc_idx)
    stack_ptr.value += 1
    stack[stack_ptr.value] = BcBlock(block_method, get_inner_as_context(frame))


@enable_shallow_tracing
def _push_block_no_ctx(stack, stack_ptr, method, current_bc_idx):
    block_method = method.get_constant(current_bc_idx)
    stack_ptr.value += 1
    stack[stack_ptr.value] = BcBlock(block_method, None)


@enable_shallow_tracing
def _push_constant(stack, stack_ptr, method, current_bc_idx):
    stack_ptr.value += 1
    stack[stack_ptr.value] = method.get_constant(current_bc_idx)


@enable_shallow_tracing
def _push_constant_0(stack, stack_ptr, method):
    stack_ptr.value += 1
    stack[stack_ptr.value] = method._literals[0] # pylint: disable=protected-access


@enable_shallow_tracing
def _push_constant_1(stack, stack_ptr, method):
    stack_ptr.value += 1
    stack[stack_ptr.value] = method._literals[1] # pylint: disable=protected-access


@enable_shallow_tracing
def _push_constant_2(stack, stack_ptr, method):
    stack_ptr.value += 1
    stack[stack_ptr.value] = method._literals[2] # pylint: disable=protected-access


@enable_shallow_tracing
def _push_0(stack, stack_ptr):
    stack_ptr.value += 1
    stack[stack_ptr.value] = int_0


@enable_shallow_tracing
def _push_1(stack, stack_ptr):
    stack_ptr.value += 1
    stack[stack_ptr.value] = int_1


@enable_shallow_tracing  # transformation can be done almost automatically!
def _push_nil(stack, stack_ptr):
    stack_ptr.value += 1
    stack[stack_ptr.value] = nilObject


@enable_shallow_tracing
def _push_global(stack, stack_ptr, method, current_universe, current_bc_idx, frame):
    global_name = method.get_constant(current_bc_idx)
    glob = current_universe.get_global(global_name)

    stack_ptr.value += 1
    if glob:
        stack[stack_ptr.value] = glob
    else:
        stack[stack_ptr.value] = (
            lookup_and_send_2(
                get_self_dynamically(frame), global_name, "unknownGlobal:"
            )
        )


@enable_shallow_tracing
def _pop(stack, stack_ptr):
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1


@enable_shallow_tracing
def _pop_frame(stack, stack_ptr, method, current_bc_idx, frame):
    value = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1
    write_frame(frame, method.get_bytecode(current_bc_idx + 1), value)


@enable_shallow_tracing
def _pop_frame_0(stack, stack_ptr, frame):
    value = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1
    write_frame(frame, FRAME_AND_INNER_RCVR_IDX + 0, value)


@enable_shallow_tracing
def _pop_frame_1(stack, stack_ptr, frame):
    value = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1
    write_frame(frame, FRAME_AND_INNER_RCVR_IDX + 1, value)


@enable_shallow_tracing
def _pop_frame_2(stack, stack_ptr, frame):
    value = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1
    write_frame(frame, FRAME_AND_INNER_RCVR_IDX + 2, value)


@enable_shallow_tracing
def _pop_inner(stack, stack_ptr, method, current_bc_idx, frame):
    idx = method.get_bytecode(current_bc_idx + 1)
    ctx_level = method.get_bytecode(current_bc_idx + 2)
    value = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1

    if ctx_level == 0:
        write_inner(frame, idx, value)
    else:
        block = get_block_at(frame, ctx_level)
        block.set_outer(idx, value)


@enable_shallow_tracing
def _pop_inner_0(stack, stack_ptr, frame):
    value = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1
    write_inner(frame, FRAME_AND_INNER_RCVR_IDX + 0, value)


@enable_shallow_tracing
def _pop_inner_1(stack, stack_ptr, frame):
    value = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1
    write_inner(frame, FRAME_AND_INNER_RCVR_IDX + 1, value)


@enable_shallow_tracing
def _pop_inner_2(stack, stack_ptr, frame):
    value = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1
    write_inner(frame, FRAME_AND_INNER_RCVR_IDX + 2, value)


@enable_shallow_tracing
def _nil_frame(method, frame, current_bc_idx):
    if we_are_jitted():
        idx = method.get_bytecode(current_bc_idx + 1)
        write_frame(frame, idx, nilObject)


@enable_shallow_tracing
def _nil_inner(method, frame, current_bc_idx):
    if we_are_jitted():
        idx = method.get_bytecode(current_bc_idx + 1)
        write_inner(frame, idx, nilObject)


@enable_shallow_tracing
def _pop_field(stack, stack_ptr, method, current_bc_idx, frame):
    field_idx = method.get_bytecode(current_bc_idx + 1)
    ctx_level = method.get_bytecode(current_bc_idx + 2)
    self_obj = get_self(frame, ctx_level)

    value = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1
    self_obj.set_field(field_idx, value)


@enable_shallow_tracing
def _pop_field_0(stack, stack_ptr, frame):
    self_obj = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)

    value = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1
    self_obj.set_field(0, value)


@enable_shallow_tracing
def _pop_field_1(stack, stack_ptr, frame):
    self_obj = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)

    value = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1
    self_obj.set_field(1, value)


@enable_shallow_tracing_argn(3)
def _send_1(method, current_universe, current_bc_idx, next_bc_idx, stack, stack_ptr):
    signature = method.get_constant(current_bc_idx)
    # receiver = stack.top()
    receiver = stack[stack_ptr.value]

    layout = receiver.get_object_layout(current_universe)
    invokable = _lookup(layout, signature, method, current_bc_idx)
    if invokable is not None:
        stack[stack_ptr.value] = invokable.invoke_1(receiver)
    elif not layout.is_latest:
        _update_object_and_invalidate_old_caches(
            receiver, method, current_bc_idx, current_universe
        )
        next_bc_idx = current_bc_idx
    else:
        stack_ptr.value = _send_does_not_understand(
            receiver, signature, stack, stack_ptr.value
        )

    return next_bc_idx


@enable_shallow_tracing_argn(3)
def _send_2(method, current_universe, current_bc_idx, next_bc_idx, stack, stack_ptr):
    signature = method.get_constant(current_bc_idx)
    # receiver = stack.take(1)
    receiver = stack[stack_ptr.value - 1]

    layout = receiver.get_object_layout(current_universe)
    invokable = _lookup(layout, signature, method, current_bc_idx)
    if invokable is not None:
        arg = stack[stack_ptr.value]
        if we_are_jitted():
            stack[stack_ptr.value] = None
        stack_ptr.value -= 1
        stack[stack_ptr.value] = invokable.invoke_2(receiver, arg)
    elif not layout.is_latest:
        _update_object_and_invalidate_old_caches(
            receiver, method, current_bc_idx, current_universe
        )
        next_bc_idx = current_bc_idx
    else:
        stack_ptr.value = _send_does_not_understand(
            receiver, signature, stack, stack_ptr.value
        )

    return next_bc_idx


@enable_shallow_tracing_argn(3)
def _send_3(method, current_universe, current_bc_idx, next_bc_idx, stack, stack_ptr):
    signature = method.get_constant(current_bc_idx)
    # receiver = stack.take(2)
    receiver = stack[stack_ptr.value - 2]

    layout = receiver.get_object_layout(current_universe)
    invokable = _lookup(layout, signature, method, current_bc_idx)
    if invokable is not None:
        arg2 = stack[stack_ptr.value]
        arg1 = stack[stack_ptr.value - 1]

        if we_are_jitted():
            stack[stack_ptr.value] = None
            stack[stack_ptr.value - 1] = None

        stack_ptr.value -= 2
        stack[stack_ptr.value] = invokable.invoke_3(receiver, arg1, arg2)
    elif not layout.is_latest:
        _update_object_and_invalidate_old_caches(
            receiver, method, current_bc_idx, current_universe
        )
        next_bc_idx = current_bc_idx
    else:
        stack_ptr.value = _send_does_not_understand(
            receiver, signature, stack, stack_ptr.value
        )

    return next_bc_idx


@enable_shallow_tracing_argn(3)
def _send_n(method, current_universe, current_bc_idx, next_bc_idx, stack, stack_ptr):
    signature = method.get_constant(current_bc_idx)
    receiver = stack[
        stack_ptr.value - (signature.get_number_of_signature_arguments() - 1)
    ]

    layout = receiver.get_object_layout(current_universe)
    invokable = _lookup(layout, signature, method, current_bc_idx)
    if invokable is not None:
        stack_ptr.value = invokable.invoke_n(stack, stack_ptr.value)
    elif not layout.is_latest:
        _update_object_and_invalidate_old_caches(
            receiver, method, current_bc_idx, current_universe
        )
        next_bc_idx = current_bc_idx
    else:
        stack_ptr.value = _send_does_not_understand(
            receiver, signature, stack, stack_ptr.value
        )

    return next_bc_idx


@enable_shallow_tracing
def _inc(stack, stack_ptr):
    val = stack[stack_ptr.value]

    from som.vmobjects.integer import Integer
    from som.vmobjects.double import Double
    from som.vmobjects.biginteger import BigInteger

    if isinstance(val, Integer):
        result = val.prim_inc()
    elif isinstance(val, Double):
        result = val.prim_inc()
    elif isinstance(val, BigInteger):
        result = val.prim_inc()
    else:
        return _not_yet_implemented()

    stack[stack_ptr.value] = result


@enable_shallow_tracing
def _dec(stack, stack_ptr):
    val = stack[stack_ptr.value]
    from som.vmobjects.integer import Integer
    from som.vmobjects.double import Double
    from som.vmobjects.biginteger import BigInteger

    if isinstance(val, Integer):
        result = val.prim_dec()
    elif isinstance(val, Double):
        result = val.prim_dec()
    elif isinstance(val, BigInteger):
        result = val.prim_dec()
    else:
        return _not_yet_implemented()
    stack[stack_ptr.value] = result


@enable_shallow_tracing
def _inc_field(method, frame, current_bc_idx):
    field_idx = method.get_bytecode(current_bc_idx + 1)
    ctx_level = method.get_bytecode(current_bc_idx + 2)
    self_obj = get_self(frame, ctx_level)

    self_obj.inc_field(field_idx)


@enable_shallow_tracing
def _inc_field_push(stack, stack_ptr, method, frame, current_bc_idx):
    field_idx = method.get_bytecode(current_bc_idx + 1)
    ctx_level = method.get_bytecode(current_bc_idx + 2)
    self_obj = get_self(frame, ctx_level)

    stack_ptr.value += 1
    stack[stack_ptr.value] = self_obj.inc_field(field_idx)


@enable_shallow_tracing
def _q_super_send_1(stack, stack_ptr, method, current_bc_idx):
    invokable = method.get_inline_cache_invokable(current_bc_idx)
    stack[stack_ptr.value] = invokable.invoke_1(stack[stack_ptr.value])


@enable_shallow_tracing
def _q_super_send_2(stack, stack_ptr, method, current_bc_idx):
    invokable = method.get_inline_cache_invokable(current_bc_idx)
    arg = stack[stack_ptr.value]
    if we_are_jitted():
        stack[stack_ptr.value] = None
    stack_ptr.value -= 1
    stack[stack_ptr.value] = invokable.invoke_2(stack[stack_ptr.value], arg)


@enable_shallow_tracing
def _q_super_send_3(stack, stack_ptr, method, current_bc_idx):
    invokable = method.get_inline_cache_invokable(current_bc_idx)
    arg2 = stack[stack_ptr.value]
    arg1 = stack[stack_ptr.value - 1]
    if we_are_jitted():
        stack[stack_ptr.value] = None
        stack[stack_ptr.value - 1] = None
    stack_ptr.value -= 2
    stack[stack_ptr.value] = invokable.invoke_3(stack[stack_ptr.value], arg1, arg2)


@enable_shallow_tracing
def _q_super_send_n(stack, stack_ptr, method, current_bc_idx):
    invokable = method.get_inline_cache_invokable(current_bc_idx)
    stack_ptr.value = invokable.invoke_n(stack, stack_ptr.value)


@enable_shallow_tracing_argn(2)
def _push_local(method, current_bc_idx, next_bc_idx):
    method.patch_variable_access(current_bc_idx)
    # retry bytecode after patching
    next_bc_idx = current_bc_idx
    return next_bc_idx


@enable_shallow_tracing_argn(2)
def _push_argument(method, current_bc_idx, next_bc_idx):
    method.patch_variable_access(current_bc_idx)
    # retry bytecode after patching
    next_bc_idx = current_bc_idx
    return next_bc_idx


@enable_shallow_tracing_argn(2)
def _pop_local(method, current_bc_idx, next_bc_idx):
    method.patch_variable_access(current_bc_idx)
    # retry bytecode after patching
    next_bc_idx = current_bc_idx
    return next_bc_idx


@enable_shallow_tracing_argn(2)
def _pop_argument(method, current_bc_idx, next_bc_idx):
    method.patch_variable_access(current_bc_idx)
    # retry bytecode after patching
    next_bc_idx = current_bc_idx
    return next_bc_idx


@jit.dont_look_inside
def _return_local(stack, stack_ptr, dummy=False):
    if dummy:
        return stack[stack_ptr.value]
    return stack[stack_ptr.value]


@jit.dont_look_inside
def _return_self(frame, dummy=False):
    if dummy:
        return nilObject
    return read_frame(frame, FRAME_AND_INNER_RCVR_IDX)


@jit.dont_look_inside
def _return_field_0(frame, dummy=False):
    if dummy:
        return nilObject
    self_obj = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)
    return self_obj.get_field(0)


@jit.dont_look_inside
def _return_field_1(frame, dummy=False):
    if dummy:
        return nilObject
    self_obj = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)
    return self_obj.get_field(1)


@jit.dont_look_inside
def _return_field_2(frame, dummy=False):
    if dummy:
        return nilObject
    self_obj = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)
    return self_obj.get_field(2)


@jit.dont_look_inside
def _is_true_object(stack, stack_ptr, dummy=False):
    if dummy:
        return True
    val = stack[stack_ptr.value]
    stack_ptr.value -= 1
    return val is trueObject


@jit.dont_look_inside
def _is_false_object(stack, stack_ptr, dummy=False):
    if dummy:
        return True
    val = stack[stack_ptr.value]
    stack_ptr.value -= 1
    return val is falseObject


@jit.dont_look_inside
def _is_greater_two(stack, stack_ptr, dummy=False):
    if dummy:
        return True
    top = stack[stack_ptr.value]
    if isinstance(top, Integer):
        top_val = top.get_embedded_integer()
    elif isinstance(top, Double):
        top_val = top.get_embedded_double()
    else:
        assert False, "top should be integer or double"

    top_2 = stack[stack_ptr.value - 1]
    if isinstance(top_2, Integer):
        top_2_val = top_2.get_embedded_integer()
    elif isinstance(top_2, Double):
        top_2_val = top_2.get_embedded_double()
    else:
        assert False, "top_2 should be integer or double"
    result = top_val > top_2_val
    if result:
        stack[stack_ptr.value] = None
        stack[stack_ptr.value - 1] = None
        stack_ptr.value -= 2
    return result


@jit.dont_look_inside
def emit_jump(current_bc_idx, next_bc_idx):
    return current_bc_idx


@jit.dont_look_inside
def emit_ret(current_bc_idx, ret_val):
    return current_bc_idx


@jit.unroll_safe
def interpret(method, frame, max_stack_size):
    from som.vm.current import current_universe

    current_bc_idx = 0

    stack = [None] * max_stack_size
    stack_ptr = StackPtr()

    tstack = t_empty()
    entry_bc_idx = 0

    while True:

        tier1jitdriver.jit_merge_point(
            current_bc_idx=current_bc_idx,
            entry_bc_idx=entry_bc_idx,
            method=method,
            frame=frame,
            stack_ptr=stack_ptr,
            stack=stack,
            tstack=tstack,
        )


        # when the current_bc_index is 0, it means a method head
        if current_bc_idx == 0:
            tier1jitdriver.can_enter_jit(
                current_bc_idx=current_bc_idx,
                entry_bc_idx=entry_bc_idx,
                method=method,
                frame=frame,
                stack_ptr=stack_ptr,
                stack=stack,
                tstack=tstack,
            )

        bytecode = method.get_bytecode(current_bc_idx)

        # Get the length of the current bytecode
        bc_length = bytecode_length(bytecode)

        # Compute the next bytecode index
        next_bc_idx = current_bc_idx + bc_length

        # promote(stack_ptr)

        # debug_print(current_bc_idx, entry_bc_idx, method, tstack)

        # print(bytecode_as_str(bytecode), "stack_ptr", stack_ptr.value)

        # Handle the current bytecode
        if bytecode == Bytecodes.halt:
            return _halt(stack, stack_ptr)

        if bytecode == Bytecodes.dup:
            _dup(stack, stack_ptr)

        elif bytecode == Bytecodes.dup_second:
            _dup_second(stack, stack_ptr)

        elif bytecode == Bytecodes.push_frame:
            _push_frame(stack, stack_ptr, method, current_bc_idx, frame)

        elif bytecode == Bytecodes.push_frame_0:
            _push_frame_0(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.push_frame_1:
            _push_frame_1(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.push_frame_2:
            _push_frame_2(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.push_inner:
            _push_inner(stack, stack_ptr, method, current_bc_idx, frame)

        elif bytecode == Bytecodes.push_inner_0:
            _push_inner_0(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.push_inner_1:
            _push_inner_1(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.push_inner_2:
            _push_inner_2(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.push_field:
            _push_field(stack, stack_ptr, method, current_bc_idx, frame)

        elif bytecode == Bytecodes.push_field_0:
            _push_field_0(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.push_field_1:
            _push_field_1(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.push_block:
            _push_block(stack, stack_ptr, method, current_bc_idx, frame)

        elif bytecode == Bytecodes.push_block_no_ctx:
            _push_block_no_ctx(stack, stack_ptr, method, current_bc_idx)

        elif bytecode == Bytecodes.push_constant:
            _push_constant(stack, stack_ptr, method, current_bc_idx)

        elif bytecode == Bytecodes.push_constant_0:
            _push_constant_0(stack, stack_ptr, method)

        elif bytecode == Bytecodes.push_constant_1:
            _push_constant_1(stack, stack_ptr, method)

        elif bytecode == Bytecodes.push_constant_2:
            _push_constant_2(stack, stack_ptr, method)

        elif bytecode == Bytecodes.push_0:
            _push_0(stack, stack_ptr)

        elif bytecode == Bytecodes.push_1:
            _push_1(stack, stack_ptr)

        elif bytecode == Bytecodes.push_nil:
            _push_nil(stack, stack_ptr)

        elif bytecode == Bytecodes.push_global:
            _push_global(stack, stack_ptr, method, current_universe, current_bc_idx, frame)

        elif bytecode == Bytecodes.pop:
            _pop(stack, stack_ptr)

        elif bytecode == Bytecodes.pop_frame:
            _pop_frame(stack, stack_ptr, method, current_bc_idx, frame)

        elif bytecode == Bytecodes.pop_frame_0:
            _pop_frame_0(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.pop_frame_1:
            _pop_frame_1(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.pop_frame_2:
            _pop_frame_2(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.pop_inner:
            _pop_inner(stack, stack_ptr, method, current_bc_idx, frame)

        elif bytecode == Bytecodes.pop_inner_0:
            _pop_inner_0(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.pop_inner_1:
            _pop_inner_1(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.pop_inner_2:
            _pop_inner_2(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.nil_frame:
            _nil_frame(method, frame, current_bc_idx)

        elif bytecode == Bytecodes.nil_inner:
            _nil_inner(method, frame, current_bc_idx)

        elif bytecode == Bytecodes.pop_field:
            _pop_field(stack, stack_ptr, method, current_bc_idx, frame)

        elif bytecode == Bytecodes.pop_field_0:
            _pop_field_0(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.pop_field_1:
            _pop_field_1(stack, stack_ptr, frame)

        elif bytecode == Bytecodes.send_1:
            next_bc_idx = _send_1(
                method, current_universe, current_bc_idx, next_bc_idx, stack, stack_ptr,
            )

        elif bytecode == Bytecodes.send_2:
            next_bc_idx = _send_2(
                method, current_universe, current_bc_idx, next_bc_idx, stack, stack_ptr
            )

        elif bytecode == Bytecodes.send_3:
            next_bc_idx = _send_3(
                method, current_universe, current_bc_idx, next_bc_idx, stack, stack_ptr
            )

        elif bytecode == Bytecodes.send_n:
            next_bc_idx = _send_n(
                method, current_universe, current_bc_idx, next_bc_idx, stack, stack_ptr
            )

        elif bytecode == Bytecodes.super_send:
            _do_super_send(stack, stack_ptr, current_bc_idx, method)

        elif bytecode == Bytecodes.return_local:
            if we_are_jitted():
                if tstack.t_is_empty():
                    ret_object = _return_local(stack, stack_ptr, dummy=True)
                    next_bc_idx = emit_ret(entry_bc_idx, ret_object)
                    tier1jitdriver.can_enter_jit(
                        current_bc_idx=current_bc_idx,
                        entry_bc_idx=entry_bc_idx,
                        method=method,
                        frame=frame,
                        stack_ptr=stack_ptr,
                        stack=stack,
                        tstack=tstack,
                    )
                else:
                    ret_object = _return_local(stack, stack_ptr, dummy=True)
                    next_bc_idx, tstack = tstack.t_pop()
                    next_bc_idx = emit_ret(next_bc_idx, ret_object)
            else:
                return _return_local(stack, stack_ptr)

        elif bytecode == Bytecodes.return_non_local:
            if we_are_jitted():
                if tstack.t_is_empty():
                    val = stack[stack_ptr.value]
                    ret_object = _do_return_non_local(
                        val, frame, method.get_bytecode(current_bc_idx + 1)
                    )
                    # TODO: manual emit_ret
                    next_bc_idx = emit_ret(entry_bc_idx, ret_object)
                    tier1jitdriver.can_enter_jit(
                        current_bc_idx=current_bc_idx,
                        entry_bc_idx=entry_bc_idx,
                        method=method,
                        frame=frame,
                        stack_ptr=stack_ptr,
                        stack=stack,
                        tstack=tstack,
                    )
                else:
                    val = stack[stack_ptr.value]
                    ret_object = _do_return_non_local(
                        val, frame, method.get_bytecode(current_bc_idx + 1)
                    )
                    next_bc_idx, tstack = tstack.t_pop()
                    next_bc_idx = emit_ret(next_bc_idx, ret_object)
            else:
                val = stack[stack_ptr.value]
                return _do_return_non_local(
                    val, frame, method.get_bytecode(current_bc_idx + 1)
                )

        elif bytecode == Bytecodes.return_self:
            if we_are_jitted():
                if tstack.t_is_empty():
                    ret_object = _return_self(frame, dummy=True)
                    next_bc_idx = emit_ret(entry_bc_idx, ret_object)
                    tier1jitdriver.can_enter_jit(
                        current_bc_idx=current_bc_idx,
                        entry_bc_idx=entry_bc_idx,
                        method=method,
                        frame=frame,
                        stack_ptr=stack_ptr,
                        stack=stack,
                        tstack=tstack,
                    )
                else:
                    ret_object = _return_self(frame, dummy=True)
                    next_bc_idx, tstack = tstack.t_pop()
                    next_bc_idx = emit_ret(next_bc_idx, ret_object)
            else:
                return _return_self(frame)

        elif bytecode == Bytecodes.inc:
            _inc(stack, stack_ptr)

        elif bytecode == Bytecodes.dec:
            _dec(stack, stack_ptr)

        elif bytecode == Bytecodes.jump:
            next_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)

        elif bytecode == Bytecodes.jump_on_true_top_nil:
            target_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)
            if we_are_jitted():
                if _is_true_object(stack, stack_ptr, dummy=True):
                    stack_ptr.value += 1
                    stack[stack_ptr.value] = nilObject
                    tstack = t_push(next_bc_idx, tstack)
                    next_bc_idx = target_bc_idx
                else:
                    tstack = t_push(target_bc_idx, tstack)
            else:
                if _is_true_object(stack, stack_ptr):
                    next_bc_idx = target_bc_idx
                    stack_ptr.value += 1
                    stack[stack_ptr.value] = nilObject

        elif bytecode == Bytecodes.jump_on_false_top_nil:
            target_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)
            if we_are_jitted():
                if _is_false_object(stack, stack_ptr, dummy=True):
                    stack_ptr.value += 1
                    stack[stack_ptr.value] = nilObject
                    tstack = t_push(next_bc_idx, tstack)
                    next_bc_idx = target_bc_idx
                else:
                    tstack = t_push(target_bc_idx, tstack)
            else:
                if _is_false_object(stack, stack_ptr):
                    next_bc_idx = target_bc_idx
                    stack_ptr.value += 1
                    stack[stack_ptr.value] = nilObject

        elif bytecode == Bytecodes.jump_on_true_pop:
            target_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)
            if we_are_jitted():
                if _is_true_object(stack, stack_ptr, dummy=True):
                    tstack = t_push(next_bc_idx, tstack)
                    next_bc_idx = target_bc_idx
                else:
                    tstack = t_push(target_bc_idx, tstack)
            else:
                if _is_true_object(stack, stack_ptr):
                    next_bc_idx = target_bc_idx

        elif bytecode == Bytecodes.jump_on_false_pop:
            target_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)
            if we_are_jitted():
                if _is_false_object(stack, stack_ptr, dummy=True):
                    tstack = t_push(next_bc_idx, tstack)
                    next_bc_idx = target_bc_idx
                else:
                    tstack = t_push(target_bc_idx, tstack)
            else:
                if _is_false_object(stack, stack_ptr):
                    next_bc_idx = target_bc_idx

        elif bytecode == Bytecodes.jump_backward:
            target_bc_idx = current_bc_idx - method.get_bytecode(current_bc_idx + 1)

            if we_are_jitted():
                if tstack.t_is_empty():
                    next_bc_idx = emit_jump(entry_bc_idx, target_bc_idx)
                    tier1jitdriver.can_enter_jit(
                        current_bc_idx=target_bc_idx,
                        entry_bc_idx=entry_bc_idx,
                        method=method,
                        frame=frame,
                        stack_ptr=stack_ptr,
                        stack=stack,
                        tstack=tstack,
                    )
                else:
                    next_bc_idx, tstack = tstack.t_pop()
                    next_bc_idx = emit_jump(next_bc_idx, target_bc_idx)
            else:
                next_bc_idx = entry_bc_idx = target_bc_idx

        elif bytecode == Bytecodes.jump_if_greater:
            target_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)

            if we_are_jitted():
                if _is_greater_two(stack, stack_ptr, dummy=True):
                    tstack = t_push(next_bc_idx, tstack)
                    next_bc_idx = target_bc_idx
                else:
                    tstack = t_push(target_bc_idx, tstack)
            else:
                if _is_greater_two(stack, stack_ptr):
                    next_bc_idx = target_bc_idx

        elif bytecode == Bytecodes.jump2:
            target_bc_idx = (
                current_bc_idx
                + method.get_bytecode(current_bc_idx + 1)
                + (method.get_bytecode(current_bc_idx + 2) << 8)
            )
            tstack = t_push(next_bc_idx, tstack)
            next_bc_idx = target_bc_idx

        elif bytecode == Bytecodes.jump2_on_true_top_nil:
            target_bc_idx = (
                current_bc_idx
                + method.get_bytecode(current_bc_idx + 1)
                + (method.get_bytecode(current_bc_idx + 2) << 8)
            )
            if we_are_jitted():
                if _is_true_object(stack, stack_ptr, dummy=True):
                    stack_ptr.value += 1
                    stack[stack_ptr.value] = nilObject
                    tstack = t_push(next_bc_idx, tstack)
                    next_bc_idx = target_bc_idx
                else:
                    tstack = t_push(target_bc_idx, tstack)
            else:
                if _is_true_object(stack, stack_ptr):
                    next_bc_idx = target_bc_idx
                    stack_ptr.value += 1
                    stack[stack_ptr.value] = nilObject

        elif bytecode == Bytecodes.jump2_on_false_top_nil:
            target_bc_idx = (
                current_bc_idx
                + method.get_bytecode(current_bc_idx + 1)
                + (method.get_bytecode(current_bc_idx + 2) << 8)
            )
            if we_are_jitted():
                if _is_false_object(stack, stack_ptr, dummy=True):
                    stack_ptr.value += 1
                    stack[stack_ptr.value] = nilObject
                    tstack = t_push(next_bc_idx, tstack)
                    next_bc_idx = target_bc_idx
                else:
                    tstack = t_push(target_bc_idx, tstack)
            else:
                if _is_false_object(stack, stack_ptr):
                    next_bc_idx = target_bc_idx
                    stack[stack_ptr.value] = nilObject

        elif bytecode == Bytecodes.jump2_on_true_pop:
            target_bc_idx = (
                current_bc_idx
                + method.get_bytecode(current_bc_idx + 1)
                + (method.get_bytecode(current_bc_idx + 2) << 8)
            )
            if we_are_jitted():
                if _is_true_object(stack, stack_ptr, dummy=True):
                    tstack = t_push(next_bc_idx, tstack)
                    next_bc_idx = target_bc_idx
                else:
                    tstack = t_push(target_bc_idx, tstack)
            else:
                if _is_true_object(stack, stack_ptr):
                    next_bc_idx = target_bc_idx

        elif bytecode == Bytecodes.jump2_on_false_pop:
            target_bc_idx = (
                current_bc_idx
                + method.get_bytecode(current_bc_idx + 1)
                + (method.get_bytecode(current_bc_idx + 2) << 8)
            )
            if we_are_jitted():
                if _is_false_object(stack, stack_ptr, dummy=True):
                    tstack = t_push(next_bc_idx, tstack)
                    next_bc_idx = target_bc_idx
                else:
                    tstack = t_push(target_bc_idx, tstack)
            else:
                if _is_false_object(stack, stack_ptr):
                    next_bc_idx = target_bc_idx

        elif bytecode == Bytecodes.jump2_if_greater:
            target_bc_idx = (
                current_bc_idx
                + method.get_bytecode(current_bc_idx + 1)
                + (method.get_bytecode(current_bc_idx + 2) << 8)
            )
            if we_are_jitted():
                if _is_greater_two(stack, stack_ptr, dummy=True):
                    tstack = t_push(next_bc_idx, tstack)
                    next_bc_idx = target_bc_idx
                else:
                    tstack = t_push(target_bc_idx, tstack)
            else:
                if _is_greater_two(stack, stack_ptr):
                    next_bc_idx = target_bc_idx

        elif bytecode == Bytecodes.jump2_backward:
            # TODO: instrument with tstack
            target_bc_idx = current_bc_idx - (
                method.get_bytecode(current_bc_idx + 1)
                + (method.get_bytecode(current_bc_idx + 2) << 8)
            )

            if we_are_jitted():
                if tstack.t_is_empty():
                    next_bc_idx = emit_jump(entry_bc_idx, target_bc_idx)
                    tier1jitdriver.can_enter_jit(
                        current_bc_idx=target_bc_idx,
                        entry_bc_idx=entry_bc_idx,
                        method=method,
                        frame=frame,
                        stack_ptr=stack_ptr,
                        stack=stack,
                        tstack=tstack,
                    )
                else:
                    next_bc_idx, tstack = tstack.t_pop()
                    next_bc_idx = emit_jump(next_bc_idx, target_bc_idx)
            else:
                next_bc_idx = entry_bc_idx = target_bc_idx

        elif bytecode == Bytecodes.q_super_send_1:
            _q_super_send_1(stack, stack_ptr, method, current_bc_idx)

        elif bytecode == Bytecodes.q_super_send_2:
            _q_super_send_2(stack, stack_ptr, method, current_bc_idx)

        elif bytecode == Bytecodes.q_super_send_3:
            _q_super_send_3(stack, stack_ptr, method, current_bc_idx)

        elif bytecode == Bytecodes.q_super_send_n:
            _q_super_send_n(stack, stack_ptr, method, current_bc_idx)

        elif bytecode == Bytecodes.push_local:
            next_bc_idx = _push_local(method, current_bc_idx, next_bc_idx)

        elif bytecode == Bytecodes.push_argument:
            next_bc_idx = _push_argument(method, current_bc_idx, next_bc_idx)

        elif bytecode == Bytecodes.pop_local:
            next_bc_idx = _pop_local(method, current_bc_idx, next_bc_idx)

        elif bytecode == Bytecodes.pop_argument:
            next_bc_idx = _pop_argument(method, current_bc_idx, next_bc_idx)

        elif bytecode == Bytecodes.nil_local:
            method.patch_variable_access(current_bc_idx)
            next_bc_idx = current_bc_idx

        else:
            _unknown_bytecode(bytecode, current_bc_idx, method)

        current_bc_idx = next_bc_idx


@jit.unroll_safe
def interpret_tier2(method, frame, max_stack_size):
    from som.vm.current import current_universe

    current_bc_idx = 0

    stack_ptr = -1
    stack = [None] * max_stack_size

    while True:
        jitdriver.jit_merge_point(
            current_bc_idx=current_bc_idx,
            stack_ptr=stack_ptr,
            method=method,
            frame=frame,
            stack=stack,
        )

        bytecode = method.get_bytecode(current_bc_idx)

        # Get the length of the current bytecode
        bc_length = bytecode_length(bytecode)

        # Compute the next bytecode index
        next_bc_idx = current_bc_idx + bc_length

        # promote(stack_ptr)

        # Handle the current bytecode
        if bytecode == Bytecodes.halt:
            return stack[stack_ptr]

        if bytecode == Bytecodes.dup:
            val = stack[stack_ptr]
            stack_ptr += 1
            stack[stack_ptr] = val

        elif bytecode == Bytecodes.dup_second:
            val = stack[stack_ptr - 1]
            stack_ptr += 1
            stack[stack_ptr] = val

        elif bytecode == Bytecodes.push_frame:
            stack_ptr += 1
            stack[stack_ptr] = read_frame(
                frame, method.get_bytecode(current_bc_idx + 1)
            )

        elif bytecode == Bytecodes.push_frame_0:
            stack_ptr += 1
            stack[stack_ptr] = read_frame(frame, FRAME_AND_INNER_RCVR_IDX + 0)

        elif bytecode == Bytecodes.push_frame_1:
            stack_ptr += 1
            stack[stack_ptr] = read_frame(frame, FRAME_AND_INNER_RCVR_IDX + 1)

        elif bytecode == Bytecodes.push_frame_2:
            stack_ptr += 1
            stack[stack_ptr] = read_frame(frame, FRAME_AND_INNER_RCVR_IDX + 2)

        elif bytecode == Bytecodes.push_inner:
            idx = method.get_bytecode(current_bc_idx + 1)
            ctx_level = method.get_bytecode(current_bc_idx + 2)

            stack_ptr += 1
            if ctx_level == 0:
                stack[stack_ptr] = read_inner(frame, idx)
            else:
                block = get_block_at(frame, ctx_level)
                stack[stack_ptr] = block.get_from_outer(idx)

        elif bytecode == Bytecodes.push_inner_0:
            stack_ptr += 1
            stack[stack_ptr] = read_inner(frame, FRAME_AND_INNER_RCVR_IDX + 0)

        elif bytecode == Bytecodes.push_inner_1:
            stack_ptr += 1
            stack[stack_ptr] = read_inner(frame, FRAME_AND_INNER_RCVR_IDX + 1)

        elif bytecode == Bytecodes.push_inner_2:
            stack_ptr += 1
            stack[stack_ptr] = read_inner(frame, FRAME_AND_INNER_RCVR_IDX + 2)

        elif bytecode == Bytecodes.push_field:
            field_idx = method.get_bytecode(current_bc_idx + 1)
            ctx_level = method.get_bytecode(current_bc_idx + 2)
            self_obj = get_self(frame, ctx_level)
            stack_ptr += 1
            stack[stack_ptr] = self_obj.get_field(field_idx)

        elif bytecode == Bytecodes.push_field_0:
            self_obj = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)
            stack_ptr += 1
            stack[stack_ptr] = self_obj.get_field(0)

        elif bytecode == Bytecodes.push_field_1:
            self_obj = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)
            stack_ptr += 1
            stack[stack_ptr] = self_obj.get_field(1)

        elif bytecode == Bytecodes.push_block:
            block_method = method.get_constant(current_bc_idx)
            stack_ptr += 1
            stack[stack_ptr] = BcBlock(block_method, get_inner_as_context(frame))

        elif bytecode == Bytecodes.push_block_no_ctx:
            block_method = method.get_constant(current_bc_idx)
            stack_ptr += 1
            stack[stack_ptr] = BcBlock(block_method, None)

        elif bytecode == Bytecodes.push_constant:
            stack_ptr += 1
            stack[stack_ptr] = method.get_constant(current_bc_idx)

        elif bytecode == Bytecodes.push_constant_0:
            stack_ptr += 1
            stack[stack_ptr] = method._literals[0]  # pylint: disable=protected-access

        elif bytecode == Bytecodes.push_constant_1:
            stack_ptr += 1
            stack[stack_ptr] = method._literals[1]  # pylint: disable=protected-access

        elif bytecode == Bytecodes.push_constant_2:
            stack_ptr += 1
            stack[stack_ptr] = method._literals[2]  # pylint: disable=protected-access

        elif bytecode == Bytecodes.push_0:
            stack_ptr += 1
            stack[stack_ptr] = int_0

        elif bytecode == Bytecodes.push_1:
            stack_ptr += 1
            stack[stack_ptr] = int_1

        elif bytecode == Bytecodes.push_nil:
            stack_ptr += 1
            stack[stack_ptr] = nilObject

        elif bytecode == Bytecodes.push_global:
            global_name = method.get_constant(current_bc_idx)
            glob = current_universe.get_global(global_name)

            stack_ptr += 1
            if glob:
                stack[stack_ptr] = glob
            else:
                stack[stack_ptr] = lookup_and_send_2(
                    get_self_dynamically(frame), global_name, "unknownGlobal:"
                )

        elif bytecode == Bytecodes.pop:
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

        elif bytecode == Bytecodes.pop_frame:
            value = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1
            write_frame(frame, method.get_bytecode(current_bc_idx + 1), value)

        elif bytecode == Bytecodes.pop_frame_0:
            value = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1
            write_frame(frame, FRAME_AND_INNER_RCVR_IDX + 0, value)

        elif bytecode == Bytecodes.pop_frame_1:
            value = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1
            write_frame(frame, FRAME_AND_INNER_RCVR_IDX + 1, value)

        elif bytecode == Bytecodes.pop_frame_2:
            value = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1
            write_frame(frame, FRAME_AND_INNER_RCVR_IDX + 2, value)

        elif bytecode == Bytecodes.pop_inner:
            idx = method.get_bytecode(current_bc_idx + 1)
            ctx_level = method.get_bytecode(current_bc_idx + 2)
            value = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

            if ctx_level == 0:
                write_inner(frame, idx, value)
            else:
                block = get_block_at(frame, ctx_level)
                block.set_outer(idx, value)

        elif bytecode == Bytecodes.pop_inner_0:
            value = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

            write_inner(frame, FRAME_AND_INNER_RCVR_IDX + 0, value)

        elif bytecode == Bytecodes.pop_inner_1:
            value = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

            write_inner(frame, FRAME_AND_INNER_RCVR_IDX + 1, value)

        elif bytecode == Bytecodes.pop_inner_2:
            value = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

            write_inner(frame, FRAME_AND_INNER_RCVR_IDX + 2, value)

        elif bytecode == Bytecodes.nil_frame:
            if we_are_jitted():
                idx = method.get_bytecode(current_bc_idx + 1)
                write_frame(frame, idx, nilObject)

        elif bytecode == Bytecodes.nil_inner:
            if we_are_jitted():
                idx = method.get_bytecode(current_bc_idx + 1)
                write_inner(frame, idx, nilObject)

        elif bytecode == Bytecodes.pop_field:
            field_idx = method.get_bytecode(current_bc_idx + 1)
            ctx_level = method.get_bytecode(current_bc_idx + 2)
            self_obj = get_self(frame, ctx_level)

            value = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

            self_obj.set_field(field_idx, value)

        elif bytecode == Bytecodes.pop_field_0:
            self_obj = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)

            value = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

            self_obj.set_field(0, value)

        elif bytecode == Bytecodes.pop_field_1:
            self_obj = read_frame(frame, FRAME_AND_INNER_RCVR_IDX)

            value = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

            self_obj.set_field(1, value)

        elif bytecode == Bytecodes.send_1:
            signature = method.get_constant(current_bc_idx)
            receiver = stack[stack_ptr]

            layout = receiver.get_object_layout(current_universe)
            invokable = _lookup(layout, signature, method, current_bc_idx)
            if invokable is not None:
                stack[stack_ptr] = invokable.invoke_1(receiver)
            elif not layout.is_latest:
                _update_object_and_invalidate_old_caches(
                    receiver, method, current_bc_idx, current_universe
                )
                next_bc_idx = current_bc_idx
            else:
                stack_ptr = _send_does_not_understand(
                    receiver, signature, stack, stack_ptr
                )

        elif bytecode == Bytecodes.send_2:
            signature = method.get_constant(current_bc_idx)
            receiver = stack[stack_ptr - 1]

            layout = receiver.get_object_layout(current_universe)
            invokable = _lookup(layout, signature, method, current_bc_idx)
            if invokable is not None:
                arg = stack[stack_ptr]
                if we_are_jitted():
                    stack[stack_ptr] = None
                stack_ptr -= 1
                stack[stack_ptr] = invokable.invoke_2(receiver, arg)
            elif not layout.is_latest:
                _update_object_and_invalidate_old_caches(
                    receiver, method, current_bc_idx, current_universe
                )
                next_bc_idx = current_bc_idx
            else:
                stack_ptr = _send_does_not_understand(
                    receiver, signature, stack, stack_ptr
                )

        elif bytecode == Bytecodes.send_3:
            signature = method.get_constant(current_bc_idx)
            receiver = stack[stack_ptr - 2]

            layout = receiver.get_object_layout(current_universe)
            invokable = _lookup(layout, signature, method, current_bc_idx)
            if invokable is not None:
                arg2 = stack[stack_ptr]
                arg1 = stack[stack_ptr - 1]

                if we_are_jitted():
                    stack[stack_ptr] = None
                    stack[stack_ptr - 1] = None

                stack_ptr -= 2
                stack[stack_ptr] = invokable.invoke_3(receiver, arg1, arg2)
            elif not layout.is_latest:
                _update_object_and_invalidate_old_caches(
                    receiver, method, current_bc_idx, current_universe
                )
                next_bc_idx = current_bc_idx
            else:
                stack_ptr = _send_does_not_understand(
                    receiver, signature, stack, stack_ptr
                )

        elif bytecode == Bytecodes.send_n:
            signature = method.get_constant(current_bc_idx)
            receiver = stack[
                stack_ptr - (signature.get_number_of_signature_arguments() - 1)
            ]

            layout = receiver.get_object_layout(current_universe)
            invokable = _lookup(layout, signature, method, current_bc_idx)
            if invokable is not None:
                stack_ptr = invokable.invoke_n(stack, stack_ptr)
            elif not layout.is_latest:
                _update_object_and_invalidate_old_caches(
                    receiver, method, current_bc_idx, current_universe
                )
                next_bc_idx = current_bc_idx
            else:
                stack_ptr = _send_does_not_understand(
                    receiver, signature, stack, stack_ptr
                )

        elif bytecode == Bytecodes.super_send:
            stack_ptr = _do_super_send_tier2(current_bc_idx, method, stack, stack_ptr)

        elif bytecode == Bytecodes.return_local:
            return stack[stack_ptr]

        elif bytecode == Bytecodes.return_non_local:
            val = stack[stack_ptr]
            return _do_return_non_local(
                val, frame, method.get_bytecode(current_bc_idx + 1)
            )

        elif bytecode == Bytecodes.return_self:
            return read_frame(frame, FRAME_AND_INNER_RCVR_IDX)

        elif bytecode == Bytecodes.inc:
            val = stack[stack_ptr]
            from som.vmobjects.integer import Integer
            from som.vmobjects.double import Double
            from som.vmobjects.biginteger import BigInteger

            if isinstance(val, Integer):
                result = val.prim_inc()
            elif isinstance(val, Double):
                result = val.prim_inc()
            elif isinstance(val, BigInteger):
                result = val.prim_inc()
            else:
                return _not_yet_implemented()
            stack[stack_ptr] = result

        elif bytecode == Bytecodes.dec:
            val = stack[stack_ptr]
            from som.vmobjects.integer import Integer
            from som.vmobjects.double import Double
            from som.vmobjects.biginteger import BigInteger

            if isinstance(val, Integer):
                result = val.prim_dec()
            elif isinstance(val, Double):
                result = val.prim_dec()
            elif isinstance(val, BigInteger):
                result = val.prim_dec()
            else:
                return _not_yet_implemented()
            stack[stack_ptr] = result

        elif bytecode == Bytecodes.jump:
            next_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)

        elif bytecode == Bytecodes.jump_on_true_top_nil:
            val = stack[stack_ptr]
            if val is trueObject:
                next_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)
                stack[stack_ptr] = nilObject
            else:
                if we_are_jitted():
                    stack[stack_ptr] = None
                stack_ptr -= 1

        elif bytecode == Bytecodes.jump_on_false_top_nil:
            val = stack[stack_ptr]
            if val is falseObject:
                next_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)
                stack[stack_ptr] = nilObject
            else:
                if we_are_jitted():
                    stack[stack_ptr] = None
                stack_ptr -= 1

        elif bytecode == Bytecodes.jump_on_true_pop:
            val = stack[stack_ptr]
            if val is trueObject:
                next_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

        elif bytecode == Bytecodes.jump_on_false_pop:
            val = stack[stack_ptr]
            if val is falseObject:
                next_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

        elif bytecode == Bytecodes.jump_if_greater:
            top = stack[stack_ptr]
            top_2 = stack[stack_ptr - 1]
            if top.get_embedded_integer() > top_2.get_embedded_integer():
                stack[stack_ptr] = None
                stack[stack_ptr - 1] = None
                stack_ptr -= 2
                next_bc_idx = current_bc_idx + method.get_bytecode(current_bc_idx + 1)

        elif bytecode == Bytecodes.jump_backward:
            next_bc_idx = current_bc_idx - method.get_bytecode(current_bc_idx + 1)
            jitdriver.can_enter_jit(
                current_bc_idx=next_bc_idx,
                stack_ptr=stack_ptr,
                method=method,
                frame=frame,
                stack=stack,
            )

        elif bytecode == Bytecodes.jump2:
            next_bc_idx = (
                current_bc_idx
                + method.get_bytecode(current_bc_idx + 1)
                + (method.get_bytecode(current_bc_idx + 2) << 8)
            )

        elif bytecode == Bytecodes.jump2_on_true_top_nil:
            val = stack[stack_ptr]
            if val is trueObject:
                next_bc_idx = (
                    current_bc_idx
                    + method.get_bytecode(current_bc_idx + 1)
                    + (method.get_bytecode(current_bc_idx + 2) << 8)
                )
                stack[stack_ptr] = nilObject
            else:
                if we_are_jitted():
                    stack[stack_ptr] = None
                stack_ptr -= 1

        elif bytecode == Bytecodes.jump2_on_false_top_nil:
            val = stack[stack_ptr]
            if val is falseObject:
                next_bc_idx = (
                    current_bc_idx
                    + method.get_bytecode(current_bc_idx + 1)
                    + (method.get_bytecode(current_bc_idx + 2) << 8)
                )
                stack[stack_ptr] = nilObject
            else:
                if we_are_jitted():
                    stack[stack_ptr] = None
                stack_ptr -= 1

        elif bytecode == Bytecodes.jump2_on_true_pop:
            val = stack[stack_ptr]
            if val is trueObject:
                next_bc_idx = (
                    current_bc_idx
                    + method.get_bytecode(current_bc_idx + 1)
                    + (method.get_bytecode(current_bc_idx + 2) << 8)
                )
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

        elif bytecode == Bytecodes.jump2_on_false_pop:
            val = stack[stack_ptr]
            if val is falseObject:
                next_bc_idx = (
                    current_bc_idx
                    + method.get_bytecode(current_bc_idx + 1)
                    + (method.get_bytecode(current_bc_idx + 2) << 8)
                )
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1

        elif bytecode == Bytecodes.jump2_if_greater:
            top = stack[stack_ptr]
            top_2 = stack[stack_ptr - 1]
            if top.get_embedded_integer() > top_2.get_embedded_integer():
                stack[stack_ptr] = None
                stack[stack_ptr - 1] = None
                stack_ptr -= 2
                next_bc_idx = (
                    current_bc_idx
                    + method.get_bytecode(current_bc_idx + 1)
                    + (method.get_bytecode(current_bc_idx + 2) << 8)
                )

        elif bytecode == Bytecodes.jump2_backward:
            next_bc_idx = current_bc_idx - (
                method.get_bytecode(current_bc_idx + 1)
                + (method.get_bytecode(current_bc_idx + 2) << 8)
            )
            jitdriver.can_enter_jit(
                current_bc_idx=next_bc_idx,
                stack_ptr=stack_ptr,
                method=method,
                frame=frame,
                stack=stack,
            )

        elif bytecode == Bytecodes.q_super_send_1:
            invokable = method.get_inline_cache_invokable(current_bc_idx)
            stack[stack_ptr] = invokable.invoke_1(stack[stack_ptr])

        elif bytecode == Bytecodes.q_super_send_2:
            invokable = method.get_inline_cache_invokable(current_bc_idx)
            arg = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1
            stack[stack_ptr] = invokable.invoke_2(stack[stack_ptr], arg)

        elif bytecode == Bytecodes.q_super_send_3:
            invokable = method.get_inline_cache_invokable(current_bc_idx)
            arg2 = stack[stack_ptr]
            arg1 = stack[stack_ptr - 1]
            if we_are_jitted():
                stack[stack_ptr] = None
                stack[stack_ptr - 1] = None
            stack_ptr -= 2
            stack[stack_ptr] = invokable.invoke_3(stack[stack_ptr], arg1, arg2)

        elif bytecode == Bytecodes.q_super_send_n:
            invokable = method.get_inline_cache_invokable(current_bc_idx)
            stack_ptr = invokable.invoke_n(stack, stack_ptr)

        elif bytecode == Bytecodes.push_local:
            method.patch_variable_access(current_bc_idx)
            # retry bytecode after patching
            next_bc_idx = current_bc_idx
        elif bytecode == Bytecodes.push_argument:
            method.patch_variable_access(current_bc_idx)
            # retry bytecode after patching
            next_bc_idx = current_bc_idx
        elif bytecode == Bytecodes.pop_local:
            method.patch_variable_access(current_bc_idx)
            # retry bytecode after patching
            next_bc_idx = current_bc_idx
        elif bytecode == Bytecodes.pop_argument:
            method.patch_variable_access(current_bc_idx)
            # retry bytecode after patching
            next_bc_idx = current_bc_idx
        elif bytecode == Bytecodes.nil_local:
            method.patch_variable_access(current_bc_idx)
            # retry bytecode after patching
            next_bc_idx = current_bc_idx
        else:
            _unknown_bytecode(bytecode, current_bc_idx, method)

        current_bc_idx = next_bc_idx


def _do_super_send_tier2(bytecode_index, method, stack, stack_ptr):
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
        else:
            bc = Bytecodes.q_super_send_n
        method.set_bytecode(bytecode_index, bc)
        stack_ptr = _invoke_invokable_slow_path(
            invokable, num_args, receiver, stack, stack_ptr
        )
    else:
        stack_ptr = _send_does_not_understand(
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


def _send_does_not_understand(receiver, selector, stack, stack_ptr):
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

    stack[stack_ptr] = lookup_and_send_3(
        receiver, selector, arguments_array, "doesNotUnderstand:arguments:"
    )

    return stack_ptr


def get_printable_location(bytecode_index, method):
    from som.vmobjects.method_bc import BcAbstractMethod

    assert isinstance(method, BcAbstractMethod)
    bc = method.get_bytecode(bytecode_index)
    return "%s @ %d in %s" % (
        bytecode_as_str(bc),
        bytecode_index,
        method.merge_point_string(),
    )


def get_printable_location_tier1(bytecode_index, entry_bc_idx, method, tstack):
    from som.vmobjects.method_bc import BcAbstractMethod

    assert isinstance(method, BcAbstractMethod)
    bc = method.get_bytecode(bytecode_index)
    return "%d: %s in %s tstack: %s" % (
        bytecode_index,
        bytecode_as_str(bc),
        method.merge_point_string(),
        t_dump(tstack),
    )


jitdriver = jit.JitDriver(
    name="Interpreter",
    greens=["current_bc_idx", "method"],
    reds=["stack_ptr", "frame", "stack"],
    # virtualizables=['frame'],
    get_printable_location=get_printable_location,
    # the next line is a workaround around a likely bug in RPython
    # for some reason, the inlining heuristics default to "never inline" when
    # two different jit drivers are involved (in our case, the primitive
    # driver, and this one).
    # the next line says that calls involving this jitdriver should always be
    # inlined once (which means that things like Integer>>< will be inlined
    # into a while loop again, when enabling this drivers).
    should_unroll_one_iteration=lambda current_bc_idx, method: True,
)


tier1jitdriver = jit.JitDriver(
    name="Threadedcode Interpreter",
    greens=["current_bc_idx", "entry_bc_idx", "method", "tstack"],
    reds=["stack_ptr", "frame", "stack"],
    get_printable_location=get_printable_location_tier1,
    should_unroll_one_iteration=lambda current_bc_idx, entry_bc_idx, method, tstack: True,
    threaded_code_gen=True,
    conditions=["_is_true_object", "_is_false_object", "_is_greater_two"],
)


def jitpolicy(_driver):
    from rpython.jit.codewriter.policy import JitPolicy  # pylint: disable=import-error

    return JitPolicy()
