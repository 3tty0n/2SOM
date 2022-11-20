import sys

from rlib.jit import not_in_trace
from rlib.debug import debug_print

class _Statistics(object):

    primitive_send = 0
    method_send = 0
    trivial_send = 0

    def _incr_primitive_send(self):
        self.primitive_send += 1

    def _incr_method_send(self):
        self.method_send += 1

    def _incr_trivial_send(self):
        self.trivial_send += 1

    @not_in_trace
    def incr(self, method):
        from som.vmobjects.method_bc import BcMethod
        from som.vmobjects.primitive import _AbstractPrimitive
        from som.vmobjects.method_trivial import AbstractTrivialMethod

        if method is None:
            return
        if isinstance(method, BcMethod):
            self._incr_method_send()
        elif isinstance(method, _AbstractPrimitive):
            self._incr_primitive_send()
        elif isinstance(method, AbstractTrivialMethod):
            self._incr_trivial_send()
        else:
            raise Exception("method %s (type: %s) should be BcMethod or Primitive" % (str(method), type(method)))

    @not_in_trace
    def report(self):
        debug_print("%d,%d,%d" % (self.primitive_send, self.trivial_send, self.method_send))

statistics = _Statistics()
