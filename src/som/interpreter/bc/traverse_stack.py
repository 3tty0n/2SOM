from rlib import jit


class TStack:
    _immutable_fields_ = ["bc_idx", "next"]

    def __init__(self, bc_idx, next):
        self.bc_idx = bc_idx
        self.next = next

    def __repr__(self):
        if self is None:
            return "None"
        return "TStack(%d, %s)" % (self.bc_idx, repr(self.next))

    def t_pop(self):
        assert self is not None
        return self.bc_idx, self.next

    @jit.elidable
    def t_is_empty(self):
        return self is _T_EMPTY


_T_EMPTY = TStack(-42, None)

memoization = {}


@jit.elidable
def t_empty():
    return _T_EMPTY


@jit.elidable
def t_push(bc_idx, next):
    key = bc_idx, next
    if key in memoization:
        return memoization[key]
    result = TStack(bc_idx, next)
    memoization[key] = result
    return result


@jit.dont_look_inside
def t_dump(tstack):
    s = "["
    while not tstack.t_is_empty():
        s += str(tstack.bc_idx)
        s += ","
        tstack = tstack.next
    s += "]"
    return s
