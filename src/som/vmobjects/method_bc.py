from __future__ import absolute_import

from rlib import jit
from rlib.min_heap_queue import heappush, heappop, HeapEntry
from som.compiler.bc.bytecode_generator import (
    emit1,
    emit3,
    emit_push_constant,
    emit_return_local,
    emit_return_non_local,
    emit_send,
    emit_super_send,
    emit_push_global,
    emit_push_block,
    emit_push_field_with_index,
    emit_pop_field_with_index,
    emit3_with_dummy,
    emit_push_local,
    emit_pop_local,
    emit_nil_local,
)
from som.interpreter.ast.frame import (
    get_inner_as_context,
    mark_as_no_longer_on_stack,
    FRAME_AND_INNER_RCVR_IDX,
    create_frame_1,
    create_frame_2,
)
from som.interpreter.bc.bytecodes import (
    Bytecodes,
    bytecode_length,
    RUN_TIME_ONLY_BYTECODES,
    bytecode_as_str,
    NOT_EXPECTED_IN_BLOCK_BYTECODES,
)

from som.interpreter.bc.frame import (
    create_frame,
    stack_pop_old_arguments_and_push_result,
    create_frame_3,
    create_frame_4
)
from som.interpreter.bc.interpreter import interpret
from som.interpreter.bc.interpreter_tier3 import interpret_tier3
from som.interpreter.bc.interpreter_inliner import interpret_inliner
from som.interpreter.bc.interpreter_lean3 import interpret_lean3
from som.interpreter.control_flow import ReturnException
from som.tier_type import is_tier4, MODE_INLINE, MODE_INLINER
from som.vmobjects.abstract_object import AbstractObject
from som.vmobjects.method import AbstractMethod


