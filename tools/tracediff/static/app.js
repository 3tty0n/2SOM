"use strict";

const $ = (s) => document.querySelector(s);
const state = { traces: [], diff: null, sel: null }; // sel = {key, occ}

function el(tag, attrs, kids) {
  const e = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    if (k === "class") e.className = attrs[k];
    else if (k === "html") e.innerHTML = attrs[k];
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), attrs[k]);
    else if (k === "data") for (const d in attrs.data) e.dataset[d] = attrs.data[d];
    else e.setAttribute(k, attrs[k]);
  }
  if (kids != null) (Array.isArray(kids) ? kids : [kids]).forEach((c) =>
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return e;
}
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])));
const fmt = (n) => (n == null ? "·" : n.toLocaleString());

async function api(path) {
  const r = await fetch(path);
  return r.json();
}

// ----------------------------------------------------------------- bootstrap
async function loadTraces() {
  state.traces = await api("/api/traces");
  const opt = (t) => {
    const tags = t.error ? "ERR" :
      `${t.loops}L ${t.bridges}b · ${t.total_ops}ops · ca=${t.call_assembler} resid=${t.residual_sends} inl=${t.portal_inlines}`;
    const o = el("option", { value: t.name }, `${t.name}   —   ${tags}`);
    return o;
  };
  for (const sel of [$("#selA"), $("#selB")]) {
    sel.innerHTML = "";
    state.traces.forEach((t) => sel.appendChild(opt(t)));
  }
  // sensible default pair: same program stem, max divergence in residual count
  pickDefaults();
  await refreshDiff();
}

function pickDefaults() {
  const ts = state.traces.filter((t) => !t.error);
  if (ts.length < 2) return;
  const names = new Set(ts.map((t) => t.name));
  // prefer the clean non-recursive lever demo if it is present
  for (const [a, b] of [["bounce.tier3.opt", "bounce.tier5resid.opt"],
                        ["bounce.tier3.opt", "bounce.tier5.opt"]]) {
    if (names.has(a) && names.has(b)) {
      $("#selA").value = a; $("#selB").value = b; return;
    }
  }
  let best = [ts[0], ts[1]], bestScore = -1;
  for (let i = 0; i < ts.length; i++)
    for (let j = 0; j < ts.length; j++) {
      if (i === j) continue;
      const a = ts[i], b = ts[j];
      const stem = (s) => s.replace(/[._-](tier\d.*|t\d.*|counter|phase|latch|purge.*|noquiesce|mode|opt).*$/i, "");
      const sameProg = stem(a.name) && stem(a.name) === stem(b.name);
      const div = Math.abs(a.residual_sends - b.residual_sends) +
        Math.abs(a.call_assembler - b.call_assembler);
      const score = (sameProg ? 100 : 0) + div;
      if (score > bestScore) { bestScore = score; best = [a, b]; }
    }
  $("#selA").value = best[0].name;
  $("#selB").value = best[1].name;
}

