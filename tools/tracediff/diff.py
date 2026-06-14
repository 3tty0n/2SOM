"""Align and diff two parsed traces.

Two levels:

* :func:`diff_traces` -- whole-trace: align blocks by greenkey/location and
  report which loops appear in A only, B only, or both, plus per-block stat
  deltas. This drives the summary band and the loop-alignment table.

* :func:`diff_block` -- op-level: align the two op streams of one matched loop
  with ``difflib`` over run-invariant op signatures, so the UI can show a
  side-by-side, gutter-coloured op diff (the jitviewer "loop view").
"""

import difflib

from jitlog import MergePoint


_SUMMARY_METRICS = [
    ("loops", "Loops"),
    ("entry_bridges", "Entry bridges"),
    ("bridges", "Bridges"),
    ("total_ops", "Total ops"),
    ("guards", "Guards"),
    ("tracing_residuals", "Residual — tracing JIT (call_assembler)"),
    ("inliner_residuals", "Residual — stack inliner (_residual_send)"),
    ("portal_inlines", "Inlined sends (portal frames)"),
    ("max_inline_depth", "Max inline depth"),
    ("methods", "Distinct methods"),
]


def _item_sig(it):
    if isinstance(it, MergePoint):
        return "MP|%s@%s|d%d" % (it.method, it.bc_idx, it.depth)
    return "OP|" + it.signature()


def diff_traces(ta, tb):
    sa, sb = ta.summary(), tb.summary()
    summary = []
    for key, label in _SUMMARY_METRICS:
        a, b = sa[key], sb[key]
        summary.append({"key": key, "label": label, "a": a, "b": b,
                        "delta": b - a})

    by_a = {}
    for blk in ta.blocks:
        by_a.setdefault(blk.align_key(), []).append(blk)
    by_b = {}
    for blk in tb.blocks:
        by_b.setdefault(blk.align_key(), []).append(blk)

    rows = []
    keys = list(by_a.keys())
    for k in by_b.keys():
        if k not in by_a:
            keys.append(k)
    for key in keys:
        la = by_a.get(key, [])
        lb = by_b.get(key, [])
        # pair up multiple same-key blocks (e.g. a method compiled twice) by order
        n = max(len(la), len(lb))
        for i in range(n):
            ba = la[i] if i < len(la) else None
            bb = lb[i] if i < len(lb) else None
            ref = ba or bb
            if ba and bb:
                status = "both"
            elif ba:
                status = "only_a"
            else:
                status = "only_b"
            occ = ("#%d" % (i + 1)) if n > 1 else ""
            rows.append({
                "key": key,
                "occurrence": i,
                "occurrence_label": occ,
                "status": status,
                "method": ref.method,
                "bytecode": ref.bytecode,
                "bc_idx": ref.bc_idx,
                "kind": ref.kind,
                "title": ref.title(),
                "a": ba.stats() if ba else None,
                "b": bb.stats() if bb else None,
            })

    # sort: changed/asymmetric first, then by method name
    def sortkey(r):
        a, b = r["a"], r["b"]
        residual_delta = abs((b["residual_sends"] if b else 0) -
                             (a["residual_sends"] if a else 0))
        op_delta = abs((b["total"] if b else 0) - (a["total"] if a else 0))
        only = 0 if r["status"] != "both" else 1
        return (only, -residual_delta, -op_delta, r["method"] or "")

    rows.sort(key=sortkey)
    return {
        "a_name": ta.name, "b_name": tb.name,
        "a_summary": sa, "b_summary": sb,
        "summary": summary, "blocks": rows,
        "send_sites": diff_send_sites(ta, tb),
        "cost": diff_summary_stats(ta, tb),
    }


# --------------------------------------------------------------------------
# send-site map: where each send was inlined vs residualized, per tier
# --------------------------------------------------------------------------

def _residual(s):
    return bool(s) and s["outcomes"]["residual"] > 0


def _inlined(s):
    return bool(s) and s["outcomes"]["inlined"] > 0


def _block_index_map(trace):
    return {b.index: b for b in trace.blocks if b.index is not None}


def _method_block_map(trace):
    """method name -> the standalone-compiled block for it (its entry bridge);
    these are the loops residual sends dispatch into."""
    m = {}
    for b in trace.blocks:
        if not b.method:
            continue
        if b.method not in m or b.kind == "entry_bridge":
            m[b.method] = b
    return m


def _resolve_target(trace, site, callee_method, bim, mbm):
    """Find the compiled loop a residualized send became: first by the
    call_assembler descr loop number, then by the callee method name (which the
    inlined side of the diff supplies)."""
    for n in site.get("target_loops", []):
        if n in bim:
            b = bim[n]
            return {"index": n, "method": b.method, "kind": b.kind,
                    "ops": b.stats()["total"], "align_key": b.align_key(),
                    "via": "descr"}
    if callee_method and callee_method in mbm:
        b = mbm[callee_method]
        return {"index": b.index, "method": b.method, "kind": b.kind,
                "ops": b.stats()["total"], "align_key": b.align_key(),
                "via": "method"}
    return None


