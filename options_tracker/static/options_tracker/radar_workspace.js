(() => {
  "use strict";
  const root = document.getElementById("workspace");
  let payload = JSON.parse(document.getElementById("workspace-data").textContent);
  let state = {...payload.state};
  let dirtyNotes = false;
  let selectedId = null;
  let generation = 0;
  let busy = false;
  let saving = Promise.resolve();
  let refreshController = null;
  const chartViews = new Map();
  let pickingAlert = false;
  let draggingChart = false;
  const csrf = document.querySelector("#csrf-form input").value;
  const el = (id) => document.getElementById(id);
  const number = (value, digits = 2) => value === null || value === undefined || !Number.isFinite(Number(value)) ? "Unavailable" : Number(value).toLocaleString("en-IN", {maximumFractionDigits: digits});
  const stamp = (value) => value ? new Date(value).toLocaleString("en-IN", {timeZone: "Asia/Kolkata", hour12: false}) + " IST" : "Unavailable";
  const clock = (value) => new Date(value).toLocaleTimeString("en-IN", {timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false});
  function node(tag, text, cls) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (cls) result.className = cls;
    return result;
  }
  function empty(id, text) { el(id).replaceChildren(node("p", text, "empty")); }
  function error(message) { el("request-error").textContent = message; el("request-error").hidden = !message; }
  async function request(url, body, signal) {
    const response = await fetch(url, {
      method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store",
      headers: body === undefined ? {Accept: "application/json"} : {"Content-Type": "application/json", "X-CSRFToken": csrf, Accept: "application/json"},
      body: body === undefined ? undefined : JSON.stringify(body), signal: signal || AbortSignal.timeout(30000),
    });
    if (!response.headers.get("content-type")?.includes("application/json")) throw new Error("Session expired or server unavailable. Reload to sign in; displayed data may be stale.");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }
  function persist() {
    const snapshot = {...state};
    history.replaceState(null, "", "?" + new URLSearchParams(snapshot));
    saving = saving.catch(() => {}).then(() => request(root.dataset.preferences, snapshot));
    saving.catch((err) => error(`Preferences were not saved: ${err.message}`));
  }
  async function refresh(persistState = false) {
    if (refreshController) refreshController.abort();
    refreshController = new AbortController();
    const controller=refreshController;
    const timeout = setTimeout(()=>controller.abort(),60000);
    const token = ++generation;
    busy = true;
    el("refresh").disabled = true;
    el("workspace-loading").textContent = "Loading chart and source data...";
    try {
      const data = await request(root.dataset.url + "?" + new URLSearchParams(state), undefined, controller.signal);
      if (token !== generation) return;
      payload = data;
      state = {...data.state};
      render();
      error("");
      if (persistState) persist();
    } catch (err) {
      if (token === generation) {
        error(`Workspace refresh failed: ${err.message} Last successful data retained; do not treat it as live.`);
        for (const button of document.querySelectorAll('#strategies form button')) button.disabled = true;
      }
    } finally {
      clearTimeout(timeout);
      if (token === generation) { busy = false; refreshController = null; el("refresh").disabled = false; el("workspace-loading").textContent=""; }
    }
  }
  function filteredRows() {
    return (payload.all_rows || payload.rows).map(item=>{
      const events=(item.filter_events || []).filter(e=>(!state.strategy || e.strategy===state.strategy) && (!state.state || e.state===state.state));
      const latest=events.find(e=>e.current) || events[0];
      return {...item,strategy:latest?.strategy || "",state:latest?.state || "",bias:latest?.current?(latest.side==="CE"?"Bullish":"Bearish"):"Unconfirmed",distance_pct:latest && item.quote.last?(latest.trigger-item.quote.last)/item.quote.last*100:null};
    }).filter(item=>{
      const events=(item.filter_events || []).filter(e=>(!state.strategy || e.strategy===state.strategy) && (!state.state || e.state===state.state));
      return (!state.q || `${item.symbol} ${item.name}`.toLowerCase().includes(state.q.toLowerCase())) &&
        (!(state.strategy || state.state) || events.length>0) &&
        (state.view==="all" || (state.view==="watchlist" && item.watched) || (state.view==="radar" && events.some(e=>e.current)) || (state.view==="positions" && events.some(e=>e.holding)));
    }).sort((a,b)=>{
      const key={change:"change_pct",rvol:"rvol",distance:"distance_pct"}[state.sort];
      if (!key) return a.symbol.localeCompare(b.symbol);
      if (a[key]===null || b[key]===null) return a[key]===null ? (b[key]===null ? a.symbol.localeCompare(b.symbol):1):-1;
      return (key==="distance" ? Math.abs(a[key])-Math.abs(b[key]):b[key]-a[key]) || a.symbol.localeCompare(b.symbol);
    });
  }
  function setState(changes) {
    if (changes.symbol && changes.symbol !== state.symbol && dirtyNotes) {
      error("Save your edited notes before switching stocks, or reload to discard them.");
      return;
    }
    const previousState=state, previousRows=payload.rows, wasBusy=busy;
    state = {...state, ...changes};
    if (["period","interval","chart_date"].some(key=>key in changes && changes[key]!==previousState[key])) chartViews.clear();
    const discovery=Object.keys(changes).every(key=>["view","q","strategy","state","sort"].includes(key));
    if (discovery) {
      payload.rows=filteredRows();
      const selected=payload.selected;
      if (!payload.rows.some(row=>row.symbol===selected?.symbol)) {
        const next=payload.rows[0]?.symbol || "";
        if (dirtyNotes) {state=previousState;payload.rows=previousRows;render();error("Save your edited notes before changing filters.");return;}
        state.symbol=next;payload.selected=null;
        render();
        if (next) {refresh(true);return;}
      }
      render();
      if(wasBusy)refresh(true);else persist();
    } else {
      if (changes.symbol && changes.symbol!==payload.selected?.symbol) {payload.selected=null;render();}
      refresh(true);
    }
  }
  function colored(value) { return value === null ? "muted" : value >= 0 ? "positive" : "negative"; }
  function cell(row, text, cls) { const result = node("td", text, cls); row.append(result); return result; }
  function grid(entries, cls = "risk-grid") {
    const result = node("div", undefined, cls);
    for (const [label, value] of entries) {
      const item = node("div");
      item.append(node("span", label), node("strong", value));
      result.append(item);
    }
    return result;
  }
  async function watch(instrumentId, action, extra = {}) {
    let saved=false;
    try {
      await request(root.dataset.watch, {instrument_id: instrumentId, action, ...extra});
      saved=true;
      for (const row of payload.all_rows || payload.rows) if(row.id===instrumentId) {row.watched=action!=="remove";if(action==="save")row.notes=extra.notes;}
      payload.watch_count=(payload.all_rows || payload.rows).filter(row=>row.watched).length;
      if (action === "save" && selectedId === instrumentId) {
        const unchanged = el("notes").value === extra.notes && (el("watched-level").value === "" ? null : Number(el("watched-level").value)) === extra.watched_level;
        dirtyNotes = !unchanged;
        el("notes-status").textContent = unchanged ? "Saved" : "Earlier revision saved; current changes unsaved";
      }
      payload.rows=filteredRows();
      el("notes-status").textContent=action==="remove" ? "Removed from your watchlist" : "Saved to your watchlist";
      if(payload.selected?.id===instrumentId){
        if(action==="save"){payload.selected.notes=extra.notes;payload.selected.watched_level=extra.watched_level;}
        if(action==="remove"){payload.selected.notes="";payload.selected.watched_level=null;}
      }
      if(state.view==="watchlist" && action==="remove" && payload.selected?.id===instrumentId){payload.selected=null;state.symbol="";}
      render();error("");
    } catch (err) { error(`${saved ? "Watchlist saved, but the view could not update" : "Watchlist update could not be confirmed"}: ${err.message}`); }
  }
  function renderTable() {
    const body = el("stocks");
    body.replaceChildren();
    for (const item of payload.rows) {
      const row = node("tr", undefined, item.symbol === state.symbol ? "selected" : "");
      const name = cell(row);
      const star = node("button", item.watched ? "\u2605" : "\u2606", "watch-toggle");
      star.type = "button";
      star.setAttribute("aria-label", `${item.watched ? "Remove" : "Add"} ${item.symbol} ${item.watched ? "from" : "to"} watchlist`);
      star.setAttribute("aria-pressed", String(item.watched));
      star.onclick = () => watch(item.id, item.watched ? "remove" : "add");
      const link = node("button", item.symbol, "stock-link");
      link.type = "button";
      link.onclick = () => setState({symbol: item.symbol, contract: ""});
      name.append(star, link, node("div", item.display_status, payload.session?.review ? "muted" : item.quote.fresh ? "positive" : "stale"));
      name.title = `${item.name}\nSource: ${stamp(item.quote.source_at)}\nReceived: ${stamp(item.quote.received_at)}\n${item.notes}`;
      const price = cell(row, number(item.display_price));
      price.append(node("div", number(item.change_pct) + (item.change_pct === null ? "" : "%"), colored(item.change_pct)));
      cell(row, number(item.rvol) + (item.rvol === null ? "" : "x")).title=item.rvol_reason || "Same-time prefix coverage required.";
      const setup = cell(row, `${item.strategy || "No setup"} / ${item.bias}`);
      setup.append(node("div", `${item.state || "Not recorded"} / ${number(item.distance_pct)}${item.distance_pct === null ? "" : "%"}`, "muted"));
      body.append(row);
    }
    if (!payload.rows.length) { const row = node("tr"); const td = cell(row, "No stocks match this view. Try All F&O or change the filters.", "empty"); td.colSpan = 4; body.append(row); }
    el("counts").textContent = `${payload.rows.length} shown / ${payload.universe} F&O stocks | ${payload.watch_count} watched`;
  }
  function renderPortfolio() {
    const p = payload.portfolio;
    el("portfolio").replaceChildren(node("h2", "Shared paper portfolio"), grid([
      ["Open positions", number(p.open_count, 0)], ["Premium exposure", "INR " + number(p.premium_exposure)],
      ["Planned stop risk + fees", "INR " + number(p.planned_stop_risk)], ["Net realised", "INR " + number(p.net_realised)],
    ]), node("p", p.warning, "muted"));
    const target = el("performance");
    target.replaceChildren(node("p", "Closed paper trades only; net of estimated round-trip charges. Samples are not evidence of a live edge.", "muted"));
    if (!p.performance.length) { target.append(node("p", "No closed paper sample yet.", "empty")); return; }
    const wrap = node("div", undefined, "table-scroll");
    const table = node("table");
    const head = node("tr");
    for (const label of ["Strategy / version", "Closed n", "Win %", "Expectancy INR", "Net INR"]) head.append(node("th", label));
    table.append(head);
    for (const item of p.performance) {
      const row = node("tr");
      for (const value of [`${item.strategy} / ${item.version}`, item.closed, number(item.win_rate), number(item.expectancy), number(item.net_pnl)]) cell(row, value);
      table.append(row);
    }
    wrap.append(table); target.append(wrap);
  }
  function renderMetrics(stock) {
    el("metrics").replaceChildren();
    el("definitions").replaceChildren();
    for (const key of ["change_pct", "gap", "high", "low", "volume", "rvol", "vwap", "vwap_distance", "atr", "range_atr", "market_strength", "sector_strength"]) {
      const m = stock.metrics[key];
      const item = node("div", undefined, "metric");
      item.title = `${m.reason}\nAs of: ${stamp(m.at)}`;
      item.append(node("small", m.label), node("strong", number(m.value) + (m.value === null ? "" : " " + m.unit)));
      el("metrics").append(item);
    }
    for (const m of Object.values(stock.metrics)) el("definitions").append(node("p", `${m.label}: ${m.reason} As of ${stamp(m.at)}. ${m.value === null ? "UNAVAILABLE." : ""}`));
    const c = stock.coverage;
    el("definitions").prepend(node("p", `${c.complete_minutes}/${c.expected_minutes} completed current-session minutes; continuous from open: ${c.prefix_complete ? "yes" : "no"}. RVOL baseline sessions: ${stock.baseline_sessions}. Sources: ${stock.chart_source.join(", ") || "none"}.`));
  }
  function renderNews(stock) {
    el("news").replaceChildren();
    for (const item of stock.news) {
      const article = node("article", undefined, "news-item");
      article.append(node("strong", item.source || "Telegram news"), node("p", item.text || ""));
      article.append(node("p", `Published ${stamp(item.published_at)} | received ${stamp(item.received_at)}`, "muted"));
      if (item.url) {
        const url = new URL(item.url, location.origin);
        if (url.protocol === "https:" && url.hostname === "t.me") {
          const link = node("a", "Original Telegram post"); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; article.append(link);
        }
      }
      el("news").append(article);
    }
    if (!stock.news.length) empty("news", "No current exact-symbol news matches in collected messages. This is not a claim that no news exists.");
  }
  function actionForm(item, symbol) {
    const form = node("form");
    form.method = "post";
    form.action = `${location.pathname}${item.id}/acknowledge/`;
    for (const [name, value] of [["csrfmiddlewaretoken", csrf], ["symbol", symbol]]) {
      const input = node("input"); input.type = "hidden"; input.name = name; input.value = value; form.append(input);
    }
    for (const [action, label] of [["take", "Take PAPER position"], ["skip", "Skip"]]) {
      const button = node("button", label); button.name = "action"; button.value = action; button.type = "submit";
      button.disabled = !item.can_acknowledge;
      form.append(button);
    }
    return form;
  }
  function renderStrategies(stock) {
    el("strategies").replaceChildren();
    for (const strategy of stock.strategies) {
      const card = node("article", undefined, "strategy-card");
      card.append(node("h3", `${strategy.code} / ${strategy.name}`), node("div", strategy.version, "muted"));
      const rules = node("details");
      rules.append(node("summary", "Strategy conditions (definition, not a passed checklist)"));
      const definition = node("ul");
      for (const rule of strategy.rules) definition.append(node("li", rule));
      definition.append(node("li", "Completed bars and warmup required; entries 09:30-14:30 IST. Bearish rules mirror bullish rules. Current option, expiry, spread, depth, momentum and portfolio risk gates must also pass."));
      rules.append(definition); card.append(rules);
      const item = strategy.opportunity;
      if (!item) { card.append(node("p", "No recorded setup. No per-stock evaluation evidence is available here; this is not a verified failed checklist.", "muted")); }
      else {
        card.append(node("p", `${item.display_state} / ${item.side}`, "state"), node("p", item.reason));
        const checks = node("ul");
        for (const check of item.checks) checks.append(node("li", `${check.label}: ${check.status} - ${check.detail}`));
        card.append(checks, grid([
          ["Underlying trigger / stop", `${number(item.trigger)} / ${number(item.stop)}`],
          ["Entry ask limit", number(item.entry_limit)],
          ["Units / lots", `${number(item.quantity, 0)} / ${item.lot_size ? number(item.quantity/item.lot_size, 0) : "Unavailable"}`],
          ["Premium deployed", number(item.premium_deployed)],
          ["Planned stop risk + fees", number(item.planned_stop_risk)],
          ["Premium emergency stop", number(item.emergency_stop)],
        ]), node("p", `Policy capital INR ${number(item.policy.capital)}; planned entry risk ${number(item.policy.risk_fraction*100)}%, entry premium ${number(item.policy.debit_fraction*100)}%, total premium ${number(item.policy.total_debit_fraction*100)}%, daily realised loss cap ${number(item.policy.daily_loss_fraction*100)}%, max ${item.policy.max_positions} positions.`, "muted"),
        node("p", `Valid until ${stamp(item.valid_until)}. Pinned contract ID ${item.contract_id || "not selected"}.`, "muted"));
        if (item.action_block) card.append(node("p", item.action_block, "negative"));
        if (item.state === "ELIGIBLE") card.append(actionForm(item, stock.symbol));
        const detail = node("details");
        detail.append(node("summary", "Recent decision audit"));
        for (const decision of item.decisions) detail.append(node("p", `${stamp(decision.at)} / ${decision.state}: ${decision.reason}`, "muted"));
        if (!item.decisions.length) detail.append(node("p", "No decision snapshots recorded.", "muted"));
        card.append(detail);
      }
      el("strategies").append(card);
    }
    el("positions").replaceChildren(node("h2", "Paper positions & outcomes"));
    const positions = stock.opportunities.filter((item) => ["TAKEN", "EXIT_PENDING", "EXITED"].includes(item.state));
    for (const item of positions) {
      const card = node("article", undefined, "position-card");
      card.append(node("h3", `${item.strategy} ${item.version} / ${item.display_state}`), grid([
        ["Entry / units", `${number(item.entry_price)} / ${number(item.quantity, 0)}`],
        ["Executable bid", number(item.executable_bid)],
        ["Net unrealised estimate", number(item.net_unrealised)],
        ["Emergency premium stop", number(item.emergency_stop)],
        ["Trail", item.trail_active ? `Active @ ${number(item.trail_level)}` : "Not active"],
        ["Exit / realised net", `${number(item.exit_price)} / ${number(item.net_pnl)}`],
      ]), node("p", item.exit_reason || item.reason, "muted"));
      el("positions").append(card);
    }
    if (!positions.length) el("positions").append(node("p", "No paper positions for this stock.", "empty"));
  }
  function renderOptions(stock) {
    const select = el("contract");
    select.replaceChildren();
    el("contracts").replaceChildren();
    const executable=stock.candidates.filter(item=>!item.rejection);
    el("option-readiness").textContent=payload.session?.review ?
      "Market closed. No executable option recommendation. The catalogue is for preparation only." :
      executable.length ? `${executable.length} contracts pass current quote checks; this does not authorize an entry.` :
      "No option passes current quote checks. Add the stock to your watchlist for bounded collection; wait for fresh bid/ask, depth and confirmation.";
    el("contract-catalogue").open=!payload.session?.review && executable.length>0;
    for (const item of stock.candidates) {
      const opt = node("option", item.label); opt.value = item.id; select.append(opt);
      const row = node("tr", undefined, String(item.id) === state.contract ? "selected" : "");
      const button = node("button", item.label); button.type = "button"; button.onclick = () => setState({contract: String(item.id)});
      cell(row).append(button);
      row.firstChild.append(node("div", `${number(item.distance_pct)}% from underlying reference | ${item.rejection || "Quote checks passed only"}`, "muted"));
      if(!item.quote.source_at){
        const missing=cell(row,"Not collected - no executable quote","muted");missing.colSpan=3;
        el("contracts").append(row);continue;
      }
      const prices = cell(row, `${number(item.quote.bid)} / ${number(item.quote.ask)}`);
      prices.append(node("div", `${number(item.spread_pct)}%`, "muted"));
      const depth = cell(row, `${number(item.quote.bid_qty, 0)} / ${number(item.quote.ask_qty, 0)}`);
      depth.append(node("div", `${number(item.quote.oi, 0)} / ${number(item.quote.volume, 0)}`, "muted"));
      const age = cell(row, `${number(item.quote.age_seconds, 1)}s`, item.quote.fresh ? "positive" : "stale");
      age.append(node("div", `${number(item.premium_momentum_pct)}% / ${number(item.momentum_seconds, 0)}s`, "muted"));
      row.title = `${item.rejection || "Quote checks passed only. All strategy, sizing and portfolio gates still apply."} Priority: executable checks, premium activity, spread, whole-lot depth, expiry, strike distance. Not gamma or profit probability.`;
      el("contracts").append(row);
    }
    select.value = state.contract;
    const selected = stock.candidates.find((item) => String(item.id) === state.contract);
    el("option-detail").replaceChildren();
    if (!selected) {
      el("option-detail").append(node("p", "No contract proximity ranking without a recent underlying reference. Check recorded coverage and the instrument registry.", "empty"));
    } else {
      el("option-detail").append(
        node("p", `${selected.label} | Dhan ID ${selected.security_id} | lot ${selected.lot_size}`),
        node("p", selected.rejection || "Quote checks passed. Not an entry authorization.", selected.rejection ? "negative" : "positive"),
        node("p", `Source ${stamp(selected.quote.source_at)} | received ${stamp(selected.quote.received_at)}`, "muted"),
        node("p", `IV / delta / gamma / theta / vega: Unavailable. ${selected.greeks_reason}`, "muted"),
      );
    }
    el("option-detail").prepend(node("p", `Underlying reference INR ${number(stock.option_reference.price)} | ${stock.option_reference.basis} | ${stamp(stock.option_reference.at)}. ${stock.option_reference.note}`, "muted"));
    el("option-detail").append(node("p", `Full-chain PCR: Unavailable. ${stock.oi_context.reason}`, "muted"));
    premiumChart(stock.option_history);
  }
  const svgNS = "http://www.w3.org/2000/svg";
  function svgNode(tag, attrs, text) {
    const result = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attrs)) result.setAttribute(key, value);
    if (text !== undefined) result.textContent = text;
    return result;
  }
  function label(svg, x, y, text, fill = "var(--chart-muted)", anchor = "start") {
    svg.append(svgNode("text", {x, y, fill, "font-size": 12, "text-anchor": anchor}, text));
  }
  function stockChart(stock) {
    candleChart(stock, false);
    candleChart(stock, true);
  }
  function chartRange(key, bars) {
    let view = chartViews.get(key);
    if (!view) { view = {count: Math.min(600, bars.length), end: null}; chartViews.set(key, view); }
    let end = view.end === null ? bars.length : bars.findIndex(b => Date.parse(b.time) > view.end);
    if (end < 0) end = bars.length;
    end = Math.max(Math.min(8, bars.length), Math.min(bars.length, end));
    const count = Math.max(Math.min(8, bars.length), Math.min(600, bars.length, view.count));
    const start = Math.max(0, end-count);
    return {view, start, end, bars: bars.slice(start, end)};
  }
  function candleChart(stock, option) {
    const target = el(option ? "option-chart" : "stock-chart");
    const detailTarget = el(option ? "option-chart-detail" : "chart-detail");
    const hadFocus = target.contains(document.activeElement);
    const allBars = stock ? (option ? stock.option_chart : stock.chart) : [];
    target.replaceChildren();
    detailTarget.textContent = "Hover or focus a candle for OHLC, volume and RSI.";
    if (!stock) { target.append(node("p", "No selected F&O stock.", "chart-empty")); return; }
    if (!allBars.length) { target.append(node("p", `No complete ${option ? "option" : "stock"} candles yet. ${option ? "Watched options collect prospectively; premium samples are not OHLC. " : ""}Missing or partial minutes are never filled.`, "chart-empty")); return; }
    const key = `${stock.symbol}:${option ? state.contract : "equity"}:${state.interval}:${state.period}:${state.chart_date}`;
    const range = chartRange(key, allBars), bars = range.bars;
    const move = (delta) => {
      const end = Math.min(allBars.length, Math.max(Math.min(allBars.length, range.view.count), range.end+delta));
      range.view.end = Date.parse(allBars[end-1].time); candleChart(stock, option);
    };
    const zoom = (factor) => { range.view.count = Math.min(600, allBars.length, Math.max(Math.min(8,allBars.length), Math.round(bars.length*factor))); candleChart(stock, option); };
    const controls = node("div", undefined, "chart-controls");
    for (const [text, action, title] of [
      ["+", () => zoom(.75), "Zoom in"], ["-", () => zoom(1.35), "Zoom out"],
      ["<", () => move(-Math.max(1,Math.floor(bars.length/4))), "Pan earlier"],
      [">", () => move(Math.max(1,Math.floor(bars.length/4))), "Pan later"],
      ["Fit", () => { range.view.count=Math.min(600,allBars.length); range.view.end=null; candleChart(stock,option); }, "Fit latest recorded window (up to 600 bars)"],
      ["Live", () => { range.view.count=Math.min(60,allBars.length); range.view.end=null; candleChart(stock,option); }, "Follow latest bars"],
    ]) {
      const button=node("button",text); button.type="button"; button.title=title; button.setAttribute("aria-label",`${option ? "Option" : "Stock"} chart ${title}`); button.onclick=action; controls.append(button);
    }
    controls.append(node("span", `${bars.length}/${allBars.length} complete bars | ${range.view.end === null ? "following latest" : "fixed view"}`, "muted"));
    target.append(controls);
    target.append(node("p",`Visible ${stamp(bars[0].time)} to ${stamp(bars[bars.length-1].time)} | ${state.interval} ${["D","W","M"].includes(state.interval) ? "aggregate" : "minute"} candles`,"muted chart-visible-range"));
    if (allBars.length>600) target.append(node("p","600-candle display limit: only part of the requested history is visible. Pan earlier, or choose a coarser interval to fit more history.","muted"));
    const flags = new Set(option ? [] : state.overlays.split(","));
    const width = Math.max(320, target.clientWidth), height = 540, left = 14, right = 95, top = 24, bottom = 265;
    const intervalMinutes = Number(state.interval) || 1440;
    const overlays = [];
    const currentReference=stock.chart_range?.current_reference!==false;
    if (flags.has("levels") && currentReference) for (const key of ["previous_high", "previous_low", "previous_close"]) {
      const m = stock.metrics[key]; if (m.value !== null) overlays.push([`Current ${m.label}`, m.value, "var(--chart-level)"]);
    }
    if (flags.has("levels") && stock.watched_level !== null) overlays.push(["Watched", stock.watched_level, "var(--chart-gold)"]);
    if (flags.has("strategy") && currentReference) for (const s of stock.strategies) if (s.opportunity && ["ARMED", "TRIGGERED", "ELIGIBLE", "REJECTED", "TAKEN", "EXIT_PENDING"].includes(s.opportunity.state)) {
      overlays.push([`${s.code} trigger`, s.opportunity.trigger, "var(--cyan)"], [`${s.code} stop`, s.opportunity.stop, "var(--chart-down)"]);
    }
    if (!option) for (const alert of payload.price_alerts || []) if (alert.instrument_id === stock.id && alert.status === "WATCHING") {
      overlays.push([`Alert #${alert.id} ${alert.direction.toLowerCase()}`, alert.level, "var(--chart-gold)"]);
    }
    const values = bars.flatMap((b) => [b.low, b.high]);
    const low = Math.min(...values), high = Math.max(...values), span = high-low || 1;
    const min = low-span*.06, max = high+span*.06;
    const start = Date.parse(bars[0].time), end = Date.parse(bars[bars.length-1].time) + intervalMinutes*60000;
    const x = (time) => {
      const at=Date.parse(time);let index=bars.findIndex(b=>Date.parse(b.time)>=at);
      if(index<0)index=bars.length-1;
      return left+index/bars.length*(width-left-right);
    };
    const y = (price) => bottom-(price-min)/(max-min)*(bottom-top);
    const contract = option ? stock.candidates.find((item) => String(item.id) === state.contract) : null;
    const title = option ? `${stock.symbol} ${contract?.label || "option"}` : stock.symbol;
    const svg = svgNode("svg", {viewBox: `0 0 ${width} ${height}`, tabindex: 0, class:"interactive-candles", role: "img", "aria-label": `${title} ${el("interval").selectedOptions[0].textContent} candles, volume, Wilder RSI(14) and RSI SMA(9). Drag to pan, Ctrl wheel to zoom, arrows to pan, 0 to reset.`});
    const clipId = option ? "option-price-pane" : "stock-price-pane";
    const clip = svgNode("clipPath", {id:clipId});
    clip.append(svgNode("rect", {x:left,y:top,width:width-left-right,height:bottom-top}));
    const defs = svgNode("defs", {});defs.append(clip);svg.append(defs);
    for (let i = 0; i <= 4; i++) {
      const value = min+(max-min)*i/4, yy = y(value);
      svg.append(svgNode("line", {x1: left, x2: width-right, y1: yy, y2: yy, stroke: "var(--chart-grid)"}));
      label(svg, width-right+5, yy+3, number(value));
    }
    const candleWidth = Math.max(1, Math.min(16, (width-left-right)/bars.length*.65));
    const maxVol = Math.max(...bars.map((b) => b.volume), 1);
    for (const b of bars) {
      const xx = x(b.time)+candleWidth/2, color = b.close >= b.open ? "var(--chart-up)" : "var(--chart-down)";
      const detail = `${stamp(b.time)} | O ${number(b.open)} H ${number(b.high)} L ${number(b.low)} C ${number(b.close)} | volume ${number(b.volume, 0)} | RSI(14) ${number(b.rsi)} | RSI SMA(9) ${number(b.rsi_ma)}`;
      const group = svgNode("g", {tabindex: 0, role: "img", "aria-label": detail});
      group.append(svgNode("title", {}, detail),
        svgNode("rect", {x: x(b.time), y: top, width: (width-left-right)/bars.length, height: 456, fill: "transparent", "pointer-events": "all"}),
        svgNode("line", {x1: xx, x2: xx, y1: y(b.high), y2: y(b.low), stroke: color}),
        svgNode("rect", {x: xx-candleWidth/2, y: Math.min(y(b.open), y(b.close)), width: candleWidth, height: Math.max(1, Math.abs(y(b.open)-y(b.close))), fill: color}),
        svgNode("rect", {x: xx-candleWidth/2, y: 335-b.volume/maxVol*45, width: candleWidth, height: b.volume/maxVol*45, fill: color, opacity: .5}));
      group.addEventListener("mouseenter", () => { detailTarget.textContent = detail; });
      group.addEventListener("focus", () => { detailTarget.textContent = detail; });
      group.addEventListener("click", () => { detailTarget.textContent = detail; });
      svg.append(group);
    }
    const levelLegend=node("div",undefined,"legend chart-levels");
    for (const [name, value, color] of overlays) {
      const item=node("span",`${name}: ${number(value)}${value<min || value>max ? " (outside view)" : ""}`);
      item.style.color=color;
      levelLegend.append(item);
      if (value < min || value > max) continue;
      const line=svgNode("line", {x1: left, x2: width-right, y1: y(value), y2: y(value), stroke: color, "stroke-dasharray": "4 4"});
      line.append(svgNode("title",{},`${name}: ${number(value)}`));
      svg.append(line);
    }
    if(overlays.length)target.append(levelLegend);
    const crosshair = svgNode("g", {"pointer-events":"none", visibility:"hidden"});
    const vertical = svgNode("line", {y1:top,y2:480,stroke:"var(--chart-muted)","stroke-dasharray":"3 3"});
    const horizontal = svgNode("line", {x1:left,x2:width-right,stroke:"var(--chart-muted)","stroke-dasharray":"3 3"});
    const crossLabel = svgNode("text", {x:left+5,y:top+12,fill:"var(--text)","font-size":11});
    crosshair.append(vertical,horizontal,crossLabel);
    let drag = null;
    const pointer = event => {
      const rect=svg.getBoundingClientRect();
      return {x:(event.clientX-rect.left)*width/rect.width,y:(event.clientY-rect.top)*height/rect.height};
    };
    svg.addEventListener("pointerdown", event => {
      if (event.button !== 0) return;
      draggingChart=true;
      const point=pointer(event); drag={x:point.x,moved:false};
      svg.setPointerCapture(event.pointerId);
    });
    svg.addEventListener("pointermove", event => {
      const point=pointer(event);
      if (drag && Math.abs(point.x-drag.x)>5) drag.moved=true;
      if (point.x<left || point.x>width-right) { crosshair.setAttribute("visibility","hidden"); return; }
      const bar=bars[Math.max(0,Math.min(bars.length-1,Math.floor((point.x-left)/(width-left-right)*bars.length)))];
      vertical.setAttribute("x1",point.x);vertical.setAttribute("x2",point.x);
      horizontal.setAttribute("y1",Math.max(top,Math.min(bottom,point.y)));horizontal.setAttribute("y2",Math.max(top,Math.min(bottom,point.y)));
      const price=max-(point.y-top)/(bottom-top)*(max-min);
      crossLabel.textContent=`${bar.time.slice(0,10)} ${clock(bar.time)} | ${number(price)}`;
      crosshair.setAttribute("visibility","visible");
      detailTarget.textContent=`${stamp(bar.time)} | O ${number(bar.open)} H ${number(bar.high)} L ${number(bar.low)} C ${number(bar.close)} | volume ${number(bar.volume,0)} | RSI ${number(bar.rsi)}`;
    });
    svg.addEventListener("pointerup", event => {
      draggingChart=false;
      if (!drag) return;
      const point=pointer(event), previous=drag; drag=null;
      if (previous.moved) move(Math.round((previous.x-point.x)/(width-left-right)*bars.length));
      else if (!option && pickingAlert && point.y>=top && point.y<=bottom && point.x>=left && point.x<=width-right) {
        el("alert-level").value=(max-(point.y-top)/(bottom-top)*(max-min)).toFixed(2);
        pickingAlert=false;el("pick-alert-level").setAttribute("aria-pressed","false");
        el("price-alert-status").textContent="Level selected. Choose direction and expiry, then Create private alert.";
      }
    });
    svg.addEventListener("pointercancel",()=>{drag=null;draggingChart=false;});
    svg.addEventListener("pointerleave",()=>crosshair.setAttribute("visibility","hidden"));
    svg.addEventListener("wheel", event=>{if(event.ctrlKey){event.preventDefault();zoom(event.deltaY<0?.75:1.35);}}, {passive:false});
    svg.addEventListener("dblclick",()=>{chartViews.delete(key);candleChart(stock,option);});
    svg.addEventListener("keydown",event=>{
      if (["+","=","-","ArrowLeft","ArrowRight","0"].includes(event.key)) event.preventDefault();
      if (event.key==="+" || event.key==="=") zoom(.75);
      if (event.key==="-") zoom(1.35);
      if (event.key==="ArrowLeft") move(-Math.max(1,Math.floor(bars.length/4)));
      if (event.key==="ArrowRight") move(Math.max(1,Math.floor(bars.length/4)));
      if (event.key==="0") { chartViews.delete(key);candleChart(stock,option); }
    });
    if (flags.has("vwap")) {
      let path = "", lastTime = null;
      for (const point of stock.vwap_points) if (point.value !== null && Date.parse(point.time) >= start && Date.parse(point.time) <= end) {
        path += `${lastTime !== null && Date.parse(point.time)-lastTime === 60000 ? "L" : "M"}${x(point.time)},${y(point.value)} `;
        lastTime = Date.parse(point.time);
      }
      svg.append(svgNode("path", {d: path, stroke: "var(--chart-gold)", fill: "none", "stroke-width": 1.5, "clip-path":`url(#${clipId})`}));
      label(svg, left+5, 15, "VWAP", "var(--chart-gold)");
    }
    if (flags.has("markers")) for (const item of stock.opportunities) for (const [when, value, text] of [[item.opened_at, item.entry_price, "ENTRY"], [item.closed_at, item.exit_price, "EXIT"]]) {
      // Premium fills belong on the premium chart; underlying markers use the observed candle.
      if (!when || value === null || Date.parse(when) < start || Date.parse(when) > end) continue;
      const bar = bars.find((b) => Date.parse(when) >= Date.parse(b.time) && Date.parse(when) < Date.parse(b.time)+intervalMinutes*60000);
      if (bar) { svg.append(svgNode("circle", {cx: x(when), cy: y(bar.close), r: 4, fill: "var(--text)"})); label(svg, x(when), y(bar.close)-8, `${item.strategy} ${text}`, "var(--text)"); }
    }
    label(svg, left+5, 285, "VOLUME (complete intervals)");
    label(svg, width-right+5, 300, number(maxVol, 0));
    label(svg, width-right+5, 335, "0");
    const rsiTop = 380, rsiBottom = 480;
    const ry = (value) => rsiBottom-value;
    svg.append(svgNode("rect", {x: left, y: ry(70), width: width-left-right, height: 40, fill: "var(--chart-rsi-band)"}));
    for (const value of [0, 30, 50, 70, 100]) {
      svg.append(svgNode("line", {x1: left, x2: width-right, y1: ry(value), y2: ry(value), stroke: "var(--chart-grid)", "stroke-dasharray": "4 4"}));
      label(svg, width-right+5, ry(value)+3, String(value));
    }
    const latest = bars[bars.length-1];
    label(svg, left+5, rsiTop-15, `RSI(14) ${number(latest.rsi)}`, "var(--chart-level)");
    label(svg, left+Math.min(220,(width-left-right)/2), rsiTop-15, `RSI SMA(9) ${number(latest.rsi_ma)}`, "var(--chart-gold)");
    for (const [key, color] of [["rsi", "var(--chart-level)"], ["rsi_ma", "var(--chart-gold)"]]) {
      let path = "", previous = null;
      for (const b of bars) {
        if (b[key] === null) { previous = null; continue; }
        path += `${previous !== null && Date.parse(b.time)-previous === intervalMinutes*60000 ? "L" : "M"}${x(b.time)+candleWidth/2},${ry(b[key])} `;
        previous = Date.parse(b.time);
        svg.append(svgNode("circle", {cx: x(b.time)+candleWidth/2, cy: ry(b[key]), r: 1.4, fill: color}));
      }
      svg.append(svgNode("path", {d: path, stroke: color, fill: "none", "stroke-width": 1.5}));
    }
    if (!state.interval.match(/^\d+$/)) label(svg,left+5,ry(50)-5,"RSI unavailable for recorded-session aggregates");
    else if (latest.rsi === null) label(svg, left+5, ry(50)-5, "RSI warming up: needs 15 consecutive complete candles");
    else if (latest.rsi_ma === null) label(svg, left+5, rsiBottom-4, "RSI SMA warming up: needs 9 valid RSI values");
    const timeline=svgNode("g", {class:"chart-timeline"});
    const tickCount=width-left-right<400?3:5;
    for (const index of [...new Set(Array.from({length:tickCount},(_,i)=>Math.floor((bars.length-1)*i/(tickCount-1))))]) {
      const dateLabel=new Date(bars[index].time).toLocaleDateString("en-GB",{timeZone:"Asia/Kolkata",day:"2-digit",month:"short"});
      const anchor=index>0 && index===bars.length-1?"end":"start";
      label(timeline,x(bars[index].time),503,clock(bars[index].time),"var(--text)",anchor);
      label(timeline,x(bars[index].time),523,dateLabel,"var(--text)",anchor);
    }
    svg.append(timeline);
    svg.append(crosshair);
    target.append(svg);
    if (hadFocus) svg.focus({preventScroll:true});
  }
  function premiumChart(points) {
    const target = el("premium-chart"); target.replaceChildren();
    if (points.length < 2) { target.append(node("p", "Premium history unavailable: collect at least two prospective samples. This does not fetch historical options.", "chart-empty")); return; }
    const svg = svgNode("svg", {viewBox: "0 0 520 190", role: "img", "aria-label": "Sampled option premium, bid and ask"});
    const values = points.flatMap((p) => [p.last, p.bid, p.ask].filter((v) => v > 0));
    const low = Math.min(...values), high = Math.max(...values), span = high-low || 1;
    const start = Date.parse(points[0].time), end = Date.parse(points[points.length-1].time);
    const x = (p) => 12+(Date.parse(p.time)-start)/(end-start)*440;
    const y = (value) => 150-(value-low)/span*120;
    for (const [key, color] of [["last", "var(--cyan)"], ["bid", "var(--chart-up)"], ["ask", "var(--chart-down)"]]) {
      let path = "", previous = null;
      for (const p of points) {
        if (p[key] <= 0) { previous = null; continue; }
        path += `${previous !== null && Date.parse(p.time)-previous <= 45000 ? "L" : "M"}${x(p)},${y(p[key])} `;
        previous = Date.parse(p.time);
        const dot = svgNode("circle", {cx: x(p), cy: y(p[key]), r: 1.8, fill: color});
        dot.append(svgNode("title", {}, `${clock(p.time)} ${key} ${number(p[key])}`)); svg.append(dot);
      }
      svg.append(svgNode("path", {d: path, stroke: color, fill: "none", "stroke-width": 1.3}));
    }
    label(svg, 12, 14, "LTP", "var(--cyan)"); label(svg, 52, 14, "BID", "var(--chart-up)"); label(svg, 92, 14, "ASK", "var(--chart-down)");
    label(svg, 460, 32, number(high)); label(svg, 460, 150, number(low));
    label(svg, 12, 178, clock(points[0].time)); label(svg, 400, 178, clock(points[points.length-1].time));
    target.append(svg);
  }
  function renderPriceAlerts() {
    const target=el("price-alert-list");target.replaceChildren();
    el("price-alert-form").querySelector('[type="submit"]').disabled=!payload.private_alerts_connected || !payload.selected;
    if (!payload.private_alerts_connected) el("price-alert-status").textContent="No private Telegram route connected for this user.";
    for (const alert of payload.price_alerts || []) {
      if (payload.selected && alert.instrument_id!==payload.selected.id) continue;
      const card=node("article");
      card.append(node("p",`#${alert.id} ${alert.symbol} cross ${alert.direction.toLowerCase()} ${number(alert.level)} | ${alert.status} | expires ${stamp(alert.expires_at)}`));
      if (alert.last_error) card.append(node("p",alert.last_error,"negative"));
      if (["WATCHING","TRIGGERED"].includes(alert.status)) {
        const cancel=node("button","Cancel alert");cancel.type="button";
        cancel.onclick=async()=>{cancel.disabled=true;try{const data=await request(root.dataset.priceAlert,{action:"cancel",id:alert.id});payload.price_alerts=payload.price_alerts.map(a=>a.id===alert.id?data.alert:a);renderPriceAlerts();stockChart(payload.selected);error("");}catch(err){cancel.disabled=false;error(err.message);}};
        card.append(cancel);
      }
      target.append(card);
    }
    if (!target.childNodes.length) target.append(node("p","No personal price alerts for this stock.","muted"));
  }
  function renderAssistant() {
    const assistant = payload.assistant;
    if (!assistant) return;
    const shortlist = assistant.shortlist;
    el("priority-lists").hidden=!shortlist.bullish.length && !shortlist.bearish.length;
    const healthy = assistant.health.healthy;
    const news=payload.health.public_news;
    el("assistant-status").textContent = `${shortlist.message} Evaluator ${assistant.health.workers["radar-evaluator"].state}; system ${healthy ? "healthy" : "needs attention"}. Telegram ${assistant.health.delivery_enabled ? "worker enabled" : "not enabled; in-app events only"}. Public BeatTheStreet ${news?.alive ? "collector online" : "collector offline/stale"} (text/captions only, not verified catalysts).`;
    el("assistant-status").className = healthy ? "muted" : "negative";
    for (const [side, target] of [["bullish", "bullish-shortlist"], ["bearish", "bearish-shortlist"]]) {
      el(target).replaceChildren();
      for (const item of shortlist[side]) {
        const card = node("article", undefined, "shortlist-card");
        const button = node("button", `${item.symbol} / ${item.score}/100 priority / ${item.state}`);
        button.type = "button";
        button.onclick = () => setState({symbol: item.symbol, view: "all", q: "", strategy: "", state: ""});
        card.append(button, node("p", item.why), node("p", `Confirm: ${item.confirmation}`, "muted"),
          node("p", `Invalidation: ${item.invalidation}`, "negative"),
          node("p", `Expires ${stamp(item.expires_at)} | source ${stamp(item.source_at)}`, "muted"));
        const details = node("details"); details.append(node("summary", "Priority components & missing sources"));
        for (const component of item.components) {
          details.append(node("p", `${component.name}: ${component.points} points. ${component.detail}`, "muted"));
          if (component.source_url) {
            const link = node("a", "Verified source"); link.href = component.source_url; link.rel = "noopener noreferrer"; link.target = "_blank"; details.append(link);
          }
        }
        card.append(details); el(target).append(card);
      }
      if (!shortlist[side].length) el(target).append(node("p", "No qualifying names. No minimum quota.", "empty"));
    }
    el("assistant-coverage").replaceChildren(node("p", shortlist.coverage || "Waiting for a fresh evaluator snapshot.", "muted"));
    if (shortlist.gaps) {
      el("assistant-coverage").append(node("p", `${shortlist.gaps.affected.length} stocks have missing/incomplete minutes. ${shortlist.gaps.conflicting_history_versions} conflicting history versions retained for audit.`, "muted"));
      for (const item of shortlist.gaps.affected.slice(0, 10)) el("assistant-coverage").append(node("p", `${item.symbol}: ${item.missing} missing / ${item.incomplete} incomplete.`, "muted"));
      el("assistant-coverage").append(node("p", shortlist.gaps.reconciliation, "muted"));
    }
    if (shortlist.excluded) el("assistant-coverage").append(node("p",`Excluded: ${Object.entries(shortlist.excluded).map(([reason,count])=>`${reason.replaceAll("_"," ")}: ${count}`).join(" | ")}`,"muted"));
    const triggerReport=assistant.trigger_report;
    el("trigger-report").replaceChildren();
    el("trigger-report").append(node("p", `${triggerReport.count} durable triggers: ${triggerReport.strategy_count} strategy confirmations and ${triggerReport.personal_price_alert_count} personal price crossings.`, "muted"),
      node("p", triggerReport.note, "muted"));
    for (const event of triggerReport.events) {
      const card=node("article", undefined, "shortlist-card");
      const title=node("button", `${stamp(event.at)} | ${event.symbol} | ${event.label} | ${event.state}`);
      title.type="button";
      title.onclick=()=>setState({symbol:event.symbol,view:"all",q:"",strategy:"",state:""});
      card.append(title,
        node("p", `Observed ${number(event.trigger)}${event.stop===null ? "" : ` | invalidation ${number(event.stop)}`}${event.valid_until ? ` | valid until ${stamp(event.valid_until)}` : ""}`, "muted"),
        node("p", event.reason, "muted"),
        node("p", event.delivery, event.delivery.startsWith("Delivery pending") ? "negative" : "muted"),
        node("p", `${event.paper_outcome_label}: ${event.paper_outcome===null ? "Unavailable" : `INR ${number(event.paper_outcome)}`}`, "muted"));
      el("trigger-report").append(card);
    }
    if (!triggerReport.events.length) el("trigger-report").append(node("p", "No recorded triggers yet. This stays empty until a confirmed strategy trigger or your observed price crossing is recorded.", "muted"));
    el("assistant-exceptions").replaceChildren();
    for (const event of assistant.exceptions) el("assistant-exceptions").append(node("p",
      `${stamp(event.created_at)} | ${event.symbol} | ${event.state} | ${event.sent_at ? "Delivered "+stamp(event.sent_at) : "In app / delivery pending"}${event.delivery_error ? " / "+event.delivery_error : ""}`));
    if (!assistant.exceptions.length) el("assistant-exceptions").append(node("p", "No lifecycle exceptions recorded. Silence is normal.", "muted"));
    const report = payload.research;
    el("assistant-research").replaceChildren(node("p", `Primary ${report.primary}: failed-break/reclaim. ${report.status}. Forward start ${stamp(report.starts_at)}. Not a proven profitable strategy.`));
    if (report.fingerprint) el("assistant-research").append(node("p", `Frozen code/config fingerprint ${report.fingerprint}`, "muted"));
    for (const group of report.groups) el("assistant-research").append(node("p",
      `${group.strategy}${group.primary ? " PRIMARY" : " comparison"}: ${group.observed} observed / ${group.triggered} triggered / ${group.entered} entered / ${group.closed} closed. Net INR ${number(group.net_pnl)}; expectancy ${number(group.expectancy)}; closed-equity drawdown ${number(group.max_drawdown)}. 30m underlying MFE ${number(group.mean_mfe_r)}R / MAE ${number(group.mean_mae_r)}R; same-time basket excess ${number(group.mean_excess_30_pct)}pp (${group.excess_count} observations).`, "muted"));
    if (report.limitations) el("assistant-research").append(node("p", report.limitations, "muted"));
    const momentum=assistant.momentum_evidence;
    if(momentum){
      el("assistant-research").append(node("h3",`${momentum.candidate} / ${momentum.status}`),
        node("p",momentum.rules),
        grid([
          ["All bullish proxy",`${momentum.all_bullish.trades} signals / ${number(momentum.all_bullish.expectancy_r)}R expectancy / PF ${number(momentum.all_bullish.profit_factor)} / DD ${number(momentum.all_bullish.max_drawdown_r)}R`],
          ["Held-out bullish proxy",`${momentum.held_out_bullish.trades} signals / ${number(momentum.held_out_bullish.expectancy_r)}R expectancy / PF ${number(momentum.held_out_bullish.profit_factor)} / DD ${number(momentum.held_out_bullish.max_drawdown_r)}R`],
        ]),node("p",momentum.data,"muted"),node("p",momentum.limitations,"negative"));
    }
  }
  function setStatus(id, text, tone) {
    el(id).textContent = text;
    const dot = el(`${id}-dot`);
    if (dot) dot.className = tone || "";
  }
  function renderCockpitStatus() {
    const nifty = payload.market?.NIFTY || {};
    const cas = payload.cas || {};
    const engine = payload.nifty_engine || {};
    const queue = payload.action_queue || {};
    setStatus("market-state", payload.session?.open ? "Market open" : "Market closed", payload.session?.open ? "good" : "warn");
    setStatus("dhan-state", nifty.fresh ? "Dhan streaming" : "Dhan awaiting live tick", nifty.fresh ? "good" : "bad");
    setStatus("cas-state", cas.healthy ? "CAS healthy" : cas.enabled ? "CAS incomplete" : "CAS unavailable", cas.healthy ? "good" : cas.enabled ? "warn" : "bad");
    el("health-regime").textContent = nifty.regime || "Unavailable";
    el("health-regime").className = nifty.fresh ? "positive" : "stale";
    el("health-regime-detail").textContent = nifty.fresh
      ? `${number(nifty.last)} · ${number(nifty.change_pct)}%`
      : `Stored ${stamp(nifty.source_at)} · not used for entry`;
    el("health-oi").textContent = nifty.pcr === null || nifty.pcr === undefined ? "Unavailable" : `PCR ${number(nifty.pcr)}`;
    el("health-oi-detail").textContent = `Support ${number(nifty.support)} · resistance ${number(nifty.resistance)}`;
    el("health-cas").textContent = cas.healthy ? "Context ready" : cas.enabled ? "Incomplete" : "Offline";
    el("health-cas").className = cas.healthy ? "positive" : "negative";
    el("health-cas-detail").textContent = cas.healthy
      ? `${cas.event_count} events · ${cas.nowcast_count} nowcasts · context only`
      : "Excluded from every decision";
    el("health-freshness").textContent = `${payload.fresh_count}/${payload.universe}`;
    el("health-freshness").className = payload.fresh_count === payload.universe ? "positive" : "warning";
    el("health-freshness-detail").textContent = payload.session?.review ? "Recorded-session review" : "Equity quotes current";
    const total = (queue.rows || []).length;
    el("health-exceptions").textContent = String(total);
    el("health-exceptions-detail").textContent = `${queue.buy_count || 0} buy · ${queue.manage_count || 0} manage · ${queue.triggered_count || 0} triggered`;
    el("health-risk").textContent = engine.position ? `${number(engine.realized_r)}R` : "0 open";
    el("health-risk").className = engine.stale ? "negative" : "positive";
    el("health-risk-detail").textContent = engine.stale
      ? `Engine ${engine.state || "offline"} · heartbeat stale`
      : `${engine.trades_today || 0}/${engine.max_trades_per_day || 0} trades · INR ${number(engine.net_pnl_today)}`;
  }
  let queueActionFilter = "ALL";
  let queueSourceFilter = "all";
  let queueSearch = "";
  function selectException(card) {
    const underlying = card.dataset.underlying;
    const detailUrl = card.dataset.detailUrl;
    if (underlying && (payload.all_rows || []).some(row => row.symbol === underlying)) {
      setState({symbol: underlying, view: "all", q: "", strategy: "", state: ""});
      el("selected-stock").scrollIntoView({block: "start", behavior: "smooth"});
      return;
    }
    if (detailUrl) window.location.assign(detailUrl);
  }
  function renderExceptionQueue() {
    const container = el("exception-queue");
    const rows = payload.action_queue?.rows || [];
    const focusedKey = container.contains(document.activeElement)
      ? document.activeElement.dataset.queueKey
      : "";
    let restoredFocus = null;
    container.replaceChildren();
    let visible = 0;
    for (const row of rows) {
      const actionMatches = queueActionFilter === "ALL"
        || row.action === queueActionFilter
        || (queueActionFilter === "REVIEW" && ["WATCH", "REVIEW"].includes(row.action));
      const sourceMatches = queueSourceFilter === "all" || row.source_key === queueSourceFilter;
      const haystack = `${row.symbol} ${row.source} ${row.reason || ""}`.toLocaleLowerCase();
      if (!actionMatches || !sourceMatches || (queueSearch && !haystack.includes(queueSearch))) continue;
      const card = node("article", undefined, "exception-card");
      card.tabIndex = 0;
      card.role = "option";
      card.dataset.actionRow = "";
      card.dataset.action = row.action;
      card.dataset.source = row.source_key;
      card.dataset.underlying = row.underlying || "";
      card.dataset.detailUrl = row.detail_url || "";
      card.dataset.queueKey = [row.source_key, row.symbol, row.timestamp, row.detail_url].join("|");
      if (focusedKey && card.dataset.queueKey === focusedKey) restoredFocus = card;
      card.setAttribute("aria-selected", String(Boolean(row.underlying && row.underlying === state.symbol)));
      const top = node("div", undefined, "exception-top");
      top.append(node("strong", row.symbol), node("span", row.action, `decision decision-${row.action.toLocaleLowerCase()}`));
      const main = node("div", undefined, "exception-main");
      main.append(node("b", row.recommendation), node("span", `Score ${row.score}`, "muted"));
      card.append(
        top,
        main,
        node("p", row.reason || row.action_reason),
        node("small", row.action_reason),
        node("small", `${row.source} · ${stamp(row.timestamp)}`),
      );
      card.addEventListener("click", () => selectException(card));
      card.addEventListener("keydown", event => {
        const cards = [...container.querySelectorAll(".exception-card")];
        const index = cards.indexOf(card);
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          cards[(index + (event.key === "ArrowDown" ? 1 : -1) + cards.length) % cards.length]?.focus();
        } else if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectException(card);
        }
      });
      container.append(card);
      visible += 1;
    }
    if (!rows.length) {
      const message = node("div", undefined, "no-trade");
      message.append(node("strong", "No suitable trade right now."), node("p", "No evidence-backed exception requires action."));
      container.append(message);
    }
    el("action-filter-empty").hidden = visible > 0 || rows.length === 0;
    restoredFocus?.focus({preventScroll: true});
  }
  function renderEligibility(stock) {
    const gates = el("eligibility-gates");
    gates.replaceChildren();
    const opportunity = stock?.opportunities?.find(item => ["ELIGIBLE", "TRIGGERED", "ARMED", "TAKEN"].includes(item.state))
      || stock?.opportunities?.[0];
    const candidate = stock?.candidates?.find(item => String(item.id) === state.contract);
    const gateRows = [
      ["Market mode", payload.session?.open ? "PASS" : "REVIEW", payload.session?.open ? "pass" : "wait"],
      ["Underlying quote", stock?.quote?.fresh ? "PASS" : "STALE", stock?.quote?.fresh ? "pass" : "fail"],
      ["Strategy trigger", opportunity ? opportunity.display_state : "WAIT", opportunity ? "wait" : "fail"],
      ["Option quote", candidate && !candidate.rejection ? "PASS" : candidate?.rejection || "WAIT", candidate && !candidate.rejection ? "pass" : "fail"],
      ["Risk budget", opportunity?.can_acknowledge ? "PASS" : "LOCKED", opportunity?.can_acknowledge ? "pass" : "wait"],
    ];
    for (const [label, value, tone] of gateRows) {
      const row = node("div", undefined, `gate gate-${tone}`);
      row.append(node("span", label), node("strong", value));
      gates.append(row);
    }
    const action = el("primary-decision-action");
    if (opportunity?.can_acknowledge) {
      action.textContent = "Open paper ticket";
      action.disabled = false;
      action.onclick = () => el("strategies").scrollIntoView({block: "center", behavior: "smooth"});
    } else if (stock?.quote?.fresh) {
      action.textContent = "Create paper alert";
      action.disabled = false;
      action.onclick = () => el("price-alert-desk").scrollIntoView({block: "center", behavior: "smooth"});
    } else {
      action.textContent = "No action — wait for fresh evidence";
      action.disabled = true;
      action.onclick = null;
    }
    const decision = stock?.decision;
    el("setup-why").textContent = decision?.setup_evidence || decision?.reason || "No selected evidence.";
    el("setup-confirmation").textContent = opportunity
      ? `${opportunity.strategy} ${opportunity.side} at ${number(opportunity.trigger)}`
      : "A recorded strategy trigger is required.";
    el("setup-invalidation").textContent = opportunity
      ? `${number(opportunity.stop)} · expires ${stamp(opportunity.valid_until)}`
      : "No active setup or expiry.";
  }
  function render() {
    document.documentElement.classList.add("js-ready");
    el("market").textContent = Object.entries(payload.market).map(([name, value]) => `${name} ${number(value.last)} (${value.fresh ? "fresh" : "stale"})`).join(" | ");
    el("updated").textContent = "As of " + stamp(payload.as_of);
    const health = payload.health;
    el("session-status").replaceChildren(node("h2",payload.session.label),
      node("p",payload.session.review ? "Preparation mode: last recorded continuous-session metrics. Quotes and entry eligibility are not made fresh by this review." :
        "Live-data mode: fresh quotes, a recorded confirmation and option/risk gates are required. No suitable trade is a normal outcome.","muted"));
    el("health").replaceChildren(node("span", `Feed: ${health.feed}`, health.feed.includes("OFFLINE") ? "stale" : ""),
      node("span", `Fresh equities: ${payload.fresh_count}/${payload.universe}`),
      node("span", `Alerts: ${health.delivery} | pending ${health.pending_alerts}`),
      node("span", `History: ${health.history.state} / ${health.history.detail}`, "muted"),
      node("span", health.feed_detail, "muted"));
    for (const item of health.errors) el("health").append(node("span", `Alert ${item.id}: ${item.last_error}`, "negative"));
    for (const button of document.querySelectorAll("[data-view]")) { button.classList.toggle("active", button.dataset.view === state.view); button.setAttribute("aria-pressed", String(button.dataset.view === state.view)); }
    for (const id of ["strategy", "state", "sort", "interval", "period"]) el(id).value = state[id];
    el("chart-date").max=new Date(payload.as_of).toLocaleDateString("en-CA",{timeZone:"Asia/Kolkata",year:"numeric",month:"2-digit",day:"2-digit"});
    if(document.activeElement!==el("chart-date"))el("chart-date").value=state.chart_date;
    if (document.activeElement !== el("search")) el("search").value = state.q;
    for (const input of document.querySelectorAll("[data-overlay]")) input.checked = state.overlays.split(",").includes(input.dataset.overlay);
    renderCockpitStatus(); renderExceptionQueue(); renderAssistant(); renderPriceAlerts(); renderTable(); renderPortfolio();
    const stock = payload.selected;
    el("selected-watch").disabled=!stock;
    el("pick-alert-level").disabled=!stock || !stock.chart.length;
    el("notes-form").querySelector("button").disabled = !stock;
    if (!stock) {
      for (const id of ["stock-heading", "decision-desk", "stock-chart", "option-chart", "option-heading", "indicator-definition", "metrics", "definitions", "news", "strategies", "contracts", "option-detail", "premium-chart", "positions"]) empty(id, "No selected F&O stock. Sync the registry or change filters.");
      el("contract").replaceChildren();
      el("chart-range").textContent="No chart selected. Clear filters or choose a stock from All F&O.";
      renderEligibility(null);
      return;
    }
    const watched=(payload.all_rows || payload.rows).find(row=>row.id===stock.id)?.watched;
    el("selected-watch").textContent=watched?"Remove selected stock from watchlist":"Add selected stock to watchlist";
    el("selected-watch").setAttribute("aria-pressed",String(!!watched));
    el("chart-range").textContent=stock.chart_range ? `Requested ${stock.chart_range.from} to ${stock.chart_range.to}. Recorded ${stock.chart_range.first_available ? stamp(stock.chart_range.first_available) : "unavailable"} to ${stock.chart_range.last_available ? stamp(stock.chart_range.last_available) : "unavailable"}. A longer selection cannot add history that has not been recorded; ranges may contain the same candles when no older data exists.` : "";
    el("chart-limitations").textContent=stock.chart_range?.note || "";
    const display=stock.metrics.last;
    el("stock-heading").replaceChildren(node("h1", `${stock.symbol} / ${stock.name}`),
      node("div",payload.session.review ? `${display.label} INR ${number(display.value)} | ${stamp(display.at)} | REVIEW ONLY` :
        `INR ${number(stock.quote.last)} | ${stock.quote.fresh ? "FRESH" : "STALE / MISSING"} | source ${stamp(stock.quote.source_at)} | received ${stamp(stock.quote.received_at)}`,payload.session.review || stock.quote.fresh ? "muted":"stale"));
    el("decision-desk").replaceChildren(node("h2",stock.decision.status),node("p",stock.decision.reason),
      node("p",stock.decision.next_action),node("p",stock.decision.coverage,"muted"),node("p",stock.decision.setup_evidence,"muted"));
    const recorded=stock.strategies.find(item=>item.code==="S1" && item.opportunity)?.opportunity ||
      stock.strategies.find(item=>item.opportunity)?.opportunity;
    if(recorded){
      el("decision-desk").append(node("p",`${recorded.strategy} ${recorded.side} / ${recorded.display_state}: ${recorded.reason}`),
        grid([["Recorded confirmation level",number(recorded.trigger)],["Recorded invalidation level",number(recorded.stop)],
          ["Idea expires",stamp(recorded.valid_until)],["Current action blocker",recorded.action_block || "Inspect strategy and risk checks below; no broker execution"]]));
    }
    renderEligibility(stock);
    if (selectedId !== stock.id || !dirtyNotes) {
      el("notes").value = stock.notes; el("watched-level").value = stock.watched_level ?? "";
      if (selectedId !== stock.id) { dirtyNotes = false; el("notes-status").textContent = ""; }
    }
    selectedId = stock.id;
    renderMetrics(stock); renderNews(stock); renderStrategies(stock); renderOptions(stock); stockChart(stock);
    const contract = stock.candidates.find((item) => String(item.id) === state.contract);
    el("option-heading").replaceChildren(node("p", contract ? `${stock.symbol} ${contract.label} | LTP INR ${number(contract.quote.last)} | ${contract.quote.fresh ? "FRESH" : "STALE / MISSING"} | ${state.interval}-minute candles | source ${stamp(contract.quote.source_at)}` : "No selected option contract.", contract?.quote.fresh ? "muted" : "stale"));
    el("indicator-definition").textContent = stock.indicator_definition;
  }
  el("refresh").onclick = () => refresh();
  for (const button of document.querySelectorAll("[data-action-filter]")) button.onclick = () => {
    queueActionFilter = button.dataset.actionFilter;
    for (const item of document.querySelectorAll("[data-action-filter]")) item.classList.toggle("active", item === button);
    renderExceptionQueue();
  };
  for (const button of document.querySelectorAll("[data-source-filter]")) button.onclick = () => {
    queueSourceFilter = button.dataset.sourceFilter;
    for (const item of document.querySelectorAll("[data-source-filter]")) item.classList.toggle("active", item === button);
    renderExceptionQueue();
  };
  el("action-search").oninput = () => {
    queueSearch = el("action-search").value.trim().toLocaleLowerCase();
    renderExceptionQueue();
  };
  el("clear-filters").onclick=()=>setState({view:"all",q:"",strategy:"",state:"",sort:"symbol"});
  el("selected-watch").onclick=()=>{
    if(!payload.selected)return;
    const row=(payload.all_rows || payload.rows).find(row=>row.id===payload.selected.id);
    watch(payload.selected.id,row?.watched?"remove":"add");
  };
  el("chart-focus").onclick=()=>{
    const active=root.classList.toggle("chart-focus");el("chart-focus").setAttribute("aria-pressed",String(active));
    el("chart-focus").textContent=active?"Restore workspace":"Expand charts";stockChart(payload.selected);
  };
  el("pick-alert-level").onclick=()=>{
    pickingAlert=!pickingAlert;el("pick-alert-level").setAttribute("aria-pressed",String(pickingAlert));
    el("price-alert-status").textContent=pickingAlert?"Click a price in the stock chart to select a level.":"Chart selection cancelled.";
    if(pickingAlert)el("stock-chart").scrollIntoView({block:"center",behavior:"smooth"});
  };
  el("price-alert-form").onsubmit=async event=>{
    event.preventDefault();if(!payload.selected)return;
    const button=event.currentTarget.querySelector('[type="submit"]');button.disabled=true;
    let saved=false;
    try {
      const data=await request(root.dataset.priceAlert,{action:"create",instrument_id:payload.selected.id,direction:el("alert-direction").value,level:Number(el("alert-level").value),minutes:Number(el("alert-minutes").value)});
      saved=true;
      payload.price_alerts.unshift(data.alert);renderPriceAlerts();stockChart(payload.selected);
      el("price-alert-status").textContent="Private one-shot alert saved. No strategy or order was changed.";error("");
    } catch(err){error(`${saved ? "Price alert saved, but the view could not update" : "Price alert save could not be confirmed"}: ${err.message}`);}finally{button.disabled=!payload.private_alerts_connected || !payload.selected;}
  };
  for (const button of document.querySelectorAll("[data-view]")) button.onclick = () => setState({view: button.dataset.view,q:"",strategy:"",state:""});
  for (const id of ["strategy", "state", "sort", "interval", "contract", "period"]) el(id).onchange = () => {
    const changes={[id]:el(id).value};
    if(id==="interval" && ["D","W","M"].includes(changes.interval) && state.period==="1D")changes.period="1M";
    setState(changes);
  };
  el("chart-date").onchange=()=>{
    if(!el("chart-date").checkValidity()){el("chart-date").value=state.chart_date;error("Choose a chart date no later than today.");return;}
    setState({chart_date:el("chart-date").value});
  };
  el("latest-chart").onclick=()=>setState({chart_date:"",period:"1D"});
  let searchTimer;
  el("search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => setState({q: el("search").value}), 350); };
  for (const input of document.querySelectorAll("[data-overlay]")) input.onchange = () => {
    state.overlays = [...document.querySelectorAll("[data-overlay]:checked")].map((item) => item.dataset.overlay).join(",");
    stockChart(payload.selected); persist();
  };
  for (const id of ["notes", "watched-level"]) el(id).oninput = () => { dirtyNotes = true; el("notes-status").textContent = "Unsaved"; };
  el("notes-form").onsubmit = (event) => {
    event.preventDefault();
    if (!payload.selected) return;
    const raw = el("watched-level").value;
    watch(payload.selected.id, "save", {notes: el("notes").value, watched_level: raw === "" ? null : Number(raw)});
  };
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !busy) refresh(); });
  window.addEventListener("arc:market-ticker", event => {
    const data = event.detail;
    setStatus("dhan-state", data?.ok && !data.stale ? `Dhan live · ${number(data.price)}` : "Dhan stale / unavailable", data?.ok && !data.stale ? "good" : "bad");
  });
  let savedDrawers = {};
  try {
    savedDrawers = JSON.parse(localStorage.getItem("arc-workspace-drawers") || "{}");
  } catch (error) {
    localStorage.removeItem("arc-workspace-drawers");
  }
  for (const [index, detail] of [...document.querySelectorAll(".radar-workspace-shell details")].entries()) {
    const key = detail.id || `drawer-${index}`;
    if (Object.hasOwn(savedDrawers, key)) detail.open = Boolean(savedDrawers[key]);
    detail.addEventListener("toggle", () => {
      savedDrawers[key] = detail.open;
      localStorage.setItem("arc-workspace-drawers", JSON.stringify(savedDrawers));
    });
  }
  window.addEventListener("beforeunload", (event) => { if (dirtyNotes) { event.preventDefault(); event.returnValue = ""; } });
  render();
  let chartWidth=0, resizeTimer;
  const chartResize=new ResizeObserver(entries=>{
    const width=Math.round(entries[0].contentRect.width);
    if(width===chartWidth)return;
    chartWidth=width;
    clearTimeout(resizeTimer);
    const redraw=()=>{
      if(draggingChart){resizeTimer=setTimeout(redraw,100);return;}
      stockChart(payload.selected);
    };
    resizeTimer=setTimeout(redraw,100);
  });
  chartResize.observe(el("stock-chart"));
  setInterval(() => {
    const editing=root.contains(document.activeElement) && document.activeElement.matches("input,textarea,select");
    if (!document.hidden && !busy && !pickingAlert && !draggingChart && !editing) refresh();
  }, 10000);
})();
