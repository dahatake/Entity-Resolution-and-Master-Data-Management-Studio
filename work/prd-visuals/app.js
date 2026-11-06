/* ER/MDM Studio — PRD データ可視化(参照プロトタイプ)。d3.js v7。データは data.js(build_prd_visuals.py が生成)。 */
(function () {
  "use strict";
  const D = window.PRD_DATA;
  const C = { ink: "#e7ecf7", mute: "#8a96b2", cyan: "#5ad1ff", red: "#ff5c7a", amber: "#ffc857", green: "#4ade80", violet: "#a78bfa", grid: "#1a2340" };
  const f3 = d3.format(".3f"), f2 = d3.format(".2f"), pct = d3.format(".1%"), n0 = d3.format(",");
  const state = { t: 0.01, mode: "cluster", c: 5 };
  const bus = d3.dispatch("t", "mode", "c");

  // ---------- 共通ヘルパー ----------
  const tip = d3.select("body").append("div").attr("class", "tip");
  const showTip = (ev, html) => { tip.html(html).style("opacity", 1); moveTip(ev); };
  const moveTip = (ev) => tip.style("left", Math.min(ev.clientX + 14, innerWidth - 320) + "px").style("top", Math.min(ev.clientY + 14, innerHeight - 120) + "px");
  const hideTip = () => tip.style("opacity", 0);
  const tipOn = (sel, fn) => sel.on("mousemove", (ev, d) => showTip(ev, fn(d))).on("mouseleave", hideTip);

  const navItems = [];
  function section(id, kicker, title, lead) {
    const s = d3.select("#main").append("section").attr("id", id).attr("class", "sec");
    s.append("div").attr("class", "kicker").text(kicker);
    s.append("h2").text(title);
    if (lead) s.append("p").attr("class", "lead").html(lead);
    navItems.push([id, title.split(/[:(（]/)[0]]);
    return s;
  }
  function figure(parent, o) {
    const f = parent.append("figure").attr("class", "fig").style("margin", "18px 0");
    if (o.title) f.append("h3").text(o.title);
    const ctrl = f.append("div").attr("class", "ctrl");
    const body = f.append("div").attr("class", "body");
    const cap = f.append("figcaption");
    cap.html(`<span class="b src">出所: ${o.source}</span><span class="b conf">確度: ${o.conf}</span>` + (o.n ? `<span class="b n">標本: ${o.n}</span>` : "") + (o.note ? `<div class="note">${o.note}</div>` : ""));
    return { fig: f, ctrl, body };
  }
  function svgIn(body, W, H, label) {
    return body.append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", label);
  }
  function tableToggle(fig, rows, name) {
    if (!rows || !rows.length) return;
    const det = fig.append("details").attr("class", "tbl");
    det.append("summary").text("表・CSVで見る(NFR-12)");
    const keys = Object.keys(rows[0]);
    const tb = det.append("table");
    tb.append("tr").selectAll("th").data(keys).join("th").text((d) => d);
    tb.selectAll("tr.r").data(rows).join("tr").attr("class", "r").selectAll("td").data((r) => keys.map((k) => r[k])).join("td").text((v) => (typeof v === "number" ? (Number.isInteger(v) ? v : f3(v)) : v));
    det.append("button").attr("class", "btn").text("CSVをダウンロード").on("click", () => {
      const csv = [keys.join(",")].concat(rows.map((r) => keys.map((k) => JSON.stringify(r[k] ?? "")).join(","))).join("\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv" }));
      a.download = name + ".csv"; a.click();
    });
  }
  const axisStyle = (g) => g.attr("class", "axis");
  function seg(ctrl, opts, cur, cb) {
    const btns = ctrl.selectAll("button").data(opts).join("button").attr("class", (d) => (d[0] === cur ? "on" : "")).text((d) => d[1]);
    btns.on("click", function (ev, d) { btns.attr("class", (x) => (x[0] === d[0] ? "on" : "")); cb(d[0]); });
  }

  // ---------- ヒーロー: クラスタ・グラフ (K4) ----------
  function heroGraph() {
    const host = d3.select("#hero-fig");
    const { fig, ctrl, body } = figure(host, {
      source: "PoC/results/poc02_predictions.csv + PoC/data/fake_1000.csv", conf: "✅(合成デモデータ)",
      n: `${D.graph.nodes.length}ノード / ${D.graph.links.length}リンク(閾値0.01で不純な28クラスタ)`,
      note: "<b>読み方</b>: 図は「閾値0.01で別人が混ざる28クラスタ」だけを取り出した部分グラフです。リンクは p≥0.001 の候補ペアのみ(ブロッキングで拾えなかったペアは存在しません)。" +
        "ノードの色は真の同一人物(正解ラベル)。リンクの緑=真の同一、赤=誤リンク。点線=現在の閾値で切断されたリンク。",
    });
    const W = 1200, H = 640;
    const svg = svgIn(body, W, H, "クラスタ・グラフ。閾値を変えると別人混在クラスタが分裂する");
    const defs = svg.append("defs");
    const glow = defs.append("filter").attr("id", "glow").attr("x", "-50%").attr("y", "-50%").attr("width", "200%").attr("height", "200%");
    glow.append("feGaussianBlur").attr("stdDeviation", 3).attr("result", "b");
    glow.append("feMerge").selectAll("feMergeNode").data(["b", "SourceGraphic"]).join("feMergeNode").attr("in", (d) => d);
    const gHull = svg.append("g"), gLink = svg.append("g"), gNode = svg.append("g");

    const nodes = D.graph.nodes.map((d) => ({ ...d }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const links = D.graph.links.map((l) => ({ source: byId.get(l.s), target: byId.get(l.t), p: l.p, true: l.true }));
    const hue = (tc) => (tc * 137.508) % 360;
    nodes.forEach((n) => (n.color = `hsl(${hue(n.tc)},78%,62%)`));

    let comps = [], compOf = new Map();
    // 各クラスタ(閾値で連結した成分)に「アンカー」を割り当てる。アンカーは d3.packSiblings で配置し、ノードをアンカーへ引き寄せる
    function recompute() {
      const parent = new Map(nodes.map((n) => [n.id, n.id]));
      const find = (x) => { while (parent.get(x) !== x) { parent.set(x, parent.get(parent.get(x))); x = parent.get(x); } return x; };
      links.forEach((l) => { if (l.p >= state.t) { const a = find(l.source.id), b = find(l.target.id); if (a !== b) parent.set(a, b); } });
      const groups = Array.from(d3.group(nodes, (n) => find(n.id)).values()).map((m) => ({ nodes: m, impure: new Set(m.map((n) => n.tc)).size > 1 }));
      comps = groups.filter((c) => c.nodes.length > 1);
      compOf = new Map();
      comps.forEach((c) => c.nodes.forEach((n) => compOf.set(n.id, c)));
      const items = groups.slice().sort((a, b) => b.nodes.length - a.nodes.length).map((g) => ({ g, r: 10.5 * Math.sqrt(g.nodes.length) + 8 }));
      d3.packSiblings(items);
      const x0 = d3.min(items, (d) => d.x - d.r), x1 = d3.max(items, (d) => d.x + d.r), y0 = d3.min(items, (d) => d.y - d.r), y1 = d3.max(items, (d) => d.y + d.r);
      const k = Math.min(1, (W - 80) / (x1 - x0), (H - 70) / (y1 - y0)), cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
      items.forEach((it) => it.g.nodes.forEach((n) => { n.ax = W / 2 + (it.x - cx) * k; n.ay = H / 2 + (it.y - cy) * k; }));
    }
    const link = gLink.selectAll("line").data(links).join("line");
    const node = gNode.selectAll("circle").data(nodes).join("circle").attr("r", 6.5).attr("fill", (d) => d.color).attr("stroke", "#0b0f1a").attr("stroke-width", 1.2)
      .style("cursor", "grab").call(d3.drag().on("start", (ev, d) => { if (!ev.active) sim.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; }).on("end", (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
    tipOn(node, (d) => { const c = compOf.get(d.id); return `<b>${d.name}</b><br>生年月日 ${d.dob}<br>市 ${d.city}<br>${d.email}<br>真の同一人物ID: ${d.tc}<br>現在のクラスタの大きさ: ${c ? c.nodes.length : 1}${c && c.impure ? "<br><b style='color:#ff5c7a'>別人が混在</b>" : ""}`; });
    node.on("mouseenter.hl", (ev, d) => { link.attr("opacity", (l) => (l.source === d || l.target === d ? 1 : 0.05)); })
      .on("mouseleave.hl", () => styleLinks());

    const stats = ctrl.append("div").attr("class", "stats").style("width", "100%");
    const mk = (cls, label) => { const s = stats.append("div").attr("class", "stat " + cls); s.append("div").attr("class", "v"); s.append("div").attr("class", "l").text(label); return s.select(".v"); };
    const vImp = mk("bad", "別人が混ざったクラスタ(閾値0.01では28)"), vFalse = mk("warn", "有効な誤リンク"), vCut = mk("warn", "切断された真のリンク"), vT = mk("good", "閾値 t");
    const slider = ctrl.append("input").attr("type", "range").attr("min", 0).attr("max", 1000).attr("aria-label", "閾値");
    const lo = Math.log10(0.001), hi = Math.log10(0.99);
    const tOf = (v) => Math.pow(10, lo + (v / 1000) * (hi - lo)), vOf = (t) => ((Math.log10(t) - lo) / (hi - lo)) * 1000;
    slider.property("value", vOf(state.t));
    const presets = [0.001, 0.01, 0.05, 0.2, 0.5, 0.9];
    ctrl.selectAll("button.p").data(presets).join("button").attr("class", "p").text((d) => "t=" + d).on("click", (ev, d) => setT(d));
    const play = ctrl.append("button").text("▶ 自動で閾値を上げる");
    let timer = null;
    play.on("click", () => {
      if (timer) { timer.stop(); timer = null; play.text("▶ 自動で閾値を上げる"); return; }
      setT(0.001); play.text("■ 停止");
      let v = 0; const end = vOf(0.6);
      timer = d3.interval(() => { v += 5; if (v >= end) { timer.stop(); timer = null; play.text("▶ 自動で閾値を上げる"); } setT(tOf(Math.min(v, end))); }, 45);
    });
    slider.on("input", function () { setT(tOf(+this.value), true); });
    function setT(t, fromSlider) { state.t = t; if (!fromSlider) slider.property("value", vOf(t)); bus.call("t", null, t); update(true); }

    function styleLinks() {
      link.attr("stroke", (l) => (l.p >= state.t ? (l.true ? C.green : C.red) : "#6b7599"))
        .attr("stroke-width", (l) => (l.p >= state.t ? 1 + 2.2 * l.p : 0.9))
        .attr("stroke-dasharray", (l) => (l.p >= state.t ? null : "2 5"))
        .attr("opacity", (l) => (l.p >= state.t ? 0.35 + 0.6 * l.p : 0.28));
    }
    const sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(links).distance(26).strength((l) => (l.p >= state.t ? 0.35 : 0)))
      .force("charge", d3.forceManyBody().strength(-26).distanceMax(70))
      .force("x", d3.forceX((d) => d.ax).strength(0.16)).force("y", d3.forceY((d) => d.ay).strength(0.16)).force("collide", d3.forceCollide(9.5));
    recompute();
    nodes.forEach((n) => { n.x = n.ax + (Math.random() - 0.5) * 30; n.y = n.ay + (Math.random() - 0.5) * 30; });
    function update(reheat) {
      recompute();
      styleLinks();
      node.attr("stroke", (d) => { const c = compOf.get(d.id); return c && c.impure ? C.red : "#0b0f1a"; }).attr("stroke-width", (d) => { const c = compOf.get(d.id); return c && c.impure ? 2.4 : 1.2; })
        .attr("filter", (d) => { const c = compOf.get(d.id); return c && c.impure ? "url(#glow)" : null; });
      vImp.text(comps.filter((c) => c.impure).length);
      vFalse.text(links.filter((l) => l.p >= state.t && !l.true).length);
      vCut.text(links.filter((l) => l.p < state.t && l.true).length);
      vT.text(state.t < 0.01 ? state.t.toFixed(4) : state.t.toFixed(3));
      sim.force("link").strength((l) => (l.p >= state.t ? 0.35 : 0));
      sim.force("x").x((d) => d.ax); sim.force("y").y((d) => d.ay);
      if (reheat) sim.alpha(0.7).restart();
      draw();
    }
    function draw() {
      link.attr("x1", (l) => l.source.x).attr("y1", (l) => l.source.y).attr("x2", (l) => l.target.x).attr("y2", (l) => l.target.y);
      node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
      const hull = gHull.selectAll("path").data(comps, (c) => c.nodes[0].id + ":" + c.nodes.length);
      hull.exit().remove();
      hull.enter().append("path").merge(hull).each(function (c) {
        const pts = c.nodes.map((n) => [n.x, n.y]);
        let dpath;
        if (pts.length >= 3) { const h = d3.polygonHull(pts); dpath = h ? "M" + h.map((p) => p.join(",")).join("L") + "Z" : null; }
        if (!dpath) dpath = "M" + pts.map((p) => p.join(",")).join("L");
        const col = c.impure ? C.red : C.cyan;
        d3.select(this).attr("d", dpath).attr("fill", col).attr("fill-opacity", c.impure ? 0.16 : 0.07).attr("stroke", col).attr("stroke-opacity", c.impure ? 0.5 : 0.2)
          .attr("stroke-width", 12).attr("stroke-linejoin", "round").attr("stroke-linecap", "round");
      });
    }
    sim.on("tick", draw);
    update(false);
    const lg = svg.append("g").attr("transform", "translate(18,20)");
    [[C.green, "真の同一(リンク)"], [C.red, "誤リンク / 別人混在の囲み"], ["#6b7599", "切断されたリンク(点線)"]].forEach(([c, t], i) => {
      lg.append("circle").attr("cx", 6).attr("cy", i * 19).attr("r", 5).attr("fill", c);
      lg.append("text").attr("x", 18).attr("y", i * 19 + 4).attr("class", "lbl").text(t);
    });
    tableToggle(fig, D.graph.links.slice(0, 400).map((l) => ({ id_a: l.s, id_b: l.t, p: l.p, 真の同一: l.true })), "poc02_graph_links(先頭400件)");
  }

  // ---------- 閾値とコスト (K3/K4/K5) ----------
  function thresholdSection() {
    const s = section("threshold", "K3 ・ K4 ・ K5", "閾値は、F1ではなく「コスト」で決める",
      "同じスコアでも、<strong>ペア単位</strong>と<strong>クラスタ単位</strong>では、指標の意味が違います。コスト比(誤統合1件 : 見逃し1件)を動かすと、最適な閾値が移動します。");
    const g = s.append("div").attr("class", "grid2");
    // 左: 指標
    const A = figure(g, { title: "適合率・再現率・F1と、別人混在クラスタ数", source: "poc02_splink_vs_rules.csv を元データから再計算(PoC-02と一致を検証)", conf: "✅(fake_1000、デモ用合成)", n: `真のペア ${n0(D.k1.true_pairs)} / 1,000レコード`,
      note: "<b>注意(NFR-11)</b>: 破線はペア単位の候補の再現率の上限(0.734)。<b>クラスタ化後の再現率は、この上限を超えうる</b>(推移性で、直接比較していないペアが同じクラスタに入るため)。指標の定義が異なるので、同じ系列として読まないこと。" });
    seg(A.ctrl, [["pair", "ペア単位"], ["cluster", "クラスタ単位"]], state.mode, (m) => { state.mode = m; bus.call("mode", null, m); });
    const W = 620, H = 470, m = { l: 50, r: 18, t: 18, b: 40 };
    const svg = svgIn(A.body, W, H, "閾値と指標の折れ線");
    const x = d3.scaleLog().domain([0.001, 0.99]).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([330, m.t]);
    svg.append("g").call(axisStyle).attr("transform", "translate(0,330)").call(d3.axisBottom(x).tickValues(D.T).tickFormat((d) => d)).selectAll("text").attr("transform", "rotate(-35)").style("text-anchor", "end");
    svg.append("g").call(axisStyle).attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5));
    svg.append("g").selectAll("line").data(y.ticks(5)).join("line").attr("class", "gridline").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y);
    svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(D.ceiling)).attr("y2", y(D.ceiling)).attr("stroke", C.amber).attr("stroke-dasharray", "6 4").attr("opacity", 0.85);
    svg.append("text").attr("x", m.l + 8).attr("y", y(D.ceiling) - 5).attr("text-anchor", "start").attr("class", "lbl").style("fill", C.amber).text("ペア単位の候補の再現率の上限 0.734");
    const series = [["precision", "適合率", C.cyan], ["recall", "再現率", C.green], ["f1", "F1", C.violet]];
    const gLines = svg.append("g");
    const rule = svg.append("g");
    rule.append("line").attr("y1", m.t).attr("y2", 330).attr("stroke", "#fff").attr("stroke-opacity", 0.55);
    const ruleTxt = rule.append("text").attr("class", "lbl").attr("y", m.t + 10);
    const readout = svg.append("text").attr("x", m.l + 6).attr("y", 372).attr("class", "lbl m");
    // 下段: 不純クラスタ数
    const yb = d3.scaleLinear().domain([0, 30]).range([450, 392]);
    svg.append("text").attr("x", m.l).attr("y", 386).attr("class", "lbl m").text("別人が混ざったクラスタ数(クラスタ単位)");
    const gb = svg.append("g").call(axisStyle).attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(yb).ticks(3));
    svg.append("g").selectAll("rect").data(D.thr).join("rect").attr("x", (d) => x(d.t) - 8).attr("width", 16).attr("y", (d) => yb(d.impure)).attr("height", (d) => 450 - yb(d.impure)).attr("fill", C.red).attr("opacity", 0.8)
      .call((sel) => tipOn(sel, (d) => `t=${d.t}<br>別人混在クラスタ: <b>${d.impure}</b><br>推移性で追加されたペア: ${d.added}${d.added_precision != null ? `<br>追加ペアの適合率: ${f3(d.added_precision)}` : ""}`));
    svg.append("g").selectAll("text").data(D.thr).join("text").attr("x", (d) => x(d.t)).attr("y", (d) => yb(d.impure) - 3).attr("text-anchor", "middle").attr("class", "lbl").text((d) => (d.impure ? d.impure : ""));
    function drawLines() {
      const rows = D.thr.map((r) => ({ t: r.t, ...r[state.mode] }));
      gLines.selectAll("*").remove();
      series.forEach(([k, name, col]) => {
        gLines.append("path").datum(rows).attr("fill", "none").attr("stroke", col).attr("stroke-width", 2.4).attr("d", d3.line().x((d) => x(d.t)).y((d) => y(d[k])).curve(d3.curveMonotoneX));
        const pts = gLines.append("g").selectAll("circle").data(rows).join("circle").attr("cx", (d) => x(d.t)).attr("cy", (d) => y(d[k])).attr("r", 4).attr("fill", col);
        tipOn(pts, (d) => `<b>${name}</b>(${state.mode === "pair" ? "ペア単位" : "クラスタ単位"}) t=${d.t}<br>${f3(d[k])}<br>tp ${d.tp} / fp ${d.fp} / fn ${d.fn}`);
        const last = rows[rows.length - 1];
        gLines.append("text").attr("x", x(last.t) - 6).attr("y", y(last[k]) - 8).attr("text-anchor", "end").attr("class", "lbl").style("fill", col).text(name);
      });
      moveRule();
    }
    function moveRule() {
      const t = Math.min(Math.max(state.t, 0.001), 0.99);
      rule.select("line").attr("x1", x(t)).attr("x2", x(t));
      ruleTxt.attr("x", x(t) + 4).text("t=" + (t < 0.01 ? t.toFixed(4) : t.toFixed(3)));
      const near = D.thr.reduce((a, b) => (Math.abs(Math.log(b.t / t)) < Math.abs(Math.log(a.t / t)) ? b : a));
      const r = near[state.mode];
      readout.text(`最寄りの測定点 t=${near.t}: 適合率 ${f3(r.precision)} / 再現率 ${f3(r.recall)} / F1 ${f3(r.f1)}`);
    }
    drawLines();
    bus.on("mode.thr", drawLines); bus.on("t.thr", moveRule);
    tableToggle(A.fig, D.thr.map((r) => ({ t: r.t, "ペアP": r.pair.precision, "ペアR": r.pair.recall, "ペアF1": r.pair.f1, "クラスタP": r.cluster.precision, "クラスタR": r.cluster.recall, "クラスタF1": r.cluster.f1, 不純クラスタ: r.impure, 追加ペア: r.added })), "poc02_threshold");

    // 右: コスト
    const B = figure(g, { title: "総コスト = 誤統合コスト比 × 誤統合数 + 見逃し数", source: "poc02_predictions.csv(PoC-04と同じ式。最適閾値4点の一致を検証)", conf: "✅", n: "fake_1000(ペア単位/クラスタ単位の切替に追従)",
      note: "コスト比が大きいほど、最適な閾値は高く(保守的に)なります。支払先マスタのように誤統合が重大な業務と、マーケティング重複排除のように誤分割が許容される業務では、最適な閾値が違います。" });
    const cw = B.ctrl.append("span").text("誤統合コスト比 c ");
    const cs = B.ctrl.append("input").attr("type", "range").attr("min", 0).attr("max", 100).attr("value", (Math.log10(state.c) / 2) * 100).attr("aria-label", "コスト比");
    const cv = B.ctrl.append("span").attr("class", "val");
    const W2 = 620, H2 = 470;
    const svg2 = svgIn(B.body, W2, H2, "コスト曲線");
    const y2 = d3.scaleLog().range([330, m.t]);
    const gx = svg2.append("g").call(axisStyle).attr("transform", "translate(0,330)").call(d3.axisBottom(x).tickValues(D.T).tickFormat((d) => d));
    gx.selectAll("text").attr("transform", "rotate(-35)").style("text-anchor", "end");
    const gy = svg2.append("g").call(axisStyle).attr("transform", `translate(${m.l},0)`);
    const gc = svg2.append("g");
    const rule2 = svg2.append("line").attr("y1", m.t).attr("y2", 330).attr("stroke", "#fff").attr("stroke-opacity", 0.55);
    const info = svg2.append("g").attr("transform", "translate(54,368)");
    const tbl = svg2.append("g").attr("transform", "translate(54,396)");
    function drawCost() {
      const c = state.c;
      const rows = D.thr.map((r) => ({ t: r.t, fp: r[state.mode].fp, fn: r[state.mode].fn, cost: c * r[state.mode].fp + r[state.mode].fn }));
      const best = rows.reduce((a, b) => (b.cost < a.cost || (b.cost === a.cost && b.t < a.t) ? b : a));
      y2.domain([Math.max(1, d3.min(rows, (d) => d.cost) * 0.8), d3.max(rows, (d) => d.cost) * 1.1]);
      gy.call(d3.axisLeft(y2).ticks(5, "~s"));
      gc.selectAll("*").remove();
      gc.append("path").datum(rows).attr("fill", "none").attr("stroke", C.amber).attr("stroke-width", 2.6).attr("d", d3.line().x((d) => x(d.t)).y((d) => y2(Math.max(1, d.cost))).curve(d3.curveMonotoneX));
      const pts = gc.selectAll("circle").data(rows).join("circle").attr("cx", (d) => x(d.t)).attr("cy", (d) => y2(Math.max(1, d.cost))).attr("r", (d) => (d === best ? 8 : 4)).attr("fill", (d) => (d === best ? C.green : C.amber));
      tipOn(pts, (d) => `t=${d.t}<br>誤統合 ${d.fp} / 見逃し ${d.fn}<br>総コスト <b>${n0(d.cost)}</b>`);
      gc.append("text").attr("x", x(best.t)).attr("y", y2(Math.max(1, best.cost)) - 14).attr("text-anchor", "middle").attr("class", "lbl").style("fill", C.green).text(`最適 t=${best.t}`);
      cv.text("c = " + (c < 10 ? c.toFixed(1) : Math.round(c)));
      info.selectAll("*").remove();
      info.append("text").attr("class", "lbl").text(`c=${c < 10 ? c.toFixed(1) : Math.round(c)} の最適閾値 t*=${best.t}(誤統合 ${best.fp}、見逃し ${n0(best.fn)}、総コスト ${n0(best.cost)})`);
      tbl.selectAll("*").remove();
      tbl.append("text").attr("class", "lbl m").text("PoC-04の4点(ペア単位): 比1→0.05 / 比5→0.20 / 比20→0.50 / 比100→0.50");
      moveRule2();
    }
    function moveRule2() { const t = Math.min(Math.max(state.t, 0.001), 0.99); rule2.attr("x1", x(t)).attr("x2", x(t)); }
    cs.on("input", function () { state.c = Math.pow(10, (+this.value / 100) * 2); bus.call("c", null, state.c); drawCost(); });
    drawCost();
    bus.on("mode.cost", drawCost); bus.on("t.cost", moveRule2);
    tableToggle(B.fig, D.thr.map((r) => ({ t: r.t, ペア誤統合: r.pair.fp, ペア見逃し: r.pair.fn, クラスタ誤統合: r.cluster.fp, クラスタ見逃し: r.cluster.fn })), "poc04_cost_inputs");
  }

  // ---------- 確認キュー (K6) ----------
  function queueSection() {
    const s = section("queue", "K6", "確認キューは、閾値の設計で量が決まる",
      "「自動マージ」「人手確認」「破棄」の3帯域に分けると、確認キューの量は自動マージ閾値(hi)と確認下限(lo)で決まります。<strong>確認者が完全に正しくても、ブロッキングで候補に入らなかったペアは回収できません</strong>。");
    const F = figure(s, { title: "自動マージ閾値 × 確認下限", source: "poc11_auto_vs_review.csv", conf: "✅(シミュレーション。確認者は完全に正しいと仮定した最良ケース)", n: "fake_1000(1,000レコード)",
      note: "キュー量を実データへ外挿するときは、レコード数と分布の違いに注意(PRD 2.2 K6)。<b>全パターンで、確認後の再現率の上限は 0.734</b>(候補に入らなかった541ペアは回収不能)。" });
    const metrics = [["queue_per_1000_records", "キュー件数 / 1,000レコード"], ["recall_after_perfect_review", "確認後の再現率"], ["queue_true_share", "キュー中の真ペア率"]];
    let cur = metrics[0][0];
    seg(F.ctrl, metrics, cur, (m) => { cur = m; draw(); });
    const W = 900, H = 330, m = { l: 150, r: 20, t: 40, b: 30 };
    const svg = svgIn(F.body, W, H, "確認キューのヒートマップ");
    const his = [0.5, 0.9, 0.99], los = [0.001, 0.01, 0.1, 0.5];
    const x = d3.scaleBand().domain(los).range([m.l, W - m.r]).padding(0.08), y = d3.scaleBand().domain(his).range([m.t, H - m.b]).padding(0.08);
    svg.append("text").attr("x", m.l).attr("y", 18).attr("class", "lbl m").text("確認の下限(lo)→  この確率以上〜hi未満を人手確認");
    svg.append("text").attr("x", 8).attr("y", m.t + 14).attr("class", "lbl m").text("自動マージ閾値(hi)↓");
    const g = svg.append("g");
    svg.append("g").call(axisStyle).attr("transform", `translate(0,${m.t - 4})`).call(d3.axisTop(x).tickSize(0)).select(".domain").remove();
    svg.append("g").call(axisStyle).attr("transform", `translate(${m.l - 4},0)`).call(d3.axisLeft(y).tickSize(0)).select(".domain").remove();
    function draw() {
      const rows = D.queue.filter((r) => r.lo <= r.hi);
      const col = d3.scaleSequential(cur === "queue_per_1000_records" ? d3.interpolateYlOrRd : d3.interpolateGnBu).domain(d3.extent(rows, (r) => r[cur]));
      const cells = g.selectAll("g.c").data(rows, (r) => r.hi + "_" + r.lo).join((e) => { const k = e.append("g").attr("class", "c"); k.append("rect").attr("rx", 10); k.append("text").attr("text-anchor", "middle").attr("class", "lbl").style("font-size", "15px"); return k; });
      cells.select("rect").transition().duration(500).attr("x", (r) => x(r.lo)).attr("y", (r) => y(r.hi)).attr("width", x.bandwidth()).attr("height", y.bandwidth()).attr("fill", (r) => col(r[cur]));
      cells.select("text").attr("x", (r) => x(r.lo) + x.bandwidth() / 2).attr("y", (r) => y(r.hi) + y.bandwidth() / 2 + 5).style("fill", (r) => (d3.hsl(col(r[cur])).l > 0.55 ? "#0b0f1a" : "#fff"))
        .text((r) => (cur === "queue_per_1000_records" ? n0(Math.round(r[cur])) : f3(r[cur])));
      tipOn(cells, (r) => `hi=${r.hi}, lo=${r.lo}<br>自動マージ ${n0(r.auto_pairs)}件(誤統合 ${r.auto_false_merges})<br>確認キュー ${n0(r.review_queue_pairs)}件(<b>${f2(r.review_queue_pairs / 1)}</b>件/1,000レコード)<br>キュー中の真ペア率 ${r.queue_true_share == null ? "-" : f3(r.queue_true_share)}<br>確認後の再現率 ${f3(r.recall_after_perfect_review)}(確認なし ${f3(r.recall_without_review)})<br>見逃し ${r.missed_true_pairs}ペア`);
    }
    draw();
    tableToggle(F.fig, D.queue.map((r) => ({ hi: r.hi, lo: r.lo, 自動: r.auto_pairs, 誤統合: r.auto_false_merges, キュー: r.review_queue_pairs, キュー真率: r.queue_true_share ?? "", 確認後再現率: r.recall_after_perfect_review, 確認なし再現率: r.recall_without_review, 見逃し: r.missed_true_pairs })), "poc11_queue");
  }

  // ---------- ブロッキング (K1/K2) ----------
  function blockingSection() {
    const s = section("blocking", "K1 ・ K2", "ブロッキングが再現率の天井をつくる",
      "全ペアの大半は非一致です。<strong>「すべて非一致」と答えるだけで Accuracy は 99.59%</strong>。ブロッキングは、99%以上のペアを捨てますが、1つのキーでは真の一致の過半を取りこぼします。");
    const g = s.append("div").attr("class", "grid2");
    const A = figure(g, { title: "Accuracyの無意味さ(1,000マス=全ペアの比率)", source: "poc01_summary.json", conf: "✅(ほぼ自明な算術)", n: `全ペア ${n0(D.k1.all_pairs)} / 真の一致 ${n0(D.k1.true_pairs)}`,
      note: "1マス=全ペアの0.1%。真の一致の割合は " + pct(D.k1.true_pairs / D.k1.all_pairs) + "(約4マス)。マス目は比率の描画であり、個々のペアではありません。" });
    const svg = svgIn(A.body, 620, 330, "ワッフル");
    const cells = d3.range(1000), match = Math.round(1000 * D.k1.true_pairs / D.k1.all_pairs);
    svg.append("g").selectAll("rect").data(cells).join("rect").attr("x", (i) => 14 + (i % 50) * 11.6).attr("y", (i) => 14 + Math.floor(i / 50) * 11.6).attr("width", 9.6).attr("height", 9.6).attr("rx", 2)
      .attr("fill", (i) => (i < match ? C.red : "#26355f")).attr("opacity", 0).transition().delay((i) => i * 1.2).duration(300).attr("opacity", 1);
    svg.append("text").attr("x", 14).attr("y", 268).attr("class", "lbl").style("font-size", "26px").style("fill", C.amber).text("Accuracy " + pct(D.k1.accuracy));
    svg.append("text").attr("x", 14).attr("y", 296).attr("class", "lbl m").text("「すべて非一致」と答える分類器 ― 再現率は 0%");
    const B = figure(g, { title: "ブロッキング方式別の 再現率(PC)と候補数", source: "poc01_blocking.csv", conf: "✅(fake_1000)", n: "1,000レコード/真のペア 2,031",
      note: "<b>注意</b>: ここの「4キー和集合」の再現率 0.812 と、閾値の図の破線 0.734 は、<b>異なるブロッキング構成</b>(別のルール集合)の値です。同じ系列として読まないこと。削減率はいずれも99.2〜99.9%。" });
    const W = 620, H = 330, m = { l: 52, r: 16, t: 14, b: 40 };
    const sv = svgIn(B.body, W, H, "ブロッキングの散布図");
    const x = d3.scaleLog().domain([500, 5000]).range([m.l, W - m.r]), y = d3.scaleLinear().domain([0, 1]).range([H - m.b, m.t]);
    sv.append("g").call(axisStyle).attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickValues([500, 1000, 2000, 5000]).tickFormat(d3.format(",")));
    sv.append("g").call(axisStyle).attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5));
    sv.append("text").attr("x", W - m.r).attr("y", H - 6).attr("text-anchor", "end").attr("class", "lbl m").text("候補ペア数(対数)");
    sv.append("text").attr("x", m.l + 4).attr("y", m.t + 8).attr("class", "lbl m").text("再現率(PC)");
    const pts = sv.append("g").selectAll("g").data(D.blocking).join("g").attr("transform", (d) => `translate(${x(d.candidates)},${y(d.pc)})`);
    pts.append("circle").attr("r", (d) => (d.scheme.startsWith("和集合") ? 10 : 6)).attr("fill", (d) => (d.scheme.startsWith("和集合") ? C.green : C.cyan)).attr("opacity", 0.9);
    pts.append("text").attr("x", 10).attr("y", 4).attr("class", "lbl").text((d) => d.scheme.replace("完全一致", "").replace("和集合(dob|surname|email|first_name)", "4キー和集合"));
    tipOn(pts, (d) => `<b>${d.scheme}</b><br>候補 ${n0(d.candidates)}<br>再現率 ${f3(d.pc)}<br>適合率(PQ) ${f3(d.pq)}<br>削減率 ${pct(d.reduction)}`);
    tableToggle(B.fig, D.blocking.map((d) => ({ 方式: d.scheme, 候補: d.candidates, 再現率: d.pc, PQ: d.pq, 削減率: d.reduction })), "poc01_blocking");
    // K14
    const F = figure(s, { title: "フィルタリング: PC≥0.9 を満たす設定のうち、PQ(候補の真ペア率)が最大のもの", source: "poc18_filtering_best.csv(スキーマ非依存。VLDB J. 2024 の要旨の主張の縮小版)", conf: "✅(縮小版。論文本文は未入手)", n: "3データ(Ditto分割から再構成した小プール)",
      note: "kNN結合が、単純なトークンブロッキングよりPQが高い。ただし比べたブロッキングは単純な方式のみ、格子も粗い。論文の数値の再現ではありません。" });
    const ds = ["Abt-Buy", "Amazon-Google", "DBLP-ACM"], short = (m) => m.replace("token-blocking+purge+CBS", "トークンブロッキング").replace("kNN-join ", "kNN ").replace("eps-join ", "ε ").replace("tfidf-", "TF-IDF ");
    const W3 = 1000, H3 = 300, colW = W3 / 3;
    const s3 = svgIn(F.body, W3, H3, "フィルタリングのドットプロット");
    ds.forEach((d, i) => {
      const rows = D.filtering.filter((r) => r.dataset === d).sort((a, b) => b.pq - a.pq);
      const gx = s3.append("g").attr("transform", `translate(${i * colW},0)`);
      const xx = d3.scaleLinear().domain([0, 1]).range([140, colW - 18]), yy = d3.scaleBand().domain(rows.map((r) => r.method)).range([40, H3 - 24]).padding(0.4);
      gx.append("text").attr("x", 140).attr("y", 20).attr("class", "lbl").style("font-size", "13px").text(d);
      gx.append("g").call(axisStyle).attr("transform", `translate(0,${H3 - 24})`).call(d3.axisBottom(xx).ticks(4));
      gx.append("g").selectAll("line").data(rows).join("line").attr("x1", xx(0)).attr("x2", (r) => xx(r.pq)).attr("y1", (r) => yy(r.method) + yy.bandwidth() / 2).attr("y2", (r) => yy(r.method) + yy.bandwidth() / 2).attr("stroke", "#33427a");
      const dots = gx.append("g").selectAll("circle").data(rows).join("circle").attr("cx", (r) => xx(r.pq)).attr("cy", (r) => yy(r.method) + yy.bandwidth() / 2).attr("r", 6).attr("fill", (r) => (r.method.startsWith("token") ? C.red : C.cyan));
      gx.append("g").selectAll("text").data(rows).join("text").attr("x", 134).attr("y", (r) => yy(r.method) + yy.bandwidth() / 2 + 4).attr("text-anchor", "end").attr("class", "lbl").text((r) => short(r.method));
      tipOn(dots, (r) => `<b>${short(r.method)}</b><br>${r.params}<br>候補 ${n0(r.candidates)}<br>PC ${f3(r.pc)} / PQ ${f3(r.pq)}<br>PC≥0.9を満たす設定の中でのPQ範囲 ${r.range}`);
    });
    s3.append("text").attr("x", W3 - 12).attr("y", H3 - 4).attr("text-anchor", "end").attr("class", "lbl m").text("PQ(候補中の真ペアの割合)");
    tableToggle(F.fig, D.filtering.map((r) => ({ データ: r.dataset, 方式: r.method, 設定: r.params, 候補: r.candidates, PC: r.pc, PQ: r.pq, PQ範囲: r.range })), "poc18_filtering");
  }

  // ---------- 日本語の正規化 (K7) ----------
  function normSection() {
    const s = section("norm", "K7", "日本語の表記揺れは、正規化の段階で決まる",
      "法人名・住所を<strong>正規化なしで完全一致</strong>させると、同一企業の2%しか一致しません。NFKC・法人格・住所の正規化、異体字、ファジー照合を重ねるごとに、再現率が上がります。");
    const F = figure(s, { title: "正規化の段階と、再現率・適合率(11シードの平均 ± SD)", source: "poc13_poc03_multiseed.csv(poc03を11シードで再実行)", conf: "✅(合成データ。生成器の仮定に依存)", n: "11シード × 約470レコード",
      note: "揺れの種類・頻度は生成器の設計で、実データを代表しません。示せるのは「段階ごとの限界の形」であり、実運用の精度ではありません。誤差棒は±1SD(シード間)。" });
    let cur = "recall";
    seg(F.ctrl, [["recall", "再現率"], ["precision", "適合率"], ["f1", "F1"]], cur, (m) => { cur = m; draw(); });
    const W = 1000, H = 330, m = { l: 330, r: 40, t: 14, b: 34 };
    const svg = svgIn(F.body, W, H, "正規化の段階の棒グラフ");
    const rows = D.norm, y = d3.scaleBand().domain(rows.map((r) => r.method)).range([m.t, H - m.b]).padding(0.28), x = d3.scaleLinear().domain([0, 1]).range([m.l, W - m.r]);
    svg.append("g").call(axisStyle).attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(5));
    svg.append("g").selectAll("text").data(rows).join("text").attr("x", m.l - 8).attr("y", (r) => y(r.method) + y.bandwidth() / 2 + 4).attr("text-anchor", "end").attr("class", "lbl").text((r) => r.method);
    const bars = svg.append("g").selectAll("rect").data(rows).join("rect").attr("x", m.l).attr("y", (r) => y(r.method)).attr("height", y.bandwidth()).attr("rx", 6).attr("fill", C.cyan).attr("width", 0);
    const err = svg.append("g").selectAll("line").data(rows).join("line").attr("stroke", "#fff").attr("stroke-width", 2);
    const val = svg.append("g").selectAll("text").data(rows).join("text").attr("class", "lbl").style("font-size", "12px");
    function draw() {
      bars.transition().duration(600).attr("width", (r) => x(r[cur].mean) - m.l).attr("fill", (r) => d3.interpolateCool(0.25 + 0.7 * r[cur].mean));
      err.transition().duration(600).attr("x1", (r) => x(r[cur].mean - r[cur].std)).attr("x2", (r) => x(r[cur].mean + r[cur].std)).attr("y1", (r) => y(r.method) + y.bandwidth() / 2).attr("y2", (r) => y(r.method) + y.bandwidth() / 2);
      val.transition().duration(600).attr("x", (r) => x(r[cur].mean + r[cur].std) + 8).attr("y", (r) => y(r.method) + y.bandwidth() / 2 + 4).textTween((r) => { const i = d3.interpolateNumber(0, r[cur].mean); return (t) => f3(i(t)); });
    }
    draw();
    tipOn(bars, (r) => `<b>${r.method}</b><br>再現率 ${f3(r.recall.mean)}±${f3(r.recall.std)}(${f3(r.recall.min)}〜${f3(r.recall.max)})<br>適合率 ${f3(r.precision.mean)}±${f3(r.precision.std)}<br>F1 ${f3(r.f1.mean)}`);
    tableToggle(F.fig, rows.map((r) => ({ 方式: r.method, 再現率平均: r.recall.mean, 再現率SD: r.recall.std, 適合率平均: r.precision.mean, 適合率SD: r.precision.std, F1平均: r.f1.mean })), "poc13_norm");
  }

  // ---------- 評価設計 (K8/K9/K10/K11/K12) ----------
  function evalSection() {
    const s = section("eval", "K8 ・ K9 ・ K10 ・ K11 ・ K12", "評価の設計: ラベル数、信頼区間、ラベルの品質",
      "ラベルは多ければよい、というものではなく、<strong>どれだけ要るか</strong>・<strong>どこまで信じてよいか</strong>に答えが要ります。");
    const g = s.append("div").attr("class", "grid3");
    // K9
    const A = figure(g, { title: "ラベル数と、適合率の95%信頼区間の幅", source: "poc04_ci_vs_sample.csv", conf: "✅(487ペアの母集団からの抽出を2,000回)", n: "n=10〜200",
      note: "n=200で被覆率が高く出るのは、有限母集団からの非復元抽出で分散が縮むため(保守的)。" });
    {
      const W = 460, H = 300, m = { l: 48, r: 16, t: 16, b: 36 };
      const svg = svgIn(A.body, W, H, "CI幅");
      const x = d3.scaleLog().domain([10, 200]).range([m.l, W - m.r]), y = d3.scaleLinear().domain([0, 0.55]).range([H - m.b, m.t]);
      svg.append("g").call(axisStyle).attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickValues([10, 20, 50, 100, 200]).tickFormat(d3.format("d")));
      svg.append("g").call(axisStyle).attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5));
      svg.append("path").datum(D.ci).attr("fill", "none").attr("stroke", C.cyan).attr("stroke-width", 2.6).attr("d", d3.line().x((d) => x(d.labeled_n)).y((d) => y(d.mean_CI_width)).curve(d3.curveMonotoneX));
      const p = svg.append("g").selectAll("circle").data(D.ci).join("circle").attr("cx", (d) => x(d.labeled_n)).attr("cy", (d) => y(d.mean_CI_width)).attr("r", 5).attr("fill", C.cyan);
      tipOn(p, (d) => `ラベル ${d.labeled_n}件<br>平均CI幅 <b>${f3(d.mean_CI_width)}</b><br>被覆率 ${f3(d.coverage_95CI)}`);
      svg.append("text").attr("x", m.l + 6).attr("y", m.t + 10).attr("class", "lbl m").text("平均CI幅(狭いほど確か)");
      svg.append("text").attr("x", W - m.r).attr("y", H - 4).attr("text-anchor", "end").attr("class", "lbl m").text("ラベル数(対数)");
      tableToggle(A.fig, D.ci.map((d) => ({ ラベル数: d.labeled_n, 平均CI幅: d.mean_CI_width, 被覆率: d.coverage_95CI })), "poc04_ci");
    }
    // K10
    const B = figure(g, { title: "ラベル量と性能(Abt-Buy、RoBERTa-base、GPU)", source: "poc06_*gpu15ep*.json", conf: "✅(公開ベンチ。単一シード)", n: "訓練 287〜5,743組 / テスト 1,916組",
      note: "287組(5%)では 0.275 に崩れる。「少数のラベルでも効く」は無条件ではない。参照線: TF-IDF(自測)と論文のDeepMatcher/Ditto(参照値)。" });
    {
      const W = 460, H = 300, m = { l: 48, r: 16, t: 16, b: 36 }, pts = D.label_eff.points;
      const svg = svgIn(B.body, W, H, "ラベル量と性能");
      const x = d3.scaleLog().domain([250, 6500]).range([m.l, W - m.r]), y = d3.scaleLinear().domain([0, 1]).range([H - m.b, m.t]);
      svg.append("g").call(axisStyle).attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).tickValues([287, 574, 1435, 2871, 5743]).tickFormat(d3.format(",")));
      svg.append("g").call(axisStyle).attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5));
      [[D.label_eff.tfidf, "TF-IDF(自測)", C.mute], [D.label_eff.paper_deepmatcher, "論文 DeepMatcher", C.amber], [D.label_eff.paper_ditto, "論文 Ditto", C.violet]].forEach(([v, t, c]) => {
        svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(v)).attr("y2", y(v)).attr("stroke", c).attr("stroke-dasharray", "5 4").attr("opacity", 0.8);
        svg.append("text").attr("x", W - m.r - 2).attr("y", y(v) - 4).attr("text-anchor", "end").attr("class", "lbl").style("fill", c).text(t + " " + f2(v));
      });
      svg.append("path").datum(pts).attr("fill", "none").attr("stroke", C.green).attr("stroke-width", 2.6).attr("d", d3.line().x((d) => x(d.n)).y((d) => y(d.f1)).curve(d3.curveMonotoneX));
      const p = svg.append("g").selectAll("circle").data(pts).join("circle").attr("cx", (d) => x(d.n)).attr("cy", (d) => y(d.f1)).attr("r", 6).attr("fill", C.green);
      tipOn(p, (d) => `訓練 ${n0(d.n)}組<br>テストF1 <b>${f3(d.f1)}</b><br>適合率 ${f3(d.p)} / 再現率 ${f3(d.r)}`);
      svg.append("text").attr("x", m.l + 6).attr("y", m.t + 10).attr("class", "lbl m").text("テストF1");
      svg.append("text").attr("x", W - m.r).attr("y", H - 4).attr("text-anchor", "end").attr("class", "lbl m").text("訓練ペア数(対数)");
      tableToggle(B.fig, pts.map((d) => ({ 訓練組: d.n, F1: d.f1, 適合率: d.p, 再現率: d.r })), "poc06_label_efficiency");
    }
    // K8
    const Cc = figure(g, { title: "教師なしSplinkの感度: 事前確率の「再現率の仮定」", source: "poc14_splink_sensitivity.csv", conf: "✅(5シード平均。シードの影響は0)", n: "fake_1000 / t=0.5",
      note: "<b>縦軸は 0.80〜1.0 に切り詰めています</b>(差が小さいため)。仮定を0.5にすると、クラスタ適合率が 1.00→0.954 に下がり、誤統合が増えます。" });
    {
      const W = 460, H = 300, m = { l: 52, r: 16, t: 20, b: 40 };
      const svg = svgIn(Cc.body, W, H, "感度");
      const x0 = d3.scaleBand().domain(D.sens.map((d) => d.assumed_recall)).range([m.l, W - m.r]).padding(0.25), y = d3.scaleLinear().domain([0.8, 1]).range([H - m.b, m.t]);
      const ks = [["cluster_precision", "クラスタ適合率", C.red], ["cluster_f1", "クラスタF1", C.violet], ["cluster_recall", "クラスタ再現率", C.green]];
      const x1 = d3.scaleBand().domain(ks.map((k) => k[0])).range([0, x0.bandwidth()]).padding(0.1);
      svg.append("g").call(axisStyle).attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x0).tickFormat((d) => "仮定 " + d));
      svg.append("g").call(axisStyle).attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4));
      const grp = svg.append("g").selectAll("g").data(D.sens).join("g").attr("transform", (d) => `translate(${x0(d.assumed_recall)},0)`);
      grp.selectAll("rect").data((d) => ks.map((k) => ({ k, v: d[k[0]], d }))).join("rect").attr("x", (d) => x1(d.k[0])).attr("width", x1.bandwidth()).attr("y", (d) => y(d.v)).attr("height", (d) => H - m.b - y(d.v)).attr("fill", (d) => d.k[2]).attr("rx", 3).call((sel) => tipOn(sel, (d) => `仮定 ${d.d.assumed_recall}<br>${d.k[1]} <b>${f3(d.v)}</b><br>λ=${d.d.lambda.toFixed(4)}`));
      grp.selectAll("text").data((d) => ks.map((k) => ({ k, v: d[k[0]] }))).join("text").attr("x", (d) => x1(d.k[0]) + x1.bandwidth() / 2).attr("y", (d) => y(d.v) - 4).attr("text-anchor", "middle").attr("class", "lbl").style("font-size", "10px").text((d) => f3(d.v));
      ks.forEach((k, i) => { svg.append("rect").attr("x", m.l + i * 120).attr("y", 4).attr("width", 10).attr("height", 10).attr("fill", k[2]); svg.append("text").attr("x", m.l + i * 120 + 14).attr("y", 13).attr("class", "lbl").text(k[1]); });
      tableToggle(Cc.fig, D.sens, "poc14_sensitivity");
    }
    // K11
    const L = figure(s, { title: "LLMのプロンプト感度: モデル × プロンプト(F1)", source: "poc12_summary.csv", conf: "✅(小標本。差の多くは標本誤差の範囲内)", n: "各セル45組(陽性15/陰性30)。解析不能の件数を!で表示",
      note: "最良のプロンプトはモデルとデータで異なる傾向がある(論文の主張と整合)。ただし<b>45組ではほとんどの差が有意ではありません</b>。解析不能(空応答・HTTP 500)は不一致として数えており、再現率を過小評価しうる。" });
    let ds = "Abt-Buy";
    seg(L.ctrl, [["Abt-Buy", "Abt-Buy"], ["DBLP-ACM", "DBLP-ACM"]], ds, (d) => { ds = d; draw(); });
    {
      const W = 900, H = 250, m = { l: 190, r: 20, t: 36, b: 20 };
      const svg = svgIn(L.body, W, H, "LLMのヒートマップ");
      const models = Array.from(new Set(D.llm.map((r) => r.model))), prompts = ["A_plain", "B_strict", "C_lenient"];
      const x = d3.scaleBand().domain(prompts).range([m.l, W - m.r]).padding(0.08), y = d3.scaleBand().domain(models).range([m.t, H - m.b]).padding(0.08);
      svg.append("g").call(axisStyle).attr("transform", `translate(0,${m.t - 4})`).call(d3.axisTop(x).tickSize(0)).select(".domain").remove();
      svg.append("g").call(axisStyle).attr("transform", `translate(${m.l - 4},0)`).call(d3.axisLeft(y).tickSize(0)).select(".domain").remove();
      const col = d3.scaleSequential(d3.interpolateViridis).domain([0.7, 1]);
      const gg = svg.append("g");
      function draw() {
        const rows = D.llm.filter((r) => r.dataset === ds);
        const c = gg.selectAll("g.c").data(rows, (r) => r.model + r.prompt).join((e) => { const k = e.append("g").attr("class", "c"); k.append("rect").attr("rx", 10); k.append("text").attr("class", "lbl").attr("text-anchor", "middle").style("font-size", "17px"); return k; });
        c.select("rect").transition().duration(500).attr("x", (r) => x(r.prompt)).attr("y", (r) => y(r.model)).attr("width", x.bandwidth()).attr("height", y.bandwidth()).attr("fill", (r) => col(r.f1));
        c.select("text").attr("x", (r) => x(r.prompt) + x.bandwidth() / 2).attr("y", (r) => y(r.model) + y.bandwidth() / 2 + 6).style("fill", (r) => (r.f1 > 0.9 ? "#0b0f1a" : "#fff")).text((r) => f3(r.f1) + (r.unparseable ? "  !" + r.unparseable : ""));
        tipOn(c, (r) => `<b>${r.model}</b> / ${r.prompt}<br>F1 ${f3(r.f1)}(適合率 ${f3(r.precision)} / 再現率 ${f3(r.recall)})<br>正解率 ${f3(r.acc)}(95%区間 ${f3(r.acc_lo)}〜${f3(r.acc_hi)})<br>解析不能 ${r.unparseable}件 / 時間 ${r.seconds}秒`);
      }
      draw();
      tableToggle(L.fig, D.llm.map((r) => ({ データ: r.dataset, モデル: r.model, プロンプト: r.prompt, F1: r.f1, 適合率: r.precision, 再現率: r.recall, 解析不能: r.unparseable, 秒: r.seconds })), "poc12_llm");
    }
    // K12
    const Wd = figure(s, { title: "ベンチマークのラベルも疑う: WDC Products 600組の再評価", source: "poc16_wdc_relabel_summary.json / wdc_relabel_adjudication.json", conf: "✅(判定者はAI。人間のアノテータではない)", n: "600組(陽性300/陰性300)。不一致43組を目視分類",
      note: "判定者(ホスト型LLM)と分類者(私=AI)は独立ではなく、判定者が原ラベルと一致した組は見ていません。<b>2.3%(明確な誤り)は下限</b>、6.7%(判断が割れるものを含む)は上限。論文のノイズ推定は約4%。" });
    {
      const w = D.wdc, W = 1000, H = 250, m = { l: 20, r: 20 };
      const svg = svgIn(Wd.body, W, H, "WDCの再評価");
      const x = d3.scaleLinear().domain([0, w.n]).range([m.l, W - m.r]);
      const rowsA = [{ k: "判定者と一致", v: w.n - w.disagree, c: "#2a4a7f" }, { k: "判定者と不一致", v: w.disagree, c: C.amber }];
      let off = 0;
      svg.append("text").attr("x", m.l).attr("y", 18).attr("class", "lbl m").text(`600組: 判定者と原ラベルの一致 ${pct(w.agree)}(κ=${f3(w.kappa)})`);
      svg.append("g").selectAll("rect").data(rowsA).join("rect").attr("x", (d) => { const o = x(off); off += d.v; return o; }).attr("y", 28).attr("height", 34).attr("width", (d) => x(d.v) - m.l).attr("fill", (d) => d.c).attr("rx", 6);
      off = 0; svg.append("g").selectAll("text").data(rowsA).join("text").attr("x", (d) => { const o = x(off) + 8; off += d.v; return o; }).attr("y", 50).attr("class", "lbl").style("font-size", "13px").text((d) => `${d.k} ${d.v}`);
      const x2 = d3.scaleLinear().domain([0, w.disagree]).range([m.l, W - m.r]);
      const rowsB = [{ k: "原ラベルの誤り(W)", v: w.W, c: C.red }, { k: "判断が割れる(B)", v: w.B, c: C.amber }, { k: "判定者の誤り(J)", v: w.J, c: C.cyan }];
      svg.append("text").attr("x", m.l).attr("y", 94).attr("class", "lbl m").text(`不一致 ${w.disagree}組の目視分類(陽性ラベル ${w.pos_label_dis} / 陰性ラベル ${w.neg_label_dis})`);
      off = 0;
      const gb = svg.append("g").selectAll("g").data(rowsB).join("g").attr("transform", (d) => { const t = `translate(${x2(off)},104)`; off += d.v; return t; });
      gb.append("rect").attr("height", 34).attr("width", (d) => x2(d.v) - m.l).attr("fill", (d) => d.c).attr("rx", 6).attr("opacity", 0.9);
      gb.append("text").attr("x", 8).attr("y", 22).attr("class", "lbl").style("fill", "#0b0f1a").style("font-size", "13px").text((d) => `${d.k} ${d.v}`);
      tipOn(gb, (d) => `<b>${d.k}</b> ${d.v}組<br>${(d.v / w.n * 100).toFixed(1)}%(600組中)`);
      svg.append("text").attr("x", m.l).attr("y", 170).attr("class", "lbl m").text("不一致率(ラベル別): 陽性ラベルに誤りが集中");
      const lb = [["陽性ラベル", w.by_group["label=1"]], ["陰性ラベル", w.by_group["label=0"]]];
      const x3 = d3.scaleLinear().domain([0, 0.15]).range([m.l + 90, W - m.r]);
      svg.append("g").selectAll("rect").data(lb).join("rect").attr("x", m.l + 90).attr("y", (d, i) => 182 + i * 26).attr("height", 18).attr("width", (d) => x3(d[1]) - (m.l + 90)).attr("fill", (d, i) => (i ? C.cyan : C.red)).attr("rx", 4);
      svg.append("g").selectAll("text").data(lb).join("text").attr("x", m.l).attr("y", (d, i) => 196 + i * 26).attr("class", "lbl").text((d) => d[0]);
      svg.append("g").selectAll("text").data(lb).join("text").attr("x", (d) => x3(d[1]) + 6).attr("y", (d, i) => 196 + i * 26).attr("class", "lbl").text((d) => pct(d[1]));
      tableToggle(Wd.fig, [{ 指標: "一致率", 値: w.agree }, { 指標: "κ", 値: w.kappa }, { 指標: "不一致", 値: w.disagree }, { 指標: "原ラベルの誤り(W)", 値: w.W }, { 指標: "判断が割れる(B)", 値: w.B }, { 指標: "判定者の誤り(J)", 値: w.J }], "poc16_wdc");
    }
  }

  // ---------- 規模 (K13) ----------
  function scaleSection() {
    const s = section("scale", "K13", "規模: 速度は線形に近く、適合率は下がる",
      "合成データでは、Splinkが100万件を約73秒で処理しました。<strong>時間は許容でも、規模とともに適合率が下がります</strong>(名前の語彙が小さい合成データで、偶然の一致が増えるため)。");
    const F = figure(s, { title: "レコード数に対する、所要時間と適合率", source: "poc05_scaling.csv + poc09_scaling_1M.csv", conf: "✅(合成。20コアARM64上のx64エミュレーション、他ジョブと並走)", n: "9千〜100万件",
      note: "上下の図は<b>別の軸</b>です(時間は対数、適合率は 0.9〜1.0 に切り詰め)。実データでは、名前・生年月日の偏りで候補数が爆発しうる(未検証)。" });
    const W = 1000, H = 420, m = { l: 62, r: 30 };
    const svg = svgIn(F.body, W, H, "規模の推移");
    const x = d3.scaleLog().domain([8000, 1.2e6]).range([m.l, W - m.r]);
    const xa = d3.axisBottom(x).tickValues([1e4, 3e4, 1e5, 3e5, 1e6]).tickFormat(d3.format(".0s"));
    const sec = D.scaling.map((d) => ({ ...d, sec: d.train_sec + d.predict_sec }));
    const y1 = d3.scaleLog().domain([2, 120]).range([190, 24]), y2 = d3.scaleLinear().domain([0.9, 1]).range([390, 236]);
    svg.append("g").call(axisStyle).attr("transform", "translate(0,190)").call(xa);
    svg.append("g").call(axisStyle).attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y1).tickValues([2, 5, 10, 30, 100]).tickFormat(d3.format("d")));
    svg.append("g").call(axisStyle).attr("transform", "translate(0,390)").call(xa);
    svg.append("g").call(axisStyle).attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y2).ticks(4));
    svg.append("text").attr("x", m.l + 6).attr("y", 16).attr("class", "lbl m").text("学習+予測の時間(秒、対数)");
    svg.append("text").attr("x", m.l + 6).attr("y", 230).attr("class", "lbl m").text("適合率(0.9〜1.0に切り詰め)");
    const line = (yy, key, col) => svg.append("path").datum(sec).attr("fill", "none").attr("stroke", col).attr("stroke-width", 2.8).attr("d", d3.line().x((d) => x(d.records)).y((d) => yy(d[key])).curve(d3.curveMonotoneX));
    line(y1, "sec", C.cyan); line(y2, "precision", C.red);
    const p1 = svg.append("g").selectAll("circle").data(sec).join("circle").attr("cx", (d) => x(d.records)).attr("cy", (d) => y1(d.sec)).attr("r", 6).attr("fill", C.cyan);
    const p2 = svg.append("g").selectAll("circle").data(sec).join("circle").attr("cx", (d) => x(d.records)).attr("cy", (d) => y2(d.precision)).attr("r", 6).attr("fill", C.red);
    const tt = (d) => `<b>${n0(d.records)}件</b><br>全ペア ${d.all_pairs.toExponential(2)}<br>学習 ${d.train_sec}s + 予測 ${d.predict_sec}s<br>適合率 ${f3(d.precision)} / 再現率 ${f3(d.recall)}`;
    tipOn(p1, tt); tipOn(p2, tt);
    svg.append("g").selectAll("text").data(sec).join("text").attr("x", (d) => x(d.records)).attr("y", (d) => y2(d.precision) + 20).attr("text-anchor", "middle").attr("class", "lbl").text((d) => f3(d.precision));
    svg.append("g").selectAll("text").data(sec).join("text").attr("x", (d) => x(d.records)).attr("y", (d) => y1(d.sec) - 10).attr("text-anchor", "middle").attr("class", "lbl").text((d) => d.sec.toFixed(1) + "s");
    tableToggle(F.fig, D.scaling.map((d) => ({ レコード: d.records, 全ペア: d.all_pairs, 学習秒: d.train_sec, 予測秒: d.predict_sec, 適合率: d.precision, 再現率: d.recall })), "poc09_scaling");
  }

  // ---------- 要求と根拠 ----------
  function evidenceSection() {
    const s = section("evidence", "13章", "要求 × 根拠: どの要求が、どの実測に支えられているか",
      "PRDの要求" + D.reqs.length + "件を、根拠のK番号(実測)に対応づけました。<strong>実測に支えられていない「必須」要求</strong>は、リスクです。セルをクリックすると、該当する要求を表示します。");
    const g = s.append("div").attr("class", "grid2");
    const F = figure(g, { title: "要求グループ × 根拠(K1〜K14)", source: "work/prd.md の6〜7章の表(ビルド時に解析)", conf: "(PRDの記述から機械的に抽出)", n: `${D.reqs.length}件(FR ${D.reqs.filter((r) => r.group !== "NFR").length} / NFR ${D.reqs.filter((r) => r.group === "NFR").length})`,
      note: "「なし」= 根拠欄にK番号がない(IA/PRの章、公的資料、未確認)。要求は複数のKを引くので、行の合計は要求数と一致しません。" });
    const groups = ["ING", "STD", "BLK", "MAT", "CLU", "REV", "XRF", "EVL", "GOV", "OPS", "TSK", "VIZ", "NFR"], ks = d3.range(1, 15).concat([0]);
    const cnt = new Map();
    D.reqs.forEach((r) => { const kk = r.ks.length ? r.ks : [0]; kk.forEach((k) => { const key = r.group + "|" + k; cnt.set(key, (cnt.get(key) || []).concat([r])); }); });
    const W = 620, H = 440, m = { l: 56, r: 10, t: 40, b: 10 };
    const svg = svgIn(F.body, W, H, "要求×根拠マトリクス");
    const x = d3.scaleBand().domain(ks).range([m.l, W - m.r]).padding(0.06), y = d3.scaleBand().domain(groups).range([m.t, H - m.b]).padding(0.06);
    svg.append("g").call(axisStyle).attr("transform", `translate(0,${m.t - 4})`).call(d3.axisTop(x).tickSize(0).tickFormat((k) => (k ? "K" + k : "なし"))).select(".domain").remove();
    svg.append("g").call(axisStyle).attr("transform", `translate(${m.l - 4},0)`).call(d3.axisLeft(y).tickSize(0)).select(".domain").remove();
    const col = d3.scaleSequential(d3.interpolateBlues).domain([0, 8]);
    const cells = svg.append("g").selectAll("rect").data(groups.flatMap((gr) => ks.map((k) => ({ gr, k, rs: cnt.get(gr + "|" + k) || [] })))).join("rect")
      .attr("x", (d) => x(d.k)).attr("y", (d) => y(d.gr)).attr("width", x.bandwidth()).attr("height", y.bandwidth()).attr("rx", 4).attr("fill", (d) => (d.rs.length ? (d.k === 0 ? d3.interpolateOranges(0.3 + 0.1 * Math.min(d.rs.length, 6)) : col(d.rs.length + 1)) : "#121a33")).style("cursor", "pointer");
    svg.append("g").selectAll("text").data(groups.flatMap((gr) => ks.map((k) => ({ gr, k, rs: cnt.get(gr + "|" + k) || [] }))).filter((d) => d.rs.length)).join("text").attr("x", (d) => x(d.k) + x.bandwidth() / 2).attr("y", (d) => y(d.gr) + y.bandwidth() / 2 + 4).attr("text-anchor", "middle").attr("class", "lbl").style("fill", "#0b0f1a").text((d) => d.rs.length);
    const side = F.body.append("div").attr("class", "side").style("margin-top", "10px").html("<h4>セルをクリックすると、要求の一覧を表示します</h4>");
    tipOn(cells, (d) => (d.rs.length ? `${d.gr} × ${d.k ? "K" + d.k : "K番号なし"}: <b>${d.rs.length}件</b>` : "該当なし"));
    cells.on("click", (ev, d) => {
      side.html(`<h4>${d.gr} × ${d.k ? "K" + d.k : "K番号なし"}(${d.rs.length}件)</h4>` + (d.rs.length ? "" : "<div>該当なし</div>") + d.rs.map((r) => `<div><b>${r.id}</b> [${r.prio}] ${r.strength} — ${r.title}</div>`).join(""));
    });
    // 優先度×根拠の強さ
    const B = figure(g, { title: "優先度別: 根拠の強さ(機能要求)", source: "work/prd.md の根拠欄(判定規則は下記)", conf: "(機械判定。目安)", n: `${D.reqs.filter((r) => r.group !== "NFR").length}件`,
      note: "判定規則: 根拠欄に ❓ があれば❓、✅ または K番号があれば✅、📄があれば📄、それ以外は⚠。<b>M(必須)で⚠のみの要求は、PoCの裏付けがない</b>(設計原則に基づく)ことを意味し、Phase 0 で検証するか、根拠を補う対象です。" });
    const prios = ["M", "S", "C", "❓"], strs = ["✅", "📄", "⚠", "❓"], colr = { "✅": C.green, "📄": C.cyan, "⚠": C.amber, "❓": C.red };
    const rows = prios.map((p) => { const o = { prio: p }; strs.forEach((st) => (o[st] = D.strength.filter((x) => x.prio === p && x.strength === st).reduce((a, b) => a + b.n, 0))); o.total = strs.reduce((a, st) => a + o[st], 0); return o; }).filter((o) => o.total);
    const W2 = 620, H2 = 330, m2 = { l: 60, r: 40, t: 18, b: 30 };
    const sv = svgIn(B.body, W2, H2, "優先度別の根拠の強さ");
    const xx = d3.scaleLinear().domain([0, d3.max(rows, (o) => o.total)]).range([m2.l, W2 - m2.r]), yy = d3.scaleBand().domain(rows.map((o) => o.prio)).range([m2.t, H2 - m2.b]).padding(0.3);
    sv.append("g").call(axisStyle).attr("transform", `translate(0,${H2 - m2.b})`).call(d3.axisBottom(xx).ticks(5));
    sv.append("g").call(axisStyle).attr("transform", `translate(${m2.l},0)`).call(d3.axisLeft(yy).tickFormat((p) => ({ M: "Must", S: "Should", C: "Could", "❓": "未確認" }[p])));
    const stack = d3.stack().keys(strs)(rows);
    sv.append("g").selectAll("g").data(stack).join("g").attr("fill", (d) => colr[d.key]).selectAll("rect").data((d) => d.map((v) => ({ v, key: d.key }))).join("rect")
      .attr("x", (d) => xx(d.v[0])).attr("y", (d) => yy(d.v.data.prio)).attr("width", (d) => xx(d.v[1]) - xx(d.v[0])).attr("height", yy.bandwidth()).attr("rx", 3)
      .call((sel) => tipOn(sel, (d) => `${d.v.data.prio}: ${d.key} <b>${d.v.data[d.key]}件</b>`));
    sv.append("g").selectAll("text").data(stack.flatMap((d) => d.map((v) => ({ v, key: d.key })).filter((x) => x.v[1] - x.v[0] > 0))).join("text").attr("x", (d) => (xx(d.v[0]) + xx(d.v[1])) / 2).attr("y", (d) => yy(d.v.data.prio) + yy.bandwidth() / 2 + 4).attr("text-anchor", "middle").attr("class", "lbl").style("fill", "#0b0f1a").text((d) => d.v[1] - d.v[0]);
    strs.forEach((st, i) => { sv.append("rect").attr("x", m2.l + i * 90).attr("y", 0).attr("width", 10).attr("height", 10).attr("fill", colr[st]); sv.append("text").attr("x", m2.l + i * 90 + 14).attr("y", 9).attr("class", "lbl").text(st); });
    const mw = D.reqs.filter((r) => r.group !== "NFR" && r.prio === "M" && r.strength === "⚠");
    B.body.append("div").attr("class", "side").style("margin-top", "10px").html(`<h4>Must で、根拠が ⚠ のみ: ${mw.length}件</h4>` + mw.map((r) => `<div><b>${r.id}</b> — ${r.title.slice(0, 56)}</div>`).join(""));
    tableToggle(B.fig, D.reqs.map((r) => ({ ID: r.id, 優先: r.prio, 根拠の強さ: r.strength, フェーズ: r.phase ?? "-", K: r.ks.join("/") })), "prd_requirements");
  }

  // ---------- タスク (FR-TSK / FR-VIZ-09) ----------
  function taskSection() {
    const s = section("tasks", "15章 ・ FR-TSK ・ FR-VIZ-09", "タスク: 依存関係・進捗・要求の網羅",
      "PRD 15章のタスク表を読み取り、依存関係(DAG)と進捗を描画します。<strong>ノードをクリックして状態を変更</strong>できます(ブラウザに保存。PRDの表は自動では更新されません)。");
    const ST = ["未着手", "進行中", "ブロック", "完了"], stCol = { 未着手: "#2a3558", 進行中: "#1f6fb2", ブロック: "#a63a52", 完了: "#2a8a57" };
    const saved = JSON.parse(localStorage.getItem("prd.taskstatus") || "{}");
    const tasks = D.tasks.map((t) => ({ ...t, status: saved[t.id] || t.status }));
    const byId = new Map(tasks.map((t) => [t.id, t]));
    const F = figure(s, { title: "タスクの依存関係(左→右が依存の方向)", source: "work/prd.md 15章の表(ビルド時に解析・検査)", conf: "(PRDの表から生成)", n: `${tasks.length}タスク`,
      note: "担当は仮置き。期間・工数は資料に情報がなく未記載(最長の依存連鎖は<b>件数</b>であり、日数ではない)。<b>点線の枠</b>=依存が未完了で着手できない / <b>黄色の枠</b>=着手可能。ホイールで拡大・ドラッグで移動。" });
    const stats = F.ctrl.append("div").attr("class", "stats").style("width", "100%");
    const mkS = (cls, l) => { const e = stats.append("div").attr("class", "stat " + cls); e.append("div").attr("class", "v"); e.append("div").attr("class", "l").text(l); return e.select(".v"); };
    const vTot = mkS("", "タスク"), vDone = mkS("good", "完了"), vReady = mkS("warn", "着手可能"), vChain = mkS("", "最長の依存連鎖(件)");
    const btnE = F.ctrl.append("button").text("状態をMarkdownで書き出す"), btnR = F.ctrl.append("button").text("保存した状態をリセット");
    const NW = 176, NH = 38, CX = 206, RY = 46;
    const phases = [0, 1, 2, 3], depthMax = d3.max(tasks, (t) => t.depth);
    const cellsN = d3.rollup(tasks, (v) => v.length, (t) => t.phase, (t) => t.depth);
    const bandH = phases.map((p) => Math.max(1, d3.max(Array.from((cellsN.get(p) || new Map()).values()), (x) => x) || 1) * RY + 38);
    const bandY = bandH.reduce((a, h, i) => (a.push((a[i - 1] ?? 0) + (i ? bandH[i - 1] : 0)), a), []).map((_, i) => d3.sum(bandH.slice(0, i)));
    const slot = new Map();
    phases.forEach((p) => { const per = new Map(); tasks.filter((t) => t.phase === p).forEach((t) => { const k = per.get(t.depth) || 0; per.set(t.depth, k + 1); slot.set(t.id, k); }); });
    tasks.forEach((t) => { t.x = 120 + (t.depth - 1) * CX; t.y = bandY[t.phase] + 30 + slot.get(t.id) * RY; });
    const W = 120 + depthMax * CX + 40, H = d3.sum(bandH) + 10;
    const svg = svgIn(F.body, W, H, "タスクの依存関係(DAG)").style("max-height", "78vh").style("cursor", "grab");
    const root = svg.append("g");
    svg.call(d3.zoom().scaleExtent([0.4, 3]).on("zoom", (ev) => root.attr("transform", ev.transform)));
    phases.forEach((p) => {
      root.append("rect").attr("x", 0).attr("y", bandY[p]).attr("width", W).attr("height", bandH[p] - 6).attr("rx", 14).attr("fill", p % 2 ? "#0e1530" : "#101a38").attr("opacity", 0.9);
      root.append("text").attr("x", 14).attr("y", bandY[p] + 22).attr("class", "lbl").style("font-size", "14px").style("fill", C.cyan).text("Phase " + p);
    });
    const links = tasks.flatMap((t) => t.deps.map((d) => ({ s: byId.get(d), t })));
    const lk = d3.linkHorizontal().x((p) => p[0]).y((p) => p[1]);
    const path = root.append("g").attr("fill", "none").selectAll("path").data(links).join("path").attr("d", (l) => lk({ source: [l.s.x + NW, l.s.y + NH / 2], target: [l.t.x, l.t.y + NH / 2] })).attr("stroke", "#3b4a80").attr("stroke-width", 1.3).attr("opacity", 0.6);
    const nd = root.append("g").selectAll("g").data(tasks).join("g").attr("transform", (t) => `translate(${t.x},${t.y})`).style("cursor", "pointer");
    nd.append("rect").attr("width", NW).attr("height", NH).attr("rx", 9);
    nd.append("text").attr("x", 8).attr("y", 15).attr("class", "lbl").style("font-weight", 700).text((t) => t.id);
    nd.append("text").attr("x", 8).attr("y", 30).attr("class", "lbl m").style("font-size", "10.5px").text((t) => (t.title.length > 21 ? t.title.slice(0, 20) + "…" : t.title));
    const side = F.body.append("div").attr("class", "side").style("margin-top", "10px").html("<h4>ノードをクリックすると詳細を表示します</h4>");
    const waiting = (t) => t.status !== "完了" && t.deps.some((d) => byId.get(d).status !== "完了");
    const ready = (t) => t.status === "未着手" && !waiting(t);
    function paint() {
      nd.select("rect").attr("fill", (t) => stCol[t.status]).attr("stroke", (t) => (ready(t) ? C.amber : waiting(t) ? "#8a96b2" : "#0000")).attr("stroke-width", (t) => (ready(t) ? 2.6 : 1.4)).attr("stroke-dasharray", (t) => (waiting(t) ? "4 3" : null)).attr("opacity", (t) => (waiting(t) && t.status === "未着手" ? 0.75 : 1));
      vTot.text(tasks.length); vDone.text(tasks.filter((t) => t.status === "完了").length + " / " + tasks.length); vReady.text(tasks.filter(ready).length); vChain.text(depthMax);
      prog.selectAll("rect.done").data(phases).attr("width", (p) => 300 * (tasks.filter((t) => t.phase === p && t.status === "完了").length / tasks.filter((t) => t.phase === p).length));
      prog.selectAll("text.pc").data(phases).text((p) => `${tasks.filter((t) => t.phase === p && t.status === "完了").length} / ${tasks.filter((t) => t.phase === p).length}`);
    }
    nd.on("click", (ev, t) => {
      const dep = t.deps.map((d) => `${d}(${byId.get(d).status})`).join(", ") || "なし";
      side.html(`<h4>${t.id}: ${t.title}</h4><div>Phase ${t.phase} / 担当(仮) ${t.owner}</div><div>依存: ${dep}</div><div>完了条件: ${t.done}</div><div>関連: ${t.related}</div><div class="st">状態: </div>`);
      side.select(".st").selectAll("button").data(ST).join("button").attr("class", (s2) => (s2 === t.status ? "on btn" : "btn")).text((s2) => s2).on("click", (e2, s2) => {
        t.status = s2; const sv = JSON.parse(localStorage.getItem("prd.taskstatus") || "{}"); sv[t.id] = s2; localStorage.setItem("prd.taskstatus", JSON.stringify(sv)); paint(); nd.filter((x) => x === t).dispatch("click");
      });
    });
    tipOn(nd, (t) => `<b>${t.id}</b> ${t.title}<br>${t.status}${waiting(t) ? "(依存が未完了)" : ready(t) ? "(着手可能)" : ""}`);
    tipOn(path, (l) => `${l.s.id} → ${l.t.id}`);
    // 進捗バー
    const prog = F.body.append("svg").attr("viewBox", "0 0 1000 120").style("margin-top", "8px").append("g");
    phases.forEach((p, i) => {
      prog.append("text").attr("x", 10).attr("y", 20 + i * 26).attr("class", "lbl").text("Phase " + p);
      prog.append("rect").attr("x", 80).attr("y", 8 + i * 26).attr("width", 300).attr("height", 16).attr("rx", 8).attr("fill", "#1a2340");
      prog.append("rect").attr("class", "done").attr("x", 80).attr("y", 8 + i * 26).attr("height", 16).attr("rx", 8).attr("fill", C.green).attr("width", 0);
      prog.append("text").attr("class", "pc lbl").attr("x", 392).attr("y", 20 + i * 26);
    });
    prog.selectAll("rect.done").data(phases); prog.selectAll("text.pc").data(phases);
    paint();
    btnE.on("click", () => {
      const md = "| ID | タスク | Phase | 担当(仮) | 依存 | 状態 |\n|---|---|---|---|---|---|\n" + tasks.map((t) => `| ${t.id} | ${t.title} | ${t.phase} | ${t.owner} | ${t.deps.join(", ") || "-"} | ${t.status} |`).join("\n");
      const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([md], { type: "text/markdown" })); a.download = "prd_tasks_status.md"; a.click();
    });
    btnR.on("click", () => { localStorage.removeItem("prd.taskstatus"); tasks.forEach((t, i) => (t.status = D.tasks[i].status)); paint(); side.html("<h4>リセットしました</h4>"); });
    // 検査
    const K = figure(s, { title: "ビルド時の整合性検査(PoC結果の再現・タスク表の検査)", source: "PoC/build_prd_visuals.py", conf: `${D.checks.filter((c) => c.ok).length} / ${D.checks.length} 件OK`, note: "図の数値がPoCの報告値と一致すること、タスクの依存が非循環であること、フェーズに割り当てた要求がタスクで網羅されていることを、生成のたびに検査します。" });
    const ck = K.body.append("div").attr("class", "checks");
    D.checks.forEach((c) => ck.append("div").html(`<span class="${c.ok ? "ok" : "ng"}">${c.ok ? "✔" : "✘"}</span> ${c.name}${c.detail ? " <small>(" + String(c.detail).slice(0, 60) + ")</small>" : ""}`));
    if (D.unassigned.length) K.body.append("div").attr("class", "note").style("margin-top", "8px").html("フェーズ未割当の要求(PRD 15章の注記どおり): <b>" + D.unassigned.join(", ") + "</b>");
  }

  // ---------- 組み立て ----------
  heroGraph();
  thresholdSection(); queueSection(); blockingSection(); normSection(); evalSection(); scaleSection(); evidenceSection(); taskSection();
  const nav = d3.select("#nav");
  nav.append("a").attr("href", "#hero").text("クラスタ");
  navItems.forEach(([id, t]) => nav.append("a").attr("href", "#" + id).text(t));
  const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) e.target.classList.add("in"); }), { threshold: 0.05 });
  document.querySelectorAll(".sec").forEach((e) => io.observe(e));
  const links = Array.from(document.querySelectorAll("nav a"));
  const io2 = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) links.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + e.target.id)); }), { rootMargin: "-40% 0px -55% 0px" });
  document.querySelectorAll("section").forEach((e) => io2.observe(e));
})();





