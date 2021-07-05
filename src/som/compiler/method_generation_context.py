from collections import OrderedDict

from som.compiler.ast.variable import Local, Argument
from som.compiler.lexical_scope import LexicalScope
from som.interpreter.ast.frame import ARG_OFFSET, FRAME_AND_INNER_RCVR_IDX


class MethodGenerationContextBase(object):
    def __init__(self, universe, outer):
        self.holder = None
        self._arguments = OrderedDict()
        self._locals = OrderedDict()
        self.outer_genc = outer
        self.is_block_method = outer is not None
        self._signature = None
        self._primitive = False  # to be changed

        # does non-local return, directly or indirectly via a nested block
        self.throws_non_local_return = False
        self.needs_to_catch_non_local_returns = False
        self._accesses_variables_of_outer_context = False

        self.universe = universe

        self.lexical_scope = None

    def __str__(self):
        result = "MGenc("
        if self.holder and self.holder.name:
            result += self.holder.name.get_embedded_string()

        if self._signature:
            result += ">>#" + self._signature.get_embedded_string()

        result += ")"
        return result

    def set_primitive(self):
        self._primitive = True

    def set_signature(self, sig):
        self._signature = sig

    def has_field(self, field):
        return self.holder.has_field(field)

    def get_field_index(self, field):
        return self.holder.get_field_index(field)

    def get_number_of_arguments(self):
        return len(self._arguments)

    def get_signature(self):
        return self._signature

    def add_argument(self, arg):
        if (
            self.lexical_scope is None
            and (arg == "self" or arg == "$blockSelf")
            and len(self._arguments) > 0
        ):
            raise RuntimeError(
                "The self argument always has to be the first argument of a method."
            )
        argument = Argument(arg, len(self._arguments))
        self._arguments[arg] = argument
        return argument

    def add_argument_if_absent(self, arg):
        if arg in self._arguments:
            return
        self.add_argument(arg)

    def add_local(self, local):
        assert (
            self.lexical_scope is None
        ), "The lexical scope object was already constructed. Can't add another local"
        result = Local(local, len(self._locals))
        self._locals[local] = result
        return result

    def add_local_if_absent(self, local):
        if local in self._locals:
            return False
        self.add_local(local)
        return True

    def complete_lexical_scope(self):
        self.lexical_scope = LexicalScope(
            self.outer_genc.lexical_scope if self.outer_genc else None,
            list(self._arguments.values()),
            list(self._locals.values()),
        )

    def make_catch_non_local_return(self):
        self.throws_non_local_return = True
        ctx = self._mark_outer_contexts_to_require_context_and_get_root_context()

        assert ctx is not None
        ctx.needs_to_catch_non_local_returns = True

    def requires_context(self):
        return self.throws_non_local_return or self._accesses_variables_of_outer_context

    def _mark_outer_contexts_to_require_context_and_get_root_context(self):
        ctx = self.outer_genc
        while ctx.outer_genc is not None:
            ctx.throws_non_local_return = True
            ctx = ctx.outer_genc
        return ctx

    @staticmethod
    def _separate_variables(
        variables, frame_offset, inner_offset, only_local_access, non_local_access
    ):
        inner_access = [False] * len(variables)
        i = 0
        for var in variables:
            if var.is_accessed_out_of_context():
                var.set_access_index(len(non_local_access) + inner_offset)
                non_local_access.append(var)
                inner_access[i] = True
            else:
                var.set_access_index(len(only_local_access) + frame_offset)
                only_local_access.append(var)
            i += 1

        return inner_access

    def prepare_frame(self):
        arg_list = list(self._arguments.values())
        args = []
        args_inner = []
        local_vars = []
        locals_vars_inner = []

        arg_list[0].set_access_index(FRAME_AND_INNER_RCVR_IDX)

        arg_inner_access = self._separate_variables(
            arg_list[1:],  # skipping self
            ARG_OFFSET,
            ARG_OFFSET,
            args,
            args_inner,
        )
        self._separate_variables(
            self._locals.values(),
            ARG_OFFSET + len(args),
            ARG_OFFSET + len(args_inner),
            local_vars,
            locals_vars_inner,
        )

        size_frame = 1 + 1 + len(args) + len(local_vars)  # Inner and Receiver
        size_inner = len(args_inner) + len(locals_vars_inner)
        if (
            self.requires_context()
            or size_inner > 0
            or self.needs_to_catch_non_local_returns
            or arg_list[0].is_accessed_out_of_context()
        ):
            size_inner += 1 + 1  # OnStack marker and Receiver

        return arg_inner_access, size_frame, size_inner