async function refreshDiff() {
  const a = $("#selA").value, b = $("#selB").value;
  if (!a || !b) return;
  $("#summary").innerHTML = '<div class="spinner">parsing & diffing…</div>';
  const d = await api(`/api/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  if (d.error) { $("#summary").innerHTML = `<div class="err">${esc(d.error)}</div>`; return; }
  state.diff = d;
  renderLogs(a, b);
  renderCost(d.cost);
  renderSendMap(d.send_sites);
  renderTraced(a, b);
  renderSummary(d);
  renderTable(d);
  $("#opdiff").classList.add("hidden");
  state.sel = null;
}

// ------------------------------------------------- traced source (jit-tracing)
async function renderTraced(aName, bName) {
  const sec = $("#traced");
  const byName = {};
  state.traces.forEach((t) => (byName[t.name] = t));
  const aHas = (byName[aName]?.siblings || {}).tracing;
  const bHas = (byName[bName]?.siblings || {}).tracing;
  if (!aHas && !bHas) { sec.classList.add("hidden"); state.traced = null; return; }
  sec.classList.remove("hidden");
  $("#tracedCoverage").innerHTML = "";
  $("#tracedCols").innerHTML = '<div class="spinner">parsing jit-tracing…</div>';
  const [ta, tb] = await Promise.all([
    aHas ? api(`/api/tracing?name=${encodeURIComponent(aName)}`) : null,
    bHas ? api(`/api/tracing?name=${encodeURIComponent(bName)}`) : null,
  ]);
  state.traced = { a: ta && !ta.error ? ta : null, b: tb && !tb.error ? tb : null,
                   aName, bName };
  drawTraced();
}

function drawTraced() {
  const t = state.traced; if (!t) return;
  if ($("#tCoverage").checked) drawCoverage(t); else $("#tracedCoverage").innerHTML = "";
  const cols = $("#tracedCols"); cols.innerHTML = "";
  cols.appendChild(tracedColumn("A", t.aName, t.a, "var(--a-color)"));
  cols.appendChild(tracedColumn("B", t.bName, t.b, "var(--b-color)"));
}

// per-source-method coverage comparison: steps walked / distinct bytecodes
function drawCoverage(t) {
  const wrap = $("#tracedCoverage"); wrap.innerHTML = "";
  const ca = (t.a && t.a.coverage) || {}, cb = (t.b && t.b.coverage) || {};
  const meths = [...new Set([...Object.keys(ca), ...Object.keys(cb)])];
  meths.sort((m1, m2) => (cb[m2]?.steps || 0) + (ca[m2]?.steps || 0) -
    ((cb[m1]?.steps || 0) + (ca[m1]?.steps || 0)));
  const tbl = el("table", { class: "covtbl" });
  tbl.appendChild(el("thead", null, el("tr", null,
    ["Source method", "A steps", "A bc", "B steps", "B bc", "walk depth A→B"].map((c) =>
      el("th", null, c)))));
  const maxSteps = Math.max(1, ...meths.map((m) =>
    Math.max(ca[m]?.steps || 0, cb[m]?.steps || 0)));
  const tb = el("tbody");
  for (const m of meths) {
    const a = ca[m], b = cb[m];
    const as = a ? a.steps : 0, bs = b ? b.steps : 0;
    const tr = el("tr");
    tr.appendChild(el("td", { class: "covm" }, m));
    tr.appendChild(barCell(as, maxSteps, "var(--a-color)"));
    tr.appendChild(el("td", { class: "num" }, a ? "" + a.idxs.length : "·"));
    tr.appendChild(barCell(bs, maxSteps, "var(--b-color)"));
    tr.appendChild(el("td", { class: "num" }, b ? "" + b.idxs.length : "·"));
    const dd = (as && !bs) ? "dropped in B" : (bs && !as) ? "new in B" :
      (as > bs ? "shallower in B" : as < bs ? "deeper in B" : "same");
    tr.appendChild(el("td", { class: "covdelta" }, dd));
    tb.appendChild(tr);
  }
  tbl.appendChild(tb);
  wrap.appendChild(tbl);
}
function barCell(v, max, color) {
  const td = el("td", { class: "num barcell" });
  const bar = el("span", { class: "bar", style:
    `width:${Math.round(60 * v / max)}px;background:${color}` });
  td.appendChild(bar);
  td.appendChild(el("span", { class: "barnum" }, "" + v));
  return td;
}

function tracedColumn(label, name, data, color) {
  const col = el("div", { class: "tracedcol" });
  if (!data) {
    col.appendChild(el("div", { class: "tracedhead" },
      [el("span", { class: "tlbl", style: `color:${color}` }, label),
       el("span", { class: "tname" }, "(no jit-tracing)")]));
    return col;
  }
  const s = data.summary;
  col.appendChild(el("div", { class: "tracedhead" }, [
    el("span", { class: "tlbl", style: `color:${color}` }, label),
    el("span", { class: "tname" }, name),
    el("span", { class: "tstat" },
      `${s.attempts} attempts · ${s.compiled} compiled · ${s.aborted} aborted · ` +
      `max inline depth ${s.max_depth} · ${s.total_steps} steps`),
  ]));
  const showAborted = $("#tAborted").checked;
  const list = el("div", { class: "attemptlist" });
  for (const a of data.attempts) {
    if (a.aborted && !showAborted) continue;
    list.appendChild(attemptItem(a, color));
  }
  col.appendChild(list);
  return col;
}

function attemptItem(a, color) {
  const head = el("div", { class: "attempthead" + (a.aborted ? " aborted" : "") }, [
    el("span", { class: "fold" }, "▸"),
    el("span", { class: "anum" }, "#" + a.index),
    el("span", { class: "aentry" }, a.entry),
    el("span", { class: "aoutcome " + (a.outcome ? "ok" : "ab") },
      a.outcome ? "→ " + a.outcome : "→ aborted"),
    el("span", { class: "ameta" },
      `${a.n_steps} steps · d${a.max_depth} · ${a.methods.length} methods`),
  ]);
  const body = el("div", { class: "attemptbody frame-hidden" });
  let built = false;
  const build = () => {
    let lastMethod = null;
    for (const st of a.steps) {
      const send = /^SEND|^SUPER_SEND/.test(st.bc);
      const enter = st.method !== lastMethod;
      lastMethod = st.method;
      const row = el("div", { class: "tstep d" + Math.min(st.depth, 6) +
        (send ? " send" : "") + (enter ? " enter" : "") });
      row.style.paddingLeft = (8 + st.depth * 14) + "px";
      row.appendChild(el("span", { class: "tbc" }, st.bc));
      row.appendChild(el("span", { class: "tidx" }, " @" + st.idx));
      if (enter) row.appendChild(el("span", { class: "tmeth" }, "  " + st.method));
      body.appendChild(row);
    }
    if (a.truncated) body.appendChild(el("div", { class: "tstep trunc" },
      "… trail truncated"));
  };
  head.addEventListener("click", () => {
    const hidden = body.classList.toggle("frame-hidden");
    head.querySelector(".fold").textContent = hidden ? "▸" : "▾";
    if (!hidden && !built) { built = true; build(); }
  });
  return el("div", { class: "attempt" }, [head, body]);
}

// ----------------------------------------------------- companion log links
function renderLogs(aName, bName) {
  const sec = $("#logs");
  const byName = {};
  state.traces.forEach((t) => (byName[t.name] = t));
  const comp = { a: state.diff.a_summary.compiler, b: state.diff.b_summary.compiler };
  const side = (label, name, color, compiler) => {
    const t = byName[name] || {};
    const sib = t.siblings || {};
    const links = [el("span", { class: "logtag", style: `color:${color}` }, label),
      compilerBadge(compiler),
      el("span", { class: "logname" }, name)];
    links.push(rawLink("log-opt", name));
    if (sib.summary) links.push(rawLink("summary", sib.summary));
    if (sib.tracing) links.push(rawLink("tracing", sib.tracing));
    return el("div", { class: "logside" }, links);
  };
  sec.innerHTML = "";
  sec.appendChild(side("A", aName, "var(--a-color)", comp.a));
  sec.appendChild(side("B", bName, "var(--b-color)", comp.b));
  sec.classList.remove("hidden");
}
const COMPILER_LABEL = {
  "tracing-JIT": "tracing JIT", "stack-inliner": "stack inliner",
  "mixed": "mixed (inliner + tracing)", "unknown": "—",
};
function compilerBadge(c) {
  return el("span", { class: "compiler " + (c || "unknown"),
    title: "compiled by " + (COMPILER_LABEL[c] || c) }, COMPILER_LABEL[c] || c);
}
function rawLink(label, name) {
  return el("a", { class: "rawlink", href: `/api/raw?name=${encodeURIComponent(name)}`,
    target: "_blank" }, label);
}

// ------------------------------------------------------- compilation cost
function renderCost(cost) {
  const sec = $("#cost"), body = $("#costBody");
  if (!cost || !cost.rows || !cost.rows.length) { sec.classList.add("hidden"); return; }
  sec.classList.remove("hidden");
  body.innerHTML = "";
  const grid = el("div", { class: "costgrid" });
  for (const r of cost.rows) {
    const a = r.a, b = r.b;
    const fmtv = (v) => v == null ? "·" : (r.prec ? v.toFixed(r.prec) : v.toLocaleString());
    let badge = "", cls = "flat";
    if (r.ratio != null && a) {
      const pct = Math.round((r.ratio - 1) * 100);
      cls = pct < 0 ? "down" : pct > 0 ? "up" : "flat";
      badge = (pct > 0 ? "+" : "") + pct + "%";
    } else if (r.delta != null) {
      cls = r.delta < 0 ? "down" : r.delta > 0 ? "up" : "flat";
      badge = (r.delta > 0 ? "+" : "") + r.delta;
    }
    grid.appendChild(el("div", { class: "costcard" }, [
      el("div", { class: "label" }, r.label),
      el("div", { class: "vals" }, [
        el("span", { class: "va" }, fmtv(a)),
        el("span", { class: "arrow" }, "→"),
        el("span", { class: "vb" }, fmtv(b)),
        el("span", { class: "delta " + cls }, badge),
      ]),
    ]));
  }
  body.appendChild(grid);
}

// --------------------------------------------- inline <-> residualize map
const SITE_ORDER_LABEL = {
  residualized: "B residualized · A inlined",
  both_residual: "both residualize (shared / recursion)",
  inlined: "B inlined · A residualized",
  only_a: "only in A", only_b: "only in B",
  both_inlined: "both inline (unchanged)",
};

function renderSendMap(sm) {
  const sec = $("#sendmap");
  if (!sm || !sm.rows) { sec.classList.add("hidden"); return; }
  sec.classList.remove("hidden");
  // headline counts
  const cc = $("#sendmapCounts"); cc.innerHTML = "";
  const order = ["residualized", "both_residual", "inlined", "only_a", "only_b"];
  for (const k of order) {
    if (sm.counts[k]) cc.appendChild(el("span", { class: "cnt " + k },
      `${sm.counts[k]} ${SITE_ORDER_LABEL[k]}`));
  }
  state.sendmap = sm;
  drawSendMap();
}

function drawSendMap() {
  const sm = state.sendmap;
  const hideTrivial = $("#mHideTrivial").checked;
  const body = $("#sendmapBody"); body.innerHTML = "";
  const tbl = el("table", { class: "sendtbl" });
  tbl.appendChild(el("thead", null, el("tr", null,
    ["Caller send", "Callee", "A", "", "B", "what changed", "→ became loop"].map((c) =>
      el("th", null, c)))));
  const tb = el("tbody");
  let shown = 0;
  for (const r of sm.rows) {
    if (hideTrivial && (r.trivial || r.change === "same")) continue;
    shown++;
    const callee = r.callee ||
      ((r.a && r.a.callees) || (r.b && r.b.callees) || []).join(", ");
    const tr = el("tr", { class: "srow change-" + r.change });
    tr.appendChild(el("td", { class: "site" }, [
      el("span", { class: "smethod" }, r.method || "?"),
      el("span", { class: "sbc" }, `  ${r.bytecode} @ ${r.bc_idx}`)]));
    tr.appendChild(el("td", { class: "callee" }, callee || "—"));
    tr.appendChild(el("td", null, sitePill(r.a, r.a_kind)));
    tr.appendChild(el("td", { class: "arrowc" }, r.a_dom !== r.b_dom ? "→" : ""));
    tr.appendChild(el("td", null, sitePill(r.b, r.b_kind)));
    tr.appendChild(el("td", null, el("span", { class: "changelbl " + r.change },
      SITE_ORDER_LABEL[r.change] || r.change)));
    tr.appendChild(el("td", { class: "became" }, targetCell(r)));
    tb.appendChild(tr);
    // compile-order strip when one side compiled this send both ways
    const mixedSide = (r.b && r.b.mixed) ? r.b : (r.a && r.a.mixed) ? r.a : null;
    if (mixedSide) tb.appendChild(timelineRow(mixedSide, r.b && r.b.mixed ? "B" : "A"));
  }
  tbl.appendChild(tb);
  body.appendChild(tbl);
  if (!shown) body.appendChild(el("div", { class: "placeholder" },
    "No send-strategy differences (uncheck the filter to see all sends)."));
}

function sitePill(s, kind) {
  if (!s) return el("span", { class: "pill absent" }, "—");
  const o = s.outcomes;
  let txt = s.dominant;
  if (s.mixed) txt = `${o.residual}×resid ${o.inlined}×inl`;
  else if (o[s.dominant] > 1) txt += ` ×${o[s.dominant]}`;
  const kindCls = (s.dominant === "residual" || s.mixed) && kind ? " k-" + kind : "";
  const k = (s.dominant === "residual" && kind) ?
    el("span", { class: "kindtag k-" + kind }, kind === "tracing" ? "trace" : kind) : "";
  return el("span", { class: "pill " + s.dominant + kindCls }, [txt, k]);
}

// "→ became Loop N 'callee' (M ops)", clickable to open that loop's view
function targetCell(r) {
  const parts = [];
  const mk = (t, side, color) => {
    if (!t) return;
    const a = el("a", { class: "becamelink", href: "#",
      onclick: (e) => { e.preventDefault(); openOpDiff(t.align_key, 0); } },
      `${side}: Loop ${t.index} · ${t.ops} ops`);
    a.style.color = color;
    parts.push(el("span", { class: "becamewrap" },
      [el("span", { class: "becamearrow" }, "↳ "), a]));
  };
  // show the side(s) that residualized
  if (r.b_target && r.a_target && r.a_target.index === r.b_target.index) {
    mk(r.b_target, "both", "var(--resid)");
  } else {
    mk(r.a_target, "A", "var(--a-color)");
    mk(r.b_target, "B", "var(--b-color)");
  }
  return parts.length ? parts : el("span", { class: "becamenone" },
    r.change === "residualized" || r.change === "both_residual" ? "portal (runtime-resolved)" : "");
}

// a compile-order strip: one cell per compiled block (in compile order), coloured
// by how this send compiled there — visualises residualized-early, inlined-later.
function timelineRow(side, which) {
  const perBlock = new Map();   // block index -> outcome (residual wins if present)
  for (const e of side.events) {
    const cur = perBlock.get(e.block);
    if (cur === "residual") continue;
    if (e.outcome === "residual" || cur == null) perBlock.set(e.block, e.outcome);
  }
  const blocks = [...perBlock.keys()].sort((a, b) => a - b);
  const cells = blocks.map((bi) => {
    const o = perBlock.get(bi);
    return el("span", { class: "tlcell " + o, title: `compiled block #${bi}: ${o}` },
      o === "residual" ? "R" : o === "inlined" ? "I" : "·");
  });
  return el("tr", { class: "tlrow" }, el("td", { colspan: "6" }, [
    el("span", { class: "tllabel" },
      `${which} compile order → (R = residualized, I = inlined; each cell = one compiled trace)`),
    el("span", { class: "tlstrip" }, cells),
  ]));
}

