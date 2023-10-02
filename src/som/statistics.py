import sys

from rlib.jit import not_in_trace
from rlib.debug import debug_print

class _Statistics(object):

    primitive_send = 0

    primitive_send_1 = 0
    primitive_send_2 = 0
    primitive_send_3 = 0
    primitive_send_n = 0

    method_send = 0
    method_send_1 = 0
    method_send_2 = 0
    method_send_3 = 0
    method_send_n = 0

    trivial_send = 0
    trivial_send_1 = 0
    trivial_send_2 = 0
    trivial_send_3 = 0
    trivial_send_n = 0

    def _incr_primitive_send(self):
        self.primitive_send += 1

    def _incr_primitive_send_with_idx(self, idx):
        if idx == 1:
            self.primitive_send_1 += 1
        elif idx == 2:
            self.primitive_send_2 += 1
        elif idx == 3:
            self.primitive_send_3 += 1
        elif idx == 10:
            self.primitive_send_n += 1
        else:
            raise Exception("incr_with_idx requires idx 1, 2, 3, or 10")

    def _incr_method_send(self):
        self.method_send += 1

    def _incr_method_send_with_idx(self, idx):
        if idx == 1:
            self.method_send_1 += 1
        elif idx == 2:
            self.method_send_2 += 1
        elif idx == 3:
            self.method_send_3 += 1
        elif idx == 10:
            self.method_send_n += 1
        else:
            raise Exception("incr_with_idx requires idx 1, 2, 3, or 10")

    def _incr_trivial_send(self):
        self.trivial_send += 1

    def _incr_trivial_send_with_idx(self, idx):
        if idx == 1:
            self.trivial_send_1 += 1
        elif idx == 2:
            self.trivial_send_2 += 1
        elif idx == 3:
            self.trivial_send_3 += 1
        elif idx == 10:
            self.trivial_send_n += 1
        else:
            raise Exception("incr_with_idx requires idx 1, 2, 3, or 10")

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
            raise Exception("method %s (type: %s) should be BcMethod, Primitive, or Trivial" % (str(method), type(method)))

    def incr_with_idx(self, method, idx):
        # idx
        #   1: send_1
        #   2: send_2
        #   3: send_3
        #   10: send_n
        from som.vmobjects.method_bc import BcMethod
        from som.vmobjects.primitive import _AbstractPrimitive
        from som.vmobjects.method_trivial import AbstractTrivialMethod

        if method is None:
            return
        if isinstance(method, BcMethod):
            self._incr_method_send_with_idx(idx)
        elif isinstance(method, _AbstractPrimitive):
            self._incr_primitive_send_with_idx(idx)
        elif isinstance(method, AbstractTrivialMethod):
            self._incr_trivial_send_with_idx(idx)
        else:
            raise Exception("method %s (type: %s) should be BcMethod, Primitive, or Trivial" % (str(method), type(method)))

    @not_in_trace
    def report(self):
        send_1_all = self.primitive_send_1 + self.trivial_send_1 + self.method_send_1
        send_2_all = self.primitive_send_2 + self.trivial_send_2 + self.method_send_2
        send_3_all = self.primitive_send_3 + self.trivial_send_3 + self.method_send_3
        send_n_all = self.primitive_send_n + self.trivial_send_n + self.method_send_n
        send_all = send_1_all + send_2_all + send_3_all + send_n_all

        self.primitive_send = self.primitive_send_1 + self.primitive_send_2 + self.primitive_send_3 + self.primitive_send_n
        self.trivial_send = self.trivial_send_1 + self.trivial_send_2 + self.trivial_send_3 + self.trivial_send_n
        self.method_send = self.method_send_1 + self.method_send_2 + self.method_send_3 + self.method_send_n

        debug_print("======== Statistics Report =============\n")
        debug_print("Ratio of SEND")
        debug_print("Primitive\tTrivial\tBcMethod")
        debug_print("%f\t%f\t%f" % (
            float(self.primitive_send) / send_all,
            float(self.trivial_send) / send_all,
            float(self.method_send) / send_all
        ))
        debug_print()
        debug_print("Ratio of Method SEND_1, 2, 3, and N")
        debug_print("SEND_1\tSEND_2\tSEND_3\tSEND_N")
        debug_print("%f\t%f\t%f\t%f" % (
            float(send_1_all) / send_all,
            float(send_2_all) / send_all,
            float(send_3_all) / send_all,
            float(send_n_all) / send_all,
        ))

statistics = _Statistics()
