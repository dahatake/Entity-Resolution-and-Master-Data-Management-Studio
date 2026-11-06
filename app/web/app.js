"use strict";
// データは必ず textContent / setAttribute 経由で描画する(innerHTML にデータを入れない)。
const $app = document.getElementById("app");
let ME = null, STATUS = null, FLASH = null;

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else if (k === "value") e.value = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(9)) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
const fmt = (x, d = 3) => x == null ? "—" : (typeof x === "number" ? (Number.isInteger(x) ? x.toLocaleString() : x.toFixed(d)) : String(x));
const pct = x => x == null ? "—" : (x * 100).toFixed(1) + "%";
const can = p => ME && (ME.perms.includes("*") || ME.perms.includes(p));

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : {}, body: body !== undefined ? JSON.stringify(body) : undefined, credentials: "same-origin" });
  const t = await r.text();
  let j = null; try { j = JSON.parse(t); } catch (_) { j = t; }
  if (!r.ok) { const e = new Error(j && j.error || r.statusText); e.status = r.status; e.body = j; throw e; }
  return j;
}
const GET = p => api("GET", p), POST = (p, b) => api("POST", p, b ?? {}), PUT = (p, b) => api("PUT", p, b ?? {});

function msg(box, text, ok) { box.replaceChildren(h("div", { class: "msg " + (ok ? "ok" : "err") }, text)); }
async function act(box, fn, okText) {
  try { const r = await fn(); if (okText) { msg(box, okText, true); if (!FLASH) FLASH = { text: okText, ok: true }; } return r; }
  catch (e) { msg(box, e.message + (e.body && e.body.violations ? "" : ""), false); }
}

let tipEl;
function tip(ev, text) {
  if (!tipEl) { tipEl = h("div", { class: "tip" }); document.body.append(tipEl); }
  if (text == null) { tipEl.style.display = "none"; return; }
  tipEl.textContent = text; tipEl.style.display = "block";
  tipEl.style.left = Math.min(ev.clientX + 12, innerWidth - 340) + "px"; tipEl.style.top = ev.clientY + 12 + "px";
}

function table(cols, rows, onclick) {
  return h("table", {}, h("thead", {}, h("tr", {}, cols.map(c => h("th", {}, c.t)))),
    h("tbody", {}, rows.map(r => h("tr", { class: onclick ? "click" : "", onclick: onclick ? () => onclick(r) : null }, cols.map(c => h("td", {}, c.f ? c.f(r) : fmt(r[c.k])))))));
}
function kpi(label, value, sub) { return h("div", { class: "kpi" }, h("b", {}, value), h("span", {}, label + (sub ? "(" + sub + ")" : ""))); }
function note(source, n, conf) { return h("div", { class: "src" }, `出典: ${source} / n=${n} / 確度: ${conf}`); }
function csvButton(name, rows) {
  return h("button", { class: "sec", onclick: () => {
    const cols = Object.keys(rows[0] || {});
    const q = v => '"' + String(v ?? "").replaceAll('"', '""') + '"';
    const txt = "\ufeff" + [cols.map(q).join(","), ...rows.map(r => cols.map(c => q(typeof r[c] === "object" ? JSON.stringify(r[c]) : r[c])).join(","))].join("\n");
    const a = h("a", { href: URL.createObjectURL(new Blob([txt], { type: "text/csv" })), download: name }); document.body.append(a); a.click(); a.remove();
  } }, "CSV");
}