// ------------------------------------------------------------------- summary
// For these metrics, B>A (more) is "worse" (red) except where noted.
const GOOD_WHEN_LOWER = new Set([
  "total_ops", "guards", "tracing_residuals", "inliner_residuals",
  "bridges", "entry_bridges"]);

function renderSummary(d) {
  const wrap = $("#summary");
  wrap.innerHTML = "";
  wrap.appendChild(metaCard(d));
  for (const m of d.summary) {
    const delta = m.delta;
    let cls = "flat", txt = "0";
    if (delta !== 0) {
      const lowerGood = GOOD_WHEN_LOWER.has(m.key);
      cls = delta > 0 ? (lowerGood ? "up" : "down") : (lowerGood ? "down" : "up");
      txt = (delta > 0 ? "+" : "") + delta;
    }
    const hero = (m.key === "tracing_residuals" || m.key === "inliner_residuals");
    const card = el("div", { class: "card" + (hero ? " hero" : "") }, [
      el("div", { class: "label" }, m.label),
      el("div", { class: "vals" }, [
        el("span", { class: "va" }, fmt(m.a)),
        el("span", { class: "arrow" }, "→"),
        el("span", { class: "vb" }, fmt(m.b)),
        el("span", { class: "delta " + cls }, txt),
      ]),
    ]);
    wrap.appendChild(card);
  }
}

