from .expression_node import ExpressionNode
from som.interpreter.objectstorage.field_accessor_node import create_read, \
    create_write

from som.vmobjects.abstract_object import AbstractObject
from som.vmobjects.object          import Object


class _AbstractFieldNode(ExpressionNode):

    _immutable_fields_ = ["_self_exp?"]
    _child_nodes_      = ["_self_exp"]

    def __init__(self, self_exp, source_section):
        ExpressionNode.__init__(self, source_section)
        self._self_exp  = self.adopt_child(self_exp)


class FieldReadNode(_AbstractFieldNode):

    _immutable_fields_ = ['_read?']
    _child_nodes_      = ['_read']

    def __init__(self, self_exp, nilObject, field_idx, source_section):
        _AbstractFieldNode.__init__(self, self_exp, source_section)
        self._read = self.adopt_child(create_read(nilObject, field_idx))

    def execute(self, frame):
        self_obj = self._self_exp.execute(frame)
        assert isinstance(self_obj, Object)
        return self._read.read(self_obj)


class FieldWriteNode(_AbstractFieldNode):

    _immutable_fields_ = ["_value_exp?", "_write?"]
    _child_nodes_      = ["_value_exp",  "_write"]

    def __init__(self, self_exp, value_exp, nilObject, field_idx, source_section):
        _AbstractFieldNode.__init__(self, self_exp, source_section)
        self._value_exp = self.adopt_child(value_exp)
        self._write     = self.adopt_child(create_write(nilObject, field_idx))

    def execute(self, frame):
        self_obj = self._self_exp.execute(frame)
        value    = self._value_exp.execute(frame)
        assert isinstance(self_obj, Object)
        assert isinstance(value, AbstractObject)
        self._write.write(self_obj, value)
        return value


def create_read_node(self_exp, nilObject, index, source_section = None):
    return FieldReadNode(self_exp, nilObject,index, source_section)


def create_write_node(self_exp, value_exp, nilObject, index, source_section = None):
    return FieldWriteNode(self_exp, value_exp, nilObject, index, source_section)