// ---------------------------------------------------------------- チャート(d3)
function lineChart(series, { w = 640, hh = 260, xlabel = "", ylabel = "", ymax = 1, vlines = [], xmax = 1 } = {}) {
  const m = { l: 44, r: 12, t: 10, b: 34 };
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${hh}`).attr("class", "chart");
  const x = d3.scaleLinear([0, xmax], [m.l, w - m.r]), y = d3.scaleLinear([0, ymax], [hh - m.b, m.t]);
  svg.append("g").attr("transform", `translate(0,${hh - m.b})`).call(d3.axisBottom(x).ticks(8)).selectAll("text").style("fill", "#93a0ba");
  svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5)).selectAll("text").style("fill", "#93a0ba");
  svg.selectAll(".domain,.tick line").attr("stroke", "#2b3750");
  svg.append("text").attr("x", w / 2).attr("y", hh - 4).attr("text-anchor", "middle").text(xlabel);
  svg.append("text").attr("x", 12).attr("y", 12).text(ylabel);
  for (const v of vlines) svg.append("line").attr("x1", x(v.x)).attr("x2", x(v.x)).attr("y1", m.t).attr("y2", hh - m.b).attr("stroke", v.color || "#f2b84b").attr("stroke-dasharray", "4 3");
  series.forEach((s, i) => {
    const pts = s.pts.filter(p => p[1] != null);
    svg.append("path").datum(pts).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", 2).attr("stroke-dasharray", s.dash || null).attr("d", d3.line().x(d => x(d[0])).y(d => y(d[1])));
    svg.append("g").selectAll("circle").data(pts).join("circle").attr("cx", d => x(d[0])).attr("cy", d => y(d[1])).attr("r", 3.5).attr("fill", s.color)
      .on("mousemove", (ev, d) => tip(ev, `${s.name}: 閾値 ${d[0]} → ${d[1].toFixed(3)}`)).on("mouseleave", () => tip(null, null));
    svg.append("text").attr("x", m.l + 8 + i * 150).attr("y", m.t + 10).style("fill", s.color).text("● " + s.name);
  });
  return svg.node();
}

function clusterGraph(members, edges, thr, cons, onSelect) {
  const w = 620, hh = 420;
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${hh}`).attr("class", "chart");
  const nodes = members.map(m => ({ id: m.rid, m }));
  const links = edges.map(e => ({ source: e.a, target: e.b, p: e.p }));
  const cannot = new Set(cons.filter(c => c.kind === "cannot").map(c => c.a + "-" + c.b));
  const col = d3.scaleLinear([0, thr.review_low, thr.auto, 1], ["#ff6b6b", "#f2b84b", "#5ab0ff", "#4cd08a"]).clamp(true);
  const sim = d3.forceSimulation(nodes).force("link", d3.forceLink(links).id(d => d.id).distance(d => 40 + (1 - d.p) * 90).strength(d => 0.2 + d.p * 0.6))
    .force("charge", d3.forceManyBody().strength(-160)).force("center", d3.forceCenter(w / 2, hh / 2)).force("x", d3.forceX(w / 2).strength(0.04)).force("y", d3.forceY(hh / 2).strength(0.04));
  const lk = svg.append("g").selectAll("line").data(links).join("line").attr("stroke", d => col(d.p)).attr("stroke-width", d => 1 + d.p * 3)
    .attr("stroke-dasharray", d => d.p < thr.review_low ? "4 3" : null)
    .on("mousemove", (ev, d) => tip(ev, `p=${d.p.toFixed(3)}${d.p < thr.review_low ? "(下限未満。他の経路でつながっている)" : ""}`)).on("mouseleave", () => tip(null, null));
  const picked = new Set();
  const nd = svg.append("g").selectAll("g").data(nodes).join("g").style("cursor", "pointer").call(d3.drag()
    .on("start", (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
    .on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; }).on("end", (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
  nd.append("circle").attr("r", 9).attr("fill", "#1d2639").attr("stroke", "#5ab0ff").attr("stroke-width", 2)
    .on("click", function (ev, d) { if (picked.has(d.id)) picked.delete(d.id); else picked.add(d.id); d3.select(this).attr("fill", picked.has(d.id) ? "#b99cff" : "#1d2639"); onSelect([...picked]); })
    .on("mousemove", (ev, d) => tip(ev, `rid ${d.id} / ${d.m.source}:${d.m.key}`)).on("mouseleave", () => tip(null, null));
  nd.append("text").attr("x", 12).attr("y", 4).text(d => d.m.label || d.id).style("fill", "#e6ebf5").style("font-size", "10px");
  sim.on("tick", () => { lk.attr("x1", d => d.source.x).attr("y1", d => d.source.y).attr("x2", d => d.target.x).attr("y2", d => d.target.y); nd.attr("transform", d => `translate(${d.x},${d.y})`); });
  return svg.node();
}

function barChart(data, { w = 560, bh = 22, color = "#5ab0ff", fmtv = fmt } = {}) {
  const m = { l: 140, r: 60 };
  const hh = data.length * (bh + 6) + 6;
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${hh}`).attr("class", "chart");
  const x = d3.scaleLinear([0, d3.max(data, d => d.v) || 1], [0, w - m.l - m.r]);
  data.forEach((d, i) => {
    const y = 6 + i * (bh + 6);
    svg.append("text").attr("x", m.l - 6).attr("y", y + bh / 2 + 4).attr("text-anchor", "end").text(d.k);
    svg.append("rect").attr("x", m.l).attr("y", y).attr("width", Math.max(1, x(d.v))).attr("height", bh).attr("fill", d.color || color).attr("rx", 3);
    svg.append("text").attr("x", m.l + x(d.v) + 6).attr("y", y + bh / 2 + 4).text(fmtv(d.v));
  });
  return svg.node();
}

// ---------------------------------------------------------------- 画面枠
const PAGES = [
  ["dashboard", "ダッシュボード", "read"], ["sources", "ソース", "read"], ["config", "設定", "read"], ["runs", "実行", "read"],
  ["entities", "エンティティ", "read"], ["review", "確認キュー", "read"], ["eval", "評価", "read"], ["labels", "ラベル付け", "label"],
  ["gov", "ガバナンス", "read"], ["tasks", "タスク", "read"],
];
const view = { cur: "dashboard", arg: null };

async function go(page, arg) {
  view.cur = page; view.arg = arg ?? null; FLASH = null;
  location.hash = "#" + page + (arg != null ? "/" + arg : "");
  await render();
}
async function render() {
  if (!ME) return renderLogin();
  try { STATUS = await GET("/api/status"); } catch (e) { if (e.status === 401) { ME = null; return renderLogin(); } }
  const body = h("main", {}, h("div", { class: "msg err", style: "display:none" }));
  $app.replaceChildren(h("nav", {}, h("h1", {}, "ER/MDM Studio"), h("div", { class: "who" }, "Phase 1 ・ ローカル専用"),
    PAGES.filter(p => can(p[2])).map(p => h("a", { class: p[0] === view.cur ? "on" : "", onclick: () => go(p[0]) }, p[1])),
    h("div", { class: "who" }, `${ME.display || ME.username}`, h("br"), ME.role_labels.join("・"), h("br"), h("a", { onclick: async () => { await POST("/api/logout"); ME = null; render(); } }, "ログアウト"))), body);
  const fn = PAGE_FN[view.cur];
  try {
    const kids = await fn(view.arg);
    if (FLASH) { kids.unshift(h("div", { class: "msg " + (FLASH.ok ? "ok" : "err") }, FLASH.text)); FLASH = null; }
    body.replaceChildren(...kids);
  }
  catch (e) { body.replaceChildren(h("div", { class: "msg err" }, "表示に失敗しました: " + e.message)); }
}

function renderLogin() {
  const box = h("div"); const u = h("input", { placeholder: "ユーザー名", autocomplete: "username" }), p = h("input", { type: "password", placeholder: "パスワード", autocomplete: "current-password" });
  const doit = async () => { try { const r = await POST("/api/login", { username: u.value, password: p.value }); ME = r.user; go("dashboard"); } catch (e) { msg(box, e.message, false); } };
  p.addEventListener("keydown", e => { if (e.key === "Enter") doit(); });
  $app.replaceChildren(h("div", { class: "card login" }, h("h2", {}, "ER/MDM Studio"), h("div", { class: "sub" }, "名寄せ・マスターデータ管理(Phase 1・ローカル)"), u, p, h("button", { onclick: doit }, "ログイン"), box));
}

// ---------------------------------------------------------------- ダッシュボード
const PAGE_FN = {};
PAGE_FN.dashboard = async () => {
  const s = STATUS, out = [h("h2", {}, "ダッシュボード"), h("div", { class: "sub" }, "現状と、まだ決まっていないこと")];
  if (s.phase0_open.length) out.push(h("div", { class: "banner" }, h("b", {}, "Phase 0 の未決事項(仮の既定値のまま運用しないでください)"), h("ul", {}, s.phase0_open.map(x => h("li", {}, x)))));
  if (!s.audit.ok) out.push(h("div", { class: "banner bad" }, `監査ログの連鎖が seq=${s.audit.first_bad} で壊れています(改ざんの疑い)`));
  if (s.governance.violations.length) out.push(h("div", { class: "banner bad" }, h("b", {}, "ガバナンス違反: 実行できません"), h("ul", {}, s.governance.violations.map(x => h("li", {}, x.message)))));
  out.push(h("div", { class: "grid" }, kpi("有効レコード", fmt(s.records.active), `削除 ${s.records.deleted}`), kpi("ソース", s.sources), kpi("エンティティ", fmt(s.entities), `複数件 ${s.multi_entities}`),
    kpi("確認待ち", s.review_open, `期限超過 ${s.review_overdue}`), kpi("未完了タスク", s.tasks_open), kpi("制約", s.constraints.cannot + s.constraints.must, `分離 ${s.constraints.cannot} / 結合 ${s.constraints.must}`),
    kpi("監査ログ", fmt(s.audit.entries), s.audit.ok ? "連鎖OK" : "破損")));
  const lr = s.last_done_run;
  if (!lr) {
    const box = h("div");
    if (s.sources > 0) {
      out.push(h("div", { class: "card" }, h("h3", {}, "次にやること"), `ソースは ${s.sources} 件、レコードは ${fmt(s.records.active)} 件あります。まだ実行していません。`,
        h("div", { class: "src" }, "設定画面で「標準化」「ブロッキング」「モデル(学習して有効化)」が済んでいることを確認し、"), h("div", { class: "row" }, h("button", { onclick: () => go("config") }, "設定へ"), h("button", { onclick: () => go("runs") }, "実行画面へ"))));
      return out;
    }
    out.push(h("div", { class: "card" }, h("h3", {}, "はじめに"), "ソースが未登録です。まず体験用のデモデータを読み込めます(合成データ。実データではありません)。",
      h("div", { class: "row" }, can("admin") ? ["persons", "companies"].map(n => h("button", { onclick: () => act(box, async () => { await POST("/api/demo", { name: n }); await POST("/api/runs", {}); await new Promise(r => setTimeout(r, 1500)); go("runs"); }, "読み込み中…") }, n === "persons" ? "人物デモ(1,000件)" : "企業デモ(4ソース)")) : "管理者に依頼してください"), box));
    return out;
  }
  const m = lr.metrics, b = m.bands || {};
  out.push(h("div", { class: "card" }, h("h3", {}, `直近の実行 #${lr.id}(${lr.ts})`),
    h("div", { class: "grid" }, kpi("候補ペア", fmt(m.n_candidates)), kpi("削減率", pct(m.reduction_ratio)), kpi("クラスタ(複数件)", fmt(m.n_multi_clusters)), kpi("最大クラスタ", fmt(m.largest_cluster)),
      kpi("要確認クラスタ", fmt(m.n_flagged_clusters), "下限未満の辺を含む")),
    h("h3", {}, "確率帯ごとのペア数"), barChart([{ k: "自動マージ", v: b.auto || 0, color: "#4cd08a" }, { k: "人手確認", v: b.review || 0, color: "#f2b84b" }, { k: "却下", v: b.discard || 0, color: "#ff6b6b" }]),
    note(`実行#${lr.id}`, fmt(m.n_candidates), "⚠ 閾値は仮の既定値"), h("div", { class: "src" }, "結果ハッシュ: " + (lr.result_hash || "").slice(0, 16)),
    m.gold ? h("div", { class: "src" }, `正解付き: PC=${pct(m.gold.pairs_completeness)}(候補に入った真の一致の割合。全体の再現率の上限)`) : null));
  return out;
};

