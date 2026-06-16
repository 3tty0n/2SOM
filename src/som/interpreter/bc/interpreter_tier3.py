'''Bytecode tracing interpreter factory.

Defines ``make_interp(policy, …)`` which derives THREE interpreter siblings from
one loop body:

* ``interpret_tier3``   (P_HYBRID)    – adaptive hybrid + plain tier-3 tracing;
  the workhorse binary that supports both full inlining and megamorphic
  residualization in a single trace graph.
* ``interpret_committed`` (P_COMMITTED) – inline every send unconditionally; no
  merge-point adaptive_tier read so the loop is maximally lean.
* ``interpret_inliner``   (P_WARM)    – residualize every send; used during the
  warm-up phase before a method has committed to a tier.

``policy`` is captured as a closure constant so its arms fold at flow-graph time,
making each sibling as lean as a hand-written one.

Lookup/dispatch helpers live in dispatch_support.py; the tier-5 trace-shaping
policy in shaping.py.
'''
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

from som.interpreter.bc.shaping import (
    _shaping_cfg, _shaping_warmup, _shaping_warmup_check, _shaping_can_never_inline,
)
from som.interpreter.bc.dispatch_support import (
    _do_super_send_tier3, _not_yet_implemented, _unknown_bytecode, get_self,
    _lookup, _update_object_and_invalidate_old_caches,
    _send_does_not_understand_tier3, _residual_mega_send_1, _residual_mega_send_2,
)

# === Policy Constants ===
P_HYBRID = 0     # interpret_tier3: red `hybrid` mode, mega routes, shaping, adaptive profiling
P_WARM = 1       # interpret_inliner: residualize every send, early-exit OSR, warm profiling
P_COMMITTED = 2  # interpret_committed: inline every send, no merge-point adaptive_tier read


# === Send Dispatch Helpers ===
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