class BcAbstractMethod(AbstractMethod):

    _immutable_fields_ = [
        "_bytecodes?[*]",
        #"_literals[*]",
        "_inline_cache_layout",
        "_inline_cache_invokable",
        "_number_of_locals",
        "_maximum_number_of_stack_elements",
        "_number_of_arguments",
        "_arg_inner_access[*]",
        "_size_frame",
        "_size_inner",
        "_inlined_loops[*]",
        # Quasi-immutable per-site residualization decision; whole-array replace via
        # adaptive._t4_set_poly so the JIT-folded _poly[site] never goes stale.
        "_poly?[*]",
        # Quasi-immutable per-site megamorphic-dispatch decision (0 normal, 1
        # residualise). Whole-array replace via adaptive._set_mega.
        "_mega?[*]",
        # Quasi-immutable committed tier (0 undecided, 3 inline, 4 hybrid), written
        # once by the controller; lets invoke_*_tier4 fold the post-commit fast path.
        "adaptive_tier?",
    ]

    def __init__(
        self,
        literals,
        num_locals,
        max_stack_elements,
        num_bytecodes,
        signature,
        arg_inner_access,
        size_frame,
        size_inner,
        lexical_scope,
        inlined_loops,
    ):
        AbstractMethod.__init__(self, signature, lexical_scope)

        # Set the number of bytecodes in this method
        self._bytecodes = ["\x00"] * num_bytecodes
        self._inline_cache_layout = [None] * num_bytecodes
        self._inline_cache_invokable = [None] * num_bytecodes

        self._receiver_types = [None] * num_bytecodes
        self._selectors = [None] * num_bytecodes
        self._invokable = [None] * num_bytecodes

        self._counts = [0] * num_bytecodes

        # Adaptive (tier-4) profiling/controller state. All mutable; gathered only
        # off-trace (see som.interpreter.bc.adaptive). Per-send-site arrays, sized
        # like _counts. cnt_a counts the dominant operand shape, cnt_b every other.
        self._cnt_a = [0] * num_bytecodes
        self._cnt_b = [0] * num_bytecodes
        # Predicate sites: previous shape (0/1/2) and interpreted shape-switch count.
        self._cmp_last = [0] * num_bytecodes
        self._cmp_switches = [0] * num_bytecodes
        # Deopt-rate re-decision bookkeeping.
        self._inl_runs = [0] * num_bytecodes
        self._bails = [0] * num_bytecodes
        self._redecided = [0] * num_bytecodes
        # Cached site classification (0 unclassified, 1 not-profiled, 2 arith, 3 pred).
        self._site_kind = [0] * num_bytecodes
        # Quasi-immutable residualization decision, via adaptive._t4_set_poly.
        self._poly = [0] * num_bytecodes
        # Megamorphic-dispatch decision (quasi-immutable, via adaptive._set_mega): a
        # site residualises once its distinct receiver-class count crosses mega_floor.
        # _mega_miss caches that count; _mega_seen is the per-site layout set.
        self._mega = [0] * num_bytecodes
        self._mega_miss = [0] * num_bytecodes
        self._mega_seen = None
        self._mega_prev_distinct = 0    # high-water distinct-class count at the last probe
        # Per-mega-site PIC (layout -> invokable), turning each residual dispatch into
        # one identity-keyed hit. Touched only behind the residual barrier; lazily a
        # dict per site.
        self._mega_cache = [None] * num_bytecodes

        # Method-level controller state (mutable).
        self.adaptive_tier = 0          # 0 undecided, 2 warm (inliner), else committed (3/4)
        self.adaptive_invocations = 0
        # Warm-phase promotion counters: warm activations, and residual sends executed
        # from warm code (bumped behind the residual barrier so a single-activation hot
        # loop still reaches the threshold mid-loop).
        self.warm_invocations = 0
        self.warm_ops = 0
        self.ab_round = 0               # A/B round counter (even=tier3, odd=tier4)
        self.t3_min = 0.0               # best-of-min timing for tier 3 (inline)
        self.t4_min = 0.0               # best-of-min timing for tier 4 (hybrid)
        self.t3_n = 0                   # number of timed tier-3 samples collected
        self.t4_n = 0                   # number of timed tier-4 samples collected
        # ---------------------------------------------------------------------

        self._literals = literals

        self._number_of_arguments = signature.get_number_of_signature_arguments()
        self._number_of_locals = num_locals
        self._maximum_number_of_stack_elements = max_stack_elements + 2

        self._arg_inner_access = arg_inner_access
        self._size_frame = size_frame
        self._size_inner = size_inner

        self._inlined_loops = inlined_loops
        self._signature = signature

    def get_number_of_locals(self):
        return self._number_of_locals

    @jit.elidable_promote("all")
    def get_maximum_number_of_stack_elements(self):
        # Compute the maximum number of stack locations (including
        # extra buffer to support doesNotUnderstand) and set the
        # number of indexable fields accordingly
        return self._maximum_number_of_stack_elements

    def set_holder(self, value):
        self._holder = value

        # Make sure all nested invokables have the same holder
        for obj in self._literals:
            assert isinstance(obj, AbstractObject)
            if obj.is_invokable():
                obj.set_holder(value)

    @jit.elidable
    def get_arg_inner_access(self):
        return self._arg_inner_access

    @jit.elidable
    def get_size_frame(self):
        return self._size_frame

    @jit.elidable
    def get_size_inner(self):
        return self._size_inner

    # XXX this means that the JIT doesn't see changes to the constants
    @jit.elidable_promote("all")
    def get_constant(self, bytecode_index):
        # Get the constant associated to a given bytecode index
        return self._literals[self.get_bytecode(bytecode_index + 1)]

    @jit.elidable_promote("all")
    def get_number_of_arguments(self):
        return self._number_of_arguments

    @jit.elidable_promote("all")
    def get_number_of_signature_arguments(self):
        return self._number_of_arguments

    def get_number_of_bytecodes(self):
        # Get the number of bytecodes in this method
        return len(self._bytecodes)

    @jit.elidable_promote("all")
    def get_bytecode(self, index):
        # Get the bytecode at the given index
        assert 0 <= index < len(self._bytecodes)
        return ord(self._bytecodes[index])

    def get_bytecodes(self):
        """For testing purposes only"""
        return [ord(b) for b in self._bytecodes]

    def set_bytecode(self, index, value):
        # Set the bytecode at the given index to the given value
        assert (
            0 <= value <= 255
        ), "Expected bytecode in the range of [0..255], but was: " + str(value)
        self._bytecodes[index] = chr(value)

    @jit.elidable
    def get_inline_cache_layout(self, bytecode_index):
        assert 0 <= bytecode_index < len(self._inline_cache_layout)
        return self._inline_cache_layout[bytecode_index]

    @jit.elidable
    def get_inline_cache_invokable(self, bytecode_index):
        assert 0 <= bytecode_index < len(self._inline_cache_invokable)
        return self._inline_cache_invokable[bytecode_index]

    def set_inline_cache(self, bytecode_index, layout, invokable):
        self._inline_cache_layout[bytecode_index] = layout
        self._inline_cache_invokable[bytecode_index] = invokable

    def mega_cache_lookup(self, bytecode_index, layout):
        """Per-site megamorphic PIC hit (layout -> invokable), or None. Called only behind
        the @jit.dont_look_inside residual barrier, so the dict access never enters a trace."""
        cache = self._mega_cache[bytecode_index]
        if cache is None:
            return None
        return cache.get(layout, None)

    def mega_cache_store(self, bytecode_index, layout, invokable):
        """Record a resolved (layout -> invokable) at a megamorphic site. Lazily allocates the
        per-site dict on first dispatch. Stale entries (layouts that became non-latest) simply
        stop matching, so no eviction is needed."""
        cache = self._mega_cache[bytecode_index]
        if cache is None:
            cache = {}
            self._mega_cache[bytecode_index] = cache
        cache[layout] = invokable

    def patch_variable_access(self, bytecode_index):
        bc = self.get_bytecode(bytecode_index)
        idx = self.get_bytecode(bytecode_index + 1)
        ctx_level = self.get_bytecode(bytecode_index + 2)

        if bc == Bytecodes.push_argument:
            var = self._lexical_scope.get_argument(idx, ctx_level)
            self.set_bytecode(bytecode_index, var.get_push_bytecode(ctx_level))
        elif bc == Bytecodes.pop_argument:
            var = self._lexical_scope.get_argument(idx, ctx_level)
            self.set_bytecode(bytecode_index, var.get_pop_bytecode(ctx_level))
        elif bc == Bytecodes.push_local:
            var = self._lexical_scope.get_local(idx, ctx_level)
            self.set_bytecode(bytecode_index, var.get_push_bytecode(ctx_level))
        elif bc == Bytecodes.pop_local:
            var = self._lexical_scope.get_local(idx, ctx_level)
            self.set_bytecode(bytecode_index, var.get_pop_bytecode(ctx_level))
        elif bc == Bytecodes.nil_local:
            var = self._lexical_scope.get_local(idx, 0)
            if var.is_accessed_out_of_context():
                bytecode = Bytecodes.nil_inner
            else:
                bytecode = Bytecodes.nil_frame
            self.set_bytecode(bytecode_index, bytecode)
        else:
            raise Exception("Unsupported bytecode?")
        assert (
            FRAME_AND_INNER_RCVR_IDX <= var.access_idx <= 255
        ), "Expected variable access index to be in valid range, but was " + str(
            var.access_idx
        )
        self.set_bytecode(bytecode_index + 1, var.access_idx)

    def set_receiver_type(self, bytecode_index, receiver_type):
        self._receiver_types[bytecode_index] = receiver_type

    @jit.elidable_promote("all")
    def get_receiver_type(self, bytecode_index):
        assert 0 <= bytecode_index < len(self._receiver_types)
        return self._receiver_types[bytecode_index]

    def set_invokable(self, bytecode_index, invokable):
        self._invokable[bytecode_index] = invokable

    @jit.elidable
    def get_invokable(self, bytecode_index):
        assert 0 <= bytecode_index < len(self._invokable)
        return self._invokable[bytecode_index]

    @jit.elidable
    def has_invokable(self, bytecode_index):
        return self._invokable[bytecode_index] is not None

    def get_count(self, bytecode_index):
        assert 0 <= bytecode_index < len(self._counts)
        return self._counts

    def incr_count(self, bytecode_index):
        assert 0 <= bytecode_index < len(self._counts)
        self._counts[bytecode_index] += 1