// ---------------------------------------------------------------- ソース
PAGE_FN.sources = async () => {
  const srcs = await GET("/api/sources"), purposes = await GET("/api/purposes");
  const box = h("div"), out = [h("h2", {}, "ソース"), h("div", { class: "sub" }, "取り込み(CSV / SQLite)。個人番号らしい列は取り込み時に隔離し、保存しません。")];
  out.push(h("div", { class: "card" }, table([{ t: "名前", k: "name" }, { t: "キー列", k: "key_column" }, { t: "件数", f: r => `${r.records}(削除 ${r.deleted})` },
    { t: "個人", f: r => r.personal ? h("span", { class: "tag warn" }, r.pseudonymized ? "仮名加工" : "個人データ") : "—" }, { t: "利用目的", f: r => r.purpose_name || "—" },
    { t: "隔離列", f: r => r.quarantine.length ? h("span", { class: "tag bad" }, r.quarantine.join(", ")) : "—" },
    { t: "状態", f: r => r.pending_schema ? h("span", { class: "tag bad" }, "スキーマ変更の承認待ち") : h("span", { class: "tag ok" }, "OK") },
    { t: "", f: r => r.pending_schema && can("ingest") ? h("button", { onclick: () => act(box, async () => { await POST(`/api/sources/${r.id}/ack-schema`); render(); }) }, "承認") : null }], srcs), box));
  if (can("ingest")) {
    const name = h("input", { placeholder: "ソース名" }), key = h("input", { placeholder: "キー列名" }), truth = h("input", { placeholder: "正解列(任意・評価用)" });
    const pers = h("input", { type: "checkbox" }), pid = h("select", {}, h("option", { value: "" }, "(利用目的なし)"), purposes.filter(p => p.active).map(p => h("option", { value: p.id }, p.name)));
    out.push(h("div", { class: "card" }, h("h3", {}, "ソースを追加"), h("div", { class: "row" }, name, key, truth, h("label", {}, pers, " 個人データを含む"), pid,
      h("button", { onclick: () => act(box, async () => { await POST("/api/sources", { name: name.value, key_column: key.value, truth_column: truth.value || null, personal: pers.checked, purpose_id: pid.value ? +pid.value : null }); render(); }, "追加しました") }, "追加"))));
    const sel = h("select", {}, srcs.map(s => h("option", { value: s.id }, s.name))), mode = h("select", {}, h("option", { value: "snapshot" }, "スナップショット(無い行は削除扱い)"), h("option", { value: "delta" }, "差分"));
    const txt = h("textarea", { rows: 6, placeholder: "CSVを貼り付け(1行目が列名)。またはファイル選択" }), file = h("input", { type: "file", accept: ".csv" });
    file.addEventListener("change", async () => { const buf = await file.files[0].arrayBuffer(); let t; try { t = new TextDecoder("utf-8", { fatal: true }).decode(buf); } catch (_) { t = new TextDecoder("shift_jis").decode(buf); } txt.value = t; });
    out.push(h("div", { class: "card" }, h("h3", {}, "データを取り込む"), h("div", { class: "row" }, sel, mode, file),
      txt, h("div", { class: "row" }, h("button", { onclick: () => act(box, async () => { const r = await POST(`/api/sources/${sel.value}/load`, { csv_text: txt.value, mode: mode.value }); const q = Object.keys(r.quarantined || {}); FLASH = { text: `取り込み結果: 追加 ${r.insert} / 更新 ${r.update} / 削除 ${r.delete} / 変更なし ${r.same}${q.length ? " / 隔離した列: " + q.join(",") : ""}`, ok: true }; render(); }) }, "取り込む")),
      h("div", { class: "src" }, "同じ入力の再投入は何も変えません(冪等)。列が増減したときは停止し、承認が必要です。")));
  }
  return out;
};

