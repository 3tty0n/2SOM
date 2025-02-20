from rlib.jit import JitDriver

from som.interp_type import is_ast_interpreter
from som.vmobjects.array import Array
from som.vmobjects.primitive import UnaryPrimitive, BinaryPrimitive, TernaryPrimitive
from som.vmobjects.method import AbstractMethod
from som.primitives.primitives import Primitives

from som.tier_type import is_tier1

if is_ast_interpreter():
    from som.vmobjects.block_ast import AstBlock as _Block
else:
    from som.vmobjects.block_bc import BcBlock as _Block


def _at(rcvr, i):
    return rcvr.get_indexable_field(i.get_embedded_integer() - 1)


def _at_put(rcvr, index, value):
    rcvr.set_indexable_field(index.get_embedded_integer() - 1, value)
    return rcvr


def _length(rcvr):
    from som.vmobjects.integer import Integer

    return Integer(rcvr.get_number_of_indexable_fields())


def _copy(rcvr):
    return rcvr.copy()


def _new(_rcvr, length):
    return Array.from_size(length.get_embedded_integer())


def get_do_index_printable_location(block_method):
    assert isinstance(block_method, AbstractMethod)
    return "#doIndexes: %s" % block_method.merge_point_string()


do_index_driver = JitDriver(
    greens=["block_method"],
    reds="auto",
    is_recursive=True,
    get_printable_location=get_do_index_printable_location,
)


def _do_indexes(rcvr, block):
    from som.vmobjects.integer import Integer

    block_method = block.get_method()

    i = 1
    length = rcvr.get_number_of_indexable_fields()
    while i <= length:  # the i is propagated to Smalltalk, so, start with 1
        do_index_driver.jit_merge_point(block_method=block_method)
        if is_tier1():
            block_method.invoke_2(block, Integer(i))
        else:
            block_method.invoke_2_tier2(block, Integer(i))
        i += 1


def get_do_printable_location(block_method):
    assert isinstance(block_method, AbstractMethod)
    return "#do: %s" % block_method.merge_point_string()


do_driver = JitDriver(
    greens=["block_method"],
    reds="auto",
    get_printable_location=get_do_printable_location,
)


def _do(rcvr, block):
    block_method = block.get_method()

    i = 0
    length = rcvr.get_number_of_indexable_fields()
    while i < length:  # the array itself is zero indexed
        do_driver.jit_merge_point(block_method=block_method)
        if is_tier1():
            block_method.invoke_2(block, rcvr.get_indexable_field(i))
        else:
            block_method.invoke_2_tier2(block, rcvr.get_indexable_field(i))
        i += 1


def _put_all(rcvr, arg):
    if isinstance(arg, _Block):
        rcvr.set_all_with_block(arg)
        return rcvr

    # It is a simple value, just put it into the array
    rcvr.set_all(arg)
    return rcvr


class ArrayPrimitivesBase(Primitives):
    def install_primitives(self):
        self._install_instance_primitive(BinaryPrimitive("at:", self.universe, _at))
        self._install_instance_primitive(
            TernaryPrimitive("at:put:", self.universe, _at_put)
        )
        self._install_instance_primitive(
            UnaryPrimitive("length", self.universe, _length)
        )
        self._install_instance_primitive(UnaryPrimitive("copy", self.universe, _copy))

        self._install_class_primitive(BinaryPrimitive("new:", self.universe, _new))

        self._install_instance_primitive(
            BinaryPrimitive("doIndexes:", self.universe, _do_indexes)
        )
        self._install_instance_primitive(BinaryPrimitive("do:", self.universe, _do))
        self._install_instance_primitive(
            BinaryPrimitive("putAll:", self.universe, _put_all)
        )