# === Interpreter Factory ===
def make_interp(policy, gpl_fn, driver_name):
    '''Build one interpreter function closed over ``policy``.

    ``policy`` is a compile-time integer constant (P_HYBRID / P_WARM /
    P_COMMITTED).  Because it is captured in the closure, each sibling's
    flow-graph sees a folded constant, letting the specializer eliminate the
    dead arms of every ``if policy ==`` branch — the result is as lean as a
    hand-written single-policy interpreter.
    '''
    if policy == P_HYBRID:
        jitdriver = jit.JitDriver(
            name=driver_name,
            greens=["current_bc_idx", "method"],
            reds=["stack_ptr", "hybrid", "frame", "stack"],
            get_printable_location=gpl_fn,
            should_unroll_one_iteration=lambda current_bc_idx, method: True,
            can_never_inline=_shaping_can_never_inline,
        )
    else:
        jitdriver = jit.JitDriver(
            name=driver_name,
            greens=["current_bc_idx", "method"],
            reds=["stack_ptr", "frame", "stack"],
            get_printable_location=gpl_fn,
            should_unroll_one_iteration=lambda current_bc_idx, method: True,
        )

    @jit.unroll_safe
    def interp(method, frame, max_stack_size, current_bc_idx=0, stack=None, stack_ptr=-1, hybrid=False, dummy=False):
        from som.vm.current import current_universe
        from som.vmobjects.method_bc import BcAbstractMethod

        if dummy:
            return

        if policy != P_COMMITTED:
            assert isinstance(method, BcAbstractMethod)

        if not stack:
            stack_ptr = -1
            stack = [None] * max_stack_size
            if policy == P_HYBRID and is_tier3() and not we_are_jitted():
                method.shaping_invocations += 1

        while True:
            if policy == P_HYBRID:
                jitdriver.jit_merge_point(
                    current_bc_idx=current_bc_idx,
                    hybrid=hybrid,
                    stack_ptr=stack_ptr,
                    method=method,
                    frame=frame,
                    stack=stack,
                )
                hybrid = promote(hybrid)
                if current_bc_idx == 0 and is_tier3() and not we_are_jitted():
                    if _shaping_cfg.enabled and (_shaping_warmup.on or _shaping_warmup.purge_pending):
                        _shaping_warmup_check()
                if hybrid == MODE_INLINER:
                    at = method.adaptive_tier
                    if at == 3:
                        hybrid = MODE_INLINE
                    elif at == 4:
                        hybrid = MODE_HYBRID
            else:
                jitdriver.jit_merge_point(
                    current_bc_idx=current_bc_idx,
                    stack_ptr=stack_ptr,
                    method=method,
                    frame=frame,
                    stack=stack,
                )
                if policy == P_WARM:
                    at = method.adaptive_tier
                    if at == 3:
                        return interpret_committed(
                            method, frame, len(stack), current_bc_idx, stack, stack_ptr
                        )
                    if at == 4:
                        return interpret_tier3(
                            method, frame, len(stack), current_bc_idx, stack, stack_ptr,
                            MODE_HYBRID,
                        )

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

                if policy == P_HYBRID and hybrid and promote(method._mega[current_bc_idx]):
                    stack[stack_ptr] = _residual_mega_send_1(
                        method, current_bc_idx, signature, receiver, current_universe
                    )
                    current_bc_idx = next_bc_idx
                    continue

                layout = receiver.get_object_layout(current_universe)
                if not we_are_jitted() and (
                    (policy == P_HYBRID and (hybrid or _profiling_active_for(method)))
                    or policy == P_WARM
                ):
                    l1 = method.get_inline_cache_layout(current_bc_idx)
                    l2 = method.get_inline_cache_layout(current_bc_idx + 1)
                    if l1 is not None and l2 is not None and layout is not l1 and layout is not l2:
                        _profile_layout(method, current_bc_idx, signature, layout)
                invokable = _lookup(layout, signature, method, current_bc_idx)
                if invokable is not None:
                    if policy == P_HYBRID:
                        stack[stack_ptr] = _dispatch_send_1(method, invokable, receiver, hybrid)
                    elif policy == P_WARM:
                        stack[stack_ptr] = _residual_send_1(method, invokable, receiver)
                    else:
                        stack[stack_ptr] = invokable.invoke_1_tier3(receiver, MODE_INLINE)
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

                if policy == P_HYBRID and hybrid and promote(method._mega[current_bc_idx]):
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
                if not we_are_jitted() and (
                    (policy == P_HYBRID and (hybrid or _profiling_active_for(method)))
                    or policy == P_WARM
                ):
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
                    if policy == P_HYBRID:
                        stack[stack_ptr] = _dispatch_send_2(
                            method, current_bc_idx, signature, invokable, receiver, arg, hybrid
                        )
                    elif policy == P_WARM:
                        kind = site_kind(method, current_bc_idx, signature)
                        if not we_are_jitted() and (
                            is_arith_kind(kind) or is_predicate_kind(kind)
                        ):
                            _profile(method, current_bc_idx, kind, receiver, arg)
                        stack[stack_ptr] = _residual_2(method, invokable, kind, receiver, arg)
                    else:
                        stack[stack_ptr] = invokable.invoke_2_tier3(receiver, arg, MODE_INLINE)
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
                    if policy == P_HYBRID:
                        stack[stack_ptr] = _dispatch_send_3(
                            method, invokable, receiver, arg1, arg2, hybrid
                        )
                    elif policy == P_WARM:
                        stack[stack_ptr] = _residual_send_3(
                            method, invokable, receiver, arg1, arg2
                        )
                    else:
                        stack[stack_ptr] = invokable.invoke_3_tier3(
                            receiver, arg1, arg2, MODE_INLINE
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
                    if policy == P_HYBRID:
                        stack[stack_ptr] = _dispatch_send_4(
                            method, invokable, receiver, arg1, arg2, arg3, hybrid
                        )
                    elif policy == P_WARM:
                        stack[stack_ptr] = _residual_send_4(
                            method, invokable, receiver, arg1, arg2, arg3
                        )
                    else:
                        stack[stack_ptr] = invokable.invoke_4_tier3(
                            receiver, arg1, arg2, arg3, MODE_INLINE
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
                    if policy == P_HYBRID:
                        stack_ptr = _dispatch_send_n(method, invokable, stack, stack_ptr, hybrid)
                    elif policy == P_WARM:
                        stack_ptr = _residual_send_n(method, invokable, stack, stack_ptr)
                    else:
                        stack_ptr = invokable.invoke_n_tier3(stack, stack_ptr, MODE_INLINE)
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
                if policy == P_HYBRID:
                    if hybrid != MODE_INLINER:
                        jitdriver.can_enter_jit(
                            current_bc_idx=next_bc_idx,
                            hybrid=hybrid,
                            stack_ptr=stack_ptr,
                            method=method,
                            frame=frame,
                            stack=stack,
                        )
                else:
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
                if policy == P_HYBRID:
                    if hybrid != MODE_INLINER:
                        jitdriver.can_enter_jit(
                            current_bc_idx=next_bc_idx,
                            hybrid=hybrid,
                            stack_ptr=stack_ptr,
                            method=method,
                            frame=frame,
                            stack=stack,
                        )
                else:
                    jitdriver.can_enter_jit(
                        current_bc_idx=next_bc_idx,
                        stack_ptr=stack_ptr,
                        method=method,
                        frame=frame,
                        stack=stack,
                    )

            elif bytecode == Bytecodes.q_super_send_1:
                invokable = method.get_inline_cache_invokable(current_bc_idx)
                if policy == P_WARM:
                    stack[stack_ptr] = _residual_send_1(method, invokable, stack[stack_ptr])
                elif policy == P_HYBRID:
                    stack[stack_ptr] = invokable.invoke_1_tier3(stack[stack_ptr], hybrid)
                else:
                    stack[stack_ptr] = invokable.invoke_1_tier3(stack[stack_ptr], MODE_INLINE)

            elif bytecode == Bytecodes.q_super_send_2:
                invokable = method.get_inline_cache_invokable(current_bc_idx)
                arg = stack[stack_ptr]
                if we_are_jitted():
                    stack[stack_ptr] = None
                stack_ptr -= 1
                if policy == P_WARM:
                    stack[stack_ptr] = _residual_send_2(method, invokable, stack[stack_ptr], arg)
                elif policy == P_HYBRID:
                    stack[stack_ptr] = invokable.invoke_2_tier3(stack[stack_ptr], arg, hybrid)
                else:
                    stack[stack_ptr] = invokable.invoke_2_tier3(stack[stack_ptr], arg, MODE_INLINE)

            elif bytecode == Bytecodes.q_super_send_3:
                invokable = method.get_inline_cache_invokable(current_bc_idx)
                arg2 = stack[stack_ptr]
                arg1 = stack[stack_ptr - 1]
                if we_are_jitted():
                    stack[stack_ptr] = None
                    stack[stack_ptr - 1] = None
                stack_ptr -= 2
                if policy == P_WARM:
                    stack[stack_ptr] = _residual_send_3(method, invokable, stack[stack_ptr], arg1, arg2)
                elif policy == P_HYBRID:
                    stack[stack_ptr] = invokable.invoke_3_tier3(stack[stack_ptr], arg1, arg2, hybrid)
                else:
                    stack[stack_ptr] = invokable.invoke_3_tier3(stack[stack_ptr], arg1, arg2, MODE_INLINE)

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
                if policy == P_WARM:
                    stack[stack_ptr] = _residual_send_4(method, invokable, stack[stack_ptr], arg1, arg2, arg3)
                elif policy == P_HYBRID:
                    stack[stack_ptr] = invokable.invoke_4_tier3(stack[stack_ptr], arg1, arg2, arg3, hybrid)
                else:
                    stack[stack_ptr] = invokable.invoke_4_tier3(stack[stack_ptr], arg1, arg2, arg3, MODE_INLINE)

            elif bytecode == Bytecodes.q_super_send_n:
                invokable = method.get_inline_cache_invokable(current_bc_idx)
                if policy == P_WARM:
                    stack_ptr = _residual_send_n(method, invokable, stack, stack_ptr)
                else:
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

    interp.__name__ = "interpret_" + driver_name
    return interp, jitdriver


# === Printable-Location Functions ===
def get_printable_location_tier3(bytecode_index, method):
    from som.vmobjects.method_bc import BcAbstractMethod

    assert isinstance(method, BcAbstractMethod)
    bc = method.get_bytecode(bytecode_index)
    return "%s @ %d in %s" % (
        bytecode_as_str(bc),
        bytecode_index,
        method.merge_point_string(),
    )


def get_printable_location_inliner(bytecode_index, method):
    from som.vmobjects.method_bc import BcAbstractMethod
    assert isinstance(method, BcAbstractMethod)
    bc = method.get_bytecode(bytecode_index)
    return "warm: %s @ %d in %s" % (bytecode_as_str(bc), bytecode_index, method.merge_point_string())


def get_printable_location_committed(bytecode_index, method):
    from som.vmobjects.method_bc import BcAbstractMethod
    assert isinstance(method, BcAbstractMethod)
    bc = method.get_bytecode(bytecode_index)
    return "t3: %s @ %d in %s" % (bytecode_as_str(bc), bytecode_index, method.merge_point_string())


# === Interpreter Instantiations ===
interpret_tier3, jitdriver = make_interp(P_HYBRID, get_printable_location_tier3, "Interpreter")
interpret_committed, committed_jitdriver = make_interp(P_COMMITTED, get_printable_location_committed, "Committed")
interpret_inliner, inliner_jitdriver = make_interp(P_WARM, get_printable_location_inliner, "Inliner")


def _env_int(name, default):
    import os
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def inliner_configure():
    threshold = _env_int("SOM_ADAPTIVE_WARM_THRESHOLD", 131)
    eagerness = _env_int("SOM_ADAPTIVE_WARM_EAGERNESS", 32)
    jit.set_param(inliner_jitdriver, "threshold", threshold)
    jit.set_param(inliner_jitdriver, "function_threshold", threshold * 2)
    jit.set_param(inliner_jitdriver, "trace_eagerness", eagerness)