// ---------------------------------------------------------------- 設定
PAGE_FN.config = async () => {
  const [st, pc, std, mods, settings] = await Promise.all([GET("/api/status"), GET("/api/pipeline"), GET("/api/std"), GET("/api/models"), GET("/api/settings")]);
  const box = h("div"), out = [h("h2", {}, "設定"), h("div", { class: "sub" }, "同一性の定義 → 標準化 → ブロッキング → モデル → 閾値。変更は版として残り、監査ログに記録されます。")];
  const idt = h("textarea", { rows: 3 }, settings.identity_definition ? settings.identity_definition.text : "");
  idt.value = settings.identity_definition ? settings.identity_definition.text : "";
  const cc = h("input", { type: "number", step: "0.5", value: settings.cost_ratio ? settings.cost_ratio.c : 5, style: "width:90px" });
  out.push(h("div", { class: "card" }, h("h3", {}, "同一性の定義とコスト比(業務側の承認事項)"),
    h("div", { class: "src" }, settings.identity_definition && settings.identity_definition.approved_by ? `承認済み(${settings.identity_definition.approved_by})` : "未承認"), idt,
    h("div", { class: "row" }, "誤統合1件のコスト ÷ 見逃し1件のコスト =", cc, settings.cost_ratio && settings.cost_ratio.approved_by ? h("span", { class: "tag ok" }, "承認済み") : h("span", { class: "tag warn" }, "仮の既定値"),
      can("settings") ? [h("button", { class: "sec", onclick: () => act(box, async () => { await PUT("/api/settings", { identity_definition: { text: idt.value }, cost_ratio: { c: +cc.value } }); render(); }, "保存(未承認)") }, "保存"),
        h("button", { onclick: () => act(box, async () => { await PUT("/api/settings", { identity_definition: { text: idt.value, approve: true }, cost_ratio: { c: +cc.value, approve: true } }); render(); }, "承認して保存しました") }, "承認として保存")] : null)));
  const stdTa = h("textarea", { rows: 14 }); stdTa.value = JSON.stringify(std.config, null, 1);
  const prev = h("div");
  out.push(h("div", { class: "card" }, h("h3", {}, `標準化(有効な版: ${std.active ?? "なし"})`), h("div", { class: "src" }, "操作: " + std.ops.join(", ")), stdTa,
    h("div", { class: "row" }, h("button", { class: "sec", onclick: () => act(prev, async () => {
        const rows = await POST("/api/std/preview", { config: JSON.parse(stdTa.value), limit: 8 });
        prev.replaceChildren(table([{ t: "元データ", f: r => JSON.stringify(r.raw) }, { t: "標準化後", f: r => JSON.stringify(r.std) }], rows));
      }) }, "プレビュー"), can("config") ? h("button", { onclick: () => act(box, async () => { const r = await POST("/api/std", { config: JSON.parse(stdTa.value) }); render(); return r; }, "保存しました(同じ内容なら同じ版)") }, "版として保存") : null,
      std.versions.slice(0, 5).map(v => h("span", { class: "tag " + (v.active ? "ok" : "") }, `v${v.version}`, !v.active && can("config") ? h("a", { onclick: () => act(box, async () => { await POST(`/api/std/${v.version}/activate`); render(); }) }, " 有効化") : "")) ), prev));
  const bt = h("textarea", { rows: 8 }); bt.value = JSON.stringify(pc, null, 1);
  const est = h("div");
  out.push(h("div", { class: "card" }, h("h3", {}, "ブロッキング・閾値・クラスタリング"), h("div", { class: "src" }, "ルールのキー変換: exact / prefix / suffix / year / ym。上限超過は実行前に停止します。"), bt,
    h("div", { class: "row" }, h("button", { class: "sec", onclick: () => act(est, async () => {
        const c = JSON.parse(bt.value), e = await POST("/api/blocking/estimate", { rules: c.blocking.rules, max_block: c.blocking.max_block, max_candidates: c.blocking.max_candidates, link_type: c.blocking.link_type });
        est.replaceChildren(h("div", { class: "msg " + (e.over_limit ? "err" : "ok") }, `候補ペア見積(上限): ${fmt(e.total_upper)} / 全ペア ${fmt(e.total_pairs)} / 削減率 ${pct(e.reduction_ratio_upper)}${e.over_limit ? " ― 上限超過: 実行は停止します" : ""}${e.gold ? ` / 正解付きPC ${pct(e.gold.pairs_completeness)}` : ""}`),
          barChart(e.rules.map(r => ({ k: r.rule, v: r.pairs_upper }))), table([{ t: "ルール", k: "rule" }, { t: "ブロック数", k: "blocks" }, { t: "最大ブロック", k: "largest_block" }, { t: "除外ブロック", k: "skipped_blocks" }], e.rules));
      }) }, "候補ペア数を見積る"), can("config") ? h("button", { onclick: () => act(box, async () => { await PUT("/api/pipeline", JSON.parse(bt.value)); render(); }, "保存しました") }, "保存") : null), est));
  const mt = h("textarea", { rows: 12 });
  const cur = mods.models.find(m => m.active) || mods.models[0];
  mt.value = cur ? JSON.stringify(cur.config, null, 1) : "";
  out.push(h("div", { class: "card" }, h("h3", {}, "モデル(Fellegi–Sunter / 決定的 / ハイブリッド)"), h("div", { class: "src" }, "比較関数: " + Object.entries(mods.comparators).map(([k, v]) => `${k}[${v.join("/")}]`).join("  ")),
    table([{ t: "ID", k: "id" }, { t: "名前", k: "name" }, { t: "方式", k: "matcher" }, { t: "学習", f: r => r.trained ? `済(λ=${r.params && r.params.lambda != null ? fmt(r.params.lambda) : "—"})` : "未" }, { t: "有効", f: r => r.active ? "●" : "" },
      { t: "", f: r => can("config") ? h("span", {}, h("button", { class: "sec", onclick: () => act(box, async () => { const x = await POST(`/api/models/${r.id}/train`); render(); return x; }, "学習しました") }, "学習"), " ", h("button", { class: "sec", onclick: () => act(box, async () => { await POST(`/api/models/${r.id}/activate`); render(); }) }, "有効化")) : null }], mods.models),
    mt, can("config") ? h("div", { class: "row" }, h("button", { onclick: () => act(box, async () => { await POST("/api/models", { name: "model-" + Date.now().toString(36), config: JSON.parse(mt.value) }); render(); }, "モデルを作成しました(学習してから有効化)") }, "新しいモデルとして保存")) : null, box));
  return out;
};