function metaCard(d) {
  return el("div", { class: "card" }, [
    el("div", { class: "label" }, "Traces (A → B)"),
    el("div", { class: "vals" }, [
      el("span", { class: "va", style: "font-size:13px" }, d.a_name),
      el("span", { class: "arrow" }, "→"),
      el("span", { class: "vb", style: "font-size:13px" }, d.b_name),
    ]),
  ]);
}

// --------------------------------------------------------------------- table
function renderTable(d) {
  const cols = ["", "Method", "Bytecode", "Kind", "ops A", "ops B", "Δ",
    "resid A", "resid B", "guards A", "guards B", "inline", "send"];
  const t = el("table");
  const thead = el("thead", null, el("tr", null, cols.map((c) =>
    el("th", { class: /A$|B$|^Δ$/.test(c) ? "num" : "" }, c))));
  t.appendChild(thead);
  const tb = el("tbody");
  for (const r of d.blocks) {
    const a = r.a, b = r.b;
    const opA = a ? a.total : null, opB = b ? b.total : null;
    const odelta = (opA != null && opB != null) ? opB - opA : null;
    const residA = a ? a.residual_sends : 0, residB = b ? b.residual_sends : 0;
    const inlA = a ? a.portal_inlines : 0, inlB = b ? b.portal_inlines : 0;
    const depthA = a ? a.max_inline_depth : 0, depthB = b ? b.max_inline_depth : 0;
    // characterise: did the send strategy change?
    let sendBadge = "";
    if (residB > residA) sendBadge = "more residual";
    else if (residB < residA) sendBadge = "more inlined";
    else if (residA > 0) sendBadge = "residual";
    else if (inlA + inlB > 0) sendBadge = "inlined";

    const tr = el("tr", { class: "row", data: { key: r.key, occ: r.occurrence } }, [
      el("td", null, [el("span", { class: "statusdot " + r.status }),
        r.occurrence_label || ""]),
      el("td", { class: "method" }, (r.method || "?") + (r.occurrence_label ? " " + r.occurrence_label : "")),
      el("td", { class: "bc" }, r.bytecode ? `${r.bytecode} @ ${r.bc_idx}` : ""),
      el("td", null, el("span", { class: "badge kind" }, r.kind.replace("_", " "))),
      el("td", { class: "num" }, fmt(opA)),
      el("td", { class: "num" }, fmt(opB)),
      el("td", { class: "num" }, deltaSpan(odelta)),
      el("td", { class: "num" + (residA ? "" : "") }, fmt(residA)),
      el("td", { class: "num" }, fmt(residB)),
      el("td", { class: "num" }, fmt(a ? a.guards : null)),
      el("td", { class: "num" }, fmt(b ? b.guards : null)),
      el("td", { class: "num" }, `${depthA}/${depthB}`),
      el("td", null, sendBadge ?
        el("span", { class: "badge " + (sendBadge.includes("residual") ? "resid" : "inl") }, sendBadge) : ""),
    ]);
    tr.addEventListener("click", () => openOpDiff(r.key, r.occurrence, tr));
    tb.appendChild(tr);
  }
  t.appendChild(tb);
  $("#blockTable").innerHTML = "";
  $("#blockTable").appendChild(t);
}

