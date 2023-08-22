TRACE_THRESHOLD = 1039 / 2


class ContinueInTier1(Exception):
    def __init__(self, method, frame, items, stack_ptr, bytecode_index):
        assert method is not None
        self.method = method
        self.frame = frame
        self.items = items
        self.stack_ptr = stack_ptr
        self.bytecode_index = bytecode_index


class ContinueInTier2(Exception):
    def __init__(self, method, frame, stack, bytecode_index):
        assert method is not None
        self.method = method
        self.frame = frame
        self.stack = stack
        self.bytecode_index = bytecode_index
