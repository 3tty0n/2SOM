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
from som.interpreter.send import lookup_and_send_2, lookup_and_send_3, lookup_and_send_2_tier3, lookup_and_send_3_tier3
from som.interpreter.bc.residual import (
    site_kind,
    is_arith_kind,
    is_predicate_kind,
    _t3_pure,
    _residual_send_1,
    _residual_send_2,
    _residual_send_3,
    _residual_send_4,
    _residual_send_n,
)
from som.interpreter.bc.adaptive import _profile, _profiling_active_for, _profile_layout
from som.tier_type import (
    is_hybrid,
    is_tier1,
    is_tier3,
    is_inliner,
    MODE_INLINE,
    MODE_HYBRID,
    MODE_INLINER,
)
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


# Send dispatch: inline (tier 3) / residualize-all (tier 2 inliner) / residualize
# poly-sites (tier 4 hybrid). is_inliner() is a per-binary constant that folds the
# inliner arm away outside the tier-2 binary; `hybrid` is the promoted-red 3-valued
# mode, so hybrid == MODE_INLINER folds within a trace too.
@always_inline
def _residual_2(method, invokable, kind, receiver, arg):
    # Pure fast path (+,-,*, comparisons on Integer^2/Double^2); self-rejects with
    # None otherwise. Only ever hits for an arith/predicate kind, so skip it (and its
    # isinstance checks) for the generic sends the inliner also routes here.
    if is_arith_kind(kind) or is_predicate_kind(kind):
        res = _t3_pure(kind, receiver, arg)
        if res is not None:
            return res
    return _residual_send_2(method, invokable, receiver, arg)


@always_inline
def _dispatch_send_1(method, invokable, receiver, hybrid):
    if is_inliner() or hybrid == MODE_INLINER:
        return _residual_send_1(method, invokable, receiver)
    return invokable.invoke_1_tier3(receiver, hybrid)


@always_inline
def _dispatch_send_2(method, bc_idx, signature, invokable, receiver, arg, hybrid):
    if is_inliner() or hybrid == MODE_INLINER:
        kind = site_kind(method, bc_idx, signature)
        if hybrid == MODE_INLINER and not we_are_jitted():
            # warm phase: operand-shape profile feeding the promotion decision
            if is_arith_kind(kind) or is_predicate_kind(kind):
                _profile(method, bc_idx, kind, receiver, arg)
        return _residual_2(method, invokable, kind, receiver, arg)
    if hybrid == MODE_HYBRID:
        kind = site_kind(method, bc_idx, signature)
        if is_arith_kind(kind) or is_predicate_kind(kind):
            if not we_are_jitted():
                _profile(method, bc_idx, kind, receiver, arg)
                if method._poly[bc_idx] == 0:
                    method._inl_runs[bc_idx] += 1
            if promote(method._poly[bc_idx]):
                return _residual_2(method, invokable, kind, receiver, arg)
    elif not we_are_jitted() and _profiling_active_for(method):
        # Controller profiling window: gather the operand-shape profile while running
        # inline, so a monomorphic method is never traced in hybrid mode.
        kind = site_kind(method, bc_idx, signature)
        if is_arith_kind(kind) or is_predicate_kind(kind):
            _profile(method, bc_idx, kind, receiver, arg)
            if method._poly[bc_idx] == 0:
                method._inl_runs[bc_idx] += 1
    return invokable.invoke_2_tier3(receiver, arg, hybrid)


@always_inline
def _dispatch_send_3(method, invokable, receiver, arg1, arg2, hybrid):
    if is_inliner() or hybrid == MODE_INLINER:
        return _residual_send_3(method, invokable, receiver, arg1, arg2)
    return invokable.invoke_3_tier3(receiver, arg1, arg2, hybrid)


@always_inline
def _dispatch_send_4(method, invokable, receiver, arg1, arg2, arg3, hybrid):
    if is_inliner() or hybrid == MODE_INLINER:
        return _residual_send_4(method, invokable, receiver, arg1, arg2, arg3)
    return invokable.invoke_4_tier3(receiver, arg1, arg2, arg3, hybrid)


