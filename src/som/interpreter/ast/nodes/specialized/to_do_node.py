from rlib import jit

from som.interpreter.ast.nodes.expression_node import ExpressionNode

from som.vmobjects.block_ast import AstBlock
from som.vmobjects.double import Double
from som.vmobjects.integer import Integer
from som.vmobjects.method_ast import AstMethod


class AbstractToDoNode(ExpressionNode):

    _immutable_fields_ = ["_rcvr_expr?", "_limit_expr?", "_body_expr?", "universe"]
    _child_nodes_ = ["_rcvr_expr", "_limit_expr", "_body_expr"]

    def __init__(self, rcvr_expr, limit_expr, body_expr, universe, source_section=None):
        ExpressionNode.__init__(self, source_section)
        self._rcvr_expr = self.adopt_child(rcvr_expr)
        self._limit_expr = self.adopt_child(limit_expr)
        self._body_expr = self.adopt_child(body_expr)
        self.universe = universe

    def execute(self, frame):
        rcvr = self._rcvr_expr.execute(frame)
        limit = self._limit_expr.execute(frame)
        body = self._body_expr.execute(frame)
        self._do_loop(rcvr, limit, body)
        return rcvr

    def execute_evaluated(self, _frame, rcvr, args):
        self._do_loop(rcvr, args[0], args[1])
        return rcvr

    @staticmethod
    def _do_loop(_rcvr, _a1, _a2):  # pylint: disable=W
        raise Exception("Implemented in Subclass")


def get_printable_location(block_method):
    assert isinstance(block_method, AstMethod)
    return "#to:do: %s" % block_method.merge_point_string()


int_driver = jit.JitDriver(
    greens=["block_method"],
    reds="auto",
    is_recursive=True,
    # virtualizables=['frame'],
    get_printable_location=get_printable_location,
)


class IntToIntDoNode(AbstractToDoNode):
    @staticmethod
    def _do_loop(rcvr, limit, body_block):
        block_method = body_block.get_method()

        i = rcvr.get_embedded_integer()
        top = limit.get_embedded_integer()
        while i <= top:
            int_driver.jit_merge_point(block_method=block_method)
            block_method.invoke_2(body_block, Integer(i))
            i += 1

    @staticmethod
    def can_specialize(selector, rcvr, args, _node):
        return (
            isinstance(args[0], Integer)
            and isinstance(rcvr, Integer)
            and len(args) > 1
            and isinstance(args[1], AstBlock)
            and selector.get_embedded_string() == "to:do:"
        )

    @staticmethod
    def specialize_node(_selector, _rcvr, _args, node):
        return node.replace(
            IntToIntDoNode(
                node._rcvr_expr,  # pylint: disable=protected-access
                node._arg_exprs[0],  # pylint: disable=protected-access
                node._arg_exprs[1],  # pylint: disable=protected-access
                node.universe,
                node.source_section,
            )
        )


double_driver = jit.JitDriver(
    greens=["block_method"],
    reds="auto",
    is_recursive=True,
    # virtualizables=['frame'],
    get_printable_location=get_printable_location,
)


class IntToDoubleDoNode(AbstractToDoNode):
    @staticmethod
    def _do_loop(rcvr, limit, body_block):
        block_method = body_block.get_method()

        i = rcvr.get_embedded_integer()
        top = limit.get_embedded_double()
        while i <= top:
            double_driver.jit_merge_point(block_method=block_method)
            block_method.invoke_2(body_block, Integer(i))
            i += 1

    @staticmethod
    def can_specialize(selector, rcvr, args, _node):
        return (
            isinstance(args[0], Double)
            and isinstance(rcvr, Integer)
            and len(args) > 1
            and isinstance(args[1], AstBlock)
            and selector.get_embedded_string() == "to:do:"
        )

    @staticmethod
    def specialize_node(_selector, _rcvr, _args, node):
        return node.replace(
            IntToDoubleDoNode(
                node._rcvr_expr,  # pylint: disable=protected-access
                node._arg_exprs[0],  # pylint: disable=protected-access
                node._arg_exprs[1],  # pylint: disable=protected-access
                node.universe,
                node.source_section,
            )
        )