// ---------------------------------------------------------------- 実行
PAGE_FN.runs = async () => {
  const runs = await GET("/api/runs"), box = h("div"), out = [h("h2", {}, "実行"), h("div", { class: "sub" }, "入力・設定・モデルの版と段階ごとの時間を記録。同じ入力と設定なら結果ハッシュが一致します。")];
  if (can("run")) {
    const mode = h("select", {}, h("option", { value: "full" }, "全体を再計算"), h("option", { value: "recluster" }, "再クラスタのみ(ペアは再利用)"));
    out.push(h("div", { class: "card" }, h("div", { class: "row" }, mode, h("button", { onclick: () => act(box, async () => {
      const body = { mode: mode.value }; if (mode.value === "recluster" && runs.length) body.recluster_from = (runs.find(r => r.status === "done") || {}).id;
      const r = await POST("/api/runs", body);
      for (let i = 0; i < 120; i++) { await new Promise(x => setTimeout(x, 500)); const s = await GET("/api/runs/" + r.run_id); if (s.status !== "queued" && s.status !== "running") { if (s.status === "failed") throw new Error(s.error); break; } }
      render();
    }, "実行しました") }, "実行"), box)));
  }
  out.push(h("div", { class: "card" }, table([{ t: "#", k: "id" }, { t: "時刻", k: "ts" }, { t: "実行者", k: "actor" }, { t: "モード", k: "mode" },
    { t: "状態", f: r => h("span", { class: "tag " + (r.status === "done" ? "ok" : r.status === "failed" ? "bad" : "warn") }, r.status) },
    { t: "候補", f: r => fmt(r.metrics && r.metrics.n_candidates) }, { t: "クラスタ(複数)", f: r => fmt(r.metrics && r.metrics.n_multi_clusters) }, { t: "最大", f: r => fmt(r.metrics && r.metrics.largest_cluster) },
    { t: "結果ハッシュ", f: r => h("span", { class: "mono" }, (r.result_hash || "").slice(0, 12)) }, { t: "所要(秒)", f: r => r.metrics && r.metrics.timings ? fmt(Object.values(r.metrics.timings).reduce((a, b) => a + b, 0), 2) : "—" }], runs)));
  const done = runs.find(r => r.status === "done");
  if (done && done.metrics.timings) out.push(h("div", { class: "card" }, h("h3", {}, `実行 #${done.id} の段階別時間(秒)`), barChart(Object.entries(done.metrics.timings).map(([k, v]) => ({ k, v })), { fmtv: v => v.toFixed(2) }), note(`実行#${done.id}`, fmt(done.metrics.n_records), "✅ 計測値")));
  return out;
};

// ---------------------------------------------------------------- エンティティ
PAGE_FN.entities = async (gid) => {
  if (gid) return entityDetail(+gid);
  const q = h("input", { placeholder: "キー・値で検索" }), ms = h("select", {}, h("option", { value: 1 }, "すべて"), h("option", { value: 2 }, "2件以上"), h("option", { value: 5 }, "5件以上")), fl = h("input", { type: "checkbox" });
  const list = h("div"), load = async () => {
    const r = await GET(`/api/entities?q=${encodeURIComponent(q.value)}&min_size=${ms.value}&flagged=${fl.checked ? 1 : 0}&size=50`);
    list.replaceChildren(h("div", { class: "src" }, `${fmt(r.total)} 件`), table([{ t: "グローバルID", k: "gid" }, { t: "件数", k: "size" }, { t: "代表", k: "label" }, { t: "内容", k: "preview" }, { t: "下限未満の辺", k: "rejected" }, { t: "最低p", f: r => fmt(r.min_p) },
      { t: "別人混在(正解)", f: r => r.impure_truth ? h("span", { class: "tag bad" }, "あり") : "" }], r.items, r => go("entities", r.gid)));
  };
  q.addEventListener("keydown", e => { if (e.key === "Enter") load(); }); ms.addEventListener("change", load); fl.addEventListener("change", load);
  await load();
  const hist = await GET("/api/entities?size=500&min_size=2");
  const sizes = d3.rollups(hist.items, v => v.length, d => Math.min(d.size, 10)).sort((a, b) => a[0] - b[0]);
  return [h("h2", {}, "エンティティ"), h("div", { class: "sub" }, "グローバルIDごとのクラスタ。選んで、根拠を見て、誤統合を分割できます。"),
    h("div", { class: "card" }, h("div", { class: "row" }, q, ms, h("label", {}, fl, " 要確認のみ"), h("button", { onclick: load }, "検索")), list),
    h("div", { class: "card" }, h("h3", {}, "クラスタの大きさの分布(2件以上)"), barChart(sizes.map(([k, v]) => ({ k: k >= 10 ? "10件以上" : k + "件", v }))), note("最新の実行", fmt(hist.total), "✅ 計測値(上位500クラスタ)"))];
};

async function entityDetail(gid) {
  const e = await GET("/api/entities/" + gid), box = h("div"), out = [h("a", { onclick: () => go("entities") }, "← 一覧へ"), h("h2", {}, `グローバルID ${gid}`)];
  if (e.status !== "active") return [...out, h("div", { class: "card" }, `このIDは ${e.status} です。` + (e.resolve.current ? ` 現在のID: ${e.resolve.current}` : ""), h("a", { onclick: () => go("entities", e.resolve.current) }, e.resolve.current ? " → 開く" : ""))];
  let picked = [];
  const why = h("input", { placeholder: "分割の理由(必須)", style: "min-width:260px" });
  const btn = h("button", { class: "bad", disabled: true, onclick: () => {
    act(box, async () => { const r = await POST(`/api/entities/${gid}/unmerge`, { parts: [picked], reason: why.value }); go("entities", r.new_ids[0]); }, "分割しました");
  } }, "選んだ点を別のIDに分割");
  const members = e.members;
  out.push(h("div", { class: "two" }, h("div", { class: "card" }, h("h3", {}, `つながり(${members.length}件 / 辺 ${e.edges.length})`),
    clusterGraph(members.map(m => ({ ...m, label: m.key })), e.edges, e.thresholds, e.constraints, p => { picked = p; btn.disabled = !(p.length && p.length < members.length); }),
    h("div", { class: "src" }, `点をクリックで選択、ドラッグで移動。辺の色=確率(赤→緑)。破線=確認下限(${e.thresholds.review_low})未満で、他の経路でつながっている辺。下限未満の辺: ${e.rejected_pairs}`),
    can("unmerge") ? h("div", { class: "row" }, why, btn) : null, box),
    h("div", { class: "card" }, h("h3", {}, "メンバー"), table([{ t: "rid", k: "rid" }, { t: "ソース", k: "source" }, { t: "キー", k: "key" }, { t: "標準化前の値", f: r => Object.values(r.raw || {}).join(" / ") }], members))));
  out.push(h("div", { class: "card" }, h("h3", {}, "履歴"), e.predecessor ? h("div", {}, `前身ID: ${e.predecessor}(${e.note || ""})`) : null,
    table([{ t: "seq", k: "seq" }, { t: "時刻", k: "ts" }, { t: "種別", k: "type" }, { t: "実行者", k: "actor" }, { t: "詳細", f: r => JSON.stringify(r.detail).slice(0, 120) }], e.events),
    e.constraints.length ? [h("h3", {}, "制約"), table([{ t: "a", k: "a" }, { t: "b", k: "b" }, { t: "種別", k: "kind" }, { t: "由来", k: "origin" }, { t: "理由", k: "reason" }, { t: "実行者", k: "actor" }], e.constraints)] : null));
  return out;
}

