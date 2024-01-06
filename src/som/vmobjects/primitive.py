from som.interp_type import is_ast_interpreter
from som.vmobjects.abstract_object import AbstractObject


class _AbstractPrimitive(AbstractObject):
    _immutable_fields_ = ["_is_empty", "_signature", "_holder"]

    def __init__(self, signature_string, universe, is_empty=False):
        AbstractObject.__init__(self)

        self._signature = universe.symbol_for(signature_string)
        self._is_empty = is_empty
        self._holder = None

    @staticmethod
    def is_primitive():
        return True

    @staticmethod
    def is_invokable():
        """We use this method to identify methods and primitives"""
        return True

    def get_signature(self):
        return self._signature

    def get_holder(self):
        return self._holder

    def set_holder(self, value):
        self._holder = value

    def is_empty(self):
        # By default a primitive is not empty
        return self._is_empty

    def get_class(self, universe):
        return universe.primitive_class

    def get_object_layout(self, universe):
        return universe.primitive_layout

    def __str__(self):
        if self._holder:
            holder = self.get_holder().get_name().get_embedded_string()
        else:
            holder = "nil"
        return "Primitive(" + holder + ">>" + str(self.get_signature()) + ")"


class _AstPrimitive(_AbstractPrimitive):
    _immutable_fields_ = ["_prim_fn"]

    def __init__(self, signature_string, universe, prim_fn, is_empty=False):
        _AbstractPrimitive.__init__(self, signature_string, universe, is_empty)
        self._prim_fn = prim_fn

    def invoke_args(self, rcvr, args):
        prim_fn = self._prim_fn
        return prim_fn(self, rcvr, args)


class _BcPrimitive(_AbstractPrimitive):
    _immutable_fields_ = ["_prim_fn"]

    def __init__(self, signature_string, universe, prim_fn, is_empty=False):
        _AbstractPrimitive.__init__(self, signature_string, universe, is_empty)
        self._prim_fn = prim_fn

    def invoke_n(self, stack, stack_ptr, ctx=None):
        prim_fn = self._prim_fn
        return prim_fn(self, stack, stack_ptr)

    def invoke_n_tier2(self, stack, stack_ptr, ctx=None):
        prim_fn = self._prim_fn
        return prim_fn(self, stack, stack_ptr)

    def get_number_of_signature_arguments(self):
        return self._signature.get_number_of_signature_arguments()

    def merge_point_string(self):
        return self._signature


class UnaryPrimitive(_AbstractPrimitive):
    _immutable_fields_ = ["_prim_fn"]

    def __init__(self, signature_string, universe, prim_fn, is_empty=False):
        _AbstractPrimitive.__init__(self, signature_string, universe, is_empty)
        self._prim_fn = prim_fn

    def invoke_1(self, rcvr, ctx=None):
        prim_fn = self._prim_fn
        return prim_fn(rcvr)

    def invoke_1_tier2(self, rcvr, ctx=None):
        prim_fn = self._prim_fn
        return prim_fn(rcvr)

    def get_number_of_signature_arguments(self):  # pylint: disable=no-self-use
        return 1


class BinaryPrimitive(_AbstractPrimitive):
    _immutable_fields_ = ["_prim_fn"]

    def __init__(self, signature_string, universe, prim_fn, is_empty=False):
        _AbstractPrimitive.__init__(self, signature_string, universe, is_empty)
        self._prim_fn = prim_fn

    def invoke_2(self, rcvr, arg, ctx=None):
        prim_fn = self._prim_fn
        return prim_fn(rcvr, arg)

    def invoke_2_tier2(self, rcvr, arg, ctx=None):
        prim_fn = self._prim_fn
        return prim_fn(rcvr, arg)

    def get_number_of_signature_arguments(self):  # pylint: disable=no-self-use
        return 2


class TernaryPrimitive(_AbstractPrimitive):
    _immutable_fields_ = ["_prim_fn"]

    def __init__(self, signature_string, universe, prim_fn, is_empty=False):
        _AbstractPrimitive.__init__(self, signature_string, universe, is_empty)
        self._prim_fn = prim_fn

    def invoke_3(self, rcvr, arg1, arg2, ctx=None):
        prim_fn = self._prim_fn
        return prim_fn(rcvr, arg1, arg2)

    def invoke_3_tier2(self, rcvr, arg1, arg2, ctx=None):
        prim_fn = self._prim_fn
        return prim_fn(rcvr, arg1, arg2)

    def get_number_of_signature_arguments(self):  # pylint: disable=no-self-use
        return 3


class QuaternaryPrimitive(_AbstractPrimitive):
    _immutable_fields_ = ["_prim_fn"]

    def __init__(self, signature_string, universe, prim_fn, is_empty=False):
        _AbstractPrimitive.__init__(self, signature_string, universe, is_empty)
        self._prim_fn = prim_fn

    def invoke_4(self, rcvr, arg1, arg2, arg3, ctx=None):
        prim_fn = self._prim_fn
        return prim_fn(rcvr, arg1, arg2, arg3)

    def invoke_4_tier2(self, rcvr, arg1, arg2, arg3, ctx=None):
        prim_fn = self._prim_fn
        return prim_fn(rcvr, arg1, arg2, arg3)

    def get_number_of_signature_arguments(self):  # pylint: disable=no-self-use
        return 4

    def merge_point_string(self):
        return self._signature


def _empty_invoke_ast(ivkbl, _rcvr, _args):
    """Write a warning to the screen"""
    print(
        "Warning: undefined primitive #%s called"
        % ivkbl.get_signature().get_embedded_string()
    )


def _empty_invoke_bc(ivkbl, _stack, stack_ptr):
    """Write a warning to the screen"""
    print(
        "Warning: undefined primitive #%s called"
        % ivkbl.get_signature().get_embedded_string()
    )
    return stack_ptr


if is_ast_interpreter():
    Primitive = _AstPrimitive
    _empty_invoke = _empty_invoke_ast
else:
    Primitive = _BcPrimitive
    _empty_invoke = _empty_invoke_bc


def empty_primitive(signature_string, universe):
    """Return an empty primitive with the given signature"""
    return Primitive(signature_string, universe, _empty_invoke, True)
