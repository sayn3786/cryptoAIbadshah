/* Scalp Terminal — the 15m scalp book on one page (admin only).
 *
 * Reads GET /api/hl/scalp with the dashboard's admin session cookie (or a
 * 401 shows the sign-in note). Every value from the API is inserted as TEXT
 * (textContent / createTextNode), never as HTML. Nothing here places orders.
 */
(function () {
  "use strict";

  var REFRESH_MS = 30000;
  var STALE_MS = 20 * 60 * 1000;              // a pass every 15 min; 20 = late
  var S = { data: null, runIdx: null, picked: false, period: "today", timer: null };

  // ── tiny DOM helpers (text only) ─────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function add(parent) {
    for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]);
    return parent;
  }
  function num(v) { var n = Number(v); return isFinite(n) ? n : null; }
  function usd(v, signed) {
    var n = num(v);
    if (n === null) return "—";
    var s = Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (signed ? (n > 0 ? "+" : n < 0 ? "−" : "") : (n < 0 ? "−" : "")) + "$" + s;
  }
  function px(v) {
    var n = num(v);
    if (n === null) return "—";
    return n.toLocaleString(undefined, { maximumSignificantDigits: 6 });
  }
  function ms(v) {
    if (v === null || v === undefined || v === "") return null;
    if (typeof v === "number") return v;
    var t = Date.parse(v);
    return isFinite(t) ? t : null;
  }
  function hhmm(t) {
    if (t === null) return "—";
    var d = new Date(t);
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }
  function dayTime(t) {
    if (t === null) return "—";
    var d = new Date(t);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " + hhmm(t);
  }
  function ago(t) {
    if (t === null) return "never";
    var m = Math.round((Date.now() - t) / 60000);
    if (m < 1) return "just now";
    if (m < 60) return m + "m ago";
    var h = Math.floor(m / 60);
    return h < 48 ? h + "h " + (m % 60) + "m ago" : Math.floor(h / 24) + "d ago";
  }
  function signCls(v) { var n = num(v); return n === null ? "" : n > 0 ? "pos" : n < 0 ? "neg" : ""; }
  function dirTxt(d) { return d === "LONG" ? "🟢 LONG" : d === "SHORT" ? "🔴 SHORT" : String(d || ""); }

  // ── load ─────────────────────────────────────────────────────────────────
  function load() {
    fetch("/api/hl/scalp", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        if (r.status === 401 || r.status === 403) { gate(); return null; }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (j) {
        if (!j) return;
        S.data = j;
        $("updated").textContent = "updated " + hhmm(Date.now());
        render();
      })
      .catch(function (e) {
        var w = $("warn");
        w.style.display = "block";
        w.textContent = "Could not load the scalp book (" + (e && e.message || "network error") +
          "). Showing the last data, if any; retrying in 30 s.";
      });
  }

  function gate() {
    $("app").style.display = "none";
    $("gate").style.display = "block";
    if (S.timer) { clearInterval(S.timer); S.timer = null; }
  }

  function runs() {
    return (S.data && Array.isArray(S.data.runs) ? S.data.runs : []).filter(function (r) {
      return r && typeof r === "object";
    });
  }

  // ── render ───────────────────────────────────────────────────────────────
  function render() {
    $("warn").style.display = "none";
    renderStatus();
    renderKpis();
    renderFlow();
    renderLog();
    renderBook();
    renderEquity();
    renderTrades();
    renderForward();
  }

  // ── the 4-hourly forward paper book (htf_forward) ────────────────────────
  function renderForward() {
    var f = (S.data && S.data.htf_forward) || null;
    var box = $("fwd");
    clear(box);
    if (!f) { box.appendChild(el("div", "empty", "Not available on this deploy yet.")); return; }
    var sm = f.summary || {};
    $("fwdSub").textContent = f.enabled ? String(sm.verdict || "") : "switched off (HTF_FORWARD)";
    var tiles = el("div", "grid g-kpi");
    tiles.style.marginTop = "0";
    [["Closed", String(sm.closed || 0), (sm.open || 0) + " open"],
     ["Net P&L", usd(sm.pnl_usd, true), "$5 risk a trade, after costs"],
     ["Win rate", sm.win_rate_pct != null ? sm.win_rate_pct + "%" : "—", "1:1 stop / target"],
     ["Profit factor", sm.pf != null ? String(sm.pf) : "—", "needs ≥ 1.15 at " + (sm.verdict_at || 100)],
     ["Opposite side", usd(sm.opposite_pnl_usd, true), "same candles, other direction"]
    ].forEach(function (x) {
      var c = el("div", "kpi");
      add(c, el("div", "label", x[0]), el("div", "val " + (x[0] === "Net P&L" ? signCls(sm.pnl_usd) : ""), x[1]),
        el("div", "note", x[2]));
      tiles.appendChild(c);
    });
    box.appendChild(tiles);
    box.appendChild(el("p", "muted small", f.rule || ""));
    var rows = (f.recent || []).slice().reverse().slice(0, 12);
    if (!rows.length) { box.appendChild(el("div", "empty", "No trades yet: the first ones open at the next 4-hour publish.")); return; }
    var t = el("table"), hd = el("tr");
    ["Opened", "Coin", "Side", "Score", "Entry", "Exit", "P&L"].forEach(function (h, k) {
      hd.appendChild(el("th", k >= 3 ? "num" : "", h));
    });
    t.appendChild(hd);
    rows.forEach(function (r) {
      var tr = el("tr");
      add(tr, el("td", "", dayTime(ms(r.slot_ms))), el("td", "", r.symbol || ""), el("td", "", dirTxt(r.direction)),
        el("td", "num", r.strength), el("td", "num", px(r.entry)),
        el("td", "num", r.state === "open" ? "open" : (r.exit_reason || "") + " " + px(r.exit)),
        el("td", "num " + signCls(r.pnl_usd), r.state === "open" ? "—" : usd(r.pnl_usd, true)));
      t.appendChild(tr);
    });
    var sc = el("div", "scroll"); sc.appendChild(t); box.appendChild(sc);
  }

  function renderStatus() {
    var d = S.data, rs = runs();
    var last = rs.length ? rs[rs.length - 1] : null;
    var t = last ? ms(last.at) : null;
    var dot = $("statusDot"), txt = $("statusText");
    var problem = last && (last.reason || last.error);
    dot.className = "dot";
    if (!d.enabled) { dot.classList.add("bad"); txt.textContent = "scalp book off"; }
    else if (t === null) { dot.classList.add("warn"); txt.textContent = "no passes logged yet"; }
    else if (Date.now() - t > STALE_MS) { dot.classList.add("bad"); txt.textContent = "stalled · last pass " + ago(t); }
    else if (problem) { dot.classList.add("warn"); txt.textContent = "running · last pass " + String(problem); }
    else { dot.classList.add("ok"); txt.textContent = "running · last pass " + ago(t); }
    $("auto").textContent = d.auto ? "auto-exec on" : "alert only";
  }

  function closedRows() {
    var h = (S.data && Array.isArray(S.data.history)) ? S.data.history : [];
    return h.filter(function (r) { return r && r.status === "CLOSED"; });
  }

  function periodStart() {
    var now = new Date();
    if (S.period === "today") return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (S.period === "week") return Date.now() - 7 * 86400000;
    return 0;
  }

  function renderKpis() {
    var since = periodStart();
    var rows = closedRows().filter(function (r) {
      var t = ms(r.closed_at) || ms(r.placed_at);
      return t !== null && t >= since;
    });
    var done = rows.filter(function (r) { return num(r.pnl_usd) !== null; });
    var pnl = done.map(function (r) { return num(r.pnl_usd); });
    var wins = pnl.filter(function (x) { return x > 0; });
    var losses = pnl.filter(function (x) { return x <= 0; });
    var net = pnl.reduce(function (a, b) { return a + b; }, 0);
    var gw = wins.reduce(function (a, b) { return a + b; }, 0);
    var gl = -losses.reduce(function (a, b) { return a + b; }, 0);
    var noRes = rows.length - done.length;
    var open = Object.keys((S.data && S.data.open) || {}).length;
    var tiles = [
      ["Net P&L", usd(net, true), done.length + " closed with a result", signCls(net)],
      ["Trades", String(done.length), open + " open now", ""],
      ["Wins", String(wins.length), done.length ? (100 * wins.length / done.length).toFixed(0) + "% win rate · break-even ≈45%" : "—", "pos"],
      ["Losses", String(losses.length), "stops, timeouts, flips", "neg"],
      ["Profit factor", gl > 0 ? (gw / gl).toFixed(2) : (gw > 0 ? "∞" : "—"), "gross win ÷ gross loss", ""],
      ["No result", String(noRes), "closed, but the fills never showed the P&L", ""]
    ];
    var k = $("kpis");
    clear(k);
    tiles.forEach(function (x) {
      var c = el("div", "card kpi");
      add(c, el("div", "label", x[0]), el("div", "val " + x[3], x[1]), el("div", "note", x[2]));
      k.appendChild(c);
    });
  }

  // ── live flow ────────────────────────────────────────────────────────────
  function defaultRunIdx() {
    var rs = runs();
    for (var i = rs.length - 1; i >= 0; i--) {
      if ((rs[i].setups || []).length) return i;
    }
    return rs.length - 1;
  }

  function stage(name, n, detail, cls, chips) {
    var s = el("div", "stage " + (cls || ""));
    add(s, el("div", "name", name), el("div", "n", n), el("div", "d", detail));
    if (chips && chips.length) {
      var c = el("div", "chips");
      chips.forEach(function (t) { c.appendChild(el("span", "chip", t)); });
      s.appendChild(c);
    }
    return s;
  }

  function renderFlow() {
    var rs = runs();
    var flow = $("flow"), list = $("flowList");
    clear(flow); clear(list);
    if (!rs.length) { list.appendChild(el("div", "empty", "No passes logged yet.")); return; }
    if (!S.picked || S.runIdx === null || S.runIdx >= rs.length) S.runIdx = defaultRunIdx();
    var r = rs[S.runIdx];
    var latestWithSetups = S.runIdx === defaultRunIdx();
    $("runLabel").textContent = dayTime(ms(r.at)) + (S.runIdx === rs.length - 1 ? " · latest" :
      latestWithSetups ? " · latest with setups" : "");
    $("prevRun").disabled = S.runIdx <= 0;
    $("nextRun").disabled = S.runIdx >= rs.length - 1;

    var skipped = Object.keys(r.skipped || {});
    var below = (r.rejected || []).length;
    var setups = (r.setups || []).length;
    var dec = Array.isArray(r.decisions) ? r.decisions : [];
    var rejected = dec.filter(function (d) { return d && d.decision === "rejected"; });
    var entered = dec.filter(function (d) { return d && d.decision === "entered"; });
    var failed = dec.filter(function (d) { return d && d.decision === "failed"; });
    var reasons = {};
    rejected.forEach(function (d) {
      var k = String(d.reason || "").split(" ")[0] || "?";
      reasons[k] = (reasons[k] || 0) + 1;
    });
    var coins = num(r.coins);
    var arrow = function () { return el("div", "arrow", "→"); };
    add(flow,
      stage("SCAN", coins !== null ? coins : "—", "coins read on the close" +
        (skipped.length ? " · " + skipped.length + " skipped" : ""), "", skipped),
      arrow(),
      stage("SCORE", setups, "setups at " + (S.data.min_strength || 100) + "+ · " + below +
        " near misses below", setups ? "pass" : ""),
      arrow(),
      stage("CHECKS", rejected.length, "rejected by the guards", rejected.length ? "stop" : "",
        Object.keys(reasons).map(function (k) { return k + " " + reasons[k]; })),
      arrow(),
      stage("EXECUTE", entered.length, entered.length ? "opened" : (failed.length ? failed.length + " order(s) failed" :
        (r.reason ? "pass skipped: " + r.reason : "nothing to open")), entered.length ? "pass" : (failed.length ? "stop" : "")),
      arrow(),
      stage("BOOK", (r.batch && num(r.batch.open) !== null) ? r.batch.open : "—", r.batch ?
        (r.batch.longs || 0) + " long · " + (r.batch.shorts || 0) + " short · " + usd(r.batch.risk_usd) + " at risk" : "", "")
    );

    // each candidate this pass, with its outcome in words
    if (!dec.length && !below) {
      list.appendChild(el("div", "empty", r.reason ? "This pass did not run the book: " + r.reason :
        "No setup reached the score this pass."));
    }
    dec.forEach(function (d) {
      if (!d) return;
      var kind = d.decision === "entered" ? "enter" : d.decision === "rejected" ? "skip" :
        d.decision === "failed" ? "fail" : "exit";
      list.appendChild(event(ms(r.at), kind, String(d.decision || "").toUpperCase().slice(0, 7),
        (d.symbol || "") + " " + dirTxt(d.direction) + (num(d.strength) !== null ? " · " + d.strength : ""),
        d.decision === "entered" ? "passed every check" + (d.protection ? " · " + d.protection : "") :
          (d.why || d.reason || ""), null));
    });
    (r.rejected || []).slice(0, 6).forEach(function (t) {
      list.appendChild(event(ms(r.at), "skip", "BELOW", String(t).split(":")[0], "score under the floor: " +
        String(t).split(":").slice(1).join(":").trim(), null));
    });
  }

  function event(t, kind, tag, what, why, amt) {
    var row = el("div", "ev");
    var k = el("span", "k " + kind, tag);
    var mid = el("div");
    add(mid, el("div", "what", what), why ? el("div", "why", why) : null);
    var a = el("span", "amt " + (amt !== null ? signCls(amt) : ""), amt !== null ? usd(amt, true) : "");
    add(row, el("span", "t", hhmm(t)), k, mid, a);
    return row;
  }

  // ── decision log (passes + exits, newest first) ───────────────────────────
  function renderLog() {
    var rs = runs();
    var evs = [];
    var first = rs.length ? ms(rs[0].at) : null;
    rs.forEach(function (r) {
      var t = ms(r.at);
      (r.decisions || []).forEach(function (d) {
        if (!d || d.decision === "managed") return;
        var kind = d.decision === "entered" ? "enter" : d.decision === "rejected" ? "skip" :
          d.decision === "failed" ? "fail" : "exit";
        var tag = { enter: "ENTER", skip: "SKIP", fail: "FAIL", exit: String(d.decision || "").toUpperCase() }[kind];
        evs.push({ t: t, kind: kind, tag: tag,
          what: (d.symbol || "") + " " + dirTxt(d.direction) + (num(d.strength) !== null ? " · score " + d.strength : ""),
          why: d.decision === "entered" ? "passed every check" + (d.protection ? " · stop " +
            String(d.protection).replace("STOP_", "").toLowerCase() : "") : (d.why || d.reason || ""), amt: null });
      });
      if (r.reason && !(r.decisions || []).length && r.reason !== "HL_SCALP_AUTO_EXECUTE_OFF") {
        evs.push({ t: t, kind: "fail", tag: "PASS", what: "book pass skipped", why: String(r.reason), amt: null });
      }
    });
    closedRows().forEach(function (h) {
      var t = ms(h.closed_at);
      if (t === null || (first !== null && t < first)) return;
      evs.push({ t: t, kind: "exit", tag: "EXIT", what: (h.symbol || "") + " " + dirTxt(h.direction),
        why: "closed at " + (h.exit_reason || "?") + (num(h.exit_price) !== null ? " · " + px(h.exit_price) : "") +
          (num(h.fill_price) !== null ? " (in " + px(h.fill_price) + ")" : ""),
        amt: num(h.pnl_usd) });
    });
    evs.sort(function (a, b) { return (b.t || 0) - (a.t || 0); });
    var log = $("log");
    clear(log);
    $("logCount").textContent = evs.length + " events · last " + rs.length + " passes";
    if (!evs.length) { log.appendChild(el("div", "empty", "Nothing yet: no setups reached the score in these passes.")); return; }
    evs.slice(0, 120).forEach(function (e) { log.appendChild(event(e.t, e.kind, e.tag, e.what, e.why, e.amt)); });
  }

  // ── the book now ─────────────────────────────────────────────────────────
  function bar(label, v, max, fmt) {
    var row = el("div", "bar-row");
    var b = el("div", "bar"), i = el("i");
    var frac = max > 0 ? Math.min(1, (num(v) || 0) / max) : 0;
    i.style.width = (frac * 100).toFixed(1) + "%";
    if (frac >= 1) i.className = "full";
    b.appendChild(i);
    b.setAttribute("role", "img");
    b.setAttribute("aria-label", label + " " + fmt);
    add(row, el("span", "", label), b, el("span", "v", fmt));
    return row;
  }

  function renderBook() {
    var p = (S.data && S.data.portfolio) || {};
    var L = p.limits || {}, st = p.state || {};
    var lim = $("limits");
    clear(lim);
    if (p.error) { lim.appendChild(el("div", "empty", "Book state unavailable: " + p.error)); }
    else {
      add(lim,
        bar("Longs", st.longs, L.max_longs, (st.longs || 0) + " / " + (L.max_longs || "—")),
        bar("Shorts", st.shorts, L.max_shorts, (st.shorts || 0) + " / " + (L.max_shorts || "—")),
        bar("Open", st.open, L.max_open, (st.open || 0) + " / " + (L.max_open || "—")),
        bar("$ at risk", st.risk_usd, L.max_risk_usd, usd(st.risk_usd) + " / " + usd(L.max_risk_usd)),
        bar("This candle", p.entries_this_candle, L.max_new_per_candle,
          (p.entries_this_candle || 0) + " / " + (L.max_new_per_candle || "—")));
    }
    $("bookSub").textContent = "adds " + (p.max_adds ? "on (" + p.max_adds + ")" : "off");

    var open = (S.data && S.data.open) || {};
    var box = $("openPos");
    clear(box);
    var coins = Object.keys(open);
    if (!coins.length) box.appendChild(el("div", "empty", "No open scalps."));
    else {
      var t = el("table");
      var hd = el("tr");
      ["Coin", "Side", "In", "Stop", "Target", "Stop check"].forEach(function (h, k) {
        hd.appendChild(el("th", k >= 2 && k <= 4 ? "num" : "", h));
      });
      t.appendChild(hd);
      coins.forEach(function (c) {
        var r = open[c] || {};
        var tr = el("tr");
        add(tr, el("td", "", r.symbol || c), el("td", "", dirTxt(r.direction) + (r.state === "pending" ? " · pending" : "")),
          el("td", "num", px(r.fill_price || r.limit)), el("td", "num", px(r.sl)), el("td", "num", px(r.target)),
          el("td", "", r.protection === "STOP_CONFIRMED" ? "✓ confirmed" :
            r.protection ? "⚠ " + r.protection : "—"));
        t.appendChild(tr);
      });
      var sc = el("div", "scroll"); sc.appendChild(t); box.appendChild(sc);
    }

    // cooldowns and pauses
    var pz = $("pauses");
    clear(pz);
    var items = [];
    var re = p.reentry || {};
    Object.keys(re).forEach(function (k) {
      if (re[k] && re[k].blocked) items.push(k.replace(":", " ") + " — " + re[k].blocked);
    });
    var hm = p.hot_market || {};
    ["LONG", "SHORT"].forEach(function (d) {
      if (hm[d] && hm[d].cooling_min > 0) items.push(d.toLowerCase() + "s — hot-market pause " + hm[d].cooling_min + "m" + (hm.enabled ? "" : " (rule off, shown only)"));
      var w = hm.wins_then_batch || {};
      if (w[d] && w[d].pause_min_left > 0) items.push(d.toLowerCase() + "s — wins-then-batch pause " + w[d].pause_min_left + "m" + (w.enabled ? "" : " (rule off, shown only)"));
    });
    pz.appendChild(el("div", "muted small", "Cooldowns & pauses"));
    if (!items.length) pz.appendChild(el("div", "empty", "None active."));
    items.forEach(function (x) { pz.appendChild(el("div", "small", "• " + x)); });
  }

  // ── equity curve: one series, crosshair tooltip, table below ─────────────
  var SVGNS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs) {
    var e = document.createElementNS(SVGNS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }

  function equityPoints() {
    var pts = closedRows().filter(function (r) { return num(r.pnl_usd) !== null && ms(r.closed_at) !== null; })
      .sort(function (a, b) { return ms(a.closed_at) - ms(b.closed_at); });
    var cum = 0;
    return pts.map(function (r) {
      cum += num(r.pnl_usd);
      return { t: ms(r.closed_at), v: cum, r: r };
    });
  }

  function renderEquity() {
    var box = $("chart"), tip = $("tip");
    Array.prototype.slice.call(box.querySelectorAll("svg, .empty")).forEach(function (n) { box.removeChild(n); });
    var pts = equityPoints();
    $("eqSub").textContent = pts.length ? usd(pts[pts.length - 1].v, true) + " over " + pts.length + " trades" : "";
    if (pts.length < 2) { box.appendChild(el("div", "empty", "Not enough closed trades with a result yet.")); return; }
    var W = 560, H = 220, m = { l: 48, r: 10, t: 12, b: 24 };
    var t0 = pts[0].t, t1 = pts[pts.length - 1].t;
    var vs = pts.map(function (p) { return p.v; }).concat([0]);
    var lo = Math.min.apply(null, vs), hi = Math.max.apply(null, vs);
    if (hi === lo) { hi += 1; lo -= 1; }
    var pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
    var X = function (t) { return m.l + (t1 === t0 ? 0 : (t - t0) / (t1 - t0)) * (W - m.l - m.r); };
    var Y = function (v) { return m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b); };
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img",
      "aria-label": "Cumulative realised P&L, " + pts.length + " closed trades, ending " + usd(pts[pts.length - 1].v, true) });
    // recessive grid: 4 ticks + zero line
    for (var i = 0; i <= 3; i++) {
      var v = lo + (hi - lo) * i / 3;
      s.appendChild(svg("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: "#1e2d44", "stroke-width": 1 }));
      var tx = svg("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end", fill: "#64748b", "font-size": 10, "font-family": "JetBrains Mono, monospace" });
      tx.textContent = (v < 0 ? "−$" : "$") + Math.abs(v).toFixed(0);
      s.appendChild(tx);
    }
    s.appendChild(svg("line", { x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0), stroke: "#94a3b8", "stroke-width": 1, "stroke-dasharray": "3 3" }));
    [t0, t1].forEach(function (t, k) {
      var tx = svg("text", { x: X(t), y: H - 6, "text-anchor": k ? "end" : "start", fill: "#64748b", "font-size": 10, "font-family": "JetBrains Mono, monospace" });
      tx.textContent = dayTime(t);
      s.appendChild(tx);
    });
    var d = pts.map(function (p, k) { return (k ? "L" : "M") + X(p.t).toFixed(1) + " " + Y(p.v).toFixed(1); }).join(" ");
    s.appendChild(svg("path", { d: d, fill: "none", stroke: "#38bdf8", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    var last = pts[pts.length - 1];
    s.appendChild(svg("circle", { cx: X(last.t), cy: Y(last.v), r: 4, fill: "#38bdf8", stroke: "#111827", "stroke-width": 2 }));
    var hair = svg("line", { y1: m.t, y2: H - m.b, stroke: "#94a3b8", "stroke-width": 1, opacity: 0 });
    var mark = svg("circle", { r: 4, fill: "#38bdf8", stroke: "#111827", "stroke-width": 2, opacity: 0 });
    s.appendChild(hair); s.appendChild(mark);
    var hit = svg("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent", tabindex: 0 });
    s.appendChild(hit);
    function show(k) {
      var p = pts[k];
      hair.setAttribute("x1", X(p.t)); hair.setAttribute("x2", X(p.t)); hair.setAttribute("opacity", 0.6);
      mark.setAttribute("cx", X(p.t)); mark.setAttribute("cy", Y(p.v)); mark.setAttribute("opacity", 1);
      clear(tip);
      add(tip, add(el("div"), el("b", "", usd(p.v, true)), document.createTextNode(" total")),
        el("div", "muted", (p.r.symbol || "") + " " + (p.r.direction || "") + " · " + (p.r.exit_reason || "") +
          " " + usd(p.r.pnl_usd, true)),
        el("div", "muted", dayTime(p.t)));
      tip.style.display = "block";
      var bw = box.getBoundingClientRect().width, xpx = X(p.t) / W * bw;
      tip.style.left = Math.max(0, Math.min(bw - 190, xpx + 10)) + "px";
      tip.style.top = "8px";
    }
    function nearest(evt) {
      var rect = s.getBoundingClientRect();
      var x = (evt.clientX - rect.left) / rect.width * W;
      var best = 0, bd = Infinity;
      pts.forEach(function (p, k) { var dd = Math.abs(X(p.t) - x); if (dd < bd) { bd = dd; best = k; } });
      return best;
    }
    var focusIdx = pts.length - 1;
    hit.addEventListener("pointermove", function (e) { show(nearest(e)); });
    hit.addEventListener("pointerleave", function () { tip.style.display = "none"; hair.setAttribute("opacity", 0); mark.setAttribute("opacity", 0); });
    hit.addEventListener("focus", function () { show(focusIdx); });
    hit.addEventListener("blur", function () { tip.style.display = "none"; });
    hit.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") { focusIdx = Math.max(0, focusIdx - 1); show(focusIdx); e.preventDefault(); }
      if (e.key === "ArrowRight") { focusIdx = Math.min(pts.length - 1, focusIdx + 1); show(focusIdx); e.preventDefault(); }
    });
    box.insertBefore(s, tip);
  }

  function renderTrades() {
    var t = $("trades");
    clear(t);
    var hd = el("tr");
    ["Closed", "Coin", "Side", "In", "Out", "Exit", "P&L"].forEach(function (h, k) {
      hd.appendChild(el("th", (k >= 3 && k !== 5) ? "num" : "", h));
    });
    t.appendChild(hd);
    var rows = closedRows().slice().sort(function (a, b) { return (ms(b.closed_at) || 0) - (ms(a.closed_at) || 0); }).slice(0, 40);
    if (!rows.length) {
      var tr0 = el("tr"), td0 = el("td", "empty", "No closed trades in the last 14 days.");
      td0.colSpan = 7; tr0.appendChild(td0); t.appendChild(tr0); return;
    }
    rows.forEach(function (r) {
      var tr = el("tr");
      add(tr, el("td", "", dayTime(ms(r.closed_at))), el("td", "", r.symbol || ""), el("td", "", dirTxt(r.direction)),
        el("td", "num", px(r.fill_price)), el("td", "num", px(r.exit_price)),
        el("td", "", r.exit_reason || "—"),
        el("td", "num " + signCls(r.pnl_usd), num(r.pnl_usd) === null ? "no result" : usd(r.pnl_usd, true)));
      t.appendChild(tr);
    });
  }

  // ── controls ─────────────────────────────────────────────────────────────
  Array.prototype.forEach.call(document.querySelectorAll(".tabs button"), function (b) {
    b.addEventListener("click", function () {
      S.period = b.getAttribute("data-p");
      Array.prototype.forEach.call(document.querySelectorAll(".tabs button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      if (S.data) renderKpis();
    });
  });
  $("prevRun").addEventListener("click", function () { if (S.runIdx > 0) { S.runIdx--; S.picked = true; renderFlow(); } });
  $("nextRun").addEventListener("click", function () {
    var n = runs().length;
    if (S.runIdx < n - 1) { S.runIdx++; S.picked = S.runIdx !== n - 1; renderFlow(); }
  });

  load();
  S.timer = setInterval(load, REFRESH_MS);
})();