// ---------------------------------------------------------------- 確認キュー
function pairView(d) {
  const ex = d.explanation, recs = d.records;
  const rows = (ex && ex.fields) || [];
  return h("div", {}, h("div", { class: "two" }, recs.map(r => h("div", { class: "card" }, h("b", {}, `${r.source}:${r.key}`), h("div", { class: "src" }, `rid ${r.rid}`),
      h("table", {}, Object.entries(r.std || {}).map(([k, v]) => h("tr", {}, h("th", {}, k), h("td", { class: rows.find(x => x.field === k && (x.level || "").includes("不一致")) ? "diff" : "" }, v))))))),
    ex ? h("div", { class: "card" }, h("h3", {}, `根拠: p=${fmt(ex.p)}(事前 λ=${fmt(ex.lambda)})`), barChart(rows.map(f => ({ k: f.field + " " + f.level, v: Math.abs(f.bits || 0), color: (f.bits || 0) >= 0 ? "#4cd08a" : "#ff6b6b" })), { fmtv: v => v.toFixed(2) + " bit" }),
      h("div", { class: "src" }, "緑=一致の証拠、赤=不一致の証拠(棒の長さ=寄与の大きさ)。ブロッキングルール: " + (d.blocking_rules || []).join(", "))) : null);
}
PAGE_FN.review = async (id) => {
  if (id) {
    const d = await GET("/api/review/" + id), box = h("div"), reason = h("input", { placeholder: "判定の理由(必須)", style: "min-width:320px" });
    const dec = k => () => act(box, async () => { await POST(`/api/review/${id}/decide`, { decision: k, reason: reason.value }); go("review"); });
    return [h("a", { onclick: () => go("review") }, "← キューへ"), h("h2", {}, `確認 #${id}`), pairView(d), can("review") && d.item.status === "open" ?
      h("div", { class: "card" }, h("div", { class: "row" }, reason, h("button", { onclick: dec("match") }, "同一(結合を固定)"), h("button", { class: "bad", onclick: dec("nonmatch") }, "別(分離を固定)"), h("button", { class: "sec", onclick: dec("unsure") }, "保留")), box,
        h("div", { class: "src" }, "判定は制約として保存され、再実行でも覆りません。")) : h("div", { class: "card" }, `状態: ${d.item.status} ${d.item.decision || ""} ${d.item.reason || ""}`)];
  }
  const [list, est] = await Promise.all([GET("/api/review?status=open&size=100"), GET("/api/review/estimate").catch(() => null)]);
  const out = [h("h2", {}, "確認キュー"), h("div", { class: "sub" }, "自動マージ閾値と確認下限の間のペアを人が判定します。")];
  if (est) out.push(h("div", { class: "card" }, h("h3", {}, "閾値の見積(決める前に量を知る)"),
    h("div", { class: "grid" }, kpi("自動マージ", fmt(est.current.auto_pairs)), kpi("確認帯", fmt(est.current.review_pairs)), kpi("未決", fmt(est.current.open_estimate)), kpi("千件あたり", fmt(est.current.per_1000_records, 1)), kpi("所要(時間)", fmt(est.current.hours_estimate, 1))),
    h("div", { class: "src" }, est.current.assumption + "。" + est.note),
    h("details", {}, h("summary", {}, "閾値の組合せ表"), table([{ t: "自動≥", k: "hi" }, { t: "下限≥", k: "lo" }, { t: "自動", k: "auto_pairs" }, { t: "確認帯", k: "review_pairs" }, { t: "時間", f: r => fmt(r.hours_estimate, 1) }], est.grid))));
  out.push(h("div", { class: "card" }, h("div", { class: "src" }, `未決 ${list.total} 件`), table([{ t: "#", k: "id" }, { t: "タスク", k: "task_id" }, { t: "a", k: "a" }, { t: "b", k: "b" }, { t: "p", f: r => fmt(r.p) }, { t: "担当", f: r => r.owner || "—" },
    { t: "期限", f: r => h("span", { class: r.overdue ? "tag bad" : "" }, (r.due || "").slice(0, 16)) }], list.items, r => go("review", r.id))));
  return out;
};

