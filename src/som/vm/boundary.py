import os

from som.placement import send_place_is_pgo


class _State(object):
    def __init__(self):
        self.loaded = False
        self.boundary_keys = {}


_state = _State()


def _read_file(path):
    fd = os.open(path, os.O_RDONLY, 0)
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return "".join(chunks)
    finally:
        os.close(fd)


def _load():
    _state.loaded = True
    if not send_place_is_pgo():
        return
    path = os.environ.get("SOM_SEND_BOUNDARY")
    if not path:
        return
    try:
        text = _read_file(path)
    except OSError:
        return

    for line in text.split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 4 or parts[0] == "holder":
            continue
        holder, sig, selector, ordinal = parts
        _state.boundary_keys[(holder, sig, selector, int(ordinal))] = True


def is_boundary(holder, sig, selector, ordinal):
    if not _state.loaded:
        _load()
    return (holder, sig, selector, ordinal) in _state.boundary_keys
