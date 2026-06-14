"""Parser for 2SOM / PyPy ``jit-log-opt`` trace dumps.

A ``PYPYLOG=jit-log-opt:FILE`` dump is a sequence of sections, each of the form::

    [<timestamp>] {jit-log-opt-loop
    # Loop 1 (JUMP_IF_GREATER @ 9 in TraceDemo>>run) : loop with 119 ops
    [i0, i1, p2, p3]
    +315: label(i0, i1, p2, p3, descr=TargetToken(...))
    debug_merge_point(0, 0, 'JUMP_IF_GREATER @ 9 in TraceDemo>>run')
    +320: guard_value(i1, 0, descr=<Guard0x...>) [i0, i1, p2, p3]
    ...
    [<timestamp>] jit-log-opt-loop}

This module turns that into a structured model the dashboard can diff:

* a *trace* is a list of *blocks* (loops, entry bridges, bridges);
* a *block* carries its greenkey, the SOM method it belongs to, its op list,
  and derived stats (ops / guards / residualized sends / inline depth);
* every op is tagged with the inline frame it belongs to, reconstructed from
  the ``debug_merge_point`` inline-depth column -- this is what lets the UI fold
  inlined call frames the way PyPy's jitviewer does.

The two signals that distinguish the tiers live entirely in the op stream:

* ``call_assembler_*``            -- a send the tracer *residualized* into a
                                     separate compiled loop (tier3 structural
                                     recursion bound, or tier5's profile hook);
* ``call_may_force(_residual_send`` -- the tier2 stack-inliner's residual send;
* ``enter_portal_frame`` / ``leave_portal_frame`` -- a send that was *inlined*.
"""

import re
import os

# ---------------------------------------------------------------------------
# line grammar
# ---------------------------------------------------------------------------

_SECTION_OPEN = re.compile(r"^\[[0-9a-f]+\]\s*\{jit-log-opt-(loop|bridge)\s*$")
_SECTION_CLOSE = re.compile(r"^\[[0-9a-f]+\]\s*jit-log-opt-(loop|bridge)\}\s*$")

# "# Loop 1 (JUMP_IF_GREATER @ 9 in TraceDemo>>run) : loop with 119 ops"
# "# Loop 2 (PUSH_FRAME_1 @ 0 in TraceDemo>>inc:) : entry bridge with 16 ops"
_HDR_LOOP = re.compile(
    r"^#\s*Loop\s+(\d+)\s*\((.*?)\)\s*:\s*(loop|entry bridge)\s+with\s+(\d+)\s+ops"
)
# "# bridge out of Guard 0x7b58cae8e610 with 40 ops"
_HDR_BRIDGE = re.compile(r"^#\s*bridge out of Guard\s+(\S+)\s+with\s+(\d+)\s+ops")

# "debug_merge_point(0, 0, 'JUMP_IF_GREATER @ 9 in TraceDemo>>run')"
_MERGE = re.compile(r"^debug_merge_point\((\d+),\s*(\d+),\s*'(.*)'\)\s*$")

# greenkey body: "JUMP_IF_GREATER @ 9 in TraceDemo>>run"
_WHERE = re.compile(r"^(.*?)\s+@\s+(\d+)\s+in\s+(.+?)\s*$")

# "+320: i12 = int_gt(i10, i11)"  /  "+380: guard_false(i12, descr=...) [i0, i1]"
_OP = re.compile(r"^\+(\d+):\s*(.*)$")
_OP_RESULT = re.compile(r"^([a-z_][a-z0-9_]*)\s*=\s*(.*)$")
_OP_NAME = re.compile(r"^([a-z_][a-z0-9_]*)\(")
_DESCR = re.compile(r"descr=(<[^>]*>|TargetToken\([^)]*\)|[^\s,)]+)")
_FAILARGS = re.compile(r"\)\s*\[([^\]]*)\]\s*$")
_INPUTARGS = re.compile(r"^\[[ipfr0-9,\s]*\]\s*$")

# normalisation: things that differ run-to-run but mean "the same op"
_SSA = re.compile(r"\b[ipfr]\d+\b")
_HEXADDR = re.compile(r"0x[0-9a-f]+")
_GUARDD = re.compile(r"<Guard0x[0-9a-f]+>")
_CONSTPTR = re.compile(r"ConstPtr\(ptr\d+\)")
_TARGETTOK = re.compile(r"TargetToken\(\d+\)")
_LOOPTOK = re.compile(r"<Loop-?\d+>")