function deltaSpan(d) {
  if (d == null) return el("span", null, "·");
  if (d === 0) return el("span", { class: "cell-delta" }, "0");
  return el("span", { class: "cell-delta " + (d > 0 ? "up" : "down") },
    (d > 0 ? "+" : "") + d);
}

// ------------------------------------------------------------------- op diff
async function openOpDiff(key, occ, tr) {
  document.querySelectorAll("#blockTable tr.sel").forEach((x) => x.classList.remove("sel"));
  if (tr) tr.classList.add("sel");
  state.sel = { key, occ };
  const a = $("#selA").value, b = $("#selB").value;
  $("#opdiff").classList.remove("hidden");
  $("#opdiffBody").innerHTML = '<div class="spinner">aligning ops…</div>';
  const bd = await api(`/api/blockdiff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&key=${encodeURIComponent(key)}&occ=${occ}`);
  state.blockdiff = bd;
  renderOpDiff(bd);
  $("#opdiff").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderOpDiff(bd) {
  const ta = bd.a, tb = bd.b;
  const name = (ta || tb).method || (ta || tb).title;
  $("#opdiffTitle").textContent = `Loop view — ${name}`;
  const s = bd.stat;
  $("#opdiffStat").innerHTML =
    `<span style="color:var(--add)">+${s.added}</span> ` +
    `<span style="color:var(--del)">−${s.removed}</span> ` +
    `<span style="color:var(--chg)">~${s.changed}</span> ` +
    `<span style="color:var(--fg-dim)">=${s.same}</span>`;

  const body = $("#opdiffBody");
  body.innerHTML = "";
  for (const row of bd.rows) {
    const aIsMp = row.a && row.a.kind === "mp";
    const bIsMp = row.b && row.b.kind === "mp";
    if (aIsMp || bIsMp) { body.appendChild(mpRow(row)); continue; }
    body.appendChild(opRow(row));
  }
  applyFilters();
}

function mpRow(row) {
  const mp = (row.a && row.a.kind === "mp") ? row.a : row.b;
  const diff = (row.a && row.b && row.a.kind === "mp" && row.b.kind === "mp" &&
    (row.a.method !== row.b.method || row.a.bc_idx !== row.b.bc_idx));
  const e = el("div", { class: "oprow mprow" + (row.tag !== "equal" ? " " + row.tag : ""),
    data: { depth: mp.depth, ismp: "1" } }, [
    el("span", { class: "fold" }, "▾"),
    el("span", { class: "depth" }, "·".repeat(mp.depth) + (mp.depth ? " " : "")),
    el("span", { class: "mname" }, mp.method || "?"),
    el("span", null, `  ${mp.bytecode} @ ${mp.bc_idx}` + (diff ? "   ⟂ differs" : "")),
  ]);
  e.addEventListener("click", () => toggleFrame(e));
  return e;
}

function opRow(row) {
  const depth = Math.max(row.a ? row.a.depth : 0, row.b ? row.b.depth : 0);
  const resid = (row.a && row.a.residual) || (row.b && row.b.residual);
  const gut = { equal: "", replace: "~", insert: "+", delete: "−" }[row.tag];
  const e = el("div", { class: "oprow " + row.tag,
    data: { depth: depth, resid: resid ? "1" : "0", tag: row.tag } }, [
    el("span", { class: "gutter" }, gut),
    opCell(row.a),
    opCell(row.b),
  ]);
  return e;
}

function opCell(op) {
  if (!op) return el("span", { class: "opcell empty" });
  const cls = "op cat-" + op.category + (op.residual ? " residual" : "");
  const parts = [];
  if (op.offset != null) parts.push(`<span class="off">+${op.offset}</span>`);
  if (op.result) parts.push(`<span class="res">${esc(op.result)} = </span>`);
  parts.push(`<span class="nm">${esc(op.opname)}</span>(${esc(op.args)})`);
  if (op.failargs) parts.push(`<span class="fa"> [${esc(op.failargs)}]</span>`);
  if (op.residual) {
    const mech = op.residual_kind === "inliner" ? "stack-inliner" : "tracing-JIT";
    const tgt = op.target_loop ? ` → Loop ${op.target_loop}` :
      (op.residual_kind === "tracing" ? " → portal (runtime)" : "");
    parts.push(`<span class="residnote ${op.residual_kind}">⟹ RESIDUAL · ${mech}${tgt}</span>`);
  } else if (op.opname === "enter_portal_frame") {
    parts.push(`<span class="residnote inlnote">⟹ INLINED (portal frame)</span>`);
  }
  return el("span", { class: "opcell" }, el("span", { class: cls, html: parts.join("") }));
}

// fold one inline frame: hide following rows with depth > this mp's depth
function toggleFrame(mpEl) {
  const d = +mpEl.dataset.depth;
  const folding = !mpEl.classList.contains("folded");
  mpEl.classList.toggle("folded", folding);
  mpEl.querySelector(".fold").textContent = folding ? "▸" : "▾";
  let n = mpEl.nextElementSibling;
  while (n) {
    const nd = +n.dataset.depth;
    if (n.dataset.ismp === "1" && nd <= d) break;
    if (nd > d || (n.dataset.ismp !== "1" && nd >= d && nd > 0)) {
      n.classList.toggle("frame-hidden", folding);
    } else if (nd <= d) break;
    n = n.nextElementSibling;
  }
}

function applyFilters() {
  const onlyDiff = $("#fOnlyDiff").checked;
  const onlyResid = $("#fResid").checked;
  const fold = $("#fFold").checked;
  document.querySelectorAll("#opdiffBody .oprow").forEach((r) => {
    const isMp = r.dataset.ismp === "1";
    let show = true;
    if (onlyResid && !isMp) show = r.dataset.resid === "1";
    if (onlyDiff && !isMp && r.dataset.tag === "equal") show = false;
    if (fold && !isMp && +r.dataset.depth > 0) show = false;
    if (fold && isMp && +r.dataset.depth > 0) {
      r.classList.add("folded");
      r.querySelector(".fold").textContent = "▸";
    }
    r.classList.toggle("frame-hidden", !show);
  });
}

// ------------------------------------------------------------------- wiring
$("#selA").addEventListener("change", refreshDiff);
$("#selB").addEventListener("change", refreshDiff);
$("#swap").addEventListener("click", () => {
  const a = $("#selA").value; $("#selA").value = $("#selB").value; $("#selB").value = a;
  refreshDiff();
});
$("#reload").addEventListener("click", loadTraces);
$("#closeOp").addEventListener("click", () => $("#opdiff").classList.add("hidden"));
["#fOnlyDiff", "#fResid", "#fFold"].forEach((s) =>
  $(s).addEventListener("change", applyFilters));
$("#mHideTrivial").addEventListener("change", drawSendMap);
$("#tCoverage").addEventListener("change", drawTraced);
$("#tAborted").addEventListener("change", drawTraced);

loadTraces();
