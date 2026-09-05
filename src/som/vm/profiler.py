import os
import sys
import time

_PROFILE = os.getenv("SOM_PROFILE") == "1"

_WINDOW_SIZE = 100000

_init_time = time.time()
_classes_loaded = 0
_methods_defined = 0
_methods_executed = {}
_sends_total = 0
_sites = {}
_windows = {}
_allocs_objects = 0
_allocs_arrays = 0

_bench_name = None
_bench_first_send_index = -1

if _PROFILE:
    _argv = sys.argv
    if len(_argv) >= 3 and _argv[-1].isdigit() and _argv[-2].isdigit():
        _bench_name = _argv[-3]


def on_class_loaded(clazz, universe):
    global _classes_loaded, _methods_defined
    from som.vmobjects.method_bc import BcMethod

    _classes_loaded += 1
    for inv in clazz.get_instance_invokables_for_disassembler():
        if isinstance(inv, BcMethod):
            _methods_defined += 1
    metaclass = clazz.get_class(universe)
    if metaclass is not None and metaclass is not clazz:
        for inv in metaclass.get_instance_invokables_for_disassembler():
            if isinstance(inv, BcMethod):
                _methods_defined += 1


def on_method_executed(method):
    _methods_executed[id(method)] = True


def on_send(method, bc_index, rcvr_class_name):
    global _sends_total, _bench_first_send_index

    holder = method.get_holder()
    holder_name = holder.get_name().get_embedded_string() if holder else "?"
    sig_name = method.get_signature().get_embedded_string()

    idx = _sends_total
    _sends_total = idx + 1

    if (
        _bench_name is not None
        and _bench_first_send_index == -1
        and holder_name == _bench_name
    ):
        _bench_first_send_index = idx

    key = (holder_name, sig_name, bc_index)
    site = _sites.get(key)
    if site is None:
        site = [0, {}, idx, idx]
        _sites[key] = site
    site[0] += 1
    if rcvr_class_name not in site[1]:
        site[1][rcvr_class_name] = True
        site[3] = idx

    window_idx = idx // _WINDOW_SIZE
    window = _windows.get(window_idx)
    if window is None:
        window = [{}, {}]
        _windows[window_idx] = window
    window[0][key] = True
    window[1][rcvr_class_name] = True


def on_alloc_object():
    global _allocs_objects
    _allocs_objects += 1


def on_alloc_array():
    global _allocs_arrays
    _allocs_arrays += 1


def _site_class(count):
    if count == 1:
        return "mono"
    if count <= 4:
        return "poly"
    return "mega"


def report():
    path = os.getenv("SOM_PROFILE_OUT", "profile.tsv")
    sites_mono = 0
    sites_poly = 0
    sites_mega = 0
    sites_stable_after_first = 0
    sites_changed_late = 0
    for key, site in _sites.items():
        count, classes, first_idx, last_new_idx = site
        n_classes = len(classes)
        kind = _site_class(n_classes)
        if kind == "mono":
            sites_mono += 1
        elif kind == "poly":
            sites_poly += 1
        else:
            sites_mega += 1
        if last_new_idx == first_idx:
            sites_stable_after_first += 1
        if n_classes > 1 and _sends_total > 0 and last_new_idx > 0.5 * _sends_total:
            sites_changed_late += 1

    window_parts = []
    for window_idx in sorted(_windows.keys()):
        sites_set, classes_set = _windows[window_idx]
        window_parts.append("%d/%d" % (len(sites_set), len(classes_set)))

    report_time = time.time()

    lines = ["# summary"]
    lines.append("classes_loaded\t%d" % _classes_loaded)
    lines.append("methods_defined\t%d" % _methods_defined)
    lines.append("methods_executed\t%d" % len(_methods_executed))
    lines.append("sends_total\t%d" % _sends_total)
    lines.append("sites_executed\t%d" % len(_sites))
    lines.append("sites_mono\t%d" % sites_mono)
    lines.append("sites_poly\t%d" % sites_poly)
    lines.append("sites_mega\t%d" % sites_mega)
    lines.append("sites_stable_after_first\t%d" % sites_stable_after_first)
    lines.append("sites_changed_late\t%d" % sites_changed_late)
    lines.append("allocs_objects\t%d" % _allocs_objects)
    lines.append("allocs_arrays\t%d" % _allocs_arrays)
    lines.append("windows\t%s" % ",".join(window_parts))
    lines.append("init_time\t%f" % _init_time)
    lines.append("report_time\t%f" % report_time)
    lines.append("elapsed_s\t%f" % (report_time - _init_time))
    lines.append("bench_name\t%s" % (_bench_name if _bench_name else ""))
    lines.append("bench_first_send_index\t%d" % _bench_first_send_index)

    lines.append("# sites")
    lines.append(
        "holder\tsignature\tbc_index\tcount\tdistinct_classes\tfirst_send_index\tlast_new_class_index"
    )
    for key, site in _sites.items():
        holder_name, sig_name, bc_index = key
        count, classes, first_idx, last_new_idx = site
        lines.append(
            "%s\t%s\t%d\t%d\t%d\t%d\t%d"
            % (
                holder_name,
                sig_name,
                bc_index,
                count,
                len(classes),
                first_idx,
                last_new_idx,
            )
        )

    with open(path, "w") as fout:
        fout.write("\n".join(lines) + "\n")