def _classify(opname):
    if opname.startswith("guard_"):
        return "guard"
    if opname.startswith("call_assembler"):
        return "call_assembler"
    if opname.startswith("call"):
        return "call"
    if opname in ("new", "new_with_vtable", "new_array", "new_array_clear") or \
       opname.startswith("new"):
        return "alloc"
    if opname.startswith("getfield") or opname.startswith("getarrayitem") or \
       opname.startswith("getinteriorfield"):
        return "load"
    if opname.startswith("setfield") or opname.startswith("setarrayitem") or \
       opname.startswith("setinteriorfield"):
        return "store"
    if opname in ("enter_portal_frame", "leave_portal_frame"):
        return "portal"
    if opname in ("label", "jump", "finish"):
        return "control"
    return "other"


def _residual_kind(opname, args):
    """Which compiler mechanism residualized this send:
    'tracing' -- tracing-JIT portal call (call_assembler -> a separate loop);
    'inliner' -- stack-inliner interpreter-level residual (call_may_force into
                 _residual_send_*).  None if the op is not a residual send."""
    if opname.startswith("call_assembler"):
        return "tracing"
    if opname.startswith("call_may_force") and "_residual_send" in args:
        return "inliner"
    return None


_LOOPNUM = re.compile(r"<Loop(-?\d+)>")


class Op:
    __slots__ = ("offset", "result", "opname", "args", "descr", "failargs",
                 "raw", "category", "is_residual_send", "residual_kind",
                 # inline-frame context, filled in after parsing
                 "frame_depth", "frame_method", "frame_bc")

    def __init__(self, offset, result, opname, args, descr, failargs, raw):
        self.offset = offset
        self.result = result
        self.opname = opname
        self.args = args
        self.descr = descr
        self.failargs = failargs
        self.raw = raw
        self.category = _classify(opname)
        self.residual_kind = _residual_kind(opname, args)
        self.is_residual_send = self.residual_kind is not None
        self.frame_depth = 0
        self.frame_method = None
        self.frame_bc = None

    def target_loop(self):
        """For a tracing residual (call_assembler), the callee loop number from
        its descr (<LoopN>), or None for a runtime-resolved portal (<Loop-1>)."""
        if self.residual_kind != "tracing" or not self.descr:
            return None
        m = _LOOPNUM.search(self.descr)
        if m:
            n = int(m.group(1))
            return n if n > 0 else None
        return None

    def signature(self):
        """Run-invariant key used to align two op streams with difflib."""
        s = self.opname + "("
        body = self.args
        body = _CONSTPTR.sub("ConstPtr", body)
        body = _GUARDD.sub("<Guard>", body)
        body = _TARGETTOK.sub("TargetToken", body)
        body = _LOOPTOK.sub("<Loop>", body)
        body = _HEXADDR.sub("0x_", body)
        body = _SSA.sub("%", body)
        s += body + ")"
        return s

    def to_json(self):
        return {
            "offset": self.offset,
            "result": self.result,
            "opname": self.opname,
            "args": self.args,
            "descr": self.descr,
            "failargs": self.failargs,
            "category": self.category,
            "residual": self.is_residual_send,
            "residual_kind": self.residual_kind,
            "target_loop": self.target_loop(),
            "depth": self.frame_depth,
            "method": self.frame_method,
            "bc": self.frame_bc,
            "sig": self.signature(),
        }


class MergePoint:
    """A debug_merge_point: marks the inline frame of the ops that follow."""
    __slots__ = ("index", "depth", "bytecode", "bc_idx", "method", "where")

    def __init__(self, index, depth, where):
        self.index = index
        self.depth = depth
        self.where = where
        m = _WHERE.match(where)
        if m:
            self.bytecode = m.group(1)
            self.bc_idx = int(m.group(2))
            self.method = m.group(3)
        else:
            self.bytecode = where
            self.bc_idx = -1
            self.method = where

    def to_json(self):
        return {
            "kind": "mp",
            "depth": self.depth,
            "bytecode": self.bytecode,
            "bc_idx": self.bc_idx,
            "method": self.method,
        }