def _interp_with_nlr(method, new_frame, max_stack_size):
    inner = get_inner_as_context(new_frame)

    try:
        result = interpret(method, new_frame, max_stack_size)
        mark_as_no_longer_on_stack(inner)
        return result
    except ReturnException as e:
        mark_as_no_longer_on_stack(inner)
        if e.has_reached_target(inner):
            return e.get_result()
        raise e


def _interpret_tier3_mode(method, new_frame, max_stack_size, hybrid):
    # In the tier-4 binary route each activation to its dedicated graph: warm ->
    # the lean inliner interpreter, committed tier 3 -> the lean tier-3 interpreter
    # (adaptive_tier is quasi-immutable, so the read folds in traces); hybrid and
    # uncommitted/profiling activations run the shared interpreter. Elsewhere
    # is_tier4() folds False and this is just the old interpret_tier3 call.
    if is_tier4():
        if hybrid == MODE_INLINER:
            return interpret_inliner(method, new_frame, max_stack_size)
        if hybrid == MODE_INLINE:
            at = method.adaptive_tier
            if at == 2:
                # Warm callee on the inline-invoke path (sends from committed
                # code). Without counting here a method hot only through this
                # path never reaches promote_inv and stays warm forever -- on
                # DeltaBlue 7 collection blocks stuck warm cost 23% steady.
                from som.interpreter.bc.adaptive import warm_callee_invocation

                warm_callee_invocation(method)
                if method.adaptive_tier == 2:
                    return interpret_inliner(method, new_frame, max_stack_size)
                at = method.adaptive_tier
            if at != 4:
                # Committed tier 3 AND undecided (0) both run the lean graph:
                # methods reached only via sends never enter the controller, so
                # without this they trace forever in the bloated shared graph
                # (DeltaBlue's hottest loops, e.g. Planner>>makePlan:, compiled
                # there). Profiling activations use _run_profiling instead.
                return interpret_lean3(method, new_frame, max_stack_size)
    return interpret_tier3(method, new_frame, max_stack_size, hybrid=hybrid)


def _interp_with_nlr_tier3(method, new_frame, max_stack_size, hybrid=False):
    inner = get_inner_as_context(new_frame)

    try:
        result = _interpret_tier3_mode(method, new_frame, max_stack_size, hybrid)
        mark_as_no_longer_on_stack(inner)
        return result
    except ReturnException as e:
        mark_as_no_longer_on_stack(inner)
        if e.has_reached_target(inner):
            return e.get_result()
        raise e


def _adaptive_with_nlr(method, new_frame, max_stack_size):
    # Non-local-return wrapper around the adaptive controller, for the DIRECT
    # invoke_*_tier4 entry of block methods (loop drivers). The dispatcher path
    # (interpret() -> _adaptive_tier4) is already wrapped by _interp_with_nlr, so
    # the controller itself must NOT wrap (see BcMethod._run_tier3).
    from som.interpreter.bc.adaptive import _adaptive_tier4
    inner = get_inner_as_context(new_frame)

    try:
        result = _adaptive_tier4(method, new_frame, max_stack_size)
        mark_as_no_longer_on_stack(inner)
        return result
    except ReturnException as e:
        mark_as_no_longer_on_stack(inner)
        if e.has_reached_target(inner):
            return e.get_result()
        raise e


