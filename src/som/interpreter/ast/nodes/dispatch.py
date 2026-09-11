from rlib import jit

from som.interpreter.send import lookup_and_send_3
from som.vm.symbols import symbol_for

from som.vmobjects.array import Array


INLINE_CACHE_SIZE = 6


class _AbstractDispatchNode(object):
    _immutable_fields_ = ["expected_layout", "next_entry?"]
    _child_nodes_ = ["next_entry"]

    def __init__(self, expected_layout, next_entry):
        self.expected_layout = expected_layout
        self.next_entry = next_entry


class GenericDispatchNode(_AbstractDispatchNode):
    _immutable_fields_ = ["universe", "_selector"]

    def __init__(self, selector, universe):
        """
        The Generic Dispatch Node sets expected_layout to None.
        This is used as a check in the dispatch, to recognize the generic case.
        """
        _AbstractDispatchNode.__init__(self, None, None)
        self.universe = universe
        self._selector = selector

    def execute_dispatch(self, rcvr, args):
        method = rcvr.get_object_layout(self.universe).lookup_invokable(self._selector)
        if method is not None:
            return method.invoke(rcvr, args)
        return self._send_dnu(rcvr, args)

    def _send_dnu(self, rcvr, args):
        # Won't use DNU caching here, because it's a megamorphic node
        return lookup_and_send_3(
            rcvr,
            self._selector,
            Array.from_values(args),
            "doesNotUnderstand:arguments:",
        )

    def dispatch_1(self, rcvr):
        method = rcvr.get_object_layout(self.universe).lookup_invokable(self._selector)
        if method is not None:
            return method.invoke_1(rcvr)
        return self._send_dnu(rcvr, [])

    def dispatch_2(self, rcvr, arg):
        method = rcvr.get_object_layout(self.universe).lookup_invokable(self._selector)
        if method is not None:
            return method.invoke_2(rcvr, arg)
        return self._send_dnu(rcvr, [arg])

    def dispatch_3(self, rcvr, arg1, arg2):
        method = rcvr.get_object_layout(self.universe).lookup_invokable(self._selector)
        if method is not None:
            return method.invoke_3(rcvr, arg1, arg2)
        return self._send_dnu(rcvr, [arg1, arg2])

    def dispatch_args(self, rcvr, args):
        method = rcvr.get_object_layout(self.universe).lookup_invokable(self._selector)
        if method is not None:
            return method.invoke_args(rcvr, args)
        return self._send_dnu(rcvr, args)

    def dispatch_n_bc(self, stack, stack_ptr, rcvr):
        method = rcvr.get_object_layout(self.universe).lookup_invokable(self._selector)
        if method is not None:
            return method.invoke_n(stack, stack_ptr)
        from som.interpreter.bc.interpreter import send_does_not_understand

        return send_does_not_understand(rcvr, self._selector, stack, stack_ptr)


class CachedDispatchNode(_AbstractDispatchNode):
    _immutable_fields_ = ["_cached_method"]

    def __init__(self, rcvr_class, method, next_entry):
        _AbstractDispatchNode.__init__(self, rcvr_class, next_entry)
        self._cached_method = method

    def dispatch_1(self, rcvr):
        return self._cached_method.invoke_1(rcvr)

    def dispatch_2(self, rcvr, arg):
        return self._cached_method.invoke_2(rcvr, arg)

    def dispatch_3(self, rcvr, arg1, arg2):
        return self._cached_method.invoke_3(rcvr, arg1, arg2)

    def dispatch_args(self, rcvr, args):
        return self._cached_method.invoke_args(rcvr, args)

    def dispatch_n_bc(self, stack, stack_ptr, _rcvr):
        return self._cached_method.invoke_n(stack, stack_ptr)


class BoundaryDispatchNode(CachedDispatchNode):
    """
    Same as CachedDispatchNode, but the JIT must not trace into the
    cached method: the callee gets its own trace/compilation unit.
    """

    def dispatch_1(self, rcvr):
        return _boundary_invoke_1(self._cached_method, rcvr)

    def dispatch_2(self, rcvr, arg):
        return _boundary_invoke_2(self._cached_method, rcvr, arg)

    def dispatch_3(self, rcvr, arg1, arg2):
        return _boundary_invoke_3(self._cached_method, rcvr, arg1, arg2)

    def dispatch_args(self, rcvr, args):
        return _boundary_invoke_args(self._cached_method, rcvr, args)

    def dispatch_n_bc(self, stack, stack_ptr, _rcvr):
        return _boundary_invoke_n(self._cached_method, stack, stack_ptr)


@jit.dont_look_inside
def _boundary_invoke_1(method, rcvr):
    return method.invoke_1(rcvr)


@jit.dont_look_inside
def _boundary_invoke_2(method, rcvr, arg):
    return method.invoke_2(rcvr, arg)


@jit.dont_look_inside
def _boundary_invoke_3(method, rcvr, arg1, arg2):
    return method.invoke_3(rcvr, arg1, arg2)


@jit.dont_look_inside
def _boundary_invoke_args(method, rcvr, args):
    return method.invoke_args(rcvr, args)


@jit.dont_look_inside
def _boundary_invoke_n(method, stack, stack_ptr):
    return method.invoke_n(stack, stack_ptr)


class TrivialFieldReadNode(_AbstractDispatchNode):
    _immutable_fields_ = ["field_idx"]

    def __init__(self, field_idx):
        _AbstractDispatchNode.__init__(self, None, None)
        self.field_idx = field_idx


class TrivialFieldWriteNode(_AbstractDispatchNode):
    _immutable_fields_ = ["field_idx"]

    def __init__(self, field_idx):
        _AbstractDispatchNode.__init__(self, None, None)
        self.field_idx = field_idx


class TrivialLiteralNode(_AbstractDispatchNode):
    _immutable_fields_ = ["value"]

    def __init__(self, value):
        _AbstractDispatchNode.__init__(self, None, None)
        self.value = value


class CachedDnuNode(_AbstractDispatchNode):
    _immutable_fields_ = ["_selector", "_cached_method"]

    def __init__(self, selector, layout, next_entry):
        _AbstractDispatchNode.__init__(
            self,
            layout,
            next_entry,
        )
        self._selector = selector
        self._cached_method = layout.lookup_invokable(
            symbol_for("doesNotUnderstand:arguments:")
        )

    def dispatch_1(self, rcvr):
        return self._cached_method.invoke_3(rcvr, self._selector, Array.from_size(0))

    def dispatch_2(self, rcvr, arg):
        return self._cached_method.invoke_3(
            rcvr, self._selector, Array.from_values([arg])
        )

    def dispatch_3(self, rcvr, arg1, arg2):
        return self._cached_method.invoke_3(
            rcvr, self._selector, Array.from_values([arg1, arg2])
        )

    def dispatch_args(self, rcvr, args):
        return self._cached_method.invoke_3(
            rcvr, self._selector, Array.from_values(args)
        )