// ---------------------------------------------------------------- 評価
PAGE_FN.eval = async () => {
  const c = STATUS.settings.cost_ratio ? STATUS.settings.cost_ratio.c : 5;
  const ev = await GET("/api/eval/summary?c=" + c).catch(e => ({ error: e.message }));
  if (ev.error) return [h("h2", {}, "評価"), h("div", { class: "msg err" }, ev.error)];
  const out = [h("h2", {}, "評価"), h("div", { class: "sub" }, "ペア単位とクラスタ単位を分けて示します。Accuracy 単独は表示しません。"), h("div", { class: "card" }, h("b", {}, "指標の定義"), h("ul", {}, Object.entries(ev.definitions).map(([k, v]) => h("li", {}, `${k}: ${v}`))))];
  const slider = h("input", { type: "range", min: 0, max: 2, step: 0.05, value: Math.log10(c), style: "width:260px" }), lab = h("span", {}), area = h("div");
  const draw = (g, lvl) => {
    const cc = Math.pow(10, +slider.value); lab.textContent = `コスト比 c=${cc.toFixed(1)}(誤統合1件=見逃し${cc.toFixed(1)}件分)`;
    const rows = g.rows, cur = ev.cost[lvl].curve.map(r => ({ t: r.t, cost: cc * r.fp + r.fn })), best = cur.reduce((a, b) => b.cost < a.cost ? b : a), mx = d3.max(cur, d => d.cost);
    const pts = (key) => rows.map(r => [r.t, r[lvl][key]]);
    area.replaceChildren(lineChart([{ name: "適合率", color: "#4cd08a", pts: pts("precision") }, { name: "再現率", color: "#5ab0ff", pts: pts("recall") }, { name: "F1", color: "#b99cff", pts: pts("f1"), dash: "5 3" }],
      { xlabel: "閾値(確率p)", ylabel: lvl === "pair" ? "ペア単位" : "クラスタ単位", vlines: [{ x: best.t }] }),
      h("div", { class: "src" }, `点線=コスト最小の閾値 ${best.t}`), lineChart([{ name: "コスト", color: "#f2b84b", pts: cur.map(r => [r.t, r.cost / mx]) }], { xlabel: "閾値(確率p)", ylabel: "正規化コスト", vlines: [{ x: best.t }], hh: 180 }));
  };
  if (ev.gold) {
    const g = ev.gold, lvl = h("select", {}, h("option", { value: "pair" }, "ペア単位"), h("option", { value: "cluster" }, "クラスタ単位"));
    const r = () => draw(g, lvl.value); slider.addEventListener("input", r); lvl.addEventListener("change", r);
    out.push(h("div", { class: "card" }, h("h3", {}, "正解付きデータでの閾値曲線"), h("div", { class: "grid" }, kpi("PC(候補に入った真の一致)", pct(g.pairs_completeness), "再現率の上限"), kpi("真の一致ペア", fmt(g.true_pairs)), kpi("候補ペア", fmt(g.n_candidates)), kpi("自動化率", pct(ev.automation_rate))),
      h("div", { class: "row" }, lvl, slider, lab), area,
      note(`実行#${ev.run_id}`, fmt(g.n_candidates) + "ペア", "✅ 正解付き(合成データ。実データでの値ではない)"), csvButton("threshold_curve.csv", g.rows.map(r => ({ t: r.t, ...Object.fromEntries(["precision", "recall", "f1"].map(k => ["pair_" + k, r.pair[k]])), ...Object.fromEntries(["precision", "recall", "f1"].map(k => ["cluster_" + k, r.cluster[k]])), impure: r.impure })))));
    r();
    out.push(h("div", { class: "card" }, h("h3", {}, "推移閉包による影響(クラスタ化後)"), table([{ t: "閾値", k: "t" }, { t: "混在クラスタ", k: "impure" }, { t: "推移で追加されたペア", k: "added" }, { t: "その適合率", f: r => fmt(r.added_precision) }], g.rows),
      h("div", { class: "src" }, "連結成分は、鎖でつながる別人を1つにします。追加ペアの適合率が低いほど、推移閉包が害になっています。")));
  } else out.push(h("div", { class: "banner" }, "正解付きデータがありません。ラベル付け画面で層化サンプルに正解を付けると、推定値と信頼区間を出せます(Phase 0 の V4)。"));
  if (ev.sample) {
    out.push(h("div", { class: "card" }, h("h3", {}, "サンプル推定(層化・信頼区間つき)"), table([{ t: "閾値", k: "t" }, { t: "適合率(ペア)", f: r => `${fmt(r.pair.precision)} [${fmt(r.pair.precision_lo)}, ${fmt(r.pair.precision_hi)}]` }, { t: "再現率(ペア)", f: r => `${fmt(r.pair.recall)} [${fmt(r.pair.recall_lo)}, ${fmt(r.pair.recall_hi)}]` }], ev.sample.rows),
      note(`実行#${ev.run_id}`, "ラベル付きサンプル", "⚠ サンプル誤差あり。区間を読むこと")));
  }
  const b = await GET("/api/eval/blocking");
  out.push(h("div", { class: "card" }, h("h3", {}, "ブロッキングのルール別寄与"), barChart(Object.entries(b.pairs_per_rule).map(([k, v]) => ({ k, v }))),
    b.true_pairs_per_rule && Object.keys(b.true_pairs_per_rule).length ? [h("div", { class: "src" }, "真の一致ペアを拾った数"), barChart(Object.entries(b.true_pairs_per_rule).map(([k, v]) => ({ k, v, color: "#4cd08a" })))] : null));
  return out;
};

// ---------------------------------------------------------------- ラベル付け
PAGE_FN.labels = async () => {
  const [samples, runs] = await Promise.all([GET("/api/labels/samples"), GET("/api/runs")]);
  const box = h("div"), out = [h("h2", {}, "ラベル付け"), h("div", { class: "sub" }, "確率を見せずに(盲検)判定します。2人以上のラベルで一致度(κ)を出し、不一致は裁定します。")];
  const done = runs.find(r => r.status === "done");
  if (done) { const per = h("input", { type: "number", value: 20, style: "width:70px" });
    out.push(h("div", { class: "card" }, h("div", { class: "row" }, `実行#${done.id} から確率帯ごとに`, per, "件ずつ抽出", h("button", { onclick: () => act(box, async () => { await POST("/api/labels/samples", { run_id: done.id, per_stratum: +per.value }); render(); }, "サンプルを作成しました") }, "サンプル作成")), box)); }
  for (const s of samples) {
    const area = h("div"), card = h("div", { class: "card" }, h("h3", {}, `サンプル #${s.sample_id}(実行#${s.run_id} / ${s.n_pairs}ペア)`), h("div", { class: "src" }, "ラベル付け済みの人: " + (s.labelers.join(", ") || "なし")), area);
    const next = async () => {
      const r = await GET(`/api/labels/samples/${s.sample_id}/next?limit=1`);
      if (!r.items.length) { area.replaceChildren("あなたの未判定はありません。"); return; }
      const it = r.items[0];
      const lab = v => async () => { await POST("/api/labels", { sample_id: s.sample_id, a: it.a, b: it.b, label: v }); next(); };
      area.replaceChildren(pairView({ records: it.pair.records }), h("div", { class: "row" }, h("button", { onclick: lab(1) }, "同一"), h("button", { class: "bad", onclick: lab(0) }, "別"), h("span", { class: "src" }, `あなたの残り ${r.total != null ? r.total - r.done : "?"} 件`)));
    };
    card.append(h("div", { class: "row" }, can("label") ? h("button", { onclick: next }, "判定を始める") : null, h("button", { class: "sec", onclick: async () => {
      const c = await GET(`/api/labels/samples/${s.sample_id}/consensus`);
      area.replaceChildren(h("pre", { class: "mono" }, JSON.stringify(c, null, 1)));
    } }, "一致度(κ)を見る")));
    out.push(card);
  }
  return out;
};

