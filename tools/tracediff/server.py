#!/usr/bin/env python3
"""Local dashboard server for diffing 2SOM jit-log-opt traces.

Run::

    python3 tools/tracediff/server.py            # scans ./traces and /tmp
    python3 tools/tracediff/server.py -d /path   # add more trace dirs
    python3 tools/tracediff/server.py -p 8099    # pick a port

Then open the printed http://localhost:PORT/ URL.

It is a read-only viewer: it parses the ``*.opt`` files it finds and serves
JSON the single-page front-end renders. No external dependencies.
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import jitlog  # noqa: E402
import diff as diffmod  # noqa: E402

STATIC = os.path.join(HERE, "static")
TRACE_EXTS = (".opt", ".log")

_CACHE = {}          # path -> (mtime, Trace)
_TRACING_CACHE = {}  # path -> (mtime, TracingLog)


class Catalog:
    def __init__(self, dirs):
        self.dirs = dirs

    def _files(self):
        seen = {}
        for d in self.dirs:
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(TRACE_EXTS):
                    continue
                path = os.path.join(d, fn)
                if not os.path.isfile(path):
                    continue
                name = fn
                # disambiguate same-named files from different dirs
                if name in seen and seen[name] != path:
                    name = os.path.basename(d.rstrip("/")) + "/" + fn
                seen[name] = path
        return seen

    def resolve(self, name):
        return self._files().get(name)

    def resolve_any(self, name):
        """Resolve a bare filename (any extension) within a scanned dir."""
        base = os.path.basename(name)
        for d in self.dirs:
            p = os.path.join(d, base)
            if os.path.isfile(p):
                return p
        return None

    def siblings(self, name):
        """Companion logs of a trace: NAME.summary / NAME.tracing, etc."""
        path = self.resolve(name)
        if not path:
            return {}
        d = os.path.dirname(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        out = {}
        for kind, exts in (("summary", (".summary", ".jitsummary")),
                           ("tracing", (".tracing", ".jit-tracing"))):
            for ext in exts:
                cand = stem + ext
                if os.path.isfile(os.path.join(d, cand)):
                    out[kind] = cand
                    break
        return out

    def load(self, name):
        path = self.resolve(name)
        if not path:
            return None
        mtime = os.path.getmtime(path)
        cached = _CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        trace = jitlog.load(path, name=name)
        _CACHE[path] = (mtime, trace)
        return trace

    def load_tracing(self, name):
        """Parse the jit-tracing companion of a trace (NAME.tracing)."""
        sib = self.siblings(name).get("tracing")
        if not sib:
            return None
        path = os.path.join(os.path.dirname(self.resolve(name)), sib)
        if not os.path.isfile(path):
            return None
        mtime = os.path.getmtime(path)
        cached = _TRACING_CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        tl = jitlog.load_tracing(path)
        _TRACING_CACHE[path] = (mtime, tl)
        return tl

    def list(self):
        out = []
        for name, path in self._files().items():
            try:
                t = self.load(name)
                s = t.summary()
                out.append({
                    "name": name, "path": path,
                    "size": os.path.getsize(path),
                    "loops": s["loops"], "bridges": s["bridges"],
                    "total_ops": s["total_ops"],
                    "call_assembler": s["call_assembler"],
                    "residual_sends": s["residual_sends"],
                    "portal_inlines": s["portal_inlines"],
                    "max_inline_depth": s["max_inline_depth"],
                    "has_summary": t.summary_stats is not None,
                    "siblings": self.siblings(name),
                })
            except Exception as e:  # pragma: no cover - never break the list
                out.append({"name": name, "path": path, "error": str(e)})
        out.sort(key=lambda r: r["name"])
        return out


class Handler(BaseHTTPRequestHandler):
    catalog = None

    def log_message(self, *a):  # quieter console
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path

        if path == "/" or path == "/index.html":
            return self._send_file(os.path.join(STATIC, "index.html"),
                                   "text/html; charset=utf-8")
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            full = os.path.normpath(os.path.join(STATIC, rel))
            if not full.startswith(STATIC):
                return self.send_error(403)
            ctype = ("text/css" if rel.endswith(".css")
                     else "application/javascript" if rel.endswith(".js")
                     else "application/octet-stream")
            return self._send_file(full, ctype + "; charset=utf-8")

        if path == "/api/traces":
            return self._send_json(self.catalog.list())

        if path == "/api/raw":
            name = (q.get("name") or [None])[0]
            full = self.catalog.resolve_any(name) if name else None
            if not full:
                return self.send_error(404)
            return self._send_file(full, "text/plain; charset=utf-8")

        if path == "/api/trace":
            name = (q.get("name") or [None])[0]
            t = self.catalog.load(name) if name else None
            if not t:
                return self._send_json({"error": "not found: %s" % name}, 404)
            return self._send_json(t.to_json())

        if path == "/api/diff":
            a = (q.get("a") or [None])[0]
            b = (q.get("b") or [None])[0]
            ta, tb = self.catalog.load(a), self.catalog.load(b)
            if not ta or not tb:
                return self._send_json({"error": "trace not found"}, 404)
            return self._send_json(diffmod.diff_traces(ta, tb))

        if path == "/api/tracing":
            name = (q.get("name") or [None])[0]
            tl = self.catalog.load_tracing(name) if name else None
            if tl is None:
                return self._send_json({"error": "no jit-tracing for %s" % name}, 404)
            return self._send_json(tl.to_json())

        if path == "/api/blockdiff":
            a = (q.get("a") or [None])[0]
            b = (q.get("b") or [None])[0]
            key = (q.get("key") or [None])[0]
            occ = int((q.get("occ") or ["0"])[0])
            ta, tb = self.catalog.load(a), self.catalog.load(b)
            if not ta or not tb:
                return self._send_json({"error": "trace not found"}, 404)
            return self._send_json(diffmod.diff_block(ta, tb, key, occ))

        return self.send_error(404)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-d", "--traces-dir", action="append", default=[],
                    help="extra directory to scan for *.opt/*.log (repeatable)")
    ap.add_argument("-p", "--port", type=int, default=8077)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-root", action="store_true",
                    help="do not scan the project root for trace logs")
    ap.add_argument("--tmp", action="store_true",
                    help="also scan /tmp for trace logs")
    args = ap.parse_args()

    dirs = list(args.traces_dir)
    dirs.append(os.path.join(HERE, "traces"))
    if not args.no_root:
        dirs.append(PROJECT_ROOT)
    if args.tmp:
        dirs.append("/tmp")
    Handler.catalog = Catalog(dirs)

    n = len(Handler.catalog._files())
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print("2SOM TraceDiff dashboard")
    print("  scanning: %s" % ", ".join(dirs))
    print("  found:    %d trace file(s)" % n)
    print("  open:     http://%s:%d/" % (args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