def diff_send_sites(ta, tb):
    sa, sb = ta.send_sites(), tb.send_sites()
    keys = set(sa) | set(sb)
    bim_a, mbm_a = _block_index_map(ta), _method_block_map(ta)
    bim_b, mbm_b = _block_index_map(tb), _method_block_map(tb)
    rows = []
    for k in keys:
        a, b = sa.get(k), sb.get(k)
        ad = a["dominant"] if a else "absent"
        bd = b["dominant"] if b else "absent"
        ref = a or b
        # the callee name: whichever side inlined the send names it
        callee_method = None
        for s in (a, b):
            if s and s["inlined_callees"]:
                callee_method = s["inlined_callees"][0]
                break
        a_target = _resolve_target(ta, a, callee_method, bim_a, mbm_a) if _residual(a) else None
        b_target = _resolve_target(tb, b, callee_method, bim_b, mbm_b) if _residual(b) else None
        # classify by whether each side residualized the send at all. This is
        # robust to recursion (where a site is inlined to a depth then bound-
        # residualized within one trace): the question is simply "does this tier
        # residualize this send, and the other inline it?".
        ar, br = _residual(a), _residual(b)
        if a is None:
            change = "only_b"
        elif b is None:
            change = "only_a"
        elif br and not ar:
            change = "residualized"       # B residualizes a send A only inlines (tier5 lever)
        elif ar and not br:
            change = "inlined"            # B inlines a send A residualized
        elif ar and br:
            change = "both_residual"      # both residualize it (shared / structural recursion)
        else:
            change = "both_inlined"       # both inline it (unchanged)
        trivial = (not ar and not br and ad in ("flat", "absent")
                   and bd in ("flat", "absent")) or change == "both_inlined"
        rows.append({
            "key": k, "method": ref["method"], "bc_idx": ref["bc_idx"],
            "bytecode": ref["bytecode"], "callee": callee_method,
            "a_dom": ad, "b_dom": bd, "change": change, "trivial": trivial,
            "a_kind": a["residual_kind"] if a else None,
            "b_kind": b["residual_kind"] if b else None,
            "a_target": a_target, "b_target": b_target,
            "a": _site_json(a), "b": _site_json(b),
        })

    order = {"residualized": 0, "both_residual": 1, "inlined": 2,
             "only_a": 3, "only_b": 4, "both_inlined": 5}
    rows.sort(key=lambda r: (r["trivial"], order.get(r["change"], 9),
                             r["method"] or "", r["bc_idx"]))
    counts = {}
    for r in rows:
        counts[r["change"]] = counts.get(r["change"], 0) + 1
    return {"a_name": ta.name, "b_name": tb.name, "rows": rows, "counts": counts}


def _site_json(s):
    if not s:
        return None
    return {"dominant": s["dominant"], "mixed": s["mixed"],
            "outcomes": s["outcomes"], "callees": s["callees"],
            "residual_kind": s["residual_kind"],
            "target_loops": s["target_loops"], "events": s["events"]}


# --------------------------------------------------------------------------
# compilation cost (jit-summary)
# --------------------------------------------------------------------------

_COST_METRICS = [
    ("total_secs", "Total JIT time (s)", 3),
    ("tracing_secs", "Tracing (s)", 3),
    ("backend_secs", "Backend (s)", 3),
    ("tracing_count", "Tracing #", 0),
    ("loops", "Loops compiled", 0),
    ("bridges", "Bridges compiled", 0),
    ("ops", "Recorded ops (total)", 0),
    ("recorded_ops", "Recorded ops", 0),
    ("guards", "Guards", 0),
    ("opt_ops", "Optimized ops", 0),
    ("abort_total", "Trace aborts", 0),
]


def diff_summary_stats(ta, tb):
    if not ta.summary_stats or not tb.summary_stats:
        return None
    a, b = ta.summary_stats.to_json(), tb.summary_stats.to_json()
    rows = []
    for key, label, prec in _COST_METRICS:
        av, bv = a.get(key), b.get(key)
        if av is None and bv is None:
            continue
        delta = (bv - av) if (av is not None and bv is not None) else None
        ratio = (bv / av) if (av and bv is not None) else None
        rows.append({"key": key, "label": label, "prec": prec,
                     "a": av, "b": bv, "delta": delta, "ratio": ratio})
    return {"rows": rows, "a_aborts": a.get("aborts", {}),
            "b_aborts": b.get("aborts", {})}


def _find_block(trace, key, occurrence):
    matches = [b for b in trace.blocks if b.align_key() == key]
    if occurrence < len(matches):
        return matches[occurrence]
    return matches[0] if matches else None


def diff_block(ta, tb, key, occurrence=0):
    ba = _find_block(ta, key, occurrence)
    bb = _find_block(tb, key, occurrence)
    items_a = ba.items if ba else []
    items_b = bb.items if bb else []
    sig_a = [_item_sig(it) for it in items_a]
    sig_b = [_item_sig(it) for it in items_b]

    sm = difflib.SequenceMatcher(a=sig_a, b=sig_b, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for off in range(i2 - i1):
                rows.append({"tag": "equal",
                             "a": items_a[i1 + off].to_json(),
                             "b": items_b[j1 + off].to_json()})
        elif tag == "replace":
            # pad the shorter side so rows stay aligned
            la, lb = i2 - i1, j2 - j1
            for off in range(max(la, lb)):
                a = items_a[i1 + off].to_json() if off < la else None
                b = items_b[j1 + off].to_json() if off < lb else None
                rows.append({"tag": "replace", "a": a, "b": b})
        elif tag == "delete":
            for off in range(i2 - i1):
                rows.append({"tag": "delete",
                             "a": items_a[i1 + off].to_json(), "b": None})
        elif tag == "insert":
            for off in range(j2 - j1):
                rows.append({"tag": "insert", "a": None,
                             "b": items_b[j1 + off].to_json()})

    added = sum(1 for r in rows if r["tag"] == "insert")
    removed = sum(1 for r in rows if r["tag"] == "delete")
    changed = sum(1 for r in rows if r["tag"] == "replace")
    return {
        "key": key,
        "occurrence": occurrence,
        "a": ba.to_json() if ba else None,
        "b": bb.to_json() if bb else None,
        "rows": rows,
        "stat": {"added": added, "removed": removed, "changed": changed,
                 "same": sum(1 for r in rows if r["tag"] == "equal")},
    }