class BcMethod(BcAbstractMethod):
    def invoke_1(self, rcvr, ctx=None):
        new_frame = create_frame_1(rcvr, self._size_frame, self._size_inner)
        return interpret(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_1_tier3(self, rcvr, hybrid=False, ctx=None):
        new_frame = create_frame_1(rcvr, self._size_frame, self._size_inner)
        return _interpret_tier3_mode(
            self, new_frame, self._maximum_number_of_stack_elements, hybrid
        )

    def invoke_1_tier4(self, rcvr, ctx=None):
        at = self.adaptive_tier
        if at == 3 or at == 4:
            # Committed: skip the controller so the JIT can inline the activation into
            # a loop-driver trace. Warm (2) and undecided (0) go through the controller.
            return self.invoke_1_tier3(rcvr, at == 4)
        from som.interpreter.bc.adaptive import _adaptive_tier4
        new_frame = create_frame_1(rcvr, self._size_frame, self._size_inner)
        return _adaptive_tier4(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_2(self, rcvr, arg1, ctx=None):
        new_frame = create_frame_2(
            rcvr,
            arg1,
            self._arg_inner_access[0],
            self._size_frame,
            self._size_inner,
        )
        return interpret(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_2_tier3(self, rcvr, arg1, hybrid=False, ctx=None):
        new_frame = create_frame_2(
            rcvr,
            arg1,
            self._arg_inner_access[0],
            self._size_frame,
            self._size_inner,
        )
        return _interpret_tier3_mode(
            self, new_frame, self._maximum_number_of_stack_elements, hybrid
        )

    def invoke_2_tier4(self, rcvr, arg1, ctx=None):
        at = self.adaptive_tier
        if at == 3 or at == 4:
            return self.invoke_2_tier3(rcvr, arg1, at == 4)
        from som.interpreter.bc.adaptive import _adaptive_tier4
        new_frame = create_frame_2(
            rcvr,
            arg1,
            self._arg_inner_access[0],
            self._size_frame,
            self._size_inner,
        )
        return _adaptive_tier4(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_3(self, rcvr, arg1, arg2, ctx=None):
        new_frame = create_frame_3(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            rcvr,
            arg1,
            arg2,
        )
        return interpret(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_3_tier3(self, rcvr, arg1, arg2, hybrid=False, ctx=None):
        new_frame = create_frame_3(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            rcvr,
            arg1,
            arg2,
        )
        return _interpret_tier3_mode(
            self, new_frame, self._maximum_number_of_stack_elements, hybrid
        )

    def invoke_3_tier4(self, rcvr, arg1, arg2, ctx=None):
        at = self.adaptive_tier
        if at == 3 or at == 4:
            return self.invoke_3_tier3(rcvr, arg1, arg2, at == 4)
        from som.interpreter.bc.adaptive import _adaptive_tier4
        new_frame = create_frame_3(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            rcvr,
            arg1,
            arg2,
        )
        return _adaptive_tier4(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_4(self, rcvr, arg1, arg2, arg3, ctx=None):
        new_frame = create_frame_4(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            rcvr,
            arg1,
            arg2,
            arg3
        )
        return interpret(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_4_tier3(self, rcvr, arg1, arg2, arg3, hybrid=False, ctx=None):
        new_frame = create_frame_4(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            rcvr,
            arg1,
            arg2,
            arg3
        )
        return _interpret_tier3_mode(
            self, new_frame, self._maximum_number_of_stack_elements, hybrid
        )

    def invoke_4_tier4(self, rcvr, arg1, arg2, arg3, ctx=None):
        at = self.adaptive_tier
        if at == 3 or at == 4:
            return self.invoke_4_tier3(rcvr, arg1, arg2, arg3, at == 4)
        from som.interpreter.bc.adaptive import _adaptive_tier4
        new_frame = create_frame_4(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            rcvr,
            arg1,
            arg2,
            arg3
        )
        return _adaptive_tier4(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_n(self, stack, stack_ptr, ctx=None):
        new_frame = create_frame(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            stack,
            stack_ptr,
            self._number_of_arguments,
        )
        result = interpret(self, new_frame, self._maximum_number_of_stack_elements)
        return stack_pop_old_arguments_and_push_result(
            stack, stack_ptr, self._number_of_arguments, result
        )

    def invoke_n_tier3(self, stack, stack_ptr, hybrid=False, ctx=None):
        new_frame = create_frame(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            stack,
            stack_ptr,
            self._number_of_arguments,
        )
        result = _interpret_tier3_mode(
            self, new_frame, self._maximum_number_of_stack_elements, hybrid
        )
        return stack_pop_old_arguments_and_push_result(
            stack, stack_ptr, self._number_of_arguments, result
        )

    def invoke_n_tier4(self, stack, stack_ptr, ctx=None):
        at = self.adaptive_tier
        if at == 3 or at == 4:
            return self.invoke_n_tier3(stack, stack_ptr, at == 4)
        from som.interpreter.bc.adaptive import _adaptive_tier4
        new_frame = create_frame(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            stack,
            stack_ptr,
            self._number_of_arguments,
        )
        result = _adaptive_tier4(
            self, new_frame, self._maximum_number_of_stack_elements
        )
        return stack_pop_old_arguments_and_push_result(
            stack, stack_ptr, self._number_of_arguments, result
        )

    def _run_tier3(self, frame, max_stack_size, hybrid):
        # Run one activation in the given execution mode (tier_type.MODE_INLINE /
        # MODE_HYBRID / MODE_INLINER; the historical bools embed as 0/1).
        # Called by the adaptive controller; overridden by BcMethodNLR to add
        # non-local-return handling.
        return _interpret_tier3_mode(self, frame, max_stack_size, hybrid)

    def _run_profiling(self, frame, max_stack_size):
        # Profile-gate activation: must run the SHARED interpreter, whose send
        # handlers carry the operand/layout profiling hooks (the lean graphs
        # deliberately have none). Non-local returns are handled by the
        # controller's outer wrapper, as for _run_tier3.
        return interpret_tier3(self, frame, max_stack_size, hybrid=False)

    def merge_scope_into(self, mgenc):
        mgenc.merge_into_scope(self._lexical_scope)

    def inline(self, mgenc, merge_scope=True):
        if merge_scope:
            mgenc.merge_into_scope(self._lexical_scope)
        self._inline_into(mgenc)

    def _create_back_jump_heap(self):
        heap = []
        if self._inlined_loops:
            for loop in self._inlined_loops:
                heappush(heap, _BackJump(loop.loop_begin_idx, loop.backward_jump_idx))
        return heap

    def _inline_into(self, mgenc):
        jumps = []  # a sorted list/priority queue. sorted by original_target index
        back_jumps = self._create_back_jump_heap()
        back_jumps_to_patch = []

        i = 0
        while i < len(self._bytecodes):
            while back_jumps and back_jumps[0].address <= i:
                jump = heappop(back_jumps)
                assert (
                    jump.address == i
                ), "we use the less or equal, but actually expect it to be strictly equal"
                heappush(
                    back_jumps_to_patch,
                    _BackJumpPatch(
                        jump.backward_jump_idx, mgenc.offset_of_next_instruction()
                    ),
                )

            while jumps and jumps[0].address <= i:
                jump = heappop(jumps)
                assert (
                    jump.address == i
                ), "we use the less or equal, but actually expect it to be strictly equal"
                mgenc.patch_jump_offset_to_point_to_next_instruction(jump.idx, None)

            bytecode = self.get_bytecode(i)
            bc_length = bytecode_length(bytecode)

            if (
                bytecode == Bytecodes.halt
                or bytecode == Bytecodes.dup
                or bytecode == Bytecodes.dup_second
            ):
                emit1(mgenc, bytecode)

            elif (
                bytecode == Bytecodes.push_field
                or bytecode == Bytecodes.pop_field
                or bytecode == Bytecodes.push_argument
                or bytecode == Bytecodes.pop_argument
            ):
                idx = self.get_bytecode(i + 1)
                ctx_level = self.get_bytecode(i + 2)

                if ctx_level == 0:
                    assert (
                        bytecode == Bytecodes.push_argument
                        or bytecode == Bytecodes.pop_argument
                    ), (
                        "This should really be push or pop argument."
                        + " everything else should have a ctx_level > 0"
                    )

                    arg = self._lexical_scope.get_argument(idx, 0)
                    idx = mgenc.get_inlined_local_idx(arg, 0)
                    if bytecode == Bytecodes.push_argument:
                        emit_push_local(mgenc, idx, 0)
                    else:
                        emit_pop_local(mgenc, idx, 0)
                elif bytecode == Bytecodes.push_field:
                    emit_push_field_with_index(mgenc, idx, ctx_level - 1)
                elif bytecode == Bytecodes.pop_field:
                    emit_pop_field_with_index(mgenc, idx, ctx_level - 1)
                else:
                    emit3(mgenc, bytecode, idx, ctx_level - 1)

            elif bytecode == Bytecodes.push_local or bytecode == Bytecodes.pop_local:
                idx = self.get_bytecode(i + 1)
                ctx_level = self.get_bytecode(i + 2)
                if ctx_level == 0:
                    # these have been inlined into the outer context already
                    # so, we need to look up the right one
                    var = self._lexical_scope.get_local(idx, 0)
                    idx = mgenc.get_inlined_local_idx(var, 0)
                else:
                    ctx_level -= 1
                emit3(mgenc, bytecode, idx, ctx_level)

            elif bytecode == Bytecodes.nil_local:
                idx = self.get_bytecode(i + 1)
                var = self._lexical_scope.get_local(idx, 0)
                idx = mgenc.get_inlined_local_idx(var, 0)
                emit_nil_local(mgenc, idx)

            elif bytecode == Bytecodes.push_block:
                literal_idx = self.get_bytecode(i + 1)
                block_method = self._literals[literal_idx]
                block_method.adapt_after_outer_inlined(1, mgenc)
                emit_push_block(mgenc, block_method, True)

            elif bytecode == Bytecodes.push_block_no_ctx:
                literal_idx = self.get_bytecode(i + 1)
                block_method = self._literals[literal_idx]
                emit_push_block(mgenc, block_method, False)

            elif bytecode == Bytecodes.push_constant:
                literal_idx = self.get_bytecode(i + 1)
                literal = self._literals[literal_idx]
                emit_push_constant(mgenc, literal)

            elif (
                bytecode == Bytecodes.push_constant_0
                or bytecode == Bytecodes.push_constant_1
                or bytecode == Bytecodes.push_constant_2
            ):
                literal_idx = bytecode - Bytecodes.push_constant_0
                literal = self._literals[literal_idx]
                emit_push_constant(mgenc, literal)

            elif (
                bytecode == Bytecodes.push_0
                or bytecode == Bytecodes.push_1
                or bytecode == Bytecodes.push_nil
                or bytecode == Bytecodes.pop
                or bytecode == Bytecodes.inc
                or bytecode == Bytecodes.dec
            ):
                emit1(mgenc, bytecode)

            elif bytecode == Bytecodes.push_global:
                literal_idx = self.get_bytecode(i + 1)
                sym = self._literals[literal_idx]
                emit_push_global(mgenc, sym)

            elif (
                bytecode == Bytecodes.send_1
                or bytecode == Bytecodes.send_2
                or bytecode == Bytecodes.send_3
                or bytecode == Bytecodes.send_4
                or bytecode == Bytecodes.send_n
            ):
                literal_idx = self.get_bytecode(i + 1)
                sym = self._literals[literal_idx]
                emit_send(mgenc, sym)

            elif bytecode == Bytecodes.super_send:
                literal_idx = self.get_bytecode(i + 1)
                sym = self._literals[literal_idx]
                emit_super_send(mgenc, sym)

            elif bytecode == Bytecodes.return_local:
                # NO OP, doesn't need to be translated
                pass

            elif bytecode == Bytecodes.return_non_local:
                new_ctx_level = self.get_bytecode(i + 1) - 1
                if new_ctx_level == 0:
                    emit_return_local(mgenc)
                else:
                    assert new_ctx_level == mgenc.get_max_context_level()
                    emit_return_non_local(mgenc)

            elif (
                bytecode == Bytecodes.jump
                or bytecode == Bytecodes.jump_on_true_top_nil
                or bytecode == Bytecodes.jump_on_true_pop
                or bytecode == Bytecodes.jump_on_false_top_nil
                or bytecode == Bytecodes.jump_on_false_pop
                or bytecode == Bytecodes.jump_if_greater
                or bytecode == Bytecodes.jump2
                or bytecode == Bytecodes.jump2_on_true_top_nil
                or bytecode == Bytecodes.jump2_on_true_pop
                or bytecode == Bytecodes.jump2_on_false_top_nil
                or bytecode == Bytecodes.jump2_on_false_pop
                or bytecode == Bytecodes.jump2_if_greater
            ):
                # emit the jump, but instead of the offset, emit a dummy
                idx = emit3_with_dummy(mgenc, bytecode)

                offset1 = self.get_bytecode(i + 1)
                offset2 = self.get_bytecode(i + 2)
                heappush(jumps, _Jump(i + (offset1 + (offset2 << 8)), bytecode, idx))

            elif (
                bytecode == Bytecodes.jump_backward
                or bytecode == Bytecodes.jump2_backward
            ):
                jump = heappop(back_jumps_to_patch)
                assert (
                    jump.address == i
                ), "the jump should match with the jump instructions"
                mgenc.emit_backwards_jump_offset_to_target(jump.loop_begin_idx, None)

            elif bytecode in RUN_TIME_ONLY_BYTECODES:
                raise Exception(
                    "Found an unexpected bytecode. i: "
                    + str(i)
                    + " bytecode: "
                    + bytecode_as_str(bytecode)
                )

            elif bytecode in NOT_EXPECTED_IN_BLOCK_BYTECODES:
                raise Exception(
                    "Found "
                    + bytecode_as_str(bytecode)
                    + " bytecode, but it's not expected in a block method"
                )
            else:
                raise Exception(
                    "Found "
                    + bytecode_as_str(bytecode)
                    + " bytecode, but inlining does not handle it yet."
                )

            i += bc_length

        assert not jumps

    def adapt_after_outer_inlined(self, removed_ctx_level, mgenc_with_inlined):
        i = 0
        while i < len(self._bytecodes):
            bytecode = self.get_bytecode(i)
            bc_length = bytecode_length(bytecode)

            if (
                bytecode == Bytecodes.halt
                or bytecode == Bytecodes.dup
                or bytecode == Bytecodes.dup_second
                or bytecode == Bytecodes.push_block_no_ctx
                or bytecode == Bytecodes.push_constant
                or bytecode == Bytecodes.push_constant_0
                or bytecode == Bytecodes.push_constant_1
                or bytecode == Bytecodes.push_constant_2
                or bytecode == Bytecodes.push_0
                or bytecode == Bytecodes.push_1
                or bytecode == Bytecodes.push_nil
                or bytecode == Bytecodes.push_global
                or bytecode == Bytecodes.pop  # push_global doesn't encode context
                or bytecode == Bytecodes.send_1
                or bytecode == Bytecodes.send_2
                or bytecode == Bytecodes.send_3
                or bytecode == Bytecodes.send_4
                or bytecode == Bytecodes.send_n
                or bytecode == Bytecodes.super_send
                or bytecode == Bytecodes.return_local
                or bytecode == Bytecodes.inc
                or bytecode == Bytecodes.dec
                or bytecode == Bytecodes.jump
                or bytecode == Bytecodes.jump_on_true_top_nil
                or bytecode == Bytecodes.jump_on_true_pop
                or bytecode == Bytecodes.jump_on_false_top_nil
                or bytecode == Bytecodes.jump_on_false_pop
                or bytecode == Bytecodes.jump_if_greater
                or bytecode == Bytecodes.jump_backward
                or bytecode == Bytecodes.jump2
                or bytecode == Bytecodes.jump2_on_true_top_nil
                or bytecode == Bytecodes.jump2_on_true_pop
                or bytecode == Bytecodes.jump2_on_false_top_nil
                or bytecode == Bytecodes.jump2_on_false_pop
                or bytecode == Bytecodes.jump2_if_greater
                or bytecode == Bytecodes.jump2_backward
            ):
                # don't use context
                pass

            elif (
                bytecode == Bytecodes.push_field
                or bytecode == Bytecodes.pop_field
                or bytecode == Bytecodes.push_argument
                or bytecode == Bytecodes.pop_argument
            ):
                ctx_level = self.get_bytecode(i + 2)
                if ctx_level > removed_ctx_level:
                    self.set_bytecode(i + 2, ctx_level - 1)
                elif ctx_level == removed_ctx_level and (
                    bytecode == Bytecodes.push_argument
                    or bytecode == Bytecodes.pop_argument
                ):
                    idx = self.get_bytecode(i + 1)
                    arg = self._lexical_scope.get_argument(idx, removed_ctx_level)
                    new_idx = mgenc_with_inlined.get_inlined_local_idx(
                        arg, removed_ctx_level
                    )
                    if bytecode == Bytecodes.push_argument:
                        self.set_bytecode(i, Bytecodes.push_local)
                    else:
                        self.set_bytecode(i, Bytecodes.pop_local)
                    self.set_bytecode(i + 1, new_idx)

            elif bytecode == Bytecodes.push_block:
                literal_idx = self.get_bytecode(i + 1)
                block_method = self._literals[literal_idx]
                block_method.adapt_after_outer_inlined(
                    removed_ctx_level + 1, mgenc_with_inlined
                )

            elif bytecode == Bytecodes.push_local or bytecode == Bytecodes.pop_local:
                ctx_level = self.get_bytecode(i + 2)
                if ctx_level == removed_ctx_level:
                    idx = self.get_bytecode(i + 1)
                    # locals have been inlined into the outer context already
                    # so, we need to look up the right one and fix up the index
                    # at this point, the lexical scope has not been changed
                    # so, we should still be able to find the right one
                    old_var = self._lexical_scope.get_local(idx, ctx_level)
                    new_idx = mgenc_with_inlined.get_inlined_local_idx(
                        old_var, ctx_level
                    )
                    self.set_bytecode(i + 1, new_idx)
                elif ctx_level > removed_ctx_level:
                    self.set_bytecode(i + 2, ctx_level - 1)

            elif bytecode == Bytecodes.nil_local:
                assert removed_ctx_level > 0, (
                    "Don't need to adjust this bytecode, "
                    + "because it only operates on ctx_level==0"
                )

            elif bytecode == Bytecodes.return_non_local:
                ctx_level = self.get_bytecode(i + 1)
                self.set_bytecode(i + 1, ctx_level - 1)

            elif bytecode in RUN_TIME_ONLY_BYTECODES:
                raise Exception(
                    "Found an unexpected bytecode. i: "
                    + str(i)
                    + " bytecode: "
                    + bytecode_as_str(bytecode)
                )

            elif bytecode in NOT_EXPECTED_IN_BLOCK_BYTECODES:
                raise Exception(
                    "Found "
                    + bytecode_as_str(bytecode)
                    + " bytecode, but it's not expected in a block method"
                )
            else:
                raise Exception(
                    "Found "
                    + bytecode_as_str(bytecode)
                    + " bytecode, but adapt_after_outer_inlined does not handle it yet."
                )

            i += bc_length

        if removed_ctx_level == 1:
            self._lexical_scope.drop_inlined_scope()


class _Jump(HeapEntry):
    def __init__(self, jump_target, bytecode, idx):
        HeapEntry.__init__(self, jump_target)
        self.bytecode = bytecode
        self.idx = idx


class _BackJump(HeapEntry):
    def __init__(self, loop_begin_idx, backward_jump_idx):
        HeapEntry.__init__(self, loop_begin_idx)
        self.backward_jump_idx = backward_jump_idx


class _BackJumpPatch(HeapEntry):
    def __init__(self, backward_jump_idx, loop_begin_idx):
        HeapEntry.__init__(self, backward_jump_idx)
        self.loop_begin_idx = loop_begin_idx


class BcMethodNLR(BcMethod):
    def invoke_1(self, rcvr, ctx=None):
        new_frame = create_frame_1(rcvr, self._size_frame, self._size_inner)
        return _interp_with_nlr(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_1_tier3(self, rcvr, hybrid=False, ctx=None):
        new_frame = create_frame_1(rcvr, self._size_frame, self._size_inner)
        return _interp_with_nlr_tier3(
            self, new_frame, self._maximum_number_of_stack_elements, hybrid
        )

    def invoke_1_tier4(self, rcvr, ctx=None):
        at = self.adaptive_tier
        if at == 3 or at == 4:
            return self.invoke_1_tier3(rcvr, at == 4)
        new_frame = create_frame_1(rcvr, self._size_frame, self._size_inner)
        return _adaptive_with_nlr(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_2(self, rcvr, arg1, ctx=None):
        new_frame = create_frame_2(
            rcvr,
            arg1,
            self._arg_inner_access[0],
            self._size_frame,
            self._size_inner,
        )
        return _interp_with_nlr(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_2_tier3(self, rcvr, arg1, hybrid=False, ctx=None):
        new_frame = create_frame_2(
            rcvr,
            arg1,
            self._arg_inner_access[0],
            self._size_frame,
            self._size_inner,
        )
        return _interp_with_nlr_tier3(
            self, new_frame, self._maximum_number_of_stack_elements, hybrid
        )

    def invoke_2_tier4(self, rcvr, arg1, ctx=None):
        at = self.adaptive_tier
        if at == 3 or at == 4:
            return self.invoke_2_tier3(rcvr, arg1, at == 4)
        new_frame = create_frame_2(
            rcvr,
            arg1,
            self._arg_inner_access[0],
            self._size_frame,
            self._size_inner,
        )
        return _adaptive_with_nlr(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_3(self, rcvr, arg1, arg2, ctx=None):
        new_frame = create_frame_3(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            rcvr,
            arg1,
            arg2,
        )
        return _interp_with_nlr(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_3_tier3(self, rcvr, arg1, arg2, hybrid=False, ctx=None):
        new_frame = create_frame_3(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            rcvr,
            arg1,
            arg2,
        )
        return _interp_with_nlr_tier3(
            self, new_frame, self._maximum_number_of_stack_elements, hybrid
        )

    def invoke_3_tier4(self, rcvr, arg1, arg2, ctx=None):
        at = self.adaptive_tier
        if at == 3 or at == 4:
            return self.invoke_3_tier3(rcvr, arg1, arg2, at == 4)
        new_frame = create_frame_3(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            rcvr,
            arg1,
            arg2,
        )
        return _adaptive_with_nlr(self, new_frame, self._maximum_number_of_stack_elements)

    def invoke_n(self, stack, stack_ptr, ctx=None):
        new_frame = create_frame(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            stack,
            stack_ptr,
            self._number_of_arguments,
        )
        inner = get_inner_as_context(new_frame)

        try:
            result = interpret(self, new_frame, self._maximum_number_of_stack_elements)
            stack_ptr = stack_pop_old_arguments_and_push_result(
                stack, stack_ptr, self._number_of_arguments, result
            )
            mark_as_no_longer_on_stack(inner)
            return stack_ptr
        except ReturnException as e:
            mark_as_no_longer_on_stack(inner)
            if e.has_reached_target(inner):
                return stack_pop_old_arguments_and_push_result(
                    stack, stack_ptr, self._number_of_arguments, e.get_result()
                )
            raise e

    def invoke_n_tier3(self, stack, stack_ptr, hybrid=False, ctx=None):
        new_frame = create_frame(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            stack,
            stack_ptr,
            self._number_of_arguments,
        )
        inner = get_inner_as_context(new_frame)

        try:
            result = _interpret_tier3_mode(
                self, new_frame, self._maximum_number_of_stack_elements, hybrid
            )
            stack_ptr = stack_pop_old_arguments_and_push_result(
                stack, stack_ptr, self._number_of_arguments, result
            )
            mark_as_no_longer_on_stack(inner)
            return stack_ptr
        except ReturnException as e:
            mark_as_no_longer_on_stack(inner)
            if e.has_reached_target(inner):
                return stack_pop_old_arguments_and_push_result(
                    stack, stack_ptr, self._number_of_arguments, e.get_result()
                )
            raise e

    def invoke_n_tier4(self, stack, stack_ptr, ctx=None):
        at = self.adaptive_tier
        if at == 3 or at == 4:
            return self.invoke_n_tier3(stack, stack_ptr, at == 4)
        new_frame = create_frame(
            self._arg_inner_access,
            self._size_frame,
            self._size_inner,
            stack,
            stack_ptr,
            self._number_of_arguments,
        )
        # _adaptive_with_nlr applies the non-local-return handling.
        result = _adaptive_with_nlr(
            self, new_frame, self._maximum_number_of_stack_elements
        )
        return stack_pop_old_arguments_and_push_result(
            stack, stack_ptr, self._number_of_arguments, result
        )

    def inline(self, mgenc, merge_scope=True):
        raise Exception(
            "Blocks should never handle non-local returns. "
            "So, this should not happen."
        )
