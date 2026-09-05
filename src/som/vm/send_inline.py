import os


class _State(object):
    def __init__(self):
        self.checked = False
        self.enabled = False


_state = _State()


def is_enabled():
    if not _state.checked:
        _state.checked = True
        _state.enabled = os.environ.get("SOM_SEND_INLINE") == "1"
    return _state.enabled
