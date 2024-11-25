class _TierManager(object):

    _HYBRID_THRESHOLD = 23

    def set_tier(self, tier):
        self._CURRENT_TIER = tier

    def set_threshold(self, value):
        self._HYBRID_THRESHOLD = value

    def get_threshold(self):
        return self._HYBRID_THRESHOLD


tier_manager = _TierManager()


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