class Block:
    """One compiled loop / entry bridge / bridge."""

    def __init__(self, kind, index=None, greenkey=None, guard=None,
                 declared_ops=0):
        self.kind = kind            # "loop" | "entry_bridge" | "bridge"
        self.index = index
        self.greenkey = greenkey
        self.guard = guard
        self.declared_ops = declared_ops
        self.inputargs = []
        self.items = []             # interleaved Op and MergePoint, in order
        # derived
        self.method = None
        self.bytecode = None
        self.bc_idx = None

    # -- stats -------------------------------------------------------------
    @property
    def ops(self):
        return [it for it in self.items if isinstance(it, Op)]

    def count(self, predicate):
        return sum(1 for op in self.ops if predicate(op))

    def stats(self):
        ops = self.ops
        cats = {}
        for op in ops:
            cats[op.category] = cats.get(op.category, 0) + 1
        residual = sum(1 for op in ops if op.is_residual_send)
        tracing_res = sum(1 for op in ops if op.residual_kind == "tracing")
        inliner_res = sum(1 for op in ops if op.residual_kind == "inliner")
        portal_enter = sum(1 for op in ops if op.opname == "enter_portal_frame")
        max_depth = 0
        methods = set()
        for it in self.items:
            if isinstance(it, MergePoint):
                if it.depth > max_depth:
                    max_depth = it.depth
                if it.method:
                    methods.add(it.method)
        return {
            "total": len(ops),
            "guards": cats.get("guard", 0),
            "calls": cats.get("call", 0) + cats.get("call_assembler", 0),
            "call_assembler": cats.get("call_assembler", 0),
            "residual_sends": residual,
            "tracing_residuals": tracing_res,
            "inliner_residuals": inliner_res,
            "allocs": cats.get("alloc", 0),
            "loads": cats.get("load", 0),
            "stores": cats.get("store", 0),
            "portal_inlines": portal_enter,
            "max_inline_depth": max_depth,
            "inlined_methods": sorted(methods),
            "categories": cats,
        }

    def title(self):
        if self.kind == "bridge":
            return "bridge ← Guard %s" % (self.guard or "?")
        label = "Loop %s" % (self.index if self.index is not None else "?")
        if self.kind == "entry_bridge":
            label += " (entry bridge)"
        return label

    def align_key(self):
        """Key for matching the same block across two traces.

        Loops & entry bridges align on their greenkey (method + bytecode);
        bridges have run-specific guard addresses, so they align on the first
        merge point's location instead.
        """
        if self.greenkey:
            return "L:" + self.greenkey
        for it in self.items:
            if isinstance(it, MergePoint):
                return "B:%s@%s" % (it.method, it.bc_idx)
        return "B:%s" % (self.guard or "?")

    def to_json(self):
        return {
            "kind": self.kind,
            "index": self.index,
            "greenkey": self.greenkey,
            "guard": self.guard,
            "method": self.method,
            "bytecode": self.bytecode,
            "bc_idx": self.bc_idx,
            "declared_ops": self.declared_ops,
            "inputargs": self.inputargs,
            "title": self.title(),
            "align_key": self.align_key(),
            "stats": self.stats(),
            "items": [it.to_json() for it in self.items],
        }


