from som.interpreter.bc.bytecodes import Bytecodes as BC


def emit_inc(mgenc):
    emit1(mgenc, BC.inc)


def emit_dec(mgenc):
    emit1(mgenc, BC.dec)


def emit_pop(mgenc):
    if not mgenc.optimize_dup_pop_pop_sequence():
        emit1(mgenc, BC.pop)


def emit_push_argument(mgenc, idx, ctx):
    emit3(mgenc, BC.push_argument, idx, ctx)


def emit_nil_local(mgenc, idx):
    emit2(mgenc, BC.nil_local, idx)


def emit_return_self(mgenc):
    mgenc.optimize_dup_pop_pop_sequence()
    emit1(mgenc, BC.return_self)


def emit_return_local(mgenc):
    emit1(mgenc, BC.return_local)


def emit_return_non_local(mgenc):
    emit2(mgenc, BC.return_non_local, mgenc.get_max_context_level())


def emit_dup(mgenc):
    emit1(mgenc, BC.dup)


def emit_dup_second(mgenc):
    emit1(mgenc, BC.dup_second)


def emit_push_block(mgenc, block_method, with_ctx):
    idx = mgenc.add_literal_if_absent(block_method)
    emit2(
        mgenc,
        BC.push_block if with_ctx else BC.push_block_no_ctx,
        idx,
    )


def emit_push_local(mgenc, idx, ctx):
    emit3(mgenc, BC.push_local, idx, ctx)


def emit_push_field(mgenc, field_name):
    ctx_level = mgenc.get_max_context_level()
    field_idx = mgenc.get_field_index(field_name)

    emit_push_field_with_index(mgenc, field_idx, ctx_level)


def emit_push_field_with_index(mgenc, field_idx, ctx_level):
    if ctx_level == 0:
        if field_idx == 0:
            emit1(mgenc, BC.push_field_0)
            return
        if field_idx == 1:
            emit1(mgenc, BC.push_field_1)
            return

    emit3(
        mgenc,
        BC.push_field,
        field_idx,
        mgenc.get_max_context_level(),
    )


def emit_push_global(mgenc, glob):
    idx = mgenc.add_literal_if_absent(glob)
    # the block needs to be able to send #unknownGlobal: to self
    if not mgenc.is_global_known(glob):
        mgenc.mark_self_as_accessed_from_outer_context()
    emit2(mgenc, BC.push_global, idx)


def emit_pop_argument(mgenc, idx, ctx):
    emit3(mgenc, BC.pop_argument, idx, ctx)


def emit_pop_local(mgenc, idx, ctx):
    emit3(mgenc, BC.pop_local, idx, ctx)


def emit_pop_field(mgenc, field_name):
    ctx_level = mgenc.get_max_context_level()
    field_idx = mgenc.get_field_index(field_name)
    emit_pop_field_with_index(mgenc, field_idx, ctx_level)


def emit_pop_field_with_index(mgenc, field_idx, ctx_level):
    if ctx_level == 0:
        if field_idx == 0:
            emit1(mgenc, BC.pop_field_0)
            return
        if field_idx == 1:
            emit1(mgenc, BC.pop_field_1)
            return
    emit3(
        mgenc,
        BC.pop_field,
        field_idx,
        ctx_level,
    )


def emit_super_send(mgenc, msg):
    idx = mgenc.add_literal_if_absent(msg)
    emit2(mgenc, BC.super_send, idx)


def emit_send(mgenc, msg):
    idx = mgenc.add_literal_if_absent(msg)
    num_args = msg.get_number_of_signature_arguments()
    if num_args == 1:
        emit2(mgenc, BC.send_1, idx)
    elif num_args == 2:
        emit2(mgenc, BC.send_2, idx)
    elif num_args == 3:
        emit2(mgenc, BC.send_3, idx)
    elif num_args == 4:
        emit2(mgenc, BC.send_4, idx)
    else:
        emit2(mgenc, BC.send_n, idx)


def emit_push_constant(mgenc, lit):
    from som.vmobjects.integer import Integer
    from som.vm.globals import nilObject

    if isinstance(lit, Integer):
        if lit.get_embedded_integer() == 0:
            emit1(mgenc, BC.push_0)
            return
        if lit.get_embedded_integer() == 1:
            emit1(mgenc, BC.push_1)
            return

    if lit is nilObject:
        emit1(mgenc, BC.push_nil)
        return

    idx = mgenc.add_literal_if_absent(lit)
    if idx == 0:
        emit1(mgenc, BC.push_constant_0)
        return
    if idx == 1:
        emit1(mgenc, BC.push_constant_1)
        return
    if idx == 2:
        emit1(mgenc, BC.push_constant_2)
        return

    emit2(mgenc, BC.push_constant, idx)


def emit_push_constant_index(mgenc, lit_index):
    emit2(mgenc, BC.push_constant, lit_index)


def emit_jump_on_bool_with_dummy_offset(mgenc, is_if_true, needs_pop):
    # Remember: true and false seem flipped here.
    # This is because if the test passes, the block is inlined directly.
    # But if the test fails, we need to jump.
    # Thus, an  `#ifTrue:` needs to generated a jump_on_false.
    if is_if_true:
        emit1(mgenc, BC.jump_on_false_pop if needs_pop else BC.jump_on_false_top_nil)
    else:
        emit1(mgenc, BC.jump_on_true_pop if needs_pop else BC.jump_on_true_top_nil)

    idx = mgenc.add_bytecode_argument_and_get_index(0)
    mgenc.add_bytecode_argument(0)
    return idx


def emit_jump_with_dummy_offset(mgenc):
    emit1(mgenc, BC.jump)
    idx = mgenc.add_bytecode_argument_and_get_index(0)
    mgenc.add_bytecode_argument(0)
    return idx


def emit_jump_if_greater_with_dummy_offset(mgenc):
    emit1(mgenc, BC.jump_if_greater)
    idx = mgenc.add_bytecode_argument_and_get_index(0)
    mgenc.add_bytecode_argument(0)
    return idx


def emit_jump_backward_with_offset(mgenc, offset):
    emit3(
        mgenc,
        BC.jump_backward if offset <= 0xFF else BC.jump2_backward,
        offset & 0xFF,
        offset >> 8,
    )


def emit1(mgenc, code):
    mgenc.add_bytecode(code)


def emit2(mgenc, code, idx):
    mgenc.add_bytecode(code)
    mgenc.add_bytecode_argument(idx)


def emit2_with_dummy(mgenc, code):
    mgenc.add_bytecode(code)
    return mgenc.add_bytecode_argument_and_get_index(0)


def emit3(mgenc, code, idx, ctx):
    mgenc.add_bytecode(code)
    mgenc.add_bytecode_argument(idx)
    mgenc.add_bytecode_argument(ctx)


def emit3_with_dummy(mgenc, code):
    mgenc.add_bytecode(code)
    idx = mgenc.add_bytecode_argument_and_get_index(0)
    mgenc.add_bytecode_argument(0)
    return idx