@always_inline
def _dispatch_send_n(method, invokable, stack, stack_ptr, hybrid):
    if is_inliner() or hybrid == MODE_INLINER:
        return _residual_send_n(method, invokable, stack, stack_ptr)
    return invokable.invoke_n_tier3(stack, stack_ptr, hybrid)

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


@jit.unroll_safe
def interpret_tier3(
    method, frame, max_stack_size, current_bc_idx=0, stack=None, stack_ptr=-1,
    hybrid=False, dummy=False
):
    from som.vm.current import current_universe

    if dummy:
        return

    if not stack:
        stack_ptr = -1
        stack = [None] * max_stack_size
        if is_tier3() and not we_are_jitted():
            method.t5_invocations += 1

    while True:
        jitdriver.jit_merge_point(
            current_bc_idx=current_bc_idx,
            hybrid=hybrid,
            stack_ptr=stack_ptr,
            method=method,
            frame=frame,
            stack=stack,
        )

        # Promote the (red) hybrid mode so a committed method's trace specialises to
        # its single steady-state mode.
        hybrid = promote(hybrid)

        # Tier 5 activation tick, at the merge point so it stays live on
        # trampoline-routed residual calls (their portal entry starts HERE, not
        # at the function prologue). Folds away inside traces (we_are_jitted).
        if current_bc_idx == 0 and is_tier3() and not we_are_jitted():
            if _t5cfg.enabled and (_t5warmup.on or _t5warmup.purge_pending):
                _t5_warmup_check()

        # Warm execution leaves as soon as the method is promoted: adaptive_tier is
        # quasi-immutable, so the promotion write invalidates the warm trace and the
        # loop deopts here, picks up the committed mode and continues at the same
        # bytecode. Undecided (0) and warm (2) stay in MODE_INLINER. In committed
        # traces this branch folds away (hybrid promotes to 0/1).
        if hybrid == MODE_INLINER:
            at = method.adaptive_tier
            if at == 3:
                hybrid = MODE_INLINE
            elif at == 4:
                hybrid = MODE_HYBRID

        bytecode = method.get_bytecode(current_bc_idx)

        # Get the length of the current bytecode
        bc_length = bytecode_length(bytecode)

        # Compute the next bytecode index
        next_bc_idx = current_bc_idx + bc_length

        promote(stack_ptr)

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
                stack[stack_ptr] = lookup_and_send_2_tier3(
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

            if hybrid and promote(method._mega[current_bc_idx]):
                # Megamorphic unary site: dispatch opaquely so the trace is not specialised
                # per receiver class (no inline-cache overflow / per-class bridge here).
                stack[stack_ptr] = _residual_mega_send_1(
                    method, current_bc_idx, signature, receiver, current_universe
                )
                current_bc_idx = next_bc_idx
                continue

            layout = receiver.get_object_layout(current_universe)
            if not we_are_jitted() and (hybrid or _profiling_active_for(method)):
                # Count distinct receiver classes, but only once the 2-entry inline
                # cache has overflowed, so the mono/2-class majority pays nothing.
                l1 = method.get_inline_cache_layout(current_bc_idx)
                l2 = method.get_inline_cache_layout(current_bc_idx + 1)
                if l1 is not None and l2 is not None and layout is not l1 and layout is not l2:
                    _profile_layout(method, current_bc_idx, signature, layout)
            invokable = _lookup(layout, signature, method, current_bc_idx)
            if invokable is not None:
                stack[stack_ptr] = _dispatch_send_1(method, invokable, receiver, hybrid)
            elif not layout.is_latest:
                _update_object_and_invalidate_old_caches(
                    receiver, method, current_bc_idx, current_universe
                )
                next_bc_idx = current_bc_idx
            else:
                stack_ptr = _send_does_not_understand_tier3(
                    receiver, signature, stack, stack_ptr
                )

        elif bytecode == Bytecodes.send_2:
            signature = method.get_constant(current_bc_idx)
            receiver = stack[stack_ptr - 1]

            if hybrid and promote(method._mega[current_bc_idx]):
                # Megamorphic site: dispatch opaquely so the trace is not specialised
                # per receiver class (no inline-cache overflow / per-class bridge here).
                arg = stack[stack_ptr]
                if we_are_jitted():
                    stack[stack_ptr] = None
                stack_ptr -= 1
                stack[stack_ptr] = _residual_mega_send_2(
                    method, current_bc_idx, signature, receiver, arg, current_universe
                )
                current_bc_idx = next_bc_idx
                continue

            layout = receiver.get_object_layout(current_universe)
            if not we_are_jitted() and (hybrid or _profiling_active_for(method)):
                # Count distinct receiver classes only once the 2-entry IC has overflowed
                # (>2 classes) -- the monomorphic/2-class majority pays nothing (see send_1).
                l1 = method.get_inline_cache_layout(current_bc_idx)
                l2 = method.get_inline_cache_layout(current_bc_idx + 1)
                if l1 is not None and l2 is not None and layout is not l1 and layout is not l2:
                    _profile_layout(method, current_bc_idx, signature, layout)
            invokable = _lookup(layout, signature, method, current_bc_idx)
            if invokable is not None:
                arg = stack[stack_ptr]
                if we_are_jitted():
                    stack[stack_ptr] = None
                stack_ptr -= 1
                stack[stack_ptr] = _dispatch_send_2(
                    method, current_bc_idx, signature, invokable, receiver, arg, hybrid
                )
            elif not layout.is_latest:
                _update_object_and_invalidate_old_caches(
                    receiver, method, current_bc_idx, current_universe
                )
                next_bc_idx = current_bc_idx
            else:
                stack_ptr = _send_does_not_understand_tier3(
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
                stack[stack_ptr] = _dispatch_send_3(
                    method, invokable, receiver, arg1, arg2, hybrid
                )
            elif not layout.is_latest:
                _update_object_and_invalidate_old_caches(
                    receiver, method, current_bc_idx, current_universe
                )
                next_bc_idx = current_bc_idx
            else:
                stack_ptr = _send_does_not_understand_tier3(
                    receiver, signature, stack, stack_ptr
                )

        elif bytecode == Bytecodes.send_4:
            signature = method.get_constant(current_bc_idx)
            receiver = stack[stack_ptr - 3]

            layout = receiver.get_object_layout(current_universe)
            invokable = _lookup(layout, signature, method, current_bc_idx)
            if invokable is not None:
                arg3 = stack[stack_ptr]
                arg2 = stack[stack_ptr - 1]
                arg1 = stack[stack_ptr - 2]

                if we_are_jitted():
                    stack[stack_ptr] = None
                    stack[stack_ptr - 1] = None
                    stack[stack_ptr - 2] = None

                stack_ptr -= 3
                stack[stack_ptr] = _dispatch_send_4(
                    method, invokable, receiver, arg1, arg2, arg3, hybrid
                )
            elif not layout.is_latest:
                _update_object_and_invalidate_old_caches(
                    receiver, method, current_bc_idx, current_universe
                )
                next_bc_idx = current_bc_idx
            else:
                stack_ptr = _send_does_not_understand_tier3(
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
                stack_ptr = _dispatch_send_n(method, invokable, stack, stack_ptr, hybrid)
            elif not layout.is_latest:
                _update_object_and_invalidate_old_caches(
                    receiver, method, current_bc_idx, current_universe
                )
                next_bc_idx = current_bc_idx
            else:
                stack_ptr = _send_does_not_understand_tier3(
                    receiver, signature, stack, stack_ptr
                )

        elif bytecode == Bytecodes.super_send:
            stack_ptr = _do_super_send_tier3(current_bc_idx, method, stack, stack_ptr)

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
            # Warm loops don't enter this driver: in the tier-4 binary a warm trace
            # here is far more expensive than the dedicated warm driver's (see
            # interpreter_inliner). The dedicated inliner interpreter traces them;
            # this driver only compiles committed code. `hybrid` is promoted, so the
            # branch folds away in committed traces.
            if hybrid != MODE_INLINER:
                jitdriver.can_enter_jit(
                    current_bc_idx=next_bc_idx,
                    hybrid=hybrid,
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
            # Warm loops stay interpreted (see jump_backward).
            if hybrid != MODE_INLINER:
                jitdriver.can_enter_jit(
                    current_bc_idx=next_bc_idx,
                    hybrid=hybrid,
                    stack_ptr=stack_ptr,
                    method=method,
                    frame=frame,
                    stack=stack,
                )

        elif bytecode == Bytecodes.q_super_send_1:
            invokable = method.get_inline_cache_invokable(current_bc_idx)
            stack[stack_ptr] = invokable.invoke_1_tier3(stack[stack_ptr], hybrid)

        elif bytecode == Bytecodes.q_super_send_2:
            invokable = method.get_inline_cache_invokable(current_bc_idx)
            arg = stack[stack_ptr]
            if we_are_jitted():
                stack[stack_ptr] = None
            stack_ptr -= 1
            stack[stack_ptr] = invokable.invoke_2_tier3(stack[stack_ptr], arg, hybrid)

        elif bytecode == Bytecodes.q_super_send_3:
            invokable = method.get_inline_cache_invokable(current_bc_idx)
            arg2 = stack[stack_ptr]
            arg1 = stack[stack_ptr - 1]
            if we_are_jitted():
                stack[stack_ptr] = None
                stack[stack_ptr - 1] = None
            stack_ptr -= 2
            stack[stack_ptr] = invokable.invoke_3_tier3(stack[stack_ptr], arg1, arg2, hybrid)

        elif bytecode == Bytecodes.q_super_send_4:
            invokable = method.get_inline_cache_invokable(current_bc_idx)
            arg3 = stack[stack_ptr]
            arg2 = stack[stack_ptr - 1]
            arg1 = stack[stack_ptr - 2]
            if we_are_jitted():
                stack[stack_ptr] = None
                stack[stack_ptr - 1] = None
                stack[stack_ptr - 2] = None
            stack_ptr -= 3
            stack[stack_ptr] = invokable.invoke_4_tier3(stack[stack_ptr], arg1, arg2, arg3, hybrid)

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


def get_printable_location_tier3(bytecode_index, method):
    from som.vmobjects.method_bc import BcAbstractMethod

    assert isinstance(method, BcAbstractMethod)
    bc = method.get_bytecode(bytecode_index)
    return "%s @ %d in %s" % (
        bytecode_as_str(bc),
        bytecode_index,
        method.merge_point_string(),
    )


# Tier 5 (adaptive trace shaping): env-gated (SOM_T5=1), default OFF.
# The ladder: 1 threaded code, 2 stack inliner, 3 tracing, 4 adaptive execution
# placement (which graph runs each method), 5 adaptive trace shaping (what each
# trace inlines). Unlike tiers 1-4, tier 5 is not a translation-time SOM_TIER --
# it is a runtime mode of the tier-3 binary, decided inside the tracer via the
# can_never_inline hook, which residualizes a portal call (CALL_ASSEMBLER)
# instead of inlining the callee.
# Default policy: a per-method invocation counter -- residualize a callee until
# it has been entered promote_inv times, inline after. The alternative global
# quiescence-phase policy (SOM_T5_QUIESCE > 0) residualizes everything until the
# JIT goes silent, then purges; it wins a few transient-heavy composites but
# regresses long-running recursion behind an uncompilable driver loop, so it is
# opt-in. See _T5Cfg.
import os as _os


class _T5Cfg(object):
    _immutable_fields_ = ["enabled?", "quiesce?"]

    def __init__(self):
        self.enabled = 0       # SOM_T5: 1 enables adaptive portal inlining (tier 5)
        # The DEFAULT policy is the per-method invocation counter: a callee is
        # residualized while method.t5_invocations < promote_inv and inlined
        # after. It is a natural warmup filter -- a short-lived method stays
        # cheap, while a hot loop crosses the ~1039 hotness threshold long after
        # passing promote_inv activations, so it traces fully inlined. No global
        # phase, no purge: nothing a long-running driver loop can lose.
        self.promote_inv = 64  # SOM_T5_PROMOTE_INV: activations before a callee inlines
        # SOM_T5_QUIESCE > 0 switches to the GLOBAL quiescence-phase policy: the
        # warmup phase residualizes every callee until the JIT goes silent for
        # this many interpreted activations, then purges. The phase mode wins on a
        # few transient-heavy composites (Mandelbrot, Experiment7/18) but loses
        # badly on a long-running recursive method behind a driver loop the
        # purge cannot recompile (Fibonacci 15x) -- hence default off.
        self.quiesce = 0       # SOM_T5_QUIESCE: activations of silence; 0 = counter mode
        self.quiesce_gc = 32   # SOM_T5_QUIESCE_GC: minor GCs of silence (phase mode only)
        # SOM_T5_PURGE (phase mode only): 1 = plain purge (loops recount from zero);
        # 2 = purge with reheat (fork API: compiled cells keep near-bound
        # hotness so retraces fire on re-entry).
        self.purge = 1


_t5cfg = _T5Cfg()


try:
    from rpython.rlib.nonconst import NonConstant as _nonconst
except ImportError:
    "NOT_RPYTHON"

    def _nonconst(x):
        return x


class _T5Warmup(object):
    # `on` is deliberately a PLAIN mutable field: the warmup lever is a promoted
    # in-trace guard, not quasi-immutable invalidation (an empty marker call
    # gets dead-code-eliminated and the folded read never registers traces).
    def __init__(self):
        self.on = False
        self.tick = 0        # interpreted activation entries (merge-point, bc 0)
        self.last_trace = 0  # tick value at the last tracer consultation
        # GC clock: interpreted-activation ticks stall once cheap traces cover
        # the program (fully-compiled execution is invisible to interpreter
        # code), so the phase end is ALSO clocked by minor collections -- the one
        # heartbeat that never stalls while the program allocates. Advanced
        # from the GC hook (main_rpython.MyHooks); plain int stores only.
        self.gc_tick = 0
        self.last_trace_gc = 0
        self.purge_pending = 0  # set by the GC-context phase end; purge runs lazily


_t5warmup = _T5Warmup()


def _t5_warmup_mark():
    # Promote the warmup flag at the invocation chokepoint. DO NOT gate this on
    # quiesce > 0: the promote is load-bearing for BOTH policies. For the phase mode
    # it is the in-trace warmup guard whose failure evicts cheap code at phase
    # end. For the counter (quiesce == 0) it keeps _interpret_tier3_mode a
    # non-trivial graph so the codewriter preserves the interpret_tier3 ->
    # interpret_tier3 recursive_call that can_never_inline hooks; gating it out
    # lets the call be restructured and the counter stops residualizing
    # entirely (measured: Exp17 0.21 -> 0.31, Fibonacci 80ms -> tier3 parity,
    # i.e. tier 5 silently becomes a no-op). The guard_value on the flag is
    # heap-cached after the first send, so the steady cost is negligible.
    if _t5cfg.enabled:
        promote(_t5warmup.on)


@jit.dont_look_inside
def _t5_end_phase():
    # dont_look_inside: set_param(None, ...) is a jit_marker the codewriter
    # cannot rewrite inside a jit-visible graph (driver is None).
    _t5warmup.on = False   # one-way: fails all warmup guards
    _t5warmup.purge_pending = 0
    # Discard ALL cheap-phase compiled code (fork set_param): every JitCell
    # forgets its procedure token, so hot loops retrace fresh with full
    # inlining. Bridge recovery alone cannot replace existing loop tokens
    # in nested call-loop structures (measured: DeltaBlue/Towers stuck at
    # 6-14x); the warmup guards evict running code, the purge makes the
    # re-entries compile clean. See _T5Cfg.purge for the value-2 trade-off.
    jit.set_param(None, "purge", _t5cfg.purge)


def _t5_warmup_check():
    # Off-trace; called once per INTERPRETED activation entry at the merge
    # point (bc 0), which stays live on trampoline-routed residual calls --
    # the portal graph starts AT the merge point, so a prologue-side counter
    # would freeze once callers compile. Checks amortized to every 256th call.
    if _t5warmup.purge_pending:
        # The GC-clocked phase end fired (in GC-hook context, where set_param is
        # off-limits); finish the phase end here, on the first interpreted
        # activation -- which the failing warmup guards guarantee promptly.
        _t5_end_phase()
        return
    _t5warmup.tick += 1
    if (_t5warmup.tick & 255) == 0:
        if _t5warmup.on:
            # Structural phase end: the tracer has been silent for `quiesce`
            # interpreted activations -> the startup compile storm is over.
            if _t5warmup.tick - _t5warmup.last_trace > _t5cfg.quiesce:
                _t5_end_phase()


def _t5_stamp():
    # Tracing-activity stamp (both clocks). Also called from t5_configure
    # (main program) so the attribute annotations are fully established there:
    # the can_never_inline hook graph is annotated separately after the main
    # graphs are fixed, and a write appearing only in the hook would
    # re-generalize the attribute and abort translation.
    _t5warmup.last_trace = _t5warmup.tick
    _t5warmup.last_trace_gc = _t5warmup.gc_tick


def t5_gc_minor():
    # GC-clocked phase end, called from MyHooks.on_gc_minor in main_rpython. Runs
    # in GC-hook context: plain int/bool stores only, NO allocation, and no
    # set_param -- ending the phase here only flips the flags; the failing
    # warmup guards then force interpreted re-entry, where the purge runs.
    if _t5cfg.enabled and _t5cfg.quiesce > 0 and _t5warmup.on:
        _t5warmup.gc_tick += 1
        if _t5warmup.gc_tick - _t5warmup.last_trace_gc > _t5cfg.quiesce_gc:
            _t5warmup.on = False
            _t5warmup.purge_pending = 1


def t5_configure():
    v = _os.environ.get("SOM_T5")
    _t5cfg.enabled = int(v) if v else _t5cfg.enabled
    v = _os.environ.get("SOM_T5_PROMOTE_INV")
    _t5cfg.promote_inv = int(v) if v else _t5cfg.promote_inv
    v = _os.environ.get("SOM_T5_QUIESCE")
    _t5cfg.quiesce = int(v) if v else _t5cfg.quiesce
    v = _os.environ.get("SOM_T5_QUIESCE_GC")
    _t5cfg.quiesce_gc = int(v) if v else _t5cfg.quiesce_gc
    v = _os.environ.get("SOM_T5_PURGE")
    _t5cfg.purge = int(v) if v else _t5cfg.purge
    # Establish generic annotations for the fields written from the GC hook
    # and the tracer hook (both annotated outside the main program); the
    # GcHooksStats.reset pattern in main_rpython documents the same need.
    _t5warmup.gc_tick = _nonconst(0)
    _t5warmup.last_trace_gc = _nonconst(0)
    _t5warmup.purge_pending = _nonconst(0)
    _t5warmup.tick = _nonconst(0)
    _t5warmup.last_trace = _nonconst(0)
    if _t5cfg.enabled and _t5cfg.quiesce > 0:
        _t5_stamp()
        _t5warmup.on = True


def _t5_can_never_inline(current_bc_idx, method):
    # Consulted by the tracer on every recursive portal-call decision -- which
    # makes it the tracing-activity signal itself: stamping the tick here is
    # what arms the quiescence phase end.
    if not _t5cfg.enabled:
        return False
    if _t5warmup.on:
        _t5_stamp()
        return True
    if _t5cfg.quiesce > 0:
        # Phase mode, phase over: inline everything -- post-purge retraces
        # must reach tier-3 shape; the counter would residualize callees
        # whose interpreted-activation count happens to sit below the bar.
        return False
    return method.t5_invocations < _t5cfg.promote_inv


# `hybrid` is the residualization mode, a jitdriver red (a green would be folded and
# rejected by warmspot, and two jit_merge_points in one graph are forbidden). The
# controller profiles methods inline, so a committed monomorphic method only ever runs
# one mode and its trace never bridges.
jitdriver = jit.JitDriver(
    name="Interpreter",
    greens=["current_bc_idx", "method"],
    # reds must be grouped by kind: INTs, then REFs, then FLOATs. hybrid is a Bool
    # (INT-kind), so it sits beside stack_ptr, ahead of the frame/stack refs.
    reds=["stack_ptr", "hybrid", "frame", "stack"],
    get_printable_location=get_printable_location_tier3,
    # the next line is a workaround around a likely bug in RPython
    # for some reason, the inlining heuristics default to "never inline" when
    # two different jit drivers are involved (in our case, the primitive
    # driver, and this one).
    # the next line says that calls involving this jitdriver should always be
    # inlined once (which means that things like Integer>>< will be inlined
    # into a while loop again, when enabling this drivers).
    should_unroll_one_iteration=lambda current_bc_idx, method: True,
    can_never_inline=_t5_can_never_inline,
)