def _parse_op(offset, rest, raw):
    m = _OP_RESULT.match(rest)
    if m:
        result = m.group(1)
        rest = m.group(2)
    else:
        result = None
    nm = _OP_NAME.match(rest)
    if not nm:
        # control op with no parens? keep whole token as opname
        opname = rest.split("(")[0].strip()
        args = ""
    else:
        opname = nm.group(1)
        # args = inside outermost parens; failargs/descr extracted from rest
        open_idx = rest.index("(")
        # find matching close paren
        depth = 0
        close_idx = -1
        for i in range(open_idx, len(rest)):
            c = rest[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    close_idx = i
                    break
        args = rest[open_idx + 1:close_idx] if close_idx > open_idx else rest[open_idx + 1:]
    dm = _DESCR.search(rest)
    descr = dm.group(1) if dm else None
    fm = _FAILARGS.search(rest)
    failargs = fm.group(1) if fm else None
    return Op(offset, result, opname, args, descr, failargs, raw)


def parse_text(text):
    """Parse the contents of a jit-log-opt dump -> list[Block]."""
    blocks = []
    cur = None
    section_kind = None
    awaiting_inputargs = False
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        mo = _SECTION_OPEN.match(line)
        if mo:
            section_kind = mo.group(1)
            continue
        if _SECTION_CLOSE.match(line):
            section_kind = None
            cur = None
            continue
        if not line.strip():
            continue

        hl = _HDR_LOOP.match(line)
        if hl:
            kind = "loop" if hl.group(3) == "loop" else "entry_bridge"
            cur = Block(kind, index=int(hl.group(1)), greenkey=hl.group(2),
                        declared_ops=int(hl.group(4)))
            wm = _WHERE.match(hl.group(2))
            if wm:
                cur.bytecode, cur.bc_idx, cur.method = (
                    wm.group(1), int(wm.group(2)), wm.group(3))
            blocks.append(cur)
            awaiting_inputargs = True
            continue
        hb = _HDR_BRIDGE.match(line)
        if hb:
            cur = Block("bridge", guard=hb.group(1),
                        declared_ops=int(hb.group(2)))
            blocks.append(cur)
            awaiting_inputargs = True
            continue

        if cur is None:
            continue

        if awaiting_inputargs and _INPUTARGS.match(line):
            cur.inputargs = [t.strip() for t in line[1:-1].split(",") if t.strip()]
            awaiting_inputargs = False
            continue

        mm = _MERGE.match(line)
        if mm:
            awaiting_inputargs = False
            cur.items.append(MergePoint(int(mm.group(1)), int(mm.group(2)),
                                        mm.group(3)))
            continue

        om = _OP.match(line)
        if om:
            awaiting_inputargs = False
            cur.items.append(_parse_op(int(om.group(1)), om.group(2), line))
            continue
        # anything else (stray lines) is ignored

    _annotate_frames(blocks)
    return blocks


def _annotate_frames(blocks):
    """Tag each Op with the inline frame established by the preceding
    debug_merge_point, so the UI can fold/indent inlined call frames.

    Also give bridges a location: unlike loops/entry-bridges they have no
    greenkey, but their first debug_merge_point is their entry point."""
    for b in blocks:
        if b.method is None:
            for it in b.items:
                if isinstance(it, MergePoint):
                    b.method, b.bytecode, b.bc_idx = (
                        it.method, it.bytecode, it.bc_idx)
                    break
        depth, method, bc = 0, b.method, None
        for it in b.items:
            if isinstance(it, MergePoint):
                depth, method, bc = it.depth, it.method, "%s @ %s" % (
                    it.bytecode, it.bc_idx)
            else:
                it.frame_depth = depth
                it.frame_method = method
                it.frame_bc = bc


# ---------------------------------------------------------------------------
# trace = a parsed file + summary
# ---------------------------------------------------------------------------

def _is_send(bytecode):
    return bytecode.startswith("SEND") or bytecode.startswith("SUPER_SEND")


def classify_send(items, i):
    """Given items[i] is a SEND merge point, decide how that send compiled:
    'inlined'  -- the tracer descended into the callee (portal frame / deeper
                  merge point follows);
    'residual' -- a call_assembler / residual call follows at the same depth
                  (the send was residualized into a separate loop);
    'flat'     -- compiled inline to primitives, no separate frame.
    Returns (outcome, callee, mechanism) where callee is the inlined callee
    method (inlined) or the residual target descr (residual), and mechanism is
    'tracing'/'inliner' for residual sends else None."""
    mp = items[i]
    res_target = None
    res_kind = None
    res_loop = None
    has_portal = False
    for j in range(i + 1, len(items)):
        it = items[j]
        if isinstance(it, MergePoint):
            if it.depth > mp.depth:
                return "inlined", it.method, None, None
            if res_target is not None:
                return "residual", res_target, res_kind, res_loop
            return ("inlined", None, None, None) if has_portal else \
                   ("flat", None, None, None)
        if it.is_residual_send and res_target is None:
            res_target = it.descr or it.opname
            res_kind = it.residual_kind
            res_loop = it.target_loop()
        if it.opname == "enter_portal_frame":
            has_portal = True
    if res_target is not None:
        return "residual", res_target, res_kind, res_loop
    if has_portal:
        return "inlined", None, None, None
    return "flat", None, None, None


class Trace:
    def __init__(self, name, path, blocks):
        self.name = name
        self.path = path
        self.blocks = blocks
        self.summary_stats = None   # SummaryStats from a sibling jit-summary

    def send_sites(self):
        """Aggregate every send site (caller method @ bytecode index) across
        all blocks, recording how it compiled. A site that is *both* residual
        and inlined within this one trace captured the residualize->inline
        transition."""
        sites = {}
        for bi, b in enumerate(self.blocks):
            for i, it in enumerate(b.items):
                if not isinstance(it, MergePoint) or not _is_send(it.bytecode):
                    continue
                outcome, callee, kind, loop = classify_send(b.items, i)
                key = "%s|%d" % (it.method, it.bc_idx)
                s = sites.get(key)
                if s is None:
                    s = sites[key] = {
                        "method": it.method, "bc_idx": it.bc_idx,
                        "bytecode": it.bytecode, "depth": it.depth,
                        "outcomes": {"inlined": 0, "residual": 0, "flat": 0},
                        "inlined_callees": set(), "residual_targets": set(),
                        "residual_kinds": set(), "target_loops": set(),
                        "events": []}
                s["outcomes"][outcome] += 1
                if outcome == "inlined" and callee:
                    s["inlined_callees"].add(callee)
                if outcome == "residual":
                    if callee:
                        s["residual_targets"].add(callee)
                    if kind:
                        s["residual_kinds"].add(kind)
                    if loop:
                        s["target_loops"].add(loop)
                s["events"].append({"block": bi, "outcome": outcome})
        # finalise: dominant outcome + mixed flag + residual mechanism
        for s in sites.values():
            o = s["outcomes"]
            mixed = o["inlined"] > 0 and o["residual"] > 0
            s["mixed"] = mixed
            s["dominant"] = ("mixed" if mixed else
                             "residual" if o["residual"] else
                             "inlined" if o["inlined"] else "flat")
            kinds = s.pop("residual_kinds")
            s["residual_kind"] = ("mixed" if len(kinds) > 1 else
                                  next(iter(kinds)) if kinds else None)
            s["inlined_callees"] = sorted(s["inlined_callees"])
            s["residual_targets"] = sorted(s["residual_targets"])
            s["target_loops"] = sorted(s["target_loops"])
            # the human-facing callee: prefer the name the inlined side gives
            s["callees"] = s["inlined_callees"] or s["residual_targets"]
        return sites

    def summary(self):
        loops = [b for b in self.blocks if b.kind == "loop"]
        entry = [b for b in self.blocks if b.kind == "entry_bridge"]
        bridges = [b for b in self.blocks if b.kind == "bridge"]
        total_ops = guards = call_asm = residual = portal = 0
        tracing_res = inliner_res = 0
        max_depth = 0
        methods = set()
        for b in self.blocks:
            s = b.stats()
            total_ops += s["total"]
            guards += s["guards"]
            call_asm += s["call_assembler"]
            residual += s["residual_sends"]
            tracing_res += s["tracing_residuals"]
            inliner_res += s["inliner_residuals"]
            portal += s["portal_inlines"]
            if s["max_inline_depth"] > max_depth:
                max_depth = s["max_inline_depth"]
            if b.method:
                methods.add(b.method)
            methods.update(s["inlined_methods"])
        # the compiler is identified by how it RESIDUALISES sends: the stack
        # inliner emits call_may_force(_residual_send) at the interpreter level,
        # the tracing JIT emits call_assembler portal calls. enter_portal_frame
        # (inlined sends) is only a weak tracing signal when nothing residualised.
        if inliner_res and tracing_res:
            compiler = "mixed"
        elif inliner_res:
            compiler = "stack-inliner"
        elif tracing_res or portal:
            compiler = "tracing-JIT"
        else:
            compiler = "unknown"
        return {
            "name": self.name,
            "path": self.path,
            "blocks": len(self.blocks),
            "loops": len(loops),
            "entry_bridges": len(entry),
            "bridges": len(bridges),
            "total_ops": total_ops,
            "guards": guards,
            "call_assembler": call_asm,
            "residual_sends": residual,
            "tracing_residuals": tracing_res,
            "inliner_residuals": inliner_res,
            "portal_inlines": portal,
            "max_inline_depth": max_depth,
            "methods": len(methods),
            "compiler": compiler,
        }

    def to_json(self):
        return {
            "name": self.name,
            "path": self.path,
            "summary": self.summary(),
            "summary_stats": self.summary_stats.to_json() if self.summary_stats else None,
            "blocks": [b.to_json() for b in self.blocks],
        }


# ---------------------------------------------------------------------------
# jit-summary (PYPYLOG=jit-summary): aggregate compile-cost stats
# ---------------------------------------------------------------------------

_SUMMARY_LABELS = {
    "ops": "ops", "recorded ops": "recorded_ops", "calls": "calls",
    "guards": "guards", "opt ops": "opt_ops", "opt guards": "opt_guards",
    "Total # of loops": "loops", "Total # of bridges": "bridges",
    "forcings": "forcings",
}


class SummaryStats:
    def __init__(self, d):
        self.d = d

    def to_json(self):
        return self.d


def parse_summary(text):
    d = {"aborts": {}, "raw": {}}
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.endswith("{jit-summary"):
            in_section = True
            continue
        if line.endswith("jit-summary}"):
            in_section = False
            continue
        if not in_section or "\t" not in line:
            continue
        parts = line.split("\t")
        label = parts[0].strip().rstrip(":").strip()
        vals = [p.strip() for p in parts[1:] if p.strip() != ""]
        if not vals:
            continue
        if label in ("Tracing", "Backend"):
            try:
                d[label.lower() + "_count"] = int(vals[0])
                d[label.lower() + "_secs"] = float(vals[1])
            except (ValueError, IndexError):
                pass
        elif label == "TOTAL":
            try:
                d["total_secs"] = float(vals[0])
            except ValueError:
                pass
        elif label.startswith("abort:"):
            reason = label[len("abort:"):].strip()
            try:
                d["aborts"][reason] = int(vals[0])
            except ValueError:
                pass
        elif label in _SUMMARY_LABELS:
            try:
                d[_SUMMARY_LABELS[label]] = int(vals[0])
            except ValueError:
                pass
        else:
            d["raw"][label] = vals[0]
    d["abort_total"] = sum(d["aborts"].values())
    return SummaryStats(d)


def load_summary(path):
    with open(path, "r", errors="replace") as f:
        return parse_summary(f.read())


# ---------------------------------------------------------------------------
# jit-tracing (PYPYLOG=jit-tracing): the bytecode trail the tracer walked
# ---------------------------------------------------------------------------
# Unlike jit-log-opt (the optimised result), jit-tracing records *every*
# source bytecode the tracer stepped through while recording, in order. That is
# exactly "which part of the source program did this trace cover" -- and the
# method changing mid-trail is an inline descent into a callee. A residualised
# send does NOT descend (the callee is traced as its own separate attempt), so
# tier3 shows a few deep walks and tier5 shows many shallow ones.

_TRACING_OPEN = re.compile(r"^\[[0-9a-f]+\]\s*\{jit-tracing\s*$")
_TRACING_CLOSE = re.compile(r"^\[[0-9a-f]+\]\s*jit-tracing\}\s*$")
_TRAIL = re.compile(r"^([A-Z_][A-Z_0-9]*)\s+@\s+(\d+)\s+in\s+(.+?)\s*$")
_OUTCOME = re.compile(r"^compiled new (loop|bridge|entry bridge)\b")
_ABORT = re.compile(r"\b(abort|aborted|cancelled|too long|giving up)\b", re.I)


class TraceAttempt:
    def __init__(self, index):
        self.index = index
        self.greenkey = None
        self.steps = []        # [(bytecode, bc_idx, method)]
        self.depths = []       # inferred inline depth per step
        self.outcome = None    # 'loop' | 'bridge' | 'entry bridge' | None
        self.aborted = False

    def entry_method(self):
        return self.steps[0][2] if self.steps else self.greenkey

    def coverage(self):
        cov = {}
        for bc, idx, method in self.steps:
            cov.setdefault(method, set()).add(idx)
        return cov

    def to_json(self, max_steps=4000):
        steps = self.steps[:max_steps]
        depths = self.depths[:max_steps]
        cov = self.coverage()
        return {
            "index": self.index,
            "greenkey": self.greenkey,
            "entry": self.entry_method(),
            "outcome": self.outcome,
            "aborted": self.aborted,
            "n_steps": len(self.steps),
            "max_depth": max(self.depths) if self.depths else 0,
            "methods": sorted(cov.keys()),
            "coverage": {m: sorted(v) for m, v in cov.items()},
            "steps": [{"bc": s[0], "idx": s[1], "method": s[2], "depth": d}
                      for s, d in zip(steps, depths)],
            "truncated": len(self.steps) > max_steps,
        }


def _annotate_attempt_depth(steps):
    """Infer the inline-call nesting from the flat trail: a method change right
    after a SEND is a descend; a change back to an ancestor is a return."""
    depths = []
    stack = []
    for i, (bc, idx, method) in enumerate(steps):
        if not stack:
            stack = [method]
        else:
            prev_bc, _, prev_m = steps[i - 1]
            if method != prev_m:
                if _is_send(prev_bc):
                    stack.append(method)            # descend into callee
                else:
                    while len(stack) > 1 and stack[-1] != method:
                        stack.pop()                 # return to caller
                    if stack[-1] != method:
                        stack[-1] = method          # sibling / tail
        depths.append(len(stack) - 1)
    return depths


def parse_tracing(text):
    attempts = []
    cur = None
    in_section = False
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if _TRACING_OPEN.match(line):
            in_section = True
            cur = TraceAttempt(len(attempts))
            attempts.append(cur)
            continue
        if _TRACING_CLOSE.match(line):
            if cur is not None:
                cur.depths = _annotate_attempt_depth(cur.steps)
                if cur.outcome is None and cur.steps:
                    cur.aborted = True
            in_section = False
            cur = None
            continue
        if not in_section or cur is None:
            continue
        s = line.strip()
        if not s or s.startswith("JIT starting"):
            continue
        om = _OUTCOME.match(s)
        if om:
            cur.outcome = om.group(1)
            continue
        tm = _TRAIL.match(s)
        if tm:
            cur.steps.append((tm.group(1), int(tm.group(2)), tm.group(3)))
            continue
        if _ABORT.search(s):
            cur.outcome = None
            cur.aborted = True
            continue
        if cur.greenkey is None:        # first non-trail line = greenkey label
            cur.greenkey = s
    # drop empty leading section (the bare "JIT starting" one)
    attempts = [a for a in attempts if a.steps]
    for i, a in enumerate(attempts):
        a.index = i
    return attempts


class TracingLog:
    def __init__(self, attempts):
        self.attempts = attempts

    def coverage(self):
        """method -> {steps: total trail steps, idxs: set of bc indices,
        attempts: how many attempts touched it}."""
        cov = {}
        for a in self.attempts:
            for method, idxs in a.coverage().items():
                c = cov.setdefault(method, {"steps": 0, "idxs": set(),
                                            "attempts": 0})
                c["attempts"] += 1
                c["idxs"] |= idxs
            for bc, idx, method in a.steps:
                cov[method]["steps"] += 1
        return cov

    def summary(self):
        compiled = sum(1 for a in self.attempts if a.outcome)
        aborted = sum(1 for a in self.attempts if a.aborted)
        steps = sum(len(a.steps) for a in self.attempts)
        cov = self.coverage()
        return {
            "attempts": len(self.attempts),
            "compiled": compiled,
            "aborted": aborted,
            "total_steps": steps,
            "methods": len(cov),
            "max_depth": max((max(a.depths) if a.depths else 0
                              for a in self.attempts), default=0),
        }

    def to_json(self, max_attempts=400):
        cov = self.coverage()
        return {
            "summary": self.summary(),
            "coverage": {m: {"steps": c["steps"], "idxs": sorted(c["idxs"]),
                             "attempts": c["attempts"]}
                         for m, c in cov.items()},
            "attempts": [a.to_json() for a in self.attempts[:max_attempts]],
            "truncated": len(self.attempts) > max_attempts,
        }


def load_tracing(path):
    with open(path, "r", errors="replace") as f:
        return TracingLog(parse_tracing(f.read()))


def _sibling_summary(path):
    """Find a jit-summary file that goes with a trace file: NAME.summary next
    to NAME.opt, or the same stem with a .summary/.jitsummary extension."""
    base, _ext = os.path.splitext(path)
    for cand in (base + ".summary", base + ".jitsummary", path + ".summary"):
        if os.path.isfile(cand):
            return cand
    return None


def load(path, name=None):
    with open(path, "r", errors="replace") as f:
        text = f.read()
    blocks = parse_text(text)
    t = Trace(name or os.path.basename(path), path, blocks)
    sib = _sibling_summary(path)
    if sib:
        try:
            t.summary_stats = load_summary(sib)
        except Exception:
            t.summary_stats = None
    return t


if __name__ == "__main__":
    import sys
    import json
    for p in sys.argv[1:]:
        t = load(p)
        print(json.dumps(t.summary(), indent=2))
