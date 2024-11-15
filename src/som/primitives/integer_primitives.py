import sys

from math import sqrt

from rlib.arithmetic import (
    ovfcheck,
    LONG_BIT,
    bigint_from_int,
    string_to_int,
    bigint_from_str,
    ParseStringOverflowError,
)
from rlib.llop import as_32_bit_unsigned_value, unsigned_right_shift
from rlib.jit import dont_look_inside, elidable

from som.primitives.primitives import Primitives
from som.vm.globals import nilObject, falseObject, trueObject
from som.vmobjects.array import Array
from som.vmobjects.biginteger import BigInteger
from som.vmobjects.double import Double
from som.vmobjects.integer import Integer
from som.vmobjects.primitive import UnaryPrimitive, BinaryPrimitive
from som.vmobjects.string import String

@dont_look_inside
def _as_string(rcvr):
    return rcvr.prim_as_string()

@dont_look_inside
def _as_double(rcvr):
    return rcvr.prim_as_double()

@dont_look_inside
def _as_32_bit_signed_value(rcvr):
    return rcvr.prim_as_32_bit_signed_value()

@dont_look_inside
def _as_32_bit_unsigned_value(rcvr):
    val = as_32_bit_unsigned_value(rcvr.get_embedded_integer())
    return Integer(val)

@dont_look_inside
def _sqrt(rcvr):
    assert isinstance(rcvr, Integer)
    res = sqrt(rcvr.get_embedded_integer())
    if res == float(int(res)):
        return Integer(int(res))
    return Double(res)

@dont_look_inside
def _plus(left, right):
    return left.prim_add(right)

@dont_look_inside
def _minus(left, right):
    return left.prim_subtract(right)

@dont_look_inside
def _multiply(left, right):
    return left.prim_multiply(right)

@dont_look_inside
def _double_div(left, right):
    return left.prim_double_div(right)

@dont_look_inside
def _int_div(left, right):
    return left.prim_int_div(right)

@dont_look_inside
def _mod(left, right):
    return left.prim_modulo(right)

@dont_look_inside
def _remainder(left, right):
    return left.prim_remainder(right)

@dont_look_inside
def _and(left, right):
    return left.prim_and(right)

@dont_look_inside
def _equals_equals(left, right):
    if isinstance(right, Integer) or isinstance(right, BigInteger):
        return left.prim_equals(right)
    return falseObject

@dont_look_inside
def _equals(left, right):
    return left.prim_equals(right)

@dont_look_inside
def _unequals(left, right):
    return left.prim_unequals(right)

@dont_look_inside
def _min(left, right):
    if left.prim_less_than(right):
        return left
    return right

@dont_look_inside
def _max(left, right):
    if left.prim_less_than(right):
        return right
    return left


@dont_look_inside
def _less_than(left, right):
    if left.prim_less_than(right):
        return trueObject
    return falseObject

@dont_look_inside
def _less_than_or_equal(left, right):
    return left.prim_less_than_or_equal(right)

@dont_look_inside
def _greater_than(left, right):
    return left.prim_greater_than(right)
@dont_look_inside

def _greater_than_or_equal(left, right):
    return left.prim_greater_than_or_equal(right)

@dont_look_inside
def _left_shift(left, right):
    assert isinstance(right, Integer)

    left_val = left.get_embedded_integer()
    right_val = right.get_embedded_integer()

    assert isinstance(left_val, int)
    assert isinstance(right_val, int)

    try:
        if not (left_val == 0 or 0 <= right_val < LONG_BIT):
            raise OverflowError
        result = ovfcheck(left_val << right_val)
        return Integer(result)
    except OverflowError:
        return BigInteger(bigint_from_int(left_val).lshift(right_val))

@dont_look_inside
def _unsigned_right_shift(left, right):
    assert isinstance(right, Integer)

    left_val = left.get_embedded_integer()
    right_val = right.get_embedded_integer()

    return Integer(unsigned_right_shift(left_val, right_val))

@dont_look_inside
def _bit_xor(left, right):
    assert isinstance(right, Integer)
    result = left.get_embedded_integer() ^ right.get_embedded_integer()
    return Integer(result)

