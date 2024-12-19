from rlib import jit

from som.primitives.integer_primitives import IntegerPrimitivesBase as _Base
from som.vmobjects.double import Double
from som.vmobjects.integer import Integer
from som.vmobjects.primitive import Primitive, TernaryPrimitive, QuaternaryPrimitive
from som.tier_type import is_tier1, is_tier2, is_hybrid


def get_printable_location_up(block_method):
    from som.vmobjects.method_bc import BcAbstractMethod

    assert isinstance(block_method, BcAbstractMethod)
    return "to:do: " + block_method.merge_point_string()


jitdriver_int = jit.JitDriver(
    name="to:do: with int",
    greens=["block_method"],
    reds="auto",
    # virtualizables=['frame'],
    is_recursive=True,
    get_printable_location=get_printable_location_up,
)

jitdriver_double = jit.JitDriver(
    name="to:do: with double",
    greens=["block_method"],
    reds="auto",
    # virtualizables=['frame'],
    is_recursive=True,
    get_printable_location=get_printable_location_up,
)


def get_printable_location_down(block_method):
    from som.vmobjects.method_bc import BcAbstractMethod

    assert isinstance(block_method, BcAbstractMethod)
    return "downToto:do: " % block_method.merge_point_string()


jitdriver_int_down = jit.JitDriver(
    name="downTo:do: with int",
    greens=["block_method"],
    reds="auto",
    # virtualizables=['frame'],
    is_recursive=True,
    get_printable_location=get_printable_location_down,
)

jitdriver_double_down = jit.JitDriver(
    name="downTo:do: with double",
    greens=["block_method"],
    reds="auto",
    # virtualizables=['frame'],
    is_recursive=True,
    get_printable_location=get_printable_location_down,
)


def _to_do_int(i, by_increment, top, block, block_method):
    assert isinstance(i, int)
    assert isinstance(top, int)
    while i <= top:
        if is_hybrid():
            jitdriver_int.jit_merge_point(block_method=block_method)

        if is_tier1():
            block_method.invoke_2(block, Integer(i))
        else:
            block_method.invoke_2_tier2(block, Integer(i))
        i += by_increment


def _to_do_double(i, by_increment, top, block, block_method):
    assert isinstance(i, int)
    assert isinstance(top, float)
    while i <= top:
        if is_tier2() or is_hybrid():
            jitdriver_double.jit_merge_point(block_method=block_method)

        if is_tier1():
            block_method.invoke_2(block, Integer(i))
        else:
            block_method.invoke_2_tier2(block, Integer(i))
        i += by_increment


def _to_do(rcvr, limit, block):
    block_method = block.get_method()

    i = rcvr.get_embedded_integer()
    if isinstance(limit, Double):
        _to_do_double(i, 1, limit.get_embedded_double(), block, block_method)
    else:
        _to_do_int(i, 1, limit.get_embedded_integer(), block, block_method)

    return rcvr


def _to_by_do(_ivkbl, stack, stack_ptr):
    block = stack[stack_ptr]
    stack[stack_ptr] = None
    stack_ptr -= 1

    by_increment = stack[stack_ptr]
    stack[stack_ptr] = None
    stack_ptr -= 1

    limit = stack[stack_ptr]
    stack[stack_ptr] = None
    stack_ptr -= 1

    block_method = block.get_method()

    self = stack[stack_ptr]

    i = self.get_embedded_integer()
    if isinstance(limit, Double):
        _to_do_double(
            i,
            by_increment.get_embedded_integer(),
            limit.get_embedded_double(),
            block,
            block_method,
        )
    else:
        _to_do_int(
            i,
            by_increment.get_embedded_integer(),
            limit.get_embedded_integer(),
            block,
            block_method,
        )

    return stack_ptr


def _to_by_do_4(_ivkbl, limit, by_increment, block):
    i = _ivkbl.get_embedded_integer()
    block_method = block.get_method()
    if isinstance(limit, Double):
        _to_do_double(
            i,
            by_increment.get_embedded_integer(),
            limit.get_embedded_double(),
            block,
            block_method,
        )
    else:
        _to_do_int(
            i,
            by_increment.get_embedded_integer(),
            limit.get_embedded_integer(),
            block,
            block_method,
        )


def _down_to_do_int(i, by_increment, bottom, block, block_method):
    assert isinstance(i, int)
    assert isinstance(bottom, int)
    while i >= bottom:
        if is_tier2() or is_hybrid():
            jitdriver_int_down.jit_merge_point(block_method=block_method)

        if is_tier1():
            block_method.invoke_2(block, Integer(i))
        else:
            block_method.invoke_2_tier2(block, Integer(i))
        i -= by_increment


def _down_to_do_double(i, by_increment, bottom, block, block_method):
    assert isinstance(i, int)
    assert isinstance(bottom, float)
    while i >= bottom:
        if is_tier2() or is_hybrid():
            jitdriver_double_down.jit_merge_point(block_method=block_method)

        if is_tier1():
            block_method.invoke_2(block, Integer(i))
        else:
            block_method.invoke_2_tier2(block, Integer(i))
        i -= by_increment


def _down_to_do(rcvr, limit, block):
    block_method = block.get_method()

    i = rcvr.get_embedded_integer()
    if isinstance(limit, Double):
        _down_to_do_double(i, 1, limit.get_embedded_double(), block, block_method)
    else:
        _down_to_do_int(i, 1, limit.get_embedded_integer(), block, block_method)

    return rcvr


class IntegerPrimitives(_Base):
    def install_primitives(self):
        _Base.install_primitives(self)
        self._install_instance_primitive(
            TernaryPrimitive("to:do:", self.universe, _to_do)
        )
        self._install_instance_primitive(
            TernaryPrimitive("downTo:do:", self.universe, _down_to_do)
        )
        # self._install_instance_primitive(
        #     Primitive("to:by:do:", self.universe, _to_by_do)
        # )
        self._install_instance_primitive(
            QuaternaryPrimitive("to:by:do:", self.universe, _to_by_do_4)
        )
