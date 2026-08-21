/* ===========================================================================
   Opus — the licensing desk.

   One UI, two backends. Against the local server it talks to /api/*; in the
   published static demo the same code runs against a baked-in mock adapter.
   That is why every read goes through API.get and every write through
   API.post, and why nothing below knows whether a server exists. It is also
   what stops the demo drifting away from the app, which is what happened last
   time they were separate.

   No framework and no build step, on purpose: this ships inside a ~33 MB
   desktop app that already carries a Python runtime, and a toolchain would buy
   nothing a few hundred lines of DOM code does not already do.
   ======================================================================== */
(function () {
  "use strict";

  /* ---------------- tiny helpers ---------------- */
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  };
  var el = function (tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (html != null) { n.innerHTML = html; }
    return n;
  };
  var num = function (v, d) { var n = parseFloat(v); return isNaN(n) ? d : n; };

  /* ---------------- API ----------------
     window.OPUS_MOCK is injected by the static demo build. When it is absent
     we are the real app and talk to the local server. */
  var API = window.OPUS_MOCK || {
    get: function (path) {
      return fetch(path, {credentials: "same-origin"}).then(readJson);
    },
    post: function (path, body) {
      return fetch(path, {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body || {})
      }).then(readJson);
    },
    live: true
  };

  function readJson(resp) {
    return resp.json().catch(function () {
      throw new Error("The app returned something unreadable.");
    }).then(function (data) {
      if (!resp.ok) {
        var err = new Error(data.error || ("Request failed (" + resp.status + ")"));
        err.detail = data.detail || "";
        err.status = resp.status;
        throw err;
      }
      return data;
    });
  }

  function fail(err) {
    toast(err && err.message ? err.message : String(err), true);
    if (err && err.detail) { console.warn(err.detail); }
  }

  var toastTimer = null;
  function toast(message, bad) {
    var t = $("toast");
    t.textContent = message;
    t.className = "toast" + (bad ? " bad" : "");
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, bad ? 6000 : 3000);
  }

  /* ---------------- state ---------------- */
  var S = {
    settings: null, status: null, orders: [], counts: {}, ledger: [],
    ledgerIntact: null, connectors: [], views: [], catalog: [],
    sort: {key: "stamped_at", dir: -1}, arranging: false, holdBelow: 1.01,
    help: null, steps: [], practice: false
  };

  var WS = ["overview", "orders", "catalog", "ledger", "conn", "settings",
          "start"];

  /* ---------------- navigation ---------------- */
  function show(name, opts) {
    if (WS.indexOf(name) < 0) { name = "overview"; }
    WS.forEach(function (w) { $("ws-" + w).hidden = (w !== name); });
    Array.prototype.forEach.call(document.querySelectorAll(".nav[data-ws]"),
      function (b) {
        b.setAttribute("aria-current", b.dataset.ws === name ? "true" : "false");
      });
    closeDrawer();
    if (!(opts && opts.silent)) {
      try { history.replaceState(null, "", "#" + name); } catch (e) { /* file:// */ }
    }
    if (name === "orders" && !S.orders.length) { loadOrders(); }
    if (name === "catalog" && !S.catalog.length) { loadCatalog(); }
    if (name === "ledger" && !S.ledger.length) { loadLedger(); }
    if (name === "conn" && !S.connectors.length) { loadConnectors(); }
  }

  /* ---------------- drawer ---------------- */
  var lastFocus = null;
  function openDrawer(title, sub, html) {
    lastFocus = document.activeElement;
    $("dtitle").textContent = title;
    $("dsub").textContent = sub || "";
    $("dbody").innerHTML = html;
    $("dbody").scrollTop = 0;
    $("drawer").hidden = false;
    $("scrim").hidden = false;
    $("dclose").focus();
  }
  function closeDrawer() {
    if ($("drawer").hidden) { return; }
    $("drawer").hidden = true;
    $("scrim").hidden = true;
    if (lastFocus && lastFocus.focus) { lastFocus.focus(); }
  }

  /* ---------------- theme + density ---------------- */
  function applyAppearance(d) {
    var root = document.documentElement;
    if (d.theme && d.theme !== "system") { root.setAttribute("data-theme", d.theme); }
    else { root.removeAttribute("data-theme"); }
    root.setAttribute("data-density", d.density || "comfortable");
  }

  /* ---------------- widgets ---------------- */
  var WIDGETS = {
    start: {name: "Getting started", render: renderStartWidget},
    tiles: {name: "Summary", render: renderTiles},
    attention: {name: "Needs a person", render: renderAttention},
    queue: {name: "Order queue", render: renderQueuePreview},
    dropped: {name: "Filtered out", render: renderDropped},
    recent: {name: "Recently issued", render: renderRecent}
  };

  function widgetConfig() {
    var d = (S.settings && S.settings.dashboard) || {};
    var list = (d.widgets || []).filter(function (w) { return WIDGETS[w.id]; });
    Object.keys(WIDGETS).forEach(function (id) {
      if (!list.some(function (w) { return w.id === id; })) {
        // Getting started leads until dismissed; anything new is appended.
        if (id === "start") { list.unshift({id: id, visible: true}); }
        else { list.push({id: id, visible: true}); }
      }
    });
    if (S.status && S.status.onboarding_dismissed) {
      list = list.filter(function (w) { return w.id !== "start"; });
    }
    return list;
  }

  function renderWidgets() {
    var host = $("widgets");
    host.innerHTML = "";
    widgetConfig().forEach(function (w, index) {
      var spec = WIDGETS[w.id];
      var card = el("div", "card widget" + (w.id === "attention" ? " attn" : ""));
      card.dataset.wid = w.id;
      card.hidden = !w.visible && !S.arranging;
      if (!w.visible) { card.style.opacity = ".55"; }

      var tools = S.arranging
        ? '<span class="r wtools">' +
            '<button data-act="up" title="Move up" aria-label="Move up">&#9650;</button>' +
            '<button data-act="down" title="Move down" aria-label="Move down">&#9660;</button>' +
            '<button data-act="toggle">' + (w.visible ? "Hide" : "Show") + '</button>' +
          "</span>"
        : "";
      var grip = S.arranging
        ? '<span class="grip" aria-hidden="true">&#8942;&#8942;</span>' : "";

      card.innerHTML = "<h2>" + grip + esc(spec.name) + tools + "</h2>" +
                       '<div class="wbody"></div>';
      if (S.arranging) {
        card.draggable = true;
        card.addEventListener("dragstart", function (e) {
          card.classList.add("dragging");
          e.dataTransfer.setData("text/plain", w.id);
        });
        card.addEventListener("dragend", function () {
          card.classList.remove("dragging");
        });
        card.addEventListener("dragover", function (e) {
          e.preventDefault(); card.classList.add("over");
        });
        card.addEventListener("dragleave", function () { card.classList.remove("over"); });
        card.addEventListener("drop", function (e) {
          e.preventDefault(); card.classList.remove("over");
          moveWidget(e.dataTransfer.getData("text/plain"), w.id);
        });
        card.querySelectorAll(".wtools button").forEach(function (b) {
          b.addEventListener("click", function () {
            var act = b.dataset.act;
            if (act === "toggle") { toggleWidget(w.id); }
            else { nudgeWidget(index, act === "up" ? -1 : 1); }
          });
        });
      }
      host.appendChild(card);
      try { spec.render(card.querySelector(".wbody")); }
      catch (e) { card.querySelector(".wbody").innerHTML =
        '<div class="body"><p>Could not draw this panel.</p></div>'; }
    });
  }

  function currentWidgets() { return widgetConfig(); }

  function persistWidgets(list) {
    S.settings.dashboard.widgets = list;
    renderWidgets();
    API.post("/api/dashboard", {values: {widgets: list}}).catch(fail);
  }
  function moveWidget(fromId, toId) {
    if (!fromId || fromId === toId) { return; }
    var list = currentWidgets();
    var from = list.findIndex(function (w) { return w.id === fromId; });
    var to = list.findIndex(function (w) { return w.id === toId; });
    if (from < 0 || to < 0) { return; }
    list.splice(to, 0, list.splice(from, 1)[0]);
    persistWidgets(list);
  }
  function nudgeWidget(index, delta) {
    var list = currentWidgets();
    var target = index + delta;
    if (target < 0 || target >= list.length) { return; }
    var tmp = list[index]; list[index] = list[target]; list[target] = tmp;
    persistWidgets(list);
  }
  function toggleWidget(id) {
    var list = currentWidgets();
    list.forEach(function (w) { if (w.id === id) { w.visible = !w.visible; } });
    persistWidgets(list);
  }

  /* ---------------- widget bodies ---------------- */
  function tile(k, v, s, cls) {
    return '<div class="tile ' + (cls || "") + '"><div class="k">' + esc(k) +
      '</div><div class="v">' + esc(v) + '</div><div class="s">' + esc(s) + "</div></div>";
  }

  function renderTiles(host) {
    var st = S.status || {}, c = S.counts || {};
    var files = S.orders.filter(function (o) { return o.verdict === "release"; })
      .reduce(function (n, o) { return n + o.files; }, 0);
    host.innerHTML = '<div class="body"><div class="tiles">' +
      tile("Orders in", c.total || 0, "in this export", "") +
      tile("Releasing", c.release || 0, files + " files", "good") +
      tile("Needs a person", (c.hold || 0) + (c.reject || 0), "held or unmatched", "warn") +
      tile("Catalogue", (st.catalog && st.catalog.pieces) || 0,
           ((st.catalog && st.catalog.parts) || 0) + " parts", "") +
      tile("Issued to date", st.issued || 0,
           st.ledger_intact === false ? "chain broken" : "ledger rows",
           st.issued ? "key" : "") +
      "</div></div>";
  }

  function renderAttention(host) {
    var flagged = S.orders.filter(function (o) { return o.verdict !== "release"; });
    if (!flagged.length) {
      host.innerHTML = '<div class="body"><p>Nothing is waiting on you.</p></div>';
      return;
    }
    var body = el("div", "body");
    flagged.slice(0, 6).forEach(function (o) {
      var p = el("p", null,
        "<b>" + esc(o.buyer) + "</b> — " + esc(o.item) + ". " +
        esc((o.reasons && o.reasons[0]) || "Below the release threshold."));
      body.appendChild(p);
      var b = el("button", "btn sm", "Open this order");
      b.addEventListener("click", function () { show("orders"); openOrder(o); });
      body.appendChild(b);
    });
    host.innerHTML = "";
    host.appendChild(body);
  }

  function renderQueuePreview(host) {
    if (!S.orders.length) {
      host.innerHTML = '<div class="body"><p>No orders loaded yet.</p></div>';
      return;
    }
    var rows = el("div", "rows");
    S.orders.slice(0, 5).forEach(function (o) { rows.appendChild(orderRow(o)); });
    host.innerHTML = "";
    host.appendChild(rows);
  }

  function renderDropped(host) {
    var w = (S.warnings || []);
    host.innerHTML = '<div class="body"><p>' +
      "Refunds, withdrawals and payments that have not cleared are dropped " +
      "before the plan is built. " +
      (w.length ? esc(w.join(" ")) : "Nothing unusual in this export.") +
      "</p></div>";
  }

  function renderRecent(host) {
    var issued = S.ledger.filter(function (r) { return r.status === "ok"; }).slice(-6).reverse();
    if (!issued.length) {
      host.innerHTML = '<div class="body"><p>Nothing issued yet.</p></div>';
      return;
    }
    var list = el("div", "rows");
    issued.forEach(function (r) {
      var b = el("button", "orow is-release",
        '<span class="d">' + esc((r.stamped_at || "").slice(5, 10)) + "</span>" +
        '<span><span class="who">' + esc(r.licensee) + "</span>" +
        '<span class="what mono">' + esc(r.file) + "</span></span>" +
        '<span class="rt"><span class="pill p-done">issued</span></span>');
      b.addEventListener("click", function () { show("ledger"); openLedgerRow(r); });
      list.appendChild(b);
    });
    host.innerHTML = "";
    host.appendChild(list);
  }

  /* ---------------- orders ---------------- */
  function matchChip(o) {
    var label = o.match === "none" ? "no match" : o.match;
    return '<span class="chip c-' + esc(o.match) + '">' + esc(label) + "</span>";
  }

  function orderRow(o) {
    var resolved = o.matched
      ? '<span class="arrow">&rarr;</span> ' + esc(o.matched)
      : '<span class="arrow">&rarr;</span> <i>nothing</i>';
    var row = el("button", "orow is-" + esc(o.verdict || "hold"),
      '<span class="d">' + esc(o.date) + "</span>" +
      '<span><span class="who">' + esc(o.buyer) + "</span>" +
      '<span class="what">' + esc(o.item) + " " + resolved + " " + matchChip(o) +
      "</span></span>" +
      '<span class="rt"><span class="pill p-' + esc(o.verdict) + '">' +
      esc(o.verdict) + "</span>" +
      '<span class="fc"><span class="score sc-' + esc(o.verdict) + '">' +
      (o.score != null ? o.score.toFixed(2) : "--") + "</span>" +
      (o.files ? o.files + " files" : "&mdash;") + "</span></span>");
    row.addEventListener("click", function () { openOrder(o); });
    return row;
  }

  function renderOrders() {
    var host = $("orderrows");
    host.innerHTML = "";
    if (!S.orders.length) {
      host.appendChild(el("div", "blank",
        "No orders. Choose a PayPal export in Settings."));
    } else {
      S.orders.forEach(function (o) { host.appendChild(orderRow(o)); });
    }
    var c = S.counts || {};
    var needing = (c.hold || 0) + (c.reject || 0);
    $("ordersbadge").hidden = !needing;
    $("ordersbadge").textContent = needing;
    $("ordersblurb").innerHTML = S.orders.length
      ? "<b>" + (c.release || 0) + " releasing</b>, " + (c.hold || 0) +
        " held, " + (c.reject || 0) + " unmatched, at a threshold of " +
        S.holdBelow.toFixed(2) + "."
      : "Nothing loaded.";
    $("runbtn").disabled = !(c.release || 0);
  }

  function openOrder(o) {
    var head = '<dl class="kv">' +
      "<dt>Buyer</dt><dd>" + esc(o.buyer) + "</dd>" +
      "<dt>Email</dt><dd>" + esc(o.email || "—") + "</dd>" +
      '<dt>Order</dt><dd class="mono">' + esc(o.order_ref) + "</dd>" +
      "<dt>Date</dt><dd>" + esc(o.date) + "</dd>" +
      "<dt>PayPal sent</dt><dd>" + esc(o.item) + "</dd>" +
      "<dt>Resolved to</dt><dd>" + (o.matched ? esc(o.matched) : "<i>nothing</i>") +
      " " + matchChip(o) + "</dd></dl>";

    var why = (o.reasons && o.reasons.length)
      ? '<p class="whyhold"><b>Why this needs a person:</b> ' +
        o.reasons.map(esc).join(" ") + "</p>" : "";

    var files = (o.parts && o.parts.length)
      ? '<p class="dsec">Files this order issues</p><ul class="filelist">' +
        o.parts.map(function (f) { return "<li>" + esc(f) + "</li>"; }).join("") + "</ul>"
      : '<p class="dsec">Files</p><p class="signote">None resolved.</p>';

    var sig = "";
    if (o.signals && o.signals.length) {
      sig = '<p class="dsec">How the score was reached</p>' +
        '<p class="signote">Score <span class="score sc-' + esc(o.verdict) + '">' +
        o.score.toFixed(2) + "</span> against a threshold of " +
        S.holdBelow.toFixed(2) + ". Every signal comes from data Opus already " +
        "holds; there is no model.</p>" +
        o.signals.map(function (s) {
          return '<div class="sig"><span>' + esc(s.name) + "</span>" +
            '<span class="bar"><i class="' + (s.value < 0.5 ? "low" : "") +
            '" style="width:' + Math.round(s.value * 100) + '%"></i></span>' +
            '<span class="vl">' + s.value.toFixed(2) + " &times;" +
            s.weight.toFixed(2) + "</span></div>" +
            '<p class="signote">' + esc(s.note) + "</p>";
        }).join("");
    }
    openDrawer(o.buyer, o.verdict === "release" ? "Ready to issue" : "Needs a person",
               head + why + files + sig);
  }

  /* ---------------- threshold dial ---------------- */
  var dialTimer = null;
  function onDial() {
    var v = num($("dial").value, 1.01);
    $("dialval").textContent = v.toFixed(2);
    clearTimeout(dialTimer);
    dialTimer = setTimeout(function () { previewThreshold(v); }, 220);
  }

  function previewThreshold(v) {
    if (!S.orders.length) {
      // Silently doing nothing here reads as a broken control. Say why.
      $("dialout").innerHTML = "";
      $("dialnote").textContent =
        "No orders loaded yet, so there is nothing to preview against.";
      return;
    }
    API.post("/api/threshold-preview", {hold_below: v}).then(function (data) {
      var c = data.counts || {};
      $("dialout").innerHTML =
        '<span class="p-release">' + (c.release || 0) + " would release</span>" +
        '<span class="p-hold">' + (c.hold || 0) + " held</span>" +
        '<span class="p-reject">' + (c.reject || 0) + " unmatched</span>";
      var changed = (c.release || 0) - (S.counts.release || 0);
      $("dialnote").textContent = changed === 0
        ? "Same as the threshold you have saved."
        : (changed > 0
            ? changed + " more order(s) would go out without you seeing them."
            : (-changed) + " fewer order(s) would go out automatically.");
    }).catch(fail);
  }

  /* ---------------- ledger ---------------- */
  var LEDGER_COLS = [
    {key: "licensee", label: "Licensee"},
    {key: "order_ref", label: "Order", cls: "mono nw"},
    {key: "file", label: "File", cls: "mono"},
    {key: "pages", label: "Pages", cls: "num"},
    {key: "confidence", label: "Score", cls: "mono"},
    {key: "delivery_channel", label: "Sent"},
    {key: "status", label: "Status"}
  ];

  function renderLedgerHead() {
    var tr = $("ledgerhead");
    tr.innerHTML = "";
    LEDGER_COLS.forEach(function (c) {
      var th = el("th");
      var mark = S.sort.key === c.key ? (S.sort.dir > 0 ? "▲" : "▼") : "";
      th.setAttribute("aria-sort", S.sort.key === c.key
        ? (S.sort.dir > 0 ? "ascending" : "descending") : "none");
      var b = el("button", null,
        esc(c.label) + ' <span class="sort">' + mark + "</span>");
      b.addEventListener("click", function () {
        S.sort = {key: c.key, dir: S.sort.key === c.key ? -S.sort.dir : 1};
        renderLedgerHead(); renderLedger();
      });
      th.appendChild(b);
      tr.appendChild(th);
    });
  }

  function filteredLedger() {
    var q = ($("ledgersearch").value || "").toLowerCase().trim();
    var status = $("ledgerstatus").value;
    var rows = S.ledger.filter(function (r) {
      if (status && r.status !== status) { return false; }
      if (!q) { return true; }
      return (r.licensee + " " + r.order_ref + " " + r.file + " " + r.item_title)
        .toLowerCase().indexOf(q) >= 0;
    });
    var k = S.sort.key, dir = S.sort.dir;
    return rows.sort(function (a, b) {
      var x = (a[k] || "").toString().toLowerCase();
      var y = (b[k] || "").toString().toLowerCase();
      if (k === "pages" || k === "confidence") {
        return (num(a[k], 0) - num(b[k], 0)) * dir;
      }
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
  }

  function renderLedger() {
    var body = $("ledgerbody");
    body.innerHTML = "";
    var rows = filteredLedger();
    $("ledgercount").textContent = S.ledger.length
      ? rows.length + " of " + S.ledger.length + " rows" +
        (S.ledgerIntact === false ? " — chain broken" :
         S.ledgerIntact === true ? " — chain intact" : "")
      : "";
    if (!S.ledger.length) {
      body.appendChild(el("tr", null,
        '<td colspan="' + LEDGER_COLS.length + '" class="blank">' +
        "Nothing issued yet.</td>"));
      return;
    }
    if (!rows.length) {
      body.appendChild(el("tr", null,
        '<td colspan="' + LEDGER_COLS.length + '" class="blank">' +
        "Nothing matches that filter.</td>"));
      return;
    }
    rows.forEach(function (r) {
      var tr = el("tr", "hit", LEDGER_COLS.map(function (c) {
        var v = r[c.key] || "";
        if (c.key === "status") {
          return '<td><span class="pill p-' +
            (v === "ok" ? "done" : "hold") + '">' +
            esc(v === "ok" ? "issued" : v) + "</span></td>";
        }
        if (c.key === "delivery_channel") {
          return "<td>" + (v ? esc(v) : '<span class="signote">not sent</span>') + "</td>";
        }
        return '<td class="' + (c.cls || "") + '">' + esc(v) + "</td>";
      }).join(""));
      tr.addEventListener("click", function () { openLedgerRow(r); });
      body.appendChild(tr);
    });
  }

  function openLedgerRow(r) {
    var rows = [
      ["Licensee", esc(r.licensee)], ["Piece", esc(r.item_title)],
      ["Order", '<span class="mono">' + esc(r.order_ref) + "</span>"],
      ["Source", '<span class="mono">' + esc(r.source) + "</span>"],
      ["Issued file", '<span class="mono">' + esc(r.file) + "</span>"],
      ["Pages", esc(r.pages)], ["Score", esc(r.confidence || "—")],
      ["Decision", esc(r.decision || "—")],
      ["Owner password", '<span class="mono">' + esc(r.password || "—") + "</span>"],
      ["Delivered", r.delivered_at
        ? esc(r.delivery_channel) + " — " + esc(r.delivery_ref)
        : "not sent"]
    ];
    var html = '<dl class="kv">' + rows.map(function (p) {
      return "<dt>" + p[0] + "</dt><dd>" + p[1] + "</dd>";
    }).join("") + "</dl>";
    if (r.notes) {
      html += '<p class="dsec">Notes</p><p class="signote">' + esc(r.notes) + "</p>";
    }
    openDrawer(r.licensee, r.file, html);
  }

  function exportLedger() {
    var rows = filteredLedger();
    if (!rows.length) { return toast("Nothing to export.", true); }
    var cols = Object.keys(rows[0]);
    var csv = [cols.join(",")].concat(rows.map(function (r) {
      return cols.map(function (c) {
        var v = (r[c] == null ? "" : String(r[c]));
        return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
      }).join(",");
    })).join("\n");
    var blob = new Blob([csv], {type: "text/csv"});
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "opus-ledger.csv";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    toast("Exported " + rows.length + " rows.");
  }

  /* ---------------- saved views ---------------- */
  function renderViews() {
    var host = $("viewlist");
    host.innerHTML = "";
    if (!S.views.length) {
      host.appendChild(el("div", "signote",
        '<span style="padding:0 10px">None yet.</span>'));
      return;
    }
    S.views.forEach(function (v) {
      var row = el("div", "viewrow");
      var b = el("button", "nav",
        '<span class="ico" aria-hidden="true">&#9673;</span><span>' +
        esc(v.name) + "</span>");
      b.addEventListener("click", function () { applyView(v); });
      var x = el("button", "xbtn", "&times;");
      x.title = "Remove this view";
      x.setAttribute("aria-label", "Remove view " + v.name);
      x.addEventListener("click", function () { removeView(v.id); });
      row.appendChild(b); row.appendChild(x);
      host.appendChild(row);
    });
  }

  function applyView(v) {
    show(v.workspace || "ledger");
    if ((v.workspace || "ledger") === "ledger") {
      $("ledgersearch").value = v.query || "";
      $("ledgerstatus").value = (v.filters && v.filters.status) || "";
      if (v.filters && v.filters.sort) { S.sort = v.filters.sort; renderLedgerHead(); }
      renderLedger();
    }
  }

  function saveCurrentView() {
    var name = window.prompt("Name this view", $("ledgersearch").value || "Held orders");
    if (!name) { return; }
    var v = {
      id: "v" + Date.now(), name: name.slice(0, 80), workspace: "ledger",
      query: $("ledgersearch").value || "",
      filters: {status: $("ledgerstatus").value, sort: S.sort}
    };
    S.views = S.views.concat([v]);
    renderViews();
    API.post("/api/views", {views: S.views})
      .then(function () { toast("Saved “" + v.name + "”."); })
      .catch(fail);
  }

  function removeView(id) {
    S.views = S.views.filter(function (v) { return v.id !== id; });
    renderViews();
    API.post("/api/views", {views: S.views}).catch(fail);
  }

  /* ---------------- connections ---------------- */
  function renderConnectors() {
    var host = $("conngrid");
    host.innerHTML = "";
    S.connectors.forEach(function (c) {
      var card = el("div", "conn " + c.state);
      var fields = (c.fields || []).map(function (f) {
        var saved = (c.values || {})[f.key];
        var id = "f-" + c.name + "-" + f.key;
        if (f.type === "bool") {
          return '<div class="field"><label class="fl">' +
            '<input type="checkbox" id="' + id + '" style="width:auto;margin-right:8px"' +
            (saved ? " checked" : "") + ">" + esc(f.label) + "</label></div>";
        }
        var type = f.type === "password" ? "password"
                 : f.type === "number" ? "number" : "text";
        var picker = (f.type === "folder" || f.type === "file")
          ? '<button class="btn sm" data-pick="' + esc(f.type) +
            '" data-for="' + id + '">Choose</button>' : "";
        return '<div class="field"><label class="fl" for="' + id + '">' +
          esc(f.label) + "</label>" +
          (picker ? '<div class="withbtn">' : "") +
          '<input type="' + type + '" id="' + id + '" value="' +
          esc(saved == null ? "" : saved) + '">' +
          (picker ? picker + "</div>" : "") + "</div>";
      }).join("");

      card.innerHTML =
        "<h3>" + esc(c.label) +
        '<span class="st st-' + esc(c.state) + '">' + esc(c.state) + "</span></h3>" +
        '<p class="desc">' + esc(c.description) + "</p>" +
        (c.state === "planned"
          ? '<p class="signote">Not built. Selecting it is an error, not a quiet no-op.</p>'
          : fields +
            '<div style="display:flex;gap:8px;margin-top:4px">' +
            '<button class="btn sm" data-act="test">Test</button>' +
            '<button class="btn sm go" data-act="save">Save</button></div>' +
            '<div class="result"></div>');
      host.appendChild(card);

      card.querySelectorAll("[data-pick]").forEach(function (b) {
        b.addEventListener("click", function () {
          openPicker(b.dataset.pick, function (path) {
            $(b.dataset.for).value = path;
          });
        });
      });
      var act = function (which) {
        var values = {};
        (c.fields || []).forEach(function (f) {
          var node = $("f-" + c.name + "-" + f.key);
          if (!node) { return; }
          values[f.key] = f.type === "bool" ? node.checked
            : f.type === "number" ? num(node.value, 0) : node.value;
        });
        var res = card.querySelector(".result");
        if (which === "save") {
          var patch = {}; patch[c.name] = values;
          API.post("/api/settings", {section: "connectors", values: patch})
            .then(function (d) {
              S.settings = d.settings;
              res.className = "result ok";
              res.textContent = "Saved.";
            }).catch(fail);
          return;
        }
        res.className = "result";
        res.textContent = "";
        API.post("/api/connectors/test",
                 {kind: c.kind, name: c.name, values: values})
          .then(function (d) {
            res.className = "result " + (d.ok ? "ok" : "bad");
            res.textContent = d.message;
          }).catch(fail);
      };
      card.querySelectorAll("[data-act]").forEach(function (b) {
        b.addEventListener("click", function () { act(b.dataset.act); });
      });
    });
  }

  /* ---------------- folder picker ---------------- */
  function openPicker(kind, onPick) {
    var startAt = "";
    function draw(path) {
      API.post("/api/browse", {path: path}).then(function (d) {
        var body = el("div");
        body.innerHTML = '<p class="pickpath">' + esc(d.path) + "</p>";
        var pick = el("div", "picker");
        if (d.parent) {
          var up = el("button", "pi",
            '<span class="ic">&#8617;</span><span>Up one level</span>');
          up.addEventListener("click", function () { draw(d.parent); });
          pick.appendChild(up);
        }
        d.entries.forEach(function (e) {
          if (kind === "file" && e.kind === "dir") { /* still navigable */ }
          var b = el("button", "pi",
            '<span class="ic">' + (e.kind === "dir" ? "&#128193;" : "&#128196;") +
            "</span><span>" + esc(e.name) + "</span>");
          b.addEventListener("click", function () {
            if (e.kind === "dir") { draw(e.path); }
            else if (kind !== "folder") { onPick(e.path); closeDrawer(); }
          });
          pick.appendChild(b);
        });
        body.appendChild(pick);
        var choose = el("button", "btn go",
          kind === "folder" ? "Use this folder" : "Cancel");
        choose.style.marginTop = "12px";
        choose.addEventListener("click", function () {
          if (kind === "folder") { onPick(d.path); }
          closeDrawer();
        });
        body.appendChild(choose);
        openDrawer(kind === "folder" ? "Choose a folder" : "Choose a file",
                   "", "");
        $("dbody").innerHTML = "";
        $("dbody").appendChild(body);
      }).catch(fail);
    }
    draw(startAt);
  }

  /* ---------------- settings ---------------- */
  var PATH_FIELDS = [
    {key: "catalog_root", label: "Catalogue folder", type: "folder",
     hint: "One subfolder per piece."},
    {key: "paypal_csv", label: "PayPal activity export", type: "file",
     hint: "The CSV downloaded from PayPal."},
    {key: "out_dir", label: "Where stamped files go", type: "folder",
     hint: "The ledger lives here too."},
    {key: "portal_root", label: "Portal folder", type: "folder",
     hint: "Only needed if you deliver by link."}
  ];

  function renderSettings() {
    var host = $("pathfields");
    host.innerHTML = "";
    PATH_FIELDS.forEach(function (f) {
      var wrap = el("div", "field");
      var id = "p-" + f.key;
      wrap.innerHTML = '<label class="fl" for="' + id + '">' + esc(f.label) +
        '</label><div class="withbtn"><input type="text" id="' + id + '" value="' +
        esc((S.settings.paths || {})[f.key] || "") + '">' +
        '<button class="btn sm" data-pick>Choose</button></div>' +
        '<p class="hintline">' + esc(f.hint) + "</p>";
      host.appendChild(wrap);
      wrap.querySelector("[data-pick]").addEventListener("click", function () {
        openPicker(f.type, function (path) {
          $(id).value = path;
          savePaths();
        });
      });
      $(id).addEventListener("change", savePaths);
    });
    var save = el("button", "btn go", "Save folders");
    save.addEventListener("click", savePaths);
    host.appendChild(save);

    var d = S.settings.dashboard || {};
    $("theme").value = d.theme || "system";
    $("density").value = d.density || "comfortable";
    $("publisher").value = S.settings.publisher || "";
    var review = S.settings.review || {};
    $("autodeliver").checked = !!review.auto_deliver;
    renderChannels();
    $("cfgpath").textContent = "Kept in " + (S.configPath || "");
  }

  function renderChannels() {
    var chosen = ((S.settings.review || {}).deliver_channels) || [];
    $("channelfields").innerHTML =
      ["portal", "smtp"].map(function (name) {
        return '<label class="fl"><input type="checkbox" data-ch="' + name +
          '" style="width:auto;margin-right:8px"' +
          (chosen.indexOf(name) >= 0 ? " checked" : "") + ">" +
          (name === "portal" ? "Publish an expiring link" : "Send an email") +
          "</label>";
      }).join("");
  }

  function savePaths() {
    var values = {};
    PATH_FIELDS.forEach(function (f) {
      var node = $("p-" + f.key);
      if (node) { values[f.key] = node.value; }
    });
    API.post("/api/settings", {section: "paths", values: values})
      .then(function (d) {
        S.settings = d.settings;
        toast("Folders saved.");
        return refreshStatus();
      }).catch(fail);
  }

  /* ---------------- loading ---------------- */
  function refreshStatus() {
    return API.get("/api/status").then(function (d) {
      S.status = d;
      S.holdBelow = d.hold_below;
      $("verline").textContent = "v" + d.version;
      $("dial").value = d.hold_below;
      $("dialval").textContent = d.hold_below.toFixed(2);
      var missing = [];
      if (!d.catalog.ready) { missing.push("a catalogue folder"); }
      if (!d.export.ready) { missing.push("a PayPal export"); }
      if (!d.out_dir) { missing.push("somewhere to put stamped files"); }
      S.practice = !!d.practice;
      $("practicebanner").hidden = !d.practice;
      // In practice mode the setup prompt would be nagging about folders she
      // is deliberately not using yet.
      $("setupbanner").hidden = !missing.length || d.practice;
      if (missing.length) {
        $("setuptext").innerHTML = "<b>Not set up yet.</b> Opus still needs " +
          esc(missing.join(", ")) + ".";
      }
      $("ovtitle").textContent = d.catalog.ready
        ? "The day's orders" : "Welcome to Opus";
      renderWidgets();
    });
  }

  function loadOrders() {
    return API.get("/api/orders").then(function (d) {
      S.orders = d.orders; S.counts = d.counts;
      S.warnings = d.warnings; S.holdBelow = d.hold_below;
      renderOrders(); renderWidgets();
    }).catch(function (e) {
      S.orders = []; S.counts = {}; renderOrders();
      if (e.status !== 409) { fail(e); }
    });
  }

  function loadCatalog() {
    return API.get("/api/catalog").then(function (d) {
      S.catalog = d.catalog;
      var host = $("pieces");
      host.innerHTML = "";
      d.catalog.forEach(function (p) {
        host.appendChild(el("div", "piece",
          "<h3>" + esc(p.title) + "</h3>" +
          '<div class="meta">' + p.files + " parts</div><ul>" +
          p.parts.map(function (f) {
            return '<li><span class="f">' + esc(f.name) + "</span>" +
              '<span class="p">' + f.pages +
              (f.pages === 1 ? " page" : " pages") + "</span></li>";
          }).join("") + "</ul>"));
      });
      $("catsub").textContent = d.catalog.length + " piece(s) in " + d.root;
    }).catch(function (e) { if (e.status !== 409) { fail(e); } });
  }

  function loadLedger() {
    return API.get("/api/ledger").then(function (d) {
      S.ledger = d.ledger; S.ledgerIntact = d.intact;
      renderLedgerHead(); renderLedger(); renderWidgets();
    }).catch(fail);
  }

  function loadConnectors() {
    return API.get("/api/connectors").then(function (d) {
      S.connectors = d.connectors;
      renderConnectors();
    }).catch(fail);
  }

  /* ---------------- run ---------------- */
  function runBatch() {
    var c = S.counts || {};
    var msg = "Stamp " + (c.release || 0) + " order(s)?" +
      ((S.settings.review || {}).auto_deliver
        ? "\n\nThey will also be delivered."
        : "\n\nNothing will be sent; files are written and logged.");
    if (!window.confirm(msg)) { return; }

    var con = $("console");
    con.hidden = false;
    con.innerHTML = "<div>Working…</div>";
    $("runbtn").disabled = true;

    API.post("/api/run", {confirm: true}).then(function (d) {
      con.innerHTML = "";
      (d.progress || []).forEach(function (line) {
        con.appendChild(el("div", null, esc(line)));
      });
      var s = d.summary || {};
      con.appendChild(el("div", "ok",
        s.orders + " order(s), " + s.files_ok + " file(s) stamped, " +
        s.files_failed + " failed, " + (s.held || 0) + " held."));
      if (d.ledger_intact === false) {
        con.appendChild(el("div", "bad", "Ledger chain check FAILED."));
      }
      con.scrollTop = con.scrollHeight;
      toast(s.files_ok + " file(s) issued.");
      return Promise.all([loadOrders(), loadLedger(), refreshStatus()]);
    }).catch(function (e) {
      con.innerHTML = "";
      con.appendChild(el("div", "bad", esc(e.message)));
      fail(e);
    }).then(function () { $("runbtn").disabled = false; });
  }

  /* ---------------- help ----------------
     Reachable from every workspace, never modal. It answers the three
     questions people actually have rather than describing the layout. */
  function openHelp(key) {
    var h = S.help && S.help.workspaces && S.help.workspaces[key];
    if (!h) { return toast("No help for that yet.", true); }
    openDrawer(h.title, "What this is for", [
      ["What it is", h.what],
      ["What you decide here", h.decide],
      ["Worth knowing", h.watch]
    ].map(function (pair) {
      return '<div class="helpsec"><h3>' + esc(pair[0]) + "</h3><p>" +
        esc(pair[1]) + "</p></div>";
    }).join(""));
  }

  /* ---------------- first run ----------------
     A checklist, not a tour. Every step reports from real settings, so it can
     never claim she is further along than she is. */
  function stepButton(step, label) {
    var go = el("button", "btn sm go", esc(label || step.action));
    go.addEventListener("click", function () {
      if (step.id === "practice") { setPractice(true); return; }
      show(step.workspace || "settings");
    });
    return go;
  }

  function renderSteps() {
    var host = $("steplist");
    if (!host) { return; }
    host.innerHTML = "";
    (S.steps || []).forEach(function (step, i) {
      var row = el("div", "step" + (step.done ? " done" : ""),
        '<span class="n">' + (step.done ? "&#10003;" : (i + 1)) + "</span>" +
        "<span><h3>" + esc(step.title) + "</h3><p>" + esc(step.body) +
        "</p></span>");
      row.appendChild(stepButton(step, step.done ? "Revisit" : step.action));
      host.appendChild(row);
    });
  }

  function renderStartWidget(host) {
    var left = (S.steps || []).filter(function (x) { return !x.done; });
    if (!left.length) {
      host.innerHTML = '<div class="body"><p>All set up. Getting started is ' +
        "still in the sidebar if you want to reread any of it.</p></div>";
      return;
    }
    var body = el("div", "body");
    body.appendChild(el("p", null,
      "<b>" + left.length + " thing" + (left.length === 1 ? "" : "s") +
      " left to set up.</b> " + esc(left[0].body)));
    body.appendChild(stepButton(left[0]));
    var all = el("button", "btn sm", "See all steps");
    all.style.marginLeft = "8px";
    all.addEventListener("click", function () { show("start"); });
    body.appendChild(all);
    host.innerHTML = "";
    host.appendChild(body);
  }

  /* ---------------- practice mode ---------------- */
  function setPractice(on) {
    return API.post("/api/practice", {on: on}).then(function (d) {
      S.practice = d.practice;
      $("practicebanner").hidden = !d.practice;
      if (d.threshold_restored) {
        toast("Practice mode off. The release threshold is back at " +
              d.threshold_restored.to.toFixed(2) +
              " — the one you set while practising does not carry over.");
      } else {
        toast(d.practice
          ? "Practice mode on. Using the made-up catalogue."
          : "Practice mode off. Back to your own folders.");
      }
      S.catalog = [];
      S.connectors = [];
      return refreshStatus().then(loadHelp).then(loadOrders).then(loadLedger);
    }).catch(fail);
  }

  function loadHelp() {
    return API.get("/api/help").then(function (d) {
      S.help = d;
      S.steps = d.steps || [];
      renderSteps();
      renderWidgets();
    }).catch(function () { /* help is never worth an error */ });
  }

  /* ---------------- wiring ---------------- */
  function wire() {
    document.querySelectorAll(".nav[data-ws]").forEach(function (b) {
      b.addEventListener("click", function () { show(b.dataset.ws); });
    });
    $("ov-open").addEventListener("click", function () { show("orders"); });
    $("gosetup").addEventListener("click", function () { show("settings"); });
    $("ov-arrange").addEventListener("click", function () {
      S.arranging = !S.arranging;
      $("ov-arrange").textContent = S.arranging ? "Done" : "Arrange";
      renderWidgets();
    });
    document.querySelectorAll("[data-help]").forEach(function (b) {
      b.addEventListener("click", function () { openHelp(b.dataset.help); });
    });
    $("practiceoff").addEventListener("click", function () { setPractice(false); });
    $("dismissstart").addEventListener("click", function () {
      API.post("/api/onboarding", {dismissed: true}).then(function () {
        if (S.status) { S.status.onboarding_dismissed = true; }
        renderWidgets();
        toast("Hidden. It is still in the sidebar.");
      }).catch(fail);
    });
    $("refreshorders").addEventListener("click", loadOrders);
    $("refreshcat").addEventListener("click", loadCatalog);
    $("runbtn").addEventListener("click", runBatch);
    $("dial").addEventListener("input", onDial);
    $("savethreshold").addEventListener("click", function () {
      var v = num($("dial").value, 1.01);
      API.post("/api/settings", {section: "review", values: {hold_below: v}})
        .then(function (d) {
          S.settings = d.settings; S.holdBelow = v;
          toast("Threshold saved at " + v.toFixed(2) + ".");
          return loadOrders();
        }).catch(fail);
    });
    $("ledgersearch").addEventListener("input", renderLedger);
    $("ledgerstatus").addEventListener("change", renderLedger);
    $("saveview").addEventListener("click", saveCurrentView);
    $("exportbtn").addEventListener("click", exportLedger);
    $("verifybtn").addEventListener("click", function () {
      API.post("/api/ledger/verify", {}).then(function (d) {
        S.ledgerIntact = d.intact;
        openDrawer("Ledger chain", d.intact === false ? "Problem found" : "",
          "<p>" + (d.report || []).map(esc).join("<br>") + "</p>");
        renderLedger();
      }).catch(fail);
    });
    $("dclose").addEventListener("click", closeDrawer);
    $("scrim").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { closeDrawer(); }
    });

    ["theme", "density"].forEach(function (id) {
      $(id).addEventListener("change", function () {
        var values = {theme: $("theme").value, density: $("density").value};
        applyAppearance(values);
        S.settings.dashboard.theme = values.theme;
        S.settings.dashboard.density = values.density;
        API.post("/api/dashboard", {values: values}).catch(fail);
      });
    });
    $("publisher").addEventListener("change", function () {
      API.post("/api/settings",
               {section: "publisher", values: $("publisher").value})
        .then(function (d) { S.settings = d.settings; }).catch(fail);
    });
    $("savedelivery").addEventListener("click", function () {
      var chosen = [];
      document.querySelectorAll("[data-ch]").forEach(function (c) {
        if (c.checked) { chosen.push(c.dataset.ch); }
      });
      API.post("/api/settings", {section: "review", values: {
        auto_deliver: $("autodeliver").checked, deliver_channels: chosen
      }}).then(function (d) {
        S.settings = d.settings; toast("Delivery settings saved.");
      }).catch(fail);
    });
  }

  /* ---------------- boot ---------------- */
  function boot() {
    wire();
    API.get("/api/settings").then(function (d) {
      S.settings = d.settings;
      S.configPath = d.config_path;
      applyAppearance(S.settings.dashboard || {});
      renderSettings();
      return API.get("/api/views");
    }).then(function (d) {
      S.views = d.views || [];
      renderViews();
      return refreshStatus();
    }).then(function () {
      return loadHelp();
    }).then(function () {
      return loadOrders();
    }).then(function () {
      return loadLedger();
    }).then(function () {
      var start = (location.hash || "").replace("#", "");
      show(WS.indexOf(start) >= 0 ? start : "overview", {silent: true});
    }).catch(fail);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