// ---------------------------------------------------------------- ガバナンス
PAGE_FN.gov = async () => {
  const box = h("div"), out = [h("h2", {}, "ガバナンス"), h("div", { class: "sub" }, "利用目的・権限・監査ログ。個人データの実行は、利用目的が無いと拒否されます。")];
  const [pur, gov] = await Promise.all([GET("/api/purposes"), GET("/api/governance/check")]);
  out.push(h("div", { class: "card" }, h("h3", {}, "利用目的(共同利用)"), table([{ t: "ID", k: "id" }, { t: "名称", k: "name" }, { t: "利用目的", k: "purpose" }, { t: "共同利用する項目", f: r => (r.data_items || []).join("、") }, { t: "共同利用者の範囲", k: "user_scope" }, { t: "管理責任者", k: "manager" }, { t: "通知の方法", k: "notice_method" }, { t: "有効", f: r => r.active ? "●" : "" }], pur)));
  if (can("purpose")) {
    const f = {}; const inp = (k, ph) => (f[k] = h("input", { placeholder: ph }));
    out.push(h("div", { class: "card" }, h("h3", {}, "利用目的を登録(個人情報保護法27条5項3号の必須項目。すべて必須)"), h("div", { class: "row" }, inp("name", "名称"), inp("purpose", "利用目的"), inp("data_items", "共同利用する項目(読点「、」かカンマ区切り)"), inp("user_scope", "共同利用者の範囲"), inp("manager", "管理責任者の氏名・名称"), inp("notice_method", "本人への通知/公表の方法"),
      h("button", { onclick: () => act(box, async () => { const b = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.value.trim()])); b.data_items = b.data_items.split(/[、,]/).map(x => x.trim()).filter(Boolean); await POST("/api/purposes", b); render(); }, "登録しました") }, "登録")), box));
  }
  if (can("admin")) { const users = await GET("/api/users"); out.push(h("div", { class: "card" }, h("h3", {}, "ユーザーと役割"), table([{ t: "ユーザー", k: "username" }, { t: "表示名", k: "display" }, { t: "役割", f: r => r.roles.join(", ") }], users))); }
  if (can("audit")) {
    const [v, log] = await Promise.all([GET("/api/audit/verify"), GET("/api/audit?limit=60")]);
    out.push(h("div", { class: "card" }, h("h3", {}, "監査ログ"), h("div", { class: "msg " + (v.ok ? "ok" : "err") }, v.ok ? `${v.entries} 件のハッシュ連鎖は正常です` : `seq=${v.first_bad_seq} で連鎖が壊れています`),
      table([{ t: "seq", k: "seq" }, { t: "時刻", k: "ts" }, { t: "実行者", k: "actor" }, { t: "操作", k: "action" }, { t: "対象", k: "target" }, { t: "詳細", f: r => JSON.stringify(r.detail).slice(0, 100) }], log)));
  }
  if (gov.violations.length) out.unshift(h("div", { class: "banner bad" }, gov.violations.map(x => h("div", {}, x.message))));
  return out;
};

// ---------------------------------------------------------------- タスク
PAGE_FN.tasks = async () => {
  const [ts, sum] = await Promise.all([GET("/api/tasks"), GET("/api/tasks-summary")]);
  const box = h("div"), out = [h("h2", {}, "タスク"), h("div", { class: "sub" }, "依存関係(DAG)つき。先行タスクが終わるまで着手できません。確認キューの項目もここに載ります。")];
  const st = { todo: "未着手", doing: "進行中", done: "完了", blocked: "ブロック" }, colr = { todo: "#93a0ba", doing: "#5ab0ff", done: "#4cd08a", blocked: "#ff6b6b" };
  out.push(h("div", { class: "grid" }, Object.entries(sum.by_status).map(([k, v]) => kpi(st[k] || k, v)), kpi("着手可能", sum.ready), kpi("最長の依存の鎖", sum.longest_chain)));
  if (can("task")) out.push(h("div", { class: "row" }, h("button", { class: "sec", onclick: () => act(box, async () => { const r = await POST("/api/tasks/import-prd"); render(); return r; }, "PRDのタスク表を取り込みました") }, "PRD 15章のタスクを取り込む"), box));
  const dag = ts.filter(t => t.kind !== "review");
  if (dag.length) {
    const depth = {}, by = Object.fromEntries(dag.map(t => [t.id, t])); const d = id => depth[id] ?? (depth[id] = 1 + Math.max(-1, ...(by[id].deps || []).filter(x => by[x]).map(d)));
    dag.forEach(t => d(t.id));
    const cols = d3.group(dag, t => depth[t.id]), nc = cols.size, maxn = d3.max([...cols.values()], v => v.length), w = Math.max(700, nc * 150), hh = Math.max(240, maxn * 34 + 20);
    const pos = {}; for (const [k, v] of cols) v.forEach((t, i) => pos[t.id] = [20 + k * 150, 18 + i * 34]);
    const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${hh}`).attr("width", w).attr("height", hh);
    for (const t of dag) for (const x of t.deps || []) if (pos[x]) svg.append("path").attr("d", d3.linkHorizontal()({ source: [pos[x][0] + 110, pos[x][1] + 10], target: [pos[t.id][0], pos[t.id][1] + 10] })).attr("fill", "none").attr("stroke", "#2b3750");
    svg.selectAll("g.t").data(dag).join("g").attr("transform", t => `translate(${pos[t.id]})`).call(g => { g.append("rect").attr("width", 110).attr("height", 20).attr("rx", 4).attr("fill", "#1d2639").attr("stroke", t => colr[t.effective_status] || "#93a0ba").attr("stroke-width", 2)
      .on("mousemove", (ev, t) => tip(ev, `${t.id} ${t.title}${t.waiting_on.length ? "\n待ち: " + t.waiting_on.join(",") : ""}`)).on("mouseleave", () => tip(null, null)); g.append("text").attr("x", 5).attr("y", 14).style("fill", "#e6ebf5").style("font-size", "10px").text(t => t.id + " " + t.title.slice(0, 6)); });
    out.push(h("div", { class: "card", style: "overflow:auto" }, h("h3", {}, "依存関係(左→右)"), svg.node(), h("div", { class: "src" }, "枠の色: 灰=未着手 / 青=進行中 / 緑=完了 / 赤=待ちまたはブロック")));
  }
  out.push(h("div", { class: "card" }, table([{ t: "ID", k: "id" }, { t: "タイトル", k: "title" }, { t: "担当", f: r => r.owner || "—" }, { t: "状態", f: r => h("span", { class: "tag" }, st[r.effective_status] || r.effective_status) },
    { t: "待ち", f: r => (r.waiting_on || []).join(", ") }, { t: "期限", f: r => h("span", { class: r.overdue ? "tag bad" : "" }, (r.due || "").slice(0, 10)) },
    { t: "", f: r => can("task") && r.status !== "done" ? h("button", { class: "sec", onclick: () => act(box, async () => { await PUT("/api/tasks/" + r.id, { status: r.status === "todo" ? "doing" : "done" }); render(); }) }, r.status === "todo" ? "着手" : "完了") : null }], ts.slice(0, 200))));
  return out;
};

addEventListener("hashchange", () => { const [p, a] = location.hash.slice(1).split("/"); if (PAGE_FN[p] && (p !== view.cur || (a ?? null) !== view.arg)) { view.cur = p; view.arg = a ?? null; FLASH = null; render(); } });
(async () => {
  try { ME = (await GET("/api/me")).user; const [p, a] = location.hash.slice(1).split("/"); if (PAGE_FN[p]) { view.cur = p; view.arg = a ?? null; } } catch (_) { ME = null; }
  render();
})();