@dont_look_inside
def _abs(rcvr):
    return rcvr.prim_abs()


if sys.version_info.major <= 2:

    def _to(rcvr, arg):
        assert isinstance(rcvr, Integer)
        assert isinstance(arg, Integer)
        return Array.from_integers(
            range(rcvr.get_embedded_integer(), arg.get_embedded_integer() + 1)
        )


else:

    def _to(rcvr, arg):
        assert isinstance(rcvr, Integer)
        assert isinstance(arg, Integer)
        return Array.from_integers(
            list(range(rcvr.get_embedded_integer(), arg.get_embedded_integer() + 1))
        )


def _from_string(_rcvr, param):
    if not isinstance(param, String):
        return nilObject

    str_val = param.get_embedded_string()

    try:
        i = string_to_int(str_val)
        return Integer(i)
    except ParseStringOverflowError:
        bigint = bigint_from_str(str_val)
        return BigInteger(bigint)


class IntegerPrimitivesBase(Primitives):
    def install_primitives(self):
        self._install_instance_primitive(
            UnaryPrimitive("asString", self.universe, _as_string)
        )
        self._install_instance_primitive(
            UnaryPrimitive("asDouble", self.universe, _as_double)
        )
        self._install_instance_primitive(
            UnaryPrimitive("as32BitSignedValue", self.universe, _as_32_bit_signed_value)
        )
        self._install_instance_primitive(
            UnaryPrimitive(
                "as32BitUnsignedValue", self.universe, _as_32_bit_unsigned_value
            )
        )

        self._install_instance_primitive(UnaryPrimitive("sqrt", self.universe, _sqrt))

        self._install_instance_primitive(BinaryPrimitive("+", self.universe, _plus))
        self._install_instance_primitive(BinaryPrimitive("-", self.universe, _minus))

        self._install_instance_primitive(BinaryPrimitive("*", self.universe, _multiply))
        self._install_instance_primitive(
            BinaryPrimitive("//", self.universe, _double_div)
        )
        self._install_instance_primitive(BinaryPrimitive("/", self.universe, _int_div))
        self._install_instance_primitive(BinaryPrimitive("%", self.universe, _mod))
        self._install_instance_primitive(
            BinaryPrimitive("rem:", self.universe, _remainder)
        )
        self._install_instance_primitive(BinaryPrimitive("&", self.universe, _and))

        self._install_instance_primitive(
            BinaryPrimitive("==", self.universe, _equals_equals)
        )

        self._install_instance_primitive(BinaryPrimitive("=", self.universe, _equals))
        self._install_instance_primitive(
            BinaryPrimitive("<", self.universe, _less_than)
        )
        self._install_instance_primitive(
            BinaryPrimitive("<=", self.universe, _less_than_or_equal)
        )
        self._install_instance_primitive(
            BinaryPrimitive(">", self.universe, _greater_than)
        )
        self._install_instance_primitive(
            BinaryPrimitive(">=", self.universe, _greater_than_or_equal)
        )
        self._install_instance_primitive(
            BinaryPrimitive("<>", self.universe, _unequals)
        )
        self._install_instance_primitive(
            BinaryPrimitive("~=", self.universe, _unequals)
        )

        self._install_instance_primitive(
            BinaryPrimitive("<<", self.universe, _left_shift)
        )
        self._install_instance_primitive(
            BinaryPrimitive("bitXor:", self.universe, _bit_xor)
        )
        self._install_instance_primitive(
            BinaryPrimitive(">>>", self.universe, _unsigned_right_shift)
        )
        self._install_instance_primitive(UnaryPrimitive("abs", self.universe, _abs))
        self._install_instance_primitive(BinaryPrimitive("max:", self.universe, _max))
        self._install_instance_primitive(BinaryPrimitive("min:", self.universe, _min))

        self._install_instance_primitive(BinaryPrimitive("to:", self.universe, _to))

        self._install_class_primitive(
            BinaryPrimitive("fromString:", self.universe, _from_string)
        )
