import os

from som.placement import send_place_is_pgo


class _State(object):
    def __init__(self):
        self.loaded = False
        self.generic_keys = {}


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


def _is_generic(distinct_classes, last_new_idx, sends_total):
    if distinct_classes >= 5:
        return True
    if distinct_classes > 1 and sends_total > 0 and last_new_idx > 0.5 * sends_total:
        return True
    return False


def _load():
    _state.loaded = True
    if not send_place_is_pgo():
        return
    path = os.environ.get("SOM_SEND_PROFILE")
    if not path:
        return
    try:
        text = _read_file(path)
    except OSError:
        return

    sends_total = 0
    in_sites = False
    for line in text.split("\n"):
        if not line:
            continue
        if line == "# sites":
            in_sites = True
            continue
        if line == "# summary":
            in_sites = False
            continue
        if not in_sites:
            if line.startswith("sends_total\t"):
                sends_total = int(line.split("\t")[1])
            continue

        parts = line.split("\t")
        if len(parts) != 9 or parts[0] == "holder":
            continue
        holder, sig, _bc_index, selector, ordinal, _count, distinct, _first, last = parts
        if _is_generic(int(distinct), int(last), sends_total):
            _state.generic_keys[(holder, sig, selector, int(ordinal))] = True


def is_marked_generic(holder, sig, selector, ordinal):
    if not _state.loaded:
        _load()
    return (holder, sig, selector, ordinal) in _state.generic_keys
