"use strict";

// ------------------------------------------------------------------- i18n
// Text that should follow the language switch is a Live value. el() remembers it on the node it creates and
// applyLang() re-evaluates it in place, so switching language never rebuilds or reloads the page: unsaved
// edits, the PDF viewer position, scroll and focus all stay as they are.
// English the first time; after that, whatever language was in use when the page was last left (see setLang).
let LANG = localStorage.getItem("lang") === "zh" ? "zh" : "en";

class Live {
  constructor(fn) { this.fn = fn; }
  toString() { return this.fn(); }
}
const pick = (zh, en) => (LANG === "zh" ? zh : en);
const T = (zh, en) => new Live(() => pick(zh, en));
const L = (fn) => new Live(fn);

// Backend messages are {zh, en} objects, or that object as JSON in a text column; older records are plain strings.
function loc(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") {
    if (!v.startsWith('{"zh"')) return v;
    try { v = JSON.parse(v); } catch { return v; }
  }
  if (typeof v === "object") return v[LANG] ?? v.zh ?? v.en ?? "";
  return String(v);
}

function applyLang(root = document.body) {
  const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  for (let n = w.currentNode; n; n = w.nextNode()) {
    if (n._live) n.data = n._live.fn();
    if (n._i18n) for (const [k, v] of Object.entries(n._i18n)) setProp(n, k, v.fn());
  }
}

function setLang(lang) {
  LANG = lang;
  localStorage.setItem("lang", lang);  // remembered, so the next visit opens in the same language
  document.documentElement.lang = pick("zh-CN", "en");
  document.title = pick("PDF 字段抽取", "PDF Field Extraction");
  document.querySelectorAll("[data-en]").forEach((e) => (e.textContent = pick(e.dataset.zh, e.dataset.en)));
  document.getElementById("lang-btn").textContent = pick("EN", "中文");
  applyLang();
}

// ------------------------------------------------------------------ helpers
const $app = document.getElementById("app");
let pollTimer = null;
let FIELD_TYPES = {
  text: { zh: "文本", en: "Text" }, number: { zh: "数字", en: "Number" }, money: { zh: "金额", en: "Amount" },
  date: { zh: "日期", en: "Date" }, boolean: { zh: "是/否", en: "Yes/No" },
};

function setProp(e, k, v) {
  if (k in e && k !== "list") e[k] = v;
  else e.setAttribute(k, v === true ? "" : v);
}

function toNode(c) {
  if (c instanceof Node) return c;
  if (c instanceof Live) {
    const n = document.createTextNode(c.fn());
    n._live = c;
    return n;
  }
  return document.createTextNode(String(c));
}

function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (v instanceof Live) { (e._i18n ||= {})[k] = v; setProp(e, k, v.fn()); }
    else setProp(e, k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    e.append(toNode(c));
  }
  return e;
}

// Like replaceChildren, but flattens arrays and skips null/false like el() does.
function mount(parent, ...children) {
  parent.replaceChildren();
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    parent.append(toNode(c));
  }
}

function opt(value, label, selected) {
  return el("option", { value, selected: String(value) === String(selected ?? "") }, label);
}

function errorText(j, fallback) {
  if (!j || !j.detail) return fallback;
  const d = j.detail;
  if (typeof d === "string" || (typeof d === "object" && !Array.isArray(d) && ("zh" in d || "en" in d))) return loc(d);
  return JSON.stringify(d);
}

const BASE = window.__PDF_EXTRACT_BASE || "";
const IS_ADMIN = window.__PDF_EXTRACT_ADMIN !== false;  // Settings is admin-only; the server enforces it too
if (!IS_ADMIN) document.querySelector('[data-nav="settings"]')?.remove();

async function api(method, url, body) {
  const init = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const r = await fetch(BASE + url, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = errorText(await r.json(), msg); } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg, isErr = false) {
  const t = el("div", { class: isErr ? "err" : "" }, msg);
  document.getElementById("toast").append(t);
  setTimeout(() => t.remove(), isErr ? 7000 : 3000);
}

async function guarded(btn, fn) {
  // Keep the label's own nodes (not a text copy) so a language switch while busy still updates them.
  const kids = [...btn.childNodes];
  btn.disabled = true;
  btn.replaceChildren(el("span", { class: "spin" }), ...kids);
  try { return await fn(); }
  catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; btn.replaceChildren(...kids); }
}

function fmtVal(v) { return v === null || v === undefined ? "" : String(v); }

function fmtTok(n) {
  n = n || 0;
  return n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n);
}
function fmtCost(c) {
  if (c === null || c === undefined) return "—";
  if (c === 0) return "$0";
  return c < 0.01 ? `$${c.toFixed(4)}` : c < 1 ? `$${c.toFixed(3)}` : `$${c.toFixed(2)}`;
}
// Cost of a usage total; "+" marks that some calls had no price and are not included.
function costText(u) {
  if (!u) return "—";
  if (u.unpriced && !u.cost) return pick("未定价", "Unpriced");
  return fmtCost(u.cost) + (u.unpriced ? "+" : "");
}
function usageTitle(u) {
  if (!u) return "";
  const n = (x) => x.toLocaleString();
  return pick(
    `${u.calls} 次调用\n输入 ${n(u.input)} · 输出 ${n(u.output)}` +
      (u.cache_read || u.cache_write ? `\n缓存读 ${n(u.cache_read)} · 缓存写 ${n(u.cache_write)}` : "") +
      (u.unpriced ? `\n其中 ${u.unpriced} 次调用的型号没有单价，未计入成本` : ""),
    `${u.calls} calls\nInput ${n(u.input)} · Output ${n(u.output)}` +
      (u.cache_read || u.cache_write ? `\nCache read ${n(u.cache_read)} · Cache write ${n(u.cache_write)}` : "") +
      (u.unpriced ? `\n${u.unpriced} calls use a model without a price and are not included in the cost` : ""));
}
function fmtMs(ms) {
  if (!ms) return "—";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}
// Average duration of the timed calls in a usage total (calls recorded before timing was added are skipped).
function avgMs(u) {
  return u && u.timed ? u.duration_ms / u.timed : 0;
}
function timingsText(t) {
  const part = (zh, en, k) => (t[k] ? pick(`${zh} ${fmtMs(t[k])}`, `${en} ${fmtMs(t[k])}`) : null);
  const list = [part("排队", "queued", "queue_ms"), part("识别模板", "match template", "classify_ms"),
    part("筛选页面", "page filter", "select_pages_ms"), part("抽取", "extract", "extract_ms")].filter(Boolean);
  return pick(`总耗时 ${fmtMs(t.total_ms)}`, `Total ${fmtMs(t.total_ms)}`) + (list.length ? ` · ${list.join(" · ")}` : "") +
    (t.render_ms ? pick(`（其中页面渲染 ${fmtMs(t.render_ms)}）`, ` (page rendering ${fmtMs(t.render_ms)} of it)`) : "");
}
function sumUsage(list) {
  const t = { calls: 0, tokens: 0, cost: 0, unpriced: 0, docs: 0 };
  for (const u of list) {
    if (!u) continue;
    t.docs++; t.calls += u.calls; t.tokens += u.tokens; t.cost += u.cost; t.unpriced += u.unpriced;
  }
  return t;
}

const STATUS = {
  uploaded: [["未处理", "Not processed"], ""], pending: [["排队中", "Queued"], "busy"],
  classifying: [["识别模板中", "Matching template"], "busy"], extracting: [["抽取中", "Extracting"], "busy"],
  done: [["完成", "Done"], "done"], error: [["出错", "Error"], "error"],
  unmatched: [["未识别模板", "No template matched"], "unmatched"],
};
const isBusy = (s) => ["pending", "classifying", "extracting"].includes(s);
const statusText = (s) => (STATUS[s] ? pick(...STATUS[s][0]) : s);

function statusBadge(s) {
  return el("span", { class: `badge ${STATUS[s]?.[1] || ""}` }, isBusy(s) ? el("span", { class: "spin" }) : null,
    L(() => statusText(s)));
}

function issueMap(issues) {
  const m = {};
  for (const i of issues || []) if (m[i.path] !== "error") m[i.path] = i.level;
  return m;
}

function cellClass(level) { return level === "error" ? "cell-error" : level === "warning" ? "cell-warn" : ""; }

function download(blob, filename) {
  const a = el("a", { href: URL.createObjectURL(blob), download: filename });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// ------------------------------------------------------------------- router
function route() {
  clearTimeout(pollTimer);
  const hash = location.hash || "#/docs";
  const [path, query] = hash.slice(2).split("?");
  const params = new URLSearchParams(query || "");
  const [section, id] = path.split("/");
  document.querySelectorAll("[data-nav]").forEach((a) => {
    const nav = a.dataset.nav;
    a.classList.toggle("active", nav === section || nav === section + "s");
  });
  const views = {
    docs: () => { if (params.get("batch")) docsState.batch = params.get("batch"); return viewDocs(); },  // #/docs?batch=<id>
    doc: () => viewDoc(Number(id)),
    templates: () => viewTemplates(),
    template: () => viewTemplate(id === "new" ? null : Number(id), params),
    formats: () => viewFormats(),
    format: () => viewFormat(id === "new" ? null : Number(id)),
    settings: () => (IS_ADMIN ? viewSettings() : viewDocs()),
    usage: () => viewUsage(),
  };
  (views[section] || views.docs)().catch((e) => {
    $app.replaceChildren(el("div", { class: "panel" }, T("加载失败：", "Failed to load: "), e.message));
  });
}
window.addEventListener("hashchange", route);

// -------------------------------------------------------------- documents
const docsState = { selected: new Set(), mode: "per_template", formatId: "", fileType: "xlsx", filter: "", auto: true,
  batch: "", exported: "", uploadBatch: "new" };

// ------------------------------------------------ batch auto-templating
// Columns of an output format that are not read from the document; unticked by default.
const AUTO_META = /^(来源文件|批次号|模板名称|抽取页码|分摊说明|核对提示|source file|batch no\.?|template|pages|allocation note|review notes)$/i;
// stage: define -> analyzing -> review -> running -> done (or error). The state lives here, not in the page, so the
// documents list can refresh itself underneath without losing what has been typed or chosen.
const autoState = { open: false, stage: "define", source: "format", formatId: "", cols: {}, manual: "", autoModel: true,
  batchId: "", jobId: "", job: null, fields: [], groups: [], own: [], result: null, error: null, recent: [] };
const normLines = (t) => t.split("\n").map((l) => l.trim()).filter(Boolean).join("\n");  // same rule as db.norm_lines
// "发票号、发票日期、提单号…" for a remembered field list: the field names only, without the notes after “:”.
const fieldListLabel = (t) => {
  const names = t.split("\n").map((l) => l.split(/[:：]/)[0].trim());
  const s = names.join("、");
  return (s.length > 34 ? s.slice(0, 34) + "…" : s) + pick(`（${names.length} 个字段）`, ` (${names.length} fields)`);
};
let autoTimer = null, autoBox = null, autoCtx = null;

function autoFields() {
  if (autoState.source === "format") {
    return Object.entries(autoState.cols).filter(([, on]) => on).map(([c]) => ({ label: c, description: "", column: c }));
  }
  return autoState.manual.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const m = l.match(/^([^:：]+)[:：]\s*(.*)$/);
    return m ? { label: m[1].trim(), description: m[2].trim(), column: "" } : { label: l, description: "", column: "" };
  });
}

function autoSetFormat(id) {
  autoState.formatId = id;
  const f = autoCtx.formats.find((x) => String(x.id) === String(id));
  autoState.cols = {};
  (f ? f.columns : []).forEach((c) => (autoState.cols[c] = !AUTO_META.test(c.trim())));
}

async function autoOpen() {
  const S = autoState;
  if (S.open && S.batchId === docsState.batch) return;
  Object.assign(S, { open: true, stage: "define", batchId: docsState.batch, job: null, groups: [], result: null, error: null });
  if (!autoCtx.formats.length) S.source = "manual";
  else if (!S.formatId || !autoCtx.formats.some((f) => String(f.id) === String(S.formatId))) autoSetFormat(autoCtx.formats[0].id);
  S.recent = await api("GET", "/api/field-lists").then((r) => r.lists).catch(() => []);
  viewDocs();
}

function autoRender() { if (autoBox && document.contains(autoBox)) renderAutoPanel(autoBox); }

async function autoAnalyze() {
  const fields = autoFields();
  if (!fields.length) return toast(pick("请先选择或输入要抽取的字段", "Choose or enter the fields to extract first"), true);
  try {
    if (autoState.source === "manual") {  // remember what was typed, so it can be picked next time
      autoState.recent = await api("POST", "/api/field-lists", { text: autoState.manual }).then((x) => x.lists).catch(() => autoState.recent);
    }
    const r = await api("POST", `/api/batches/${autoState.batchId}/auto-template/preview`);
    Object.assign(autoState, { stage: "analyzing", jobId: r.job_id, job: null, error: null, fields });
    autoRender(); autoPoll();
  } catch (e) { toast(e.message, true); }
}

async function autoRun() {
  const S = autoState;
  if (!S.groups.some((g) => g.action !== "skip")) return toast(pick("所有分组都选了“跳过”，没有要处理的", "Every group is set to skip; nothing to do"), true);
  const bad = S.groups.find((g) => g.action === "existing" && !g.template_id);
  if (bad) return toast(pick(`请为“${bad.name}”选择一个已有模板`, `Choose an existing template for “${bad.name}”`), true);
  try {
    const r = await api("POST", `/api/batches/${S.batchId}/auto-template/run`, {
      fields: S.fields, lang: LANG, auto_model: S.autoModel,
      format_id: S.source === "format" && S.formatId ? Number(S.formatId) : null,
      groups: S.groups.map((g) => ({ name: g.name, doc_ids: g.doc_ids, action: g.action,
        template_id: g.action === "existing" ? Number(g.template_id) : null })) });
    Object.assign(S, { stage: "running", jobId: r.job_id, job: null, error: null });
    autoRender(); autoPoll();
  } catch (e) { toast(e.message, true); }
}

async function autoPoll() {
  clearTimeout(autoTimer);
  const S = autoState;
  if (!S.open) return;
  try {
    const j = await api("GET", `/api/auto-template/jobs/${S.jobId}`);
    S.job = j;
    if (j.state === "error") { S.error = j.error; S.stage = "error"; }
    else if (j.state === "done") {
      if (S.stage === "analyzing") {
        S.groups = j.result.groups; S.own = j.result.own_companies; S.stage = "review";
      } else if (S.stage === "running") {
        S.result = j.result; S.stage = "done";
        if ((location.hash || "#/docs").startsWith("#/docs")) viewDocs();  // the documents are being extracted now
      }
    } else {
      autoTimer = setTimeout(autoPoll, 1000);
      // While documents are being extracted, let the list below catch up every few seconds as well.
      S.tick = (S.tick || 0) + 1;
      if (S.stage === "running" && S.tick % 3 === 0 && (location.hash || "#/docs").startsWith("#/docs")) viewDocs();
    }
  } catch (e) { S.error = { zh: e.message, en: e.message }; S.stage = "error"; }
  autoRender();
}

function renderAutoPanel(box) {
  const S = autoState, ctx = autoCtx;
  const rerender = () => renderAutoPanel(box);
  const close = () => { clearTimeout(autoTimer); S.open = false; viewDocs(); };
  const batch = ctx.batches.find((b) => String(b.id) === S.batchId);
  const unmatched = ctx.docs.filter((d) => String(d.batch_id) === S.batchId && d.status === "unmatched").length;
  const head = el("div", { class: "row", style: "margin-bottom:10px" },
    el("h3", { style: "margin:0" }, T("✨ 按字段批量建模板", "✨ Create templates from a field list")),
    el("span", { class: "muted small" }, L(() => pick(`批次 ${batch ? loc(batch.label) : ""} · 为未识别模板的文档按供应商分组建模板`,
      `Batch ${batch ? loc(batch.label) : ""} · groups the documents without a template by vendor and creates templates`))),
    el("span", { class: "spacer" }), el("button", { class: "link", onclick: close }, T("关闭", "Close")));
  const mark = (state) => state === "done" ? el("span", { style: "color:var(--ok);font-weight:700" }, "✓")
    : state === "error" ? el("span", { style: "color:#b42318;font-weight:700" }, "✗")
    : state === "running" || state === "working" ? el("span", { class: "spin" })
    : el("span", { class: "muted" }, state === "skipped" ? "–" : "○");
  const clock = (j) => {
    const s = Math.max(0, Math.floor((j.finished || Date.now() / 1000) - j.started));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };
  const actionText = (a) => a === "new" ? pick("专用模板", "dedicated") : a === "generic" ? pick("通用模板", "generic") : pick("已有模板", "existing");
  // The stages of a background job, in order: a mark, the stage name, a count and bar, and a detail line. The
  // "templates" stage also lists each vendor group with its own state.
  const progress = (j) => {
    if (!j || !j.steps.length) return el("div", { style: "margin:10px 0" }, el("span", { class: "spin" }), L(() => pick("正在启动…", "Starting…")));
    const current = j.steps.find((s) => s.state === "running") || j.steps.find((s) => s.state === "pending");
    return el("div", { style: "margin:6px 0" },
      el("div", { class: "small muted", style: "margin-bottom:6px" },
        L(() => pick(`已用时 ${clock(j)}`, `Elapsed ${clock(j)}`)),
        current ? L(() => pick(` · 当前：${loc(current.label)}`, ` · Now: ${loc(current.label)}`)) : null),
      j.steps.map((s, n) => el("div", { style: `margin:0 0 8px;${s.state === "pending" ? "opacity:.55" : ""}` },
        el("div", { class: "row", style: "flex-wrap:nowrap;gap:8px" },
          el("span", { style: "width:16px;text-align:center" }, mark(s.state)),
          el("b", {}, L(() => `${n + 1}. ${loc(s.label)}`)),
          s.total > 0 ? el("span", { class: "muted small" }, `${s.done}/${s.total}`) : null,
          s.total > 0 && s.state !== "pending" ? el("progress", { value: s.done, max: s.total, style: "width:160px" }) : null),
        s.detail ? el("div", { class: "small muted", style: "margin-left:24px" }, L(() => loc(s.detail))) : null,
        s.key === "templates" && j.items.length && s.state !== "pending" ? el("div", { style: "margin:4px 0 0 24px" }, j.items.map((it) =>
          el("div", { class: "small", style: "margin:2px 0" },
            el("span", { style: "display:inline-block;width:16px" }, mark(it.state)),
            L(() => loc(it.label)), " ", el("span", { class: "badge" }, L(() => actionText(it.action))),
            L(() => pick(` · ${it.docs} 份`, ` · ${it.docs} docs`)),
            it.detail ? el("span", { class: "muted" }, " · ", L(() => loc(it.detail))) : null))) : null)));
  };
  let body;

  if (S.stage === "define") {
    const fmts = ctx.formats;
    const fmt = fmts.find((f) => String(f.id) === String(S.formatId));
    const radio = (val, label) => el("label", { style: "margin-right:16px" }, el("input", { type: "radio", name: "autosrc", checked: S.source === val,
      disabled: val === "format" && !fmts.length, onchange: () => { S.source = val; rerender(); } }), " ", label);
    body = el("div", {},
      el("div", { class: "small muted", style: "margin-bottom:8px" }, T(
        `这个批次里有 ${unmatched} 份文档没有匹配到模板。告诉我你要抽取哪些字段，系统会先识别每份文档是哪家供应商开的，让你确认分组，再为每家自动建模板并抽取。只抽取表头字段，不含明细表。`,
        `${unmatched} documents in this batch matched no template. Tell me which fields you want; the system works out which vendor issued each document, lets you confirm the groups, then creates a template per vendor and extracts. Header fields only, no item table.`)),
      el("div", { class: "row" }, T("字段来源：", "Fields from: "), radio("format", T("输出格式的列", "Output format columns")),
        radio("manual", T("手动输入", "Type them in"))),
      S.source === "format"
        ? el("div", { style: "margin-top:8px" },
            el("div", { class: "row" }, T("输出格式：", "Output format: "),
              el("select", { onchange: (e) => { autoSetFormat(e.target.value); rerender(); } },
                fmts.map((f) => opt(f.id, f.name, S.formatId))),
              el("span", { class: "small muted" }, T("勾选要抽取的列；建好的模板会自动对应到这个格式，导出统一格式时不用再逐个对应",
                "Tick the columns to extract; the new templates are mapped to this format automatically, so unified export works at once"))),
            el("div", { class: "row", style: "margin-top:6px;gap:6px 18px" }, fmt ? fmt.columns.map((c) => el("label", {},
              el("input", { type: "checkbox", checked: !!S.cols[c], onchange: (e) => (S.cols[c] = e.target.checked) }), " " + c)) : null))
        : el("div", { style: "margin-top:8px" },
            S.recent.length ? el("div", { class: "row", style: "margin-bottom:6px" },
              el("select", { style: "max-width:520px", title: T("最近用过的字段列表，选一份填入下面的输入框", "Recently used field lists; pick one to fill the box below"),
                onchange: async (e) => {
                  const v = e.target.value;
                  if (v === "__clear") {
                    if (!confirm(pick("清空所有已记住的字段列表？", "Forget all remembered field lists?"))) return rerender();
                    S.recent = (await api("DELETE", "/api/field-lists")).lists;
                  } else if (v !== "") S.manual = S.recent[Number(v)];
                  rerender();
                } },
                opt("", T("最近用过的字段列表…", "Recent field lists…"), ""),
                S.recent.map((t, i) => opt(String(i), fieldListLabel(t), "")),
                opt("__clear", T("清空历史", "Clear history"), "")),
              S.recent.includes(normLines(S.manual)) ? el("button", { class: "link danger", onclick: async () => {
                S.recent = (await api("DELETE", `/api/field-lists?text=${encodeURIComponent(normLines(S.manual))}`)).lists; rerender();
              } }, T("忘掉这份", "Forget this one")) : null) : null,
            el("textarea", { rows: 7, value: S.manual, oninput: (e) => (S.manual = e.target.value),
              placeholder: pick("每行一个字段；可在字段名后用“：”写说明\n发票号\n发票日期\n提单号：主运单号或提单号\n金额\n币种",
                "One field per line; add a note after “:” if you like\nInvoice No\nInvoice Date\nBL No: master AWB or bill of lading\nAmount\nCurrency") })),
      el("div", { class: "row", style: "margin-top:10px" },
        el("label", {}, el("input", { type: "checkbox", checked: S.autoModel, onchange: (e) => (S.autoModel = e.target.checked) }),
          T(" 为每家自动选择模型（更准，每家多花几分钱）", " Pick the model for each vendor automatically (more accurate, a few cents more each)"))),
      IS_ADMIN ? el("div", { class: "small muted", style: "margin-top:6px" }, T("在", "Fill in "), el("a", { href: "#/settings" }, T("设置", "Settings")),
        T("里填写“我方公司名称”，识别供应商时会排除它们（否则付款方可能被当成供应商）。", " your own company names; they are excluded when working out vendors (otherwise the payer may be taken for a vendor).")) : null,
      el("div", { class: "row", style: "margin-top:12px" },
        (() => { const b = el("button", { class: "primary", onclick: () => guarded(b, autoAnalyze) }, T("分析供应商", "Analyse vendors")); return b; })(),
        el("button", { onclick: close }, T("取消", "Cancel"))));
  } else if (S.stage === "analyzing" || S.stage === "running") {
    body = el("div", {}, progress(S.job),
      el("div", { class: "small muted" }, S.stage === "analyzing"
        ? T("每份文档调用一次便宜的模型识别供应商，通常几十秒。可以先去做别的，任务在后台继续。", "One cheap model call per document to identify the vendor, usually a few tens of seconds. You can do something else; the job continues in the background.")
        : T("哪家的模板先好，那家的文档就先开始抽取，结果在下面的列表里陆续出现。可以先去做别的，任务在后台继续。", "As soon as a vendor's template is ready its documents start extracting; results appear in the list below. You can do something else; the job continues in the background.")));
  } else if (S.stage === "review") {
    const fieldChips = S.fields.map((f) => el("span", { class: "badge", style: "margin:0 4px 4px 0" }, f.label));
    const rows = S.groups.map((g) => {
      const isVendor = g.kind === "vendor";
      const nameCell = isVendor ? el("input", { type: "text", value: g.name, oninput: (e) => (g.name = e.target.value) })
        : el("span", { class: "muted" }, g.kind === "skip" ? T("（不是业务单据，如系统日报）", "(not a business document, e.g. a system report)")
          : T("（未能识别供应商）", "(vendor not recognised)"));
      const actSel = el("select", { onchange: (e) => { g.action = e.target.value; rerender(); } },
        isVendor ? opt("new", T("新建专用模板", "New dedicated template"), g.action) : null,
        opt("generic", T("用通用模板", "Use the generic template"), g.action),
        opt("existing", T("用已有模板", "Use an existing template"), g.action),
        opt("skip", T("跳过", "Skip"), g.action));
      const tplSel = g.action === "existing" ? el("select", { onchange: (e) => (g.template_id = Number(e.target.value) || null) },
        opt("", T("选择模板…", "Choose a template…"), g.template_id || ""), ctx.templates.map((t) => opt(t.id, t.name, g.template_id || ""))) : null;
      return el("tr", {},
        el("td", {}, nameCell),
        el("td", { title: g.files.join("\n") + (g.count > g.files.length ? "\n…" : "") },
          `${g.count}`, g.scanned ? el("span", { class: "muted small" }, L(() => pick(`（扫描件 ${g.scanned}）`, ` (${g.scanned} scanned)`))) : null),
        el("td", { class: "small muted" }, g.doc_type),
        el("td", {}, el("div", { class: "row", style: "flex-wrap:nowrap" }, actSel, tplSel)));
    });
    body = el("div", {},
      el("div", { class: "small", style: "margin-bottom:6px" }, L(() => pick(`识别了 ${S.job?.result?.documents ?? ""} 份文档，分为 ${S.groups.length} 组。要抽取的字段：`,
        `${S.job?.result?.documents ?? ""} documents identified, in ${S.groups.length} groups. Fields to extract: `)), fieldChips),
      S.own.length ? el("div", { class: "small muted", style: "margin-bottom:6px" }, L(() => pick("已排除我方公司：", "Excluded as our own companies: ") + S.own.join("、")))
        : el("div", { class: "small", style: "margin-bottom:6px;color:#92400e" }, T(
          "还没有设置“我方公司名称”，付款方（如你们自己）可能被误认成供应商。可以在设置里填写后重新分析。",
          "No own company names are set, so the payer (your own company) may be taken for a vendor. Set them in Settings and analyse again.")),
      el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, [["供应商（可改名）", "Vendor (editable)"], ["份数", "Docs"], ["类型", "Type"], ["处理方式", "What to do"]]
          .map((h) => el("th", {}, T(...h))))), el("tbody", {}, rows))),
      el("div", { class: "small muted", style: "margin-top:6px" }, T(
        "“专用模板”：为这家供应商写识别关键词和每个字段在文档上的位置，之后同类文档自动匹配（适合份数多的）。“通用模板”：不区分供应商，所有选了它的文档共用一个模板，不会自动匹配，适合只有一两份的。",
        "“Dedicated” writes detection keywords and field locations for the vendor so later documents are matched automatically (best for many documents). “Generic” is one shared template for every group that picks it; it is never matched automatically (best for one or two documents).")),
      el("div", { class: "row", style: "margin-top:12px" },
        (() => { const b = el("button", { class: "primary", onclick: () => guarded(b, autoRun) }, T("开始生成并抽取", "Create templates and extract")); return b; })(),
        el("button", { onclick: () => { S.stage = "define"; rerender(); } }, T("← 修改字段", "← Change fields")),
        el("button", { onclick: close }, T("取消", "Cancel"))));
  } else if (S.stage === "done") {
    const r = S.result;
    const kindText = (c) => c.kind === "generic" ? pick("通用模板", "Generic template") : c.kind === "existing" ? pick("已有模板", "Existing template") : pick("新建", "New");
    body = el("div", {},
      el("div", { style: "color:var(--ok);margin-bottom:6px" }, L(() => {
        const x = r.extraction, ok = x.finished - x.failed;
        return pick(`✓ 已完成：${r.documents} 份文档中 ${ok} 份抽取完成` + (x.failed ? `，${x.failed} 份出错` : "") + (x.finished < x.sent ? `，${x.sent - x.finished} 份还在处理` : "") + `，用时 ${clock(S.job)}。`,
          `✓ Done: ${ok} of ${r.documents} documents extracted` + (x.failed ? `, ${x.failed} failed` : "") + (x.finished < x.sent ? `, ${x.sent - x.finished} still processing` : "") + `, took ${clock(S.job)}.`);
      })),
      el("ul", { style: "margin:6px 0 6px 18px" }, r.created.map((c) => el("li", {},
        el("a", { href: `#/template/${c.template_id}` }, c.template_name), " ",
        el("span", { class: "badge" }, L(() => kindText(c))), L(() => pick(` · ${c.docs} 份`, ` · ${c.docs} docs`)),
        c.keywords_added && c.keywords_added.length ? el("span", { class: "small muted" }, L(() => pick(" · 补充了关键词：", " · keywords added: ") + c.keywords_added.join("、"))) : null))),
      r.keyword_check.total ? el("div", { class: "small muted" }, L(() => pick(
        `关键词检查：${r.keyword_check.total} 份电子版文档里 ${r.keyword_check.matched} 份能按关键词匹配到各自的模板。`,
        `Keyword check: ${r.keyword_check.matched} of ${r.keyword_check.total} digital documents match their template by keywords.`))) : null,
      r.warnings.length ? el("ul", { class: "issues", style: "margin-top:6px" }, r.warnings.map((w) => el("li", { class: "warning" }, L(() => loc(w))))) : null,
      el("div", { class: "small muted", style: "margin-top:6px" }, T(
        "自动生成的模板在“模板”页带有“自动生成”标记，可以像普通模板一样修改。先抽查几份结果，再决定要不要调整字段说明。",
        "Generated templates are marked “Auto-generated” on the Templates page and can be edited like any other. Check a few results, then adjust field notes if needed.")),
      el("div", { class: "row", style: "margin-top:10px" }, el("button", { class: "primary", onclick: close }, T("完成", "Done"))));
  } else {
    body = el("div", {}, el("div", { style: "color:#b42318" }, T("出错了：", "Something went wrong: "), L(() => loc(S.error))),
      el("div", { class: "row", style: "margin-top:10px" }, el("button", { onclick: () => { S.stage = "define"; rerender(); } }, T("← 返回", "← Back")),
        el("button", { onclick: close }, T("关闭", "Close"))));
  }
  mount(box, el("div", { class: "panel", style: "border-color:#93c5fd" }, head, body));
}

async function viewDocs() {
  const [docs, templates, formats, batches] = await Promise.all([
    api("GET", "/api/documents"), api("GET", "/api/templates"), api("GET", "/api/output-formats"), api("GET", "/api/batches"),
  ]);
  if (location.hash && !location.hash.startsWith("#/docs") && location.hash !== "#/") return;
  const ids = new Set(docs.map((d) => d.id));
  for (const id of [...docsState.selected]) if (!ids.has(id)) docsState.selected.delete(id);
  const batchIds = new Set(batches.map((b) => String(b.id)));
  if (docsState.batch && !batchIds.has(docsState.batch)) docsState.batch = "";
  if (docsState.uploadBatch !== "new" && !batchIds.has(docsState.uploadBatch)) docsState.uploadBatch = "new";

  // Keep in step with SUPPORTED in backend/ingest.py. The server converts everything to PDF.
  const UPLOAD_EXTS = [".pdf", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".eml", ".msg", ".xlsx", ".xlsm", ".csv"];
  const fileInput = el("input", { type: "file", multiple: true, accept: UPLOAD_EXTS.join(","), style: "display:none",
    onchange: () => upload(fileInput.files) });
  const drop = el("div", { class: "dropzone", onclick: () => fileInput.click() },
    el("div", { style: "font-size:15px;color:#374151" },
      T("拖拽文件到这里，或点击选择文件（可多选）", "Drag files here, or click to choose files (multiple allowed)")),
    el("div", { class: "small" },
      T("支持 PDF、图片、邮件（.eml / .msg，附件会单独成为文档）、表格（.xlsx / .csv）。上传后会自动识别供应商模板并抽取字段",
        "PDF, images, e-mails (.eml / .msg; attachments become documents of their own) and spreadsheets (.xlsx / .csv). After upload, the vendor template is detected and fields are extracted automatically")));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files); });

  async function upload(files) {
    const picked = [...files].filter((f) => UPLOAD_EXTS.some((x) => f.name.toLowerCase().endsWith(x)));
    if (!picked.length) return toast(pick("没有支持的文件（PDF、图片、.eml / .msg、.xlsx / .csv）", "No supported files (PDF, images, .eml / .msg, .xlsx / .csv)"), true);
    const fd = new FormData();
    picked.forEach((f) => fd.append("files", f));
    const target = docsState.uploadBatch === "new" ? "" : `&batch_id=${docsState.uploadBatch}`;
    try {
      const r = await api("POST", `/api/documents?auto=${docsState.auto}${target}`, fd);
      const b = (await api("GET", "/api/batches")).find((x) => x.id === r.batch_id);
      const lbl = b ? loc(b.label) : r.batch_id;
      const n = r.ids.length;  // an e-mail can become several documents
      toast(pick(`已上传 ${n} 份文档到批次 ${lbl}`, `Uploaded ${n} document(s) to batch ${lbl}`));
      docsState.batch = String(r.batch_id);
      docsState.uploadBatch = "new";
      docsState.filter = "";
      docsState.exported = "";
      viewDocs();
    } catch (e) { toast(e.message, true); }
  }

  async function newBatch() {
    try {
      const b = await api("POST", "/api/batches");
      docsState.uploadBatch = String(b.id);
      docsState.batch = String(b.id);
      const lbl = loc(b.label);
      toast(pick(`已创建批次 ${lbl}，现在上传的文件会放进这个批次`, `Created batch ${lbl}; files you upload now go into it`));
      viewDocs();
    } catch (e) { toast(e.message, true); }
  }

  const filtered = docs.filter((d) => {
    if (docsState.batch && String(d.batch_id) !== docsState.batch) return false;
    if (docsState.exported === "no" && d.exported_at) return false;
    if (docsState.exported === "yes" && !d.exported_at) return false;
    const f = docsState.filter;
    if (!f) return true;
    if (f.startsWith("t:")) return String(d.template_id) === f.slice(2);
    return d.status === f || (f === "review" && d.status === "done" && !d.verified && (d.errors || d.warnings));
  });
  const batchLabel = (b) => {
    const extra = `${b.docs}${pick(" 份", " docs")}${b.usage ? " · " + costText(b.usage) : ""}`;
    return `${loc(b.label)}${pick(`（${extra}）`, ` (${extra})`)}`;
  };
  const shown = sumUsage(filtered.map((d) => d.usage));
  const usageBar = el("div", { class: "usage-bar" },
    L(() => pick(
      `当前筛选 ${filtered.length} 份文档 · 已统计用量 ${shown.docs} 份 · Token ${fmtTok(shown.tokens)} · 估算成本 `,
      `${filtered.length} docs in view · usage recorded for ${shown.docs} · Tokens ${fmtTok(shown.tokens)} · Est. cost `)),
    el("b", {}, L(() => costText(shown))),
    shown.docs ? L(() => pick(" · 平均每份 ", " · avg per doc ") + fmtCost(shown.cost / shown.docs)) : "",
    shown.unpriced ? el("span", { class: "muted" },
      L(() => pick(`（${shown.unpriced} 次调用没有单价，未计入）`, ` (${shown.unpriced} calls without a price not included)`))) : null,
    "　", el("a", { href: "#/usage" }, T("用量详情 →", "Usage details →")));
  const curBatch = batches.find((b) => String(b.id) === docsState.uploadBatch);

  const allBox = el("input", { type: "checkbox",
    checked: filtered.length > 0 && filtered.every((d) => docsState.selected.has(d.id)),
    onchange: () => { filtered.forEach((d) => allBox.checked ? docsState.selected.add(d.id) : docsState.selected.delete(d.id)); viewDocs(); } });

  const rows = filtered.map((d) => el("tr", {},
    el("td", {}, el("input", { type: "checkbox", checked: docsState.selected.has(d.id),
      onchange: (e) => { e.target.checked ? docsState.selected.add(d.id) : docsState.selected.delete(d.id); applyLang(exportBtn); } })),
    el("td", {}, el("a", { href: `#/doc/${d.id}` }, d.filename)),
    el("td", { class: "small", style: "white-space:nowrap" }, L(() => loc(d.batch))),
    el("td", {}, d.page_count),
    el("td", {}, d.has_text ? T("电子版", "Digital") : el("span", { class: "muted" }, T("扫描件", "Scanned"))),
    el("td", {}, d.template_name || el("span", { class: "muted" }, "—")),
    el("td", { title: L(() => loc(d.error) || loc(d.match_info)) }, statusBadge(d.status),
      d.verified ? [" ", el("span", { class: "badge verified" }, T("已核对", "Verified"))] : null,
      d.exported_at ? [" ", el("span", { class: "badge", title: L(() => pick("导出于 ", "Exported at ") + d.exported_at.replace("T", " ")) },
        T("已导出", "Exported"))] : null),
    el("td", {}, d.status === "done" ? d.items : ""),
    el("td", { class: "small", style: "white-space:nowrap", title: L(() => usageTitle(d.usage)) },
      d.usage ? [fmtTok(d.usage.tokens), el("span", { class: "muted" }, " · "), L(() => costText(d.usage))] : el("span", { class: "muted" }, "—")),
    el("td", {}, d.errors ? el("span", { class: "badge error" }, L(() => pick(`${d.errors} 错误`, `${d.errors} errors`))) : null, " ",
      d.warnings ? el("span", { class: "badge unmatched" }, L(() => pick(`${d.warnings} 提示`, `${d.warnings} warnings`))) : null),
    el("td", { style: "white-space:nowrap" },
      el("a", { class: "btn", href: `#/doc/${d.id}` }, T("查看", "View")), " ",
      el("button", { class: "link", disabled: isBusy(d.status), onclick: async () => {
        try { await api("POST", `/api/documents/${d.id}/process`, { template_id: null }); viewDocs(); } catch (e) { toast(e.message, true); }
      } }, T("重新识别", "Re-detect")),
      el("button", { class: "link danger", onclick: async () => {
        if (!confirm(pick(`删除 ${d.filename}？`, `Delete ${d.filename}?`))) return;
        await api("DELETE", `/api/documents/${d.id}`); viewDocs();
      } }, T("删除", "Delete"))),
  ));

  // Without a selection, export the finished documents in the current view that have not been exported yet.
  const exportCandidates = () => filtered.filter((d) => d.status === "done" && (docsState.exported === "yes" || !d.exported_at));
  const exportLabel = () => docsState.selected.size
    ? pick(`导出选中（${docsState.selected.size}）`, `Export selected (${docsState.selected.size})`)
    : pick(`导出未导出的已完成（${exportCandidates().length}）`, `Export finished, not yet exported (${exportCandidates().length})`);
  const formatSel = el("select", { onchange: (e) => (docsState.formatId = e.target.value) },
    opt("", T("选择输出格式…", "Choose output format…"), docsState.formatId), formats.map((f) => opt(f.id, f.name, docsState.formatId)));
  formatSel.style.display = docsState.mode === "unified" ? "" : "none";
  const exportBtn = el("button", { class: "primary", onclick: () => guarded(exportBtn, doExport) }, L(exportLabel));

  async function doExport() {
    let ids = [...docsState.selected];
    if (!ids.length) ids = exportCandidates().map((d) => d.id);
    if (!ids.length) throw new Error(pick(
      "当前筛选下没有可导出的文档（已完成且未导出）。可以勾选文档，或把导出状态筛选为“已导出”后重新导出",
      "Nothing to export in the current view (finished and not yet exported). Select documents, or set the export filter to “Exported” to export again"));
    if (docsState.mode === "unified" && !docsState.formatId) throw new Error(pick(
      "请先选择输出格式（可在“输出格式”页新建）", "Choose an output format first (you can create one on the Output formats page)"));
    const r = await fetch(BASE + "/api/export", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_ids: ids, mode: docsState.mode, format_id: docsState.formatId ? Number(docsState.formatId) : null,
        file_type: docsState.fileType, lang: LANG }),
    });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(errorText(j, r.statusText)); }
    const cd = r.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename\*=UTF-8''([^;]+)/);
    download(await r.blob(), m ? decodeURIComponent(m[1]) : "export.xlsx");
    docsState.selected.clear();
    toast(pick(`已导出 ${ids.length} 份文档，并标记为“已导出”`, `Exported ${ids.length} document(s) and marked them as exported`));
    viewDocs();
  }

  const headers = [["文件", "File"], ["批次", "Batch"], ["页数", "Pages"], ["类型", "Type"], ["模板", "Template"], ["状态", "Status"],
    ["明细行", "Items"], ["用量", "Usage"], ["问题", "Issues"], ["操作", "Actions"]];

  autoCtx = { docs, templates, formats, batches };
  autoBox = el("div");
  if (autoState.open) renderAutoPanel(autoBox);
  const unmatchedHere = docsState.batch ? docs.filter((d) => String(d.batch_id) === docsState.batch && d.status === "unmatched").length : 0;
  const autoBtn = el("button", { disabled: !unmatchedHere, onclick: autoOpen,
    title: L(() => docsState.batch ? "" : pick("先在左边选择一个批次", "Choose a batch on the left first")) },
    L(() => pick(`✨ 按字段批量建模板（${unmatchedHere} 份未识别）`, `✨ Create templates from fields (${unmatchedHere} unmatched)`)));

  $app.replaceChildren(
    el("div", { class: "panel" }, drop, fileInput,
      el("div", { class: "row", style: "margin-top:8px" },
        T("上传到：", "Upload to:"),
        el("select", { onchange: (e) => (docsState.uploadBatch = e.target.value) },
          opt("new", T("自动创建新批次（编号由系统生成）", "New batch (number assigned automatically)"), docsState.uploadBatch),
          batches.map((b) => opt(b.id, L(() => pick("追加到 ", "Add to ") + batchLabel(b)), docsState.uploadBatch))),
        el("button", { onclick: newBatch }, T("+ 新建空批次", "+ New empty batch")),
        curBatch && !curBatch.docs ? el("button", { class: "link danger", onclick: async () => {
          await api("DELETE", `/api/batches/${curBatch.id}`);
          if (docsState.batch === String(curBatch.id)) docsState.batch = "";
          docsState.uploadBatch = "new"; viewDocs();
        } }, T("删除此空批次", "Delete this empty batch")) : null,
        el("span", { class: "spacer" }),
        el("label", {}, el("input", { type: "checkbox", checked: docsState.auto, onchange: (e) => (docsState.auto = e.target.checked) }),
          T(" 上传后自动抽取", " Extract automatically after upload")))),
    autoBox,
    el("div", { class: "panel" },
      el("div", { class: "row", style: "margin-bottom:12px" },
        el("select", { onchange: (e) => {
          docsState.batch = e.target.value;
          if (autoState.open && ["define", "review", "error"].includes(autoState.stage)) autoState.open = false;
          viewDocs();
        } },
          opt("", T("全部批次", "All batches"), docsState.batch), batches.map((b) => opt(b.id, L(() => batchLabel(b)), docsState.batch))),
        el("select", { onchange: (e) => { docsState.exported = e.target.value; viewDocs(); } },
          opt("", T("导出状态：全部", "Export status: all"), docsState.exported), opt("no", T("未导出", "Not exported"), docsState.exported),
          opt("yes", T("已导出", "Exported"), docsState.exported)),
        el("select", { onchange: (e) => { docsState.filter = e.target.value; viewDocs(); } },
          opt("", T("全部文档", "All documents"), docsState.filter), opt("review", T("待核对（有提示）", "Needs review (has warnings)"), docsState.filter),
          opt("unmatched", T("未识别模板", "No template matched"), docsState.filter), opt("error", T("出错", "Error"), docsState.filter),
          opt("done", T("已完成", "Done"), docsState.filter),
          templates.map((t) => opt(`t:${t.id}`, L(() => pick("模板：", "Template: ") + t.name), docsState.filter))),
        el("button", { onclick: () => viewDocs() }, T("刷新", "Refresh")),
        autoBtn,
        el("span", { class: "spacer" }),
        el("select", { onchange: (e) => { docsState.mode = e.target.value; formatSel.style.display = e.target.value === "unified" ? "" : "none"; } },
          opt("per_template", T("按模板分 Sheet", "One sheet per template"), docsState.mode),
          opt("unified", T("合并为统一格式", "Merge into one output format"), docsState.mode)),
        formatSel,
        el("select", { onchange: (e) => (docsState.fileType = e.target.value) },
          opt("xlsx", "Excel", docsState.fileType), opt("csv", "CSV", docsState.fileType)),
        exportBtn),
      docs.length ? usageBar : null,
      docs.length
        ? el("div", { class: "table-wrap" }, el("table", {},
            el("thead", {}, el("tr", {}, el("th", {}, allBox), headers.map((h) => el("th", {}, T(...h))))),
            el("tbody", {}, rows)))
        : el("div", { class: "empty" }, T("还没有文档，先上传文件吧", "No documents yet. Upload a file to get started.")),
      docs.length && !filtered.length ? el("div", { class: "empty" }, T("当前筛选下没有文档", "No documents match the current filters")) : null),
  );

  // While the panel waits for input, refreshing the page underneath would redraw it, so the list stops refreshing.
  const typing = autoState.open && ["define", "review"].includes(autoState.stage);
  if (!typing && docs.some((d) => isBusy(d.status))) {
    pollTimer = setTimeout(() => { if ((location.hash || "#/docs").startsWith("#/docs")) viewDocs(); }, 2000);
  }
}

async function viewDoc(id) {
  const [doc, templates, list, docUsage] = await Promise.all([
    api("GET", `/api/documents/${id}`), api("GET", "/api/templates"), api("GET", "/api/documents"),
    api("GET", `/api/documents/${id}/usage`),
  ]);
  const tpl = templates.find((t) => t.id === doc.template_id);
  const idx = list.findIndex((d) => d.id === id);
  const summary = list[idx];
  const prev = list[idx + 1], next = list[idx - 1]; // list is newest first

  const firstPage = (doc.pages && doc.pages[0]) || 1;
  const frame = el("iframe", { class: "pdf-frame", src: `${BASE}/api/documents/${id}/file#page=${firstPage}&navpanes=0&view=FitH` });

  const tplSel = el("select", {}, opt("", T("自动识别", "Auto-detect"), ""), templates.map((t) => opt(t.id, t.name, doc.template_id)));
  const rerunBtn = el("button", { disabled: isBusy(doc.status), onclick: () => guarded(rerunBtn, async () => {
    await api("POST", `/api/documents/${id}/process`, { template_id: tplSel.value ? Number(tplSel.value) : null });
    viewDoc(id);
  }) }, T("用此模板重新抽取", "Re-extract with this template"));

  const head = el("div", { class: "panel" },
    el("div", { class: "row" },
      el("a", { href: "#/docs" }, T("← 文档列表", "← Documents")),
      el("span", { class: "spacer" }),
      el("button", { disabled: !prev, onclick: () => (location.hash = `#/doc/${prev.id}`) }, T("上一份", "Previous")),
      el("button", { disabled: !next, onclick: () => (location.hash = `#/doc/${next.id}`) }, T("下一份", "Next"))),
    el("h2", { style: "margin:10px 0 6px" }, doc.filename, " ", statusBadge(doc.status),
      doc.verified ? [" ", el("span", { class: "badge verified" }, T("已核对", "Verified"))] : null),
    el("div", { class: "small muted" }, L(() =>
      pick(`${doc.page_count} 页 · ${doc.has_text ? "电子版" : "扫描件"}`, `${doc.page_count} pages · ${doc.has_text ? "Digital" : "Scanned"}`) +
      (summary ? pick(` · 批次：${loc(summary.batch)}${summary.exported_at ? " · 已导出" : ""}`,
        ` · Batch: ${loc(summary.batch)}${summary.exported_at ? " · Exported" : ""}`) : "") +
      (doc.pages?.length ? pick(" · 抽取页：", " · Extracted pages: ") + doc.pages.join(", ") : "") +
      (doc.model_used ? ` · ${doc.model_used}` : ""))),
    doc.match_info ? el("div", { class: "small muted", style: "margin-top:4px" },
      T("模板识别：", "Template match: "), L(() => loc(doc.match_info))) : null,
    doc.source ? el("div", { class: "small muted", style: "margin-top:4px" },
      T("来源：", "Source: "), L(() => loc(doc.source))) : null,
    doc.timings?.total_ms ? el("div", { class: "small muted", style: "margin-top:4px" },
      T("耗时：", "Time: "), L(() => timingsText(doc.timings))) : null,
    el("div", { class: "row", style: "margin-top:10px" }, T("模板：", "Template: "), tplSel, rerunBtn,
      tpl ? el("a", { class: "btn", href: `#/template/${tpl.id}?doc=${id}` }, T("编辑模板", "Edit template")) : null,
      el("a", { class: "btn", href: `#/template/new?doc=${id}` }, T("用此文档新建模板", "New template from this document"))),
  );

  const usagePanel = docUsage.calls.length ? el("details", { class: "panel", style: "padding:10px 16px" },
    el("summary", { style: "cursor:pointer" }, T("用量：", "Usage: "),
      L(() => `${fmtTok(docUsage.total.tokens)} token · ` + pick("估算成本 ", "est. cost ")), el("b", {}, L(() => costText(docUsage.total))),
      el("span", { class: "muted small" }, L(() => pick(`（文档处理 ${docUsage.total.calls} 次调用；点击展开明细）`,
        ` (${docUsage.total.calls} processing calls; click for details)`)))),
    el("table", { style: "margin-top:8px" },
      el("thead", {}, el("tr", {}, el("th", {}, T("用途", "Purpose")), el("th", {}, T("型号", "Model")),
        [["输入", "Input"], ["输出", "Output"], ["缓存读", "Cache read"], ["缓存写", "Cache write"], ["耗时", "Time"], ["估算成本", "Est. cost"]]
          .map((h) => el("th", { class: "num" }, T(...h))))),
      el("tbody", {}, docUsage.calls.map((c) => el("tr", {},
        el("td", { class: "small" }, L(() => loc(c.label))), el("td", { class: "small" }, c.model),
        el("td", { class: "num" }, c.input_tokens.toLocaleString()), el("td", { class: "num" }, c.output_tokens.toLocaleString()),
        el("td", { class: "num" }, c.cache_read_tokens.toLocaleString()), el("td", { class: "num" }, c.cache_write_tokens.toLocaleString()),
        el("td", { class: "num" }, fmtMs(c.duration_ms)),
        el("td", { class: "num" }, c.cost === null ? T("未定价", "Unpriced") : fmtCost(c.cost)))))),
    el("div", { class: "small muted", style: "margin-top:6px" }, T(
      "“模板试抽取”等在模板页产生的调用只在这里显示，不计入文档和批次的合计。",
      "Calls made on the template page (such as test extractions) are listed here only and are not included in document or batch totals."))) : null;

  const right = el("div", { class: "right" }, head, usagePanel);
  $app.replaceChildren(el("div", { class: "split" }, el("div", { class: "left" }, frame), right));

  if (isBusy(doc.status)) {
    right.append(el("div", { class: "panel empty" }, el("span", { class: "spin" }), L(() => statusText(doc.status)), "…"));
    pollTimer = setTimeout(() => { if (location.hash === `#/doc/${id}`) viewDoc(id); }, 2000);
    return;
  }
  if (doc.status === "error") {
    right.append(el("div", { class: "panel" }, el("b", { style: "color:#b42318" }, T("抽取失败：", "Extraction failed: ")),
      L(() => loc(doc.error))));
    return;
  }
  if (doc.status === "unmatched" || doc.status === "uploaded" || !tpl || !doc.data) {
    right.append(el("div", { class: "panel" },
      doc.status === "unmatched" ? T("没有找到匹配的模板。", "No matching template found.") : T("还没有抽取结果。", "No extraction result yet."),
      T(" 请在上方选择一个模板后点“用此模板重新抽取”，或者点“用此文档新建模板”。",
        " Choose a template above and click “Re-extract with this template”, or click “New template from this document”.")));
    return;
  }
  right.append(resultEditor(doc, tpl, () => viewDoc(id)));
}

function resultEditor(doc, tpl, reload) {
  const data = structuredClone(doc.data);
  const levels = issueMap(doc.issues);
  const wrap = el("div");

  if (doc.issues?.length || doc.notes) {
    wrap.append(el("div", { class: "panel" },
      el("b", {}, T("核对提示", "Review notes")),
      el("ul", { class: "issues" }, (doc.issues || []).map((i) => el("li", { class: i.level }, L(() => loc(i.msg))))),
      doc.notes ? el("div", { class: "small muted", style: "margin-top:6px" }, T("AI 备注：", "AI notes: "), doc.notes) : null));
  }

  const input = (f, value, onset, level, sized = false) => {
    if (f.type === "boolean") {
      return el("select", { class: cellClass(level), onchange: (e) => onset(e.target.value === "" ? null : e.target.value === "true") },
        opt("", "", value === null ? "" : String(value)), opt("true", T("是", "Yes"), String(value)), opt("false", T("否", "No"), String(value)));
    }
    // In the items table, size each input to its content so long IDs stay readable.
    const size = sized ? Math.min(Math.max(fmtVal(value).length + 2, f.type === "date" ? 11 : 6), 40) : undefined;
    return el("input", { type: "text", value: fmtVal(value), class: cellClass(level), size,
      placeholder: f.type === "date" ? "YYYY-MM-DD" : "", oninput: (e) => onset(e.target.value) });
  };

  const hdr = el("div", { class: "hdr-form" },
    tpl.header_fields.map((f) => [
      el("div", { class: "lbl" + (f.required ? " req" : ""), title: f.description }, f.label),
      input(f, data.header[f.key], (v) => (data.header[f.key] = v), levels[`header.${f.key}`]),
    ]));
  wrap.append(el("div", { class: "panel" }, el("h3", { style: "margin-top:0" }, T("表头字段", "Header fields")), hdr));

  if (tpl.item_fields.length) {
    const tbody = el("tbody");
    const renderRows = () => {
      tbody.replaceChildren(...data.items.map((row, ri) => el("tr", {},
        el("td", { class: "muted small" }, ri + 1),
        tpl.item_fields.map((f) => el("td", {}, input(f, row[f.key], (v) => (row[f.key] = v), levels[`items.${ri}.${f.key}`], true))),
        el("td", {}, el("button", { class: "link danger", title: T("删除此行", "Delete this row"),
          onclick: () => { data.items.splice(ri, 1); renderRows(); } }, "✕")))));
    };
    renderRows();
    wrap.append(el("div", { class: "panel" },
      el("div", { class: "row" }, el("h3", { style: "margin:0" }, tpl.items_label || T("明细", "Items")),
        el("span", { class: "muted small" }, L(() => pick(`${data.items.length} 行`, `${data.items.length} rows`))),
        el("span", { class: "spacer" }),
        el("button", { onclick: () => { data.items.push(Object.fromEntries(tpl.item_fields.map((f) => [f.key, null]))); renderRows(); } },
          T("+ 添加行", "+ Add row"))),
      el("div", { class: "table-wrap", style: "margin-top:8px" }, el("table", { class: "items-table" },
        el("thead", {}, el("tr", {}, el("th", {}, "#"), tpl.item_fields.map((f) => el("th", { title: f.description }, f.label)), el("th"))),
        tbody))));
  }

  const save = (verified) => async () => {
    await api("PUT", `/api/documents/${doc.id}/result`, { data, verified });
    toast(verified ? pick("已保存并标记为已核对", "Saved and marked as verified") : pick("已保存", "Saved"));
    reload();
  };
  const b1 = el("button", { class: "primary", onclick: () => guarded(b1, save(true)) }, T("保存并标记已核对", "Save & mark verified"));
  const b2 = el("button", { onclick: () => guarded(b2, save(false)) }, T("仅保存", "Save only"));
  const b3 = el("button", { onclick: () => {
    if (!confirm(pick("放弃所有修改，恢复 AI 原始抽取结果？", "Discard all changes and restore the original AI result?"))) return;
    api("PUT", `/api/documents/${doc.id}/result`, { data: doc.original_data, verified: false }).then(reload, (e) => toast(e.message, true));
  } }, T("恢复 AI 原始结果", "Restore AI result"));
  wrap.append(el("div", { class: "panel row" }, b1, b2, el("span", { class: "spacer" }), b3,
    el("div", { class: "small muted", style: "width:100%" }, T(
      "“已核对”的结果会作为该模板的参考样本，帮助以后同一供应商的文档抽得更准。",
      "Verified results are used as reference examples for this template, so future documents from the same vendor are extracted more accurately."))));
  return wrap;
}

// -------------------------------------------------------------- templates
async function viewTemplates() {
  const templates = await api("GET", "/api/templates");
  const headers = [["名称", "Name"], ["识别关键词", "Keywords"], ["页面筛选", "Page filter"], ["字段数", "Fields"], ["模型", "Model"],
    ["文档 / 已核对", "Docs / verified"], ["", ""]];
  $app.replaceChildren(el("div", { class: "panel" },
    el("div", { class: "row", style: "margin-bottom:12px" }, el("h2", { style: "margin:0" }, T("模板", "Templates")),
      el("span", { class: "muted small" }, T("一家供应商一个模板；上传的 PDF 会按“识别关键词”自动套用",
        "One template per vendor; uploaded PDFs are matched to a template by its keywords")),
      el("span", { class: "spacer" }),
      el("a", { class: "btn primary", href: "#/template/new", style: "color:#fff;background:var(--primary)" }, T("+ 新建模板", "+ New template"))),
    templates.length ? el("table", {},
      el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, T(...h))))),
      el("tbody", {}, templates.map((t) => el("tr", {},
        el("td", {}, el("a", { href: `#/template/${t.id}` }, t.name),
          t.auto_generated ? [" ", el("span", { class: "badge", title: T("由“按字段批量建模板”自动生成", "Created by “Create templates from fields”") }, T("自动生成", "Auto-generated"))] : null),
        el("td", { class: "small" }, L(() => t.keywords.join(pick("，", ", ")) || pick("（通用模板：不自动匹配）", "(generic: never matched automatically)"))),
        el("td", { class: "small muted" }, t.page_rule || T("全部页", "All pages")),
        el("td", {}, `${t.header_fields.length} + ${t.item_fields.length}`),
        el("td", { class: "small" }, t.provider === "default" ? T("默认", "Default") : t.provider, t.model ? ` / ${t.model}` : ""),
        el("td", {}, `${t.docs} / ${t.verified}`),
        el("td", {}, el("a", { class: "btn", href: `#/template/${t.id}` }, T("编辑", "Edit")), " ",
          el("button", { class: "link danger", onclick: async () => {
            if (!confirm(pick(`删除模板“${t.name}”？使用它的文档会变成“未识别”。`,
              `Delete template “${t.name}”? Documents using it will become unmatched.`))) return;
            await api("DELETE", `/api/templates/${t.id}`); viewTemplates();
          } }, T("删除", "Delete")))))))
      : el("div", { class: "empty" }, T("还没有模板。先在“文档”页上传一份 PDF，然后点“用此文档新建模板”，让 AI 推荐字段。",
        "No templates yet. Upload a PDF on the Documents page, then click “New template from this document” to let AI suggest fields."))));
}

function emptyTemplate() {
  return { name: "", keywords: [], page_rule: "", provider: "default", model: "", instructions: "", send_images: false, model_note: "",
    header_fields: [], item_fields: [], items_label: "", unique_field: "", checks: [] };
}

async function viewTemplate(id, params) {
  const [docs, settings, hintList] = await Promise.all([api("GET", "/api/documents"), api("GET", "/api/settings"),
    api("GET", "/api/hints").catch(() => ({ hints: [] }))]);
  let hints = hintList.hints;  // recently used "extra instructions" for AI suggest fields
  let hintText = "";
  FIELD_TYPES = settings.field_types || FIELD_TYPES;
  const tpl = id ? await api("GET", `/api/templates/${id}`) : emptyTemplate();
  let docId = Number(params.get("doc")) || (docs.find((d) => id && d.template_id === id) || docs[0] || {}).id || null;
  let testResult = null;
  // Item table (rows) is optional: off for new templates, on for existing ones that already have item fields.
  let hasItems = tpl.item_fields.length > 0 || !!tpl.items_label;

  const frame = el("iframe", { class: "pdf-frame" });
  const setFrame = () => { frame.src = docId ? `${BASE}/api/documents/${docId}/file#navpanes=0&view=FitH` : "about:blank"; };
  setFrame();
  const docSel = el("select", { onchange: (e) => { docId = Number(e.target.value) || null; setFrame(); } },
    opt("", T("选择样本文档…", "Choose a sample document…"), docId),
    docs.map((d) => opt(d.id, d.template_name ? L(() => d.filename + pick(`（${d.template_name}）`, ` (${d.template_name})`)) : d.filename, docId)));

  const right = el("div", { class: "right" });
  $app.replaceChildren(el("div", { class: "split" },
    el("div", { class: "left" }, el("div", { class: "row", style: "margin-bottom:8px" }, T("样本：", "Sample: "), docSel), frame), right));

  const fieldOptions = (sel, fields, current, emptyLabel) => {
    sel.replaceChildren(opt("", emptyLabel, current),
      ...fields.map((f) => opt(f.key, L(() => pick(`${f.label}（${f.key}）`, `${f.label} (${f.key})`)), current)));
  };

  function fieldsTable(list, title) {
    const body = el("tbody", {}, list.map((f, i) => el("tr", {},
      el("td", { style: "width:14%" }, el("input", { type: "text", value: f.label, placeholder: T("名称", "Label"), oninput: (e) => (f.label = e.target.value) })),
      el("td", { style: "width:13%" }, el("input", { type: "text", value: f.key, placeholder: T("英文 key", "English key"), oninput: (e) => (f.key = e.target.value) })),
      el("td", { style: "width:9%" }, el("select", { onchange: (e) => (f.type = e.target.value) },
        Object.entries(FIELD_TYPES).map(([k, v]) => opt(k, L(() => loc(v)), f.type || "text")))),
      el("td", { style: "width:5%;text-align:center" }, el("input", { type: "checkbox", checked: !!f.required, title: T("必填", "Required"),
        onchange: (e) => (f.required = e.target.checked) })),
      el("td", {}, el("input", { type: "text", value: f.description || "",
        placeholder: T("说明：在文档哪里、印刷的标签是什么", "Where it is on the document and what its printed label says"),
        oninput: (e) => (f.description = e.target.value) })),
      el("td", { style: "width:13%" }, el("input", { type: "text", value: f.example || "", placeholder: T("示例值", "Example value"),
        oninput: (e) => (f.example = e.target.value) })),
      el("td", { style: "width:90px;white-space:nowrap" },
        el("button", { class: "link", disabled: i === 0, onclick: () => { [list[i - 1], list[i]] = [list[i], list[i - 1]]; render(); } }, "↑"),
        el("button", { class: "link", disabled: i === list.length - 1, onclick: () => { [list[i + 1], list[i]] = [list[i], list[i + 1]]; render(); } }, "↓"),
        el("button", { class: "link danger", onclick: () => { list.splice(i, 1); render(); } }, "✕")))));
    const headers = [["名称", "Label"], ["Key", "Key"], ["类型", "Type"], ["必填", "Required"],
      ["说明（越具体越准）", "Description (the more specific, the more accurate)"], ["示例", "Example"], ["", ""]];
    return el("div", { class: "panel" },
      el("div", { class: "row" }, el("h3", { style: "margin:0" }, title), el("span", { class: "spacer" }),
        el("button", { onclick: () => { list.push({ key: "", label: "", type: "text", required: false, description: "", example: "" }); render(); } },
          T("+ 添加字段", "+ Add field"))),
      list.length ? el("table", { class: "fields-table", style: "margin-top:8px" },
        el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, T(...h))))), body)
        : el("div", { class: "muted small", style: "margin-top:8px" }, T("暂无字段", "No fields yet")));
  }

  function testPanel() {
    if (!testResult) return null;
    const r = testResult;
    const levels = issueMap(r.issues);
    return el("div", { class: "panel", style: "border-color:#93c5fd" },
      el("h3", { style: "margin-top:0" }, T("试抽取结果 ", "Test extraction result "),
        el("span", { class: "small muted" }, L(() => pick("页码 ", "Pages ") + `${r.pages.join(", ")} · ${r.model_used}`))),
      r.issues.length ? el("ul", { class: "issues" }, r.issues.map((i) => el("li", { class: i.level }, L(() => loc(i.msg)))))
        : el("div", { style: "color:var(--ok)" }, T("✓ 没有发现问题", "✓ No issues found")),
      r.notes ? el("div", { class: "small muted" }, T("AI 备注：", "AI notes: "), r.notes) : null,
      el("table", { style: "margin-top:8px" }, el("tbody", {}, tpl.header_fields.map((f) =>
        el("tr", {}, el("th", { style: "width:180px" }, f.label), el("td", { class: cellClass(levels[`header.${f.key}`]) }, fmtVal(r.data.header[f.key])))))),
      r.data.items.length ? el("div", { class: "table-wrap", style: "margin-top:10px" }, el("table", {},
        el("thead", {}, el("tr", {}, tpl.item_fields.map((f) => el("th", {}, f.label)))),
        el("tbody", {}, r.data.items.map((row, ri) => el("tr", {}, tpl.item_fields.map((f) =>
          el("td", { class: cellClass(levels[`items.${ri}.${f.key}`]) }, fmtVal(row[f.key])))))))) : null);
  }

  const needSample = () => pick("请先在左上角选择一份样本文档", "Choose a sample document in the top left first");

  function render() {
    const sum = tpl.checks.find((c) => c.type === "sum") || {};
    const noDup = T("不查重", "No duplicate check"), noCheck = T("不校验", "No check");
    const uniqSel = el("select", { onfocus: () => fieldOptions(uniqSel, tpl.header_fields, tpl.unique_field, noDup),
      onchange: (e) => (tpl.unique_field = e.target.value) });
    fieldOptions(uniqSel, tpl.header_fields, tpl.unique_field, noDup);
    const sumItem = el("select", { onfocus: () => fieldOptions(sumItem, tpl.item_fields, sum.item_field, noCheck), onchange: updSum });
    const sumHead = el("select", { onfocus: () => fieldOptions(sumHead, tpl.header_fields, sum.header_field, "—"), onchange: updSum });
    fieldOptions(sumItem, tpl.item_fields, sum.item_field, noCheck);
    fieldOptions(sumHead, tpl.header_fields, sum.header_field, "—");
    function updSum() {
      tpl.checks = tpl.checks.filter((c) => c.type !== "sum");
      if (sumItem.value && sumHead.value) tpl.checks.push({ type: "sum", item_field: sumItem.value, header_field: sumHead.value, tolerance: 0.01 });
    }

    const itemsToggle = el("input", { type: "checkbox", checked: hasItems, onchange: (e) => {
      if (e.target.checked) hasItems = true;
      else {
        if (tpl.item_fields.length &&
          !confirm(pick("取消后会清除已有的明细字段、明细表名称和合计校验，继续？", "This clears the existing item fields, items table name and sum check. Continue?"))) {
          e.target.checked = true;
          return;
        }
        hasItems = false;
        Object.assign(tpl, { item_fields: [], items_label: "", checks: tpl.checks.filter((c) => c.type !== "sum") });
      }
      render();
    } });
    const hint = el("input", { type: "text", style: "flex:1", value: hintText, oninput: (e) => (hintText = e.target.value),
      placeholder: T("可选：给 AI 的补充要求，如“只要发票号、金额和运单号”",
        "Optional: extra instructions for the AI, e.g. “only invoice number, amount and AWB number”") });
    const recentHints = hints.length ? el("select", { style: "max-width:240px", title: T("最近用过的补充要求，选一条填入输入框", "Recently used instructions; pick one to fill the box"),
      onchange: async (e) => {
        const v = e.target.value;
        if (v === "__clear") {
          if (!confirm(pick("清空所有已记住的补充要求？", "Forget all remembered instructions?"))) return render();
          hints = (await api("DELETE", "/api/hints")).hints;
        } else if (v) hintText = v;
        render();
      } },
      opt("", T("最近用过…", "Recent…"), ""),
      hints.map((h) => opt(h, h.length > 36 ? h.slice(0, 36) + "…" : h, "")),
      opt("__clear", T("清空历史", "Clear history"), "")) : null;
    const forgetHint = hints.includes(hintText.trim()) ? el("button", { class: "link danger",
      onclick: async () => { hints = (await api("DELETE", `/api/hints?text=${encodeURIComponent(hintText.trim())}`)).hints; render(); } },
      T("忘掉这条", "Forget this one")) : null;
    const suggestBtn = el("button", { onclick: () => guarded(suggestBtn, async () => {
      if (!docId) throw new Error(needSample());
      if ((tpl.header_fields.length || tpl.item_fields.length) &&
        !confirm(pick("AI 推荐会替换当前的字段设置，继续？", "AI suggestions will replace the current fields. Continue?"))) return;
      const s = await api("POST", "/api/templates/suggest", { doc_id: docId, hint: hint.value, lang: LANG, with_items: hasItems });
      hints = (await api("GET", "/api/hints")).hints;  // the server just remembered this one
      Object.assign(tpl, s, { name: tpl.name || s.name });
      testResult = null;
      render();
      toast(pick("已生成推荐字段，请检查后保存。", "Suggested fields are ready; review them, then save. ") + loc(s.model_note));
    }) }, T("✨ AI 推荐字段", "✨ AI suggest fields"));
    const pickBtn = el("button", { title: T("用样本文档从便宜到贵依次试抽取，选出够用的最便宜的模型",
      "Test-extract the sample with each model from cheapest to priciest and choose the cheapest one that is good enough"),
      onclick: () => guarded(pickBtn, async () => {
        if (!docId) throw new Error(needSample());
        if (!tpl.header_fields.length && !tpl.item_fields.length) throw new Error(pick("请先添加字段", "Add fields first"));
        const r = await api("POST", "/api/templates/pick-model", { doc_id: docId, template: tpl, template_id: id });
        Object.assign(tpl, { provider: r.provider, model: r.model, model_note: r.model_note });
        render();
        toast(pick("已选择模型，保存模板后生效", "Model chosen; it takes effect once you save the template"));
      }) }, T("🎯 自动选择模型", "🎯 Auto-pick model"));
    const presets = settings.model_presets || [];
    const modelKey = () => (tpl.provider === "default" && !tpl.model ? "default"
      : (presets.find((p) => p.provider === tpl.provider && p.model === tpl.model) || { key: "manual" }).key);
    const modelSel = el("select", { onchange: (e) => {
      const v = e.target.value;
      if (v === "default") Object.assign(tpl, { provider: "default", model: "" });
      else if (v === "manual") tpl.provider = tpl.provider === "default" ? "claude" : tpl.provider;
      else { const p = presets.find((x) => x.key === v); Object.assign(tpl, { provider: p.provider, model: p.model }); }
      tpl.model_note = "";
      render();
    } },
      opt("default", L(() => pick(`默认（跟随设置：${settings.settings.claude_model}）`, `Default (from settings: ${settings.settings.claude_model})`)), modelKey()),
      presets.map((p) => opt(p.key, L(() => loc(p.label)), modelKey())),
      opt("manual", T("手动指定…", "Specify manually…"), modelKey()));
    const manualInput = () => {
      const options = (settings.model_options || {})[tpl.provider];
      if (!Array.isArray(options)) return el("input", { type: "text", value: tpl.model, placeholder: pick("模型", "Model"), oninput: (e) => (tpl.model = e.target.value) });
      const models = [...options];
      if (tpl.model && !models.some((m) => m.id === tpl.model)) models.unshift({ id: tpl.model, name: tpl.model });
      return el("select", { onchange: (e) => (tpl.model = e.target.value) },
        opt("", pick("— 选择模型 —", "— choose a model —"), tpl.model),
        ...models.map((m) => opt(m.id, m.name && m.name !== m.id ? `${m.id}（${m.name}）` : m.id, tpl.model)));
    };
    const manualModel = modelKey() === "manual" ? el("div", { class: "row", style: "flex-wrap:nowrap" },
      el("select", { onchange: (e) => { tpl.provider = e.target.value; tpl.model = ""; render(); } },
        opt("claude", "Claude", tpl.provider), opt("deepseek", "DeepSeek", tpl.provider), opt("custom", T("自定义 / Ollama", "Custom / Ollama"), tpl.provider)),
      manualInput()) : el("div");
    const testBtn = el("button", { onclick: () => guarded(testBtn, async () => {
      if (!docId) throw new Error(needSample());
      testResult = await api("POST", "/api/templates/test", { doc_id: docId, template: tpl, template_id: id });
      render();
    }) }, T("▶ 试抽取", "▶ Test extraction"));
    const saveBtn = el("button", { class: "primary", onclick: () => guarded(saveBtn, async () => {
      if (!tpl.name.trim()) throw new Error(pick("请填写模板名称", "Enter a template name"));
      const saved = id ? await api("PUT", `/api/templates/${id}`, tpl) : await api("POST", "/api/templates", tpl);
      toast(pick("模板已保存", "Template saved"));
      if (!id) location.hash = `#/template/${saved.id}${docId ? "?doc=" + docId : ""}`;
      else { Object.assign(tpl, saved); render(); }
    }) }, T("保存模板", "Save template"));

    mount(right,
      el("div", { class: "panel" },
        el("div", { class: "row" }, el("a", { href: "#/templates" }, T("← 模板列表", "← Templates")), el("span", { class: "spacer" }), testBtn, saveBtn),
        el("h2", { style: "margin:10px 0" }, id ? L(() => pick("编辑模板：", "Edit template: ") + tpl.name) : T("新建模板", "New template")),
        el("div", { class: "row", style: "margin-bottom:12px" }, suggestBtn, hint, recentHints, forgetHint),
        el("div", { class: "grid2" },
          el("label", { class: "field" }, T("模板名称", "Template name"), el("input", { type: "text", value: tpl.name,
            placeholder: T("如：United Logistics 发票", "e.g. United Logistics Invoice"), oninput: (e) => (tpl.name = e.target.value) })),
          el("label", { class: "field" }, T("识别关键词（逗号分隔，命中越多越优先）", "Detection keywords (comma-separated; more matches = higher priority)"),
            el("input", { type: "text", value: tpl.keywords.join(", "), placeholder: T("供应商名、地址、邮箱域名…", "Vendor name, address, email domain…"),
              oninput: (e) => (tpl.keywords = e.target.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)) }))),
        el("label", { class: "field", style: "margin-top:10px" },
          T("页面筛选规则（多文档合订的 PDF 用；留空=处理全部页）", "Page filter rule (for PDFs that bundle several documents; leave empty to process all pages)"),
          el("input", { type: "text", value: tpl.page_rule, placeholder: T("如：只处理 MANPOWER LLC 开出的 INVOICE 页", "e.g. only the INVOICE pages issued by MANPOWER LLC"),
            oninput: (e) => (tpl.page_rule = e.target.value) })),
        el("div", { class: "grid3", style: "margin-top:10px" },
          el("label", { class: "field" }, T("抽取模型", "Extraction model"), modelSel),
          el("div", { class: "field" }, manualModel),
          hasItems ? el("label", { class: "field" }, T("明细表名称", "Items table name"), el("input", { type: "text", value: tpl.items_label,
            placeholder: T("如：运单明细", "e.g. AWB Details"), oninput: (e) => (tpl.items_label = e.target.value) })) : el("div")),
        el("div", { class: "row", style: "margin-top:6px" }, pickBtn,
          el("span", { class: "small muted", style: "flex:1" }, L(() => loc(tpl.model_note) || pick(
            "AI 推荐字段时会自动选择；之后这个模板的文档都用这里选定的模型。",
            "Chosen automatically when AI suggests fields; this template's documents then all use the model selected here.")))),
        el("label", { style: "display:block;margin-top:10px" },
          el("input", { type: "checkbox", checked: !!tpl.send_images, onchange: (e) => (tpl.send_images = e.target.checked) }),
          T(" 同时发送页面图片（更准但更贵）", " Also send page images (more accurate, but pricier)"),
          el("span", { class: "small muted" }, T(
            " 默认只发文字层；有文字层的页面抽错列、漏字时再打开。没有文字层的扫描页总是发图片。",
            " By default only the text layer is sent; turn this on if digital pages get columns mixed up or characters missed. Scanned pages without a text layer always send images."))),
        el("label", { class: "field", style: "margin-top:10px" }, T("给 AI 的额外说明（可选）", "Extra instructions for the AI (optional)"),
          el("textarea", { rows: 2, value: tpl.instructions,
            placeholder: T("如：金额列为空的行保持为空，不要自己分摊", "e.g. leave rows with an empty amount empty; don't split amounts yourself"),
            oninput: (e) => (tpl.instructions = e.target.value) })),
        el("label", { style: "display:block;margin-top:10px" }, itemsToggle,
          T(" 这份文档有明细表（表格的每一行都要抽取）", " This document has an item table (every table row is extracted)"),
          el("span", { class: "small muted" }, T(
            " 默认不需要。勾选后才会出现明细字段、明细表名称和合计校验，AI 推荐字段时也会一并推荐明细字段。",
            " Off by default. Turn it on to get item fields, an items table name and the sum check; AI suggestions then include item fields too."))),
        el("div", { class: "row", style: "margin-top:10px" },
          T("查重字段：", "Duplicate check field: "), uniqSel,
          hasItems ? [el("span", { style: "width:20px" }), T("合计校验：明细", "Sum check: items"), sumItem, T("之和 = 表头", "total = header"), sumHead] : null)),
      fieldsTable(tpl.header_fields, T("表头字段（每份文档一个值）", "Header fields (one value per document)")),
      hasItems ? fieldsTable(tpl.item_fields, T("明细字段（表格的每一行）", "Item fields (each table row)")) : null,
      testPanel(),
    );
  }
  render();
}

// ---------------------------------------------------------- output formats
async function viewFormats() {
  const formats = await api("GET", "/api/output-formats");
  $app.replaceChildren(el("div", { class: "panel" },
    el("div", { class: "row", style: "margin-bottom:12px" }, el("h2", { style: "margin:0" }, T("输出格式", "Output formats")),
      el("span", { class: "muted small" }, T("定义一套统一的列（如 ERP 导入格式），把各模板的字段对应过去，合并导出成一张表",
        "Define one set of columns (such as an ERP import layout), map each template's fields to it, and export everything as one table")),
      el("span", { class: "spacer" }),
      el("a", { class: "btn", href: "#/format/new", style: "color:#fff;background:var(--primary)" }, T("+ 新建输出格式", "+ New output format"))),
    formats.length ? el("table", {},
      el("thead", {}, el("tr", {}, [["名称", "Name"], ["列", "Columns"], ["", ""]].map((h) => el("th", {}, T(...h))))),
      el("tbody", {}, formats.map((f) => el("tr", {},
        el("td", {}, el("a", { href: `#/format/${f.id}` }, f.name)),
        el("td", { class: "small" }, f.columns.join(" | ")),
        el("td", {}, el("a", { class: "btn", href: `#/format/${f.id}` }, T("编辑", "Edit")), " ",
          el("button", { class: "link danger", onclick: async () => {
            if (!confirm(pick(`删除输出格式“${f.name}”？`, `Delete output format “${f.name}”?`))) return;
            await api("DELETE", `/api/output-formats/${f.id}`); viewFormats();
          } }, T("删除", "Delete"))))))) : el("div", { class: "empty" }, T("还没有输出格式", "No output formats yet"))));
}

const DEFAULT_COLUMNS = {
  zh: ["供应商", "发票号", "发票日期", "运单号", "件数", "重量", "金额", "发票总额", "来源文件"],
  en: ["Vendor", "Invoice No.", "Invoice Date", "AWB No.", "Pieces", "Weight", "Amount", "Invoice Total", "Source File"],
};

async function viewFormat(id) {
  const templates = await api("GET", "/api/templates");
  const fmt = id ? await api("GET", `/api/output-formats/${id}`).catch(async () => (await api("GET", "/api/output-formats")).find((f) => f.id === id))
    : { name: "", columns: [...DEFAULT_COLUMNS[LANG]], mappings: {}, allocate: {} };
  fmt.allocate = Object.assign({ enabled: false, amount_col: "", weight_col: "", method: "weight" }, fmt.allocate || {});

  function sourceSelect(t, col) {
    const m = (fmt.mappings[t.id] ||= {});
    const cur = m[col] || "";
    const isConst = cur.startsWith("const:");
    const constInput = el("input", { type: "text", value: isConst ? cur.slice(6) : "", placeholder: T("固定值", "Fixed value"),
      style: isConst ? "" : "display:none", oninput: (e) => (m[col] = "const:" + e.target.value) });
    const sel = el("select", { onchange: (e) => {
      if (e.target.value === "const:") { constInput.style.display = ""; m[col] = "const:" + constInput.value; }
      else { constInput.style.display = "none"; m[col] = e.target.value; }
    } },
      opt("", T("（留空）", "(empty)"), isConst ? "const:" : cur),
      el("optgroup", { label: T("文件信息", "File info") }, opt("meta.filename", T("来源文件名", "Source file name"), cur),
        opt("meta.template", T("模板名称", "Template name"), cur), opt("meta.pages", T("抽取页码", "Extracted pages"), cur),
        opt("meta.batch", T("批次号", "Batch No."), cur)),
      el("optgroup", { label: T("表头字段", "Header fields") }, t.header_fields.map((f) => opt(`header.${f.key}`, f.label, cur))),
      t.item_fields.length ? el("optgroup", { label: T("明细字段", "Item fields") }, t.item_fields.map((f) => opt(`item.${f.key}`, f.label, cur))) : null,
      opt("const:", T("固定值…", "Fixed value…"), isConst ? "const:" : cur));
    return el("div", { class: "row", style: "flex-wrap:nowrap" }, sel, constInput);
  }

  function render() {
    const colSel = (key, emptyLabel) => el("select", { onchange: (e) => (fmt.allocate[key] = e.target.value) },
      opt("", emptyLabel, fmt.allocate[key]), fmt.columns.map((c) => opt(c, c, fmt.allocate[key])));
    const saveBtn = el("button", { class: "primary", onclick: () => guarded(saveBtn, async () => {
      if (!fmt.name.trim()) throw new Error(pick("请填写名称", "Enter a name"));
      const saved = id ? await api("PUT", `/api/output-formats/${id}`, fmt) : await api("POST", "/api/output-formats", fmt);
      toast(pick("已保存", "Saved"));
      if (!id) location.hash = `#/format/${saved.id}`;
    }) }, T("保存", "Save"));

    mount($app,
      el("div", { class: "panel" },
        el("div", { class: "row" }, el("a", { href: "#/formats" }, T("← 输出格式列表", "← Output formats")), el("span", { class: "spacer" }), saveBtn),
        el("h2", { style: "margin:10px 0" }, id ? L(() => pick("编辑输出格式：", "Edit output format: ") + fmt.name) : T("新建输出格式", "New output format")),
        el("div", { class: "grid2" },
          el("label", { class: "field" }, T("名称", "Name"), el("input", { type: "text", value: fmt.name,
            placeholder: T("如：ERP 应付导入", "e.g. ERP AP import"), oninput: (e) => (fmt.name = e.target.value) })),
          el("label", { class: "field" }, T("列（每行一个，按顺序；修改后点击别处生效）", "Columns (one per line, in order; click elsewhere to apply)"),
            el("textarea", { rows: 10, value: fmt.columns.join("\n"),
              onchange: (e) => { fmt.columns = e.target.value.split("\n").map((s) => s.trim()).filter(Boolean); render(); } }))),
        el("h3", {}, T("金额分摊（一笔费用对应多个运单时）", "Amount allocation (when one charge covers several AWBs)")),
        el("div", { class: "row" },
          el("label", {}, el("input", { type: "checkbox", checked: fmt.allocate.enabled, onchange: (e) => (fmt.allocate.enabled = e.target.checked) }),
            T(" 启用", " Enabled")),
          T("金额列", "Amount column"), colSel("amount_col", T("选择…", "Choose…")),
          T("方式", "Method"), el("select", { onchange: (e) => (fmt.allocate.method = e.target.value) },
            opt("weight", T("按重量", "By weight"), fmt.allocate.method), opt("even", T("平均", "Evenly"), fmt.allocate.method)),
          T("重量列", "Weight column"), colSel("weight_col", T("选择…", "Choose…"))),
        el("div", { class: "small muted", style: "margin-top:4px" }, T(
          "同一份文档里只有部分行有金额时（如 Manpower 一笔 $325 对应 3 个运单），把合计金额分摊到每一行，并加一列“分摊说明”。",
          "When only some rows of a document have an amount (e.g. one Manpower charge of $325 for 3 AWBs), the total is spread over every row and an “Allocation Note” column is added."))),
      templates.length ? templates.map((t) => {
        const aiBtn = el("button", { onclick: () => guarded(aiBtn, async () => {
          fmt.mappings[t.id] = await api("POST", "/api/output-formats/suggest-mapping", { columns: fmt.columns, template_id: t.id });
          render();
        }) }, T("✨ AI 自动对应", "✨ AI auto-map"));
        return el("div", { class: "panel" },
          el("div", { class: "row" }, el("h3", { style: "margin:0" }, t.name), el("span", { class: "spacer" }), aiBtn),
          el("table", { class: "mapping-table", style: "margin-top:8px" },
            el("thead", {}, el("tr", {}, el("th", {}, T("输出列", "Output column")), el("th", {}, T("来自模板的字段", "Template field")))),
            el("tbody", {}, fmt.columns.map((c) => el("tr", {}, el("td", {}, c), el("td", {}, sourceSelect(t, c)))))));
      }) : el("div", { class: "panel empty" }, T("还没有模板", "No templates yet")),
    );
  }
  render();
}

// ------------------------------------------------------------------- usage
const usageState = { days: 30, batch: "" };

async function viewUsage() {
  const q = new URLSearchParams({ days: usageState.days });
  if (usageState.batch) q.set("batch_id", usageState.batch);
  const [r, batches] = await Promise.all([api("GET", `/api/usage?${q}`), api("GET", "/api/batches")]);
  if (!(location.hash || "").startsWith("#/usage")) return;

  const stat = (k, v, sub) => el("div", { class: "stat" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v),
    sub ? el("div", { class: "k" }, sub) : null);
  const cols = [["调用", "Calls"], ["输入", "Input"], ["输出", "Output"], ["缓存读", "Cache read"], ["总 Token", "Total tokens"],
    ["平均耗时", "Avg time"], ["估算成本", "Est. cost"]];
  const table = (title, rows, first, limit = 50) => el("div", { class: "panel" },
    el("h3", { style: "margin-top:0" }, title),
    rows.length ? el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, first), cols.map((h) => el("th", { class: "num" }, T(...h))))),
      el("tbody", {}, rows.slice(0, limit).map((x) => el("tr", {},
        el("td", {}, L(() => loc(x.label))),
        el("td", { class: "num" }, x.calls), el("td", { class: "num" }, x.input.toLocaleString()), el("td", { class: "num" }, x.output.toLocaleString()),
        el("td", { class: "num" }, x.cache_read.toLocaleString()), el("td", { class: "num" }, fmtTok(x.tokens)),
        el("td", { class: "num", title: L(() => pick("每次调用的平均耗时（不含升级前没有计时的记录）", "Average time per call (calls recorded before timing was added are not counted)")) }, fmtMs(avgMs(x))),
        el("td", { class: "num", title: x.unpriced ? L(() => pick(`${x.unpriced} 次调用没有单价`, `${x.unpriced} calls without a price`)) : "" },
          L(() => costText(x)))))))) : el("div", { class: "muted small" }, T("暂无数据", "No data")));

  mount($app,
    el("div", { class: "row", style: "margin-bottom:12px" }, el("h2", { style: "margin:0" }, T("用量与成本", "Usage & cost")),
      el("span", { class: "muted small" }, T("成本按“设置”里的模型单价估算，实际以服务商账单为准",
        "Costs are estimated from the model prices in Settings; your provider's bill is what counts")),
      el("span", { class: "spacer" }),
      el("select", { onchange: (e) => { usageState.days = Number(e.target.value); viewUsage(); } },
        [[7, "最近 7 天", "Last 7 days"], [30, "最近 30 天", "Last 30 days"], [90, "最近 90 天", "Last 90 days"], [0, "全部时间", "All time"]]
          .map(([v, zh, en]) => opt(v, T(zh, en), usageState.days))),
      el("select", { onchange: (e) => { usageState.batch = e.target.value; viewUsage(); } },
        opt("", T("全部批次", "All batches"), usageState.batch), batches.map((b) => opt(b.id, L(() => loc(b.label)), usageState.batch))),
      el("button", { onclick: () => viewUsage() }, T("刷新", "Refresh"))),
    r.total.unpriced ? el("div", { class: "panel", style: "background:#fffbeb;border-color:#fcd34d" },
      L(() => pick(`有 ${r.total.unpriced} 次调用的型号没有单价，成本没有计入。请到`,
        `${r.total.unpriced} calls use a model without a price, so their cost is not included. Add model prices in `)),
      IS_ADMIN ? el("a", { href: "#/settings" }, T("设置", "Settings")) : T("设置", "Settings"), T("里补充模型单价。", ".")) : null,
    el("div", { class: "stats" },
      stat(T("估算总成本", "Est. total cost"), L(() => costText(r.total)), L(() => pick(`${r.total.calls} 次调用`, `${r.total.calls} calls`))),
      stat(T("总 Token", "Total tokens"), fmtTok(r.total.tokens), L(() => {
        const inp = fmtTok(r.total.input + r.total.cache_read + r.total.cache_write), out = fmtTok(r.total.output);
        return pick(`输入 ${inp} · 输出 ${out}`, `Input ${inp} · Output ${out}`);
      })),
      stat(T("已处理文档", "Documents processed"), r.docs, T("只统计自动处理的文档", "Automatic processing only")),
      stat(T("平均每份文档", "Avg per document"), r.avg_cost_per_doc === null ? "—" : fmtCost(r.avg_cost_per_doc),
        r.avg_tokens_per_doc === null ? "" : `${fmtTok(r.avg_tokens_per_doc)} token`),
      stat(T("平均每份耗时", "Avg time per document"), fmtMs(r.avg_ms_per_doc), T("从开始处理到完成，不含排队", "From start of processing to done, excluding queue time")),
      stat(T("文档处理成本", "Processing cost"), L(() => costText(r.auto)), T("其余为模板试抽取、AI 推荐等", "The rest is test extractions, AI suggestions, etc."))),
    table(T("按批次（文档处理）", "By batch (processing)"), r.by_batch, T("批次", "Batch")),
    table(T("按模板（文档处理）", "By template (processing)"), r.by_template, T("模板", "Template")),
    table(T("按用途", "By purpose"), r.by_purpose, T("用途", "Purpose")),
    table(T("按模型", "By model"), r.by_model, T("模型", "Model")),
    table(T("按日期", "By date"), r.by_day, T("日期", "Date"), 31));
}

// ---------------------------------------------------------------- settings
async function viewSettings() {
  const { settings: s, providers, model_presets: presets } = await api("GET", "/api/settings");
  const inp = (key, type = "text") => el("input", { type, value: s[key], oninput: (e) => (s[key] = type === "number" ? Number(e.target.value) : e.target.value) });
  const ok = (b) => el("span", { class: `badge ${b ? "done" : "error"}` }, b ? T("Key 已配置", "Key configured") : T("未配置 Key", "Key not configured"));

  // Each vendor has a "Models" dialog where the administrator fetches the vendor's models and switches on the ones
  // that may be used. Only switched-on models (plus the one currently saved) appear in the model drop-downs.
  s.model_catalog ||= {};
  s.enabled_models ||= {};
  const modelSelects = [];
  const newestFirst = (list) => [...list].sort((a, b) => (b.created || 0) - (a.created || 0) || b.id.localeCompare(a.id));
  const modelLabel = (m) => (m.name && m.name !== m.id ? `${m.id}（${m.name}）` : m.id);
  const enabledModels = (provider) => {
    const on = new Set(s.enabled_models[provider] || []);
    const known = s.model_catalog[provider] || [];
    const listed = newestFirst(known).filter((m) => on.has(m.id));
    const extra = [...on].filter((id) => !known.some((m) => m.id === id)).map((id) => ({ id, name: id }));
    return [...listed, ...extra];
  };
  const modelInput = (key, provider) => {
    const select = el("select", { onchange: (e) => { s[key] = e.target.value; } });
    const refresh = () => {
      const models = enabledModels(provider);
      if (s[key] && !models.some((m) => m.id === s[key])) models.unshift({ id: s[key], name: s[key] });
      select.replaceChildren(...models.map((m) => opt(m.id, modelLabel(m), s[key])));
    };
    refresh();
    modelSelects.push({ provider, refresh });
    return select;
  };
  const EYE = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17.9 17.9A10.6 10.6 0 0 1 12 19C5.6 19 2 12 2 12a18 18 0 0 1 4.1-5M9.9 5.2A9.9 9.9 0 0 1 12 5c6.4 0 10 7 10 7a18 18 0 0 1-2.2 3.2M1 1l22 22M14.1 14.1a3 3 0 0 1-4.2-4.2"/></svg>';
  const openModels = (provider, vendor) => {
    let list = newestFirst((s.model_catalog[provider] || []));
    const on = new Set(s.enabled_models[provider] || []);
    for (const id of on) if (!list.some((m) => m.id === id)) list.push({ id, name: id, created: 0 });
    let filter = "";
    const dlg = el("dialog", { class: "models-dialog" });
    const rowsBox = el("div", { class: "models-list" });
    const count = el("span", { class: "muted" });
    const status = el("span", { class: "small muted" });
    const visible = () => {
      const q = filter.trim().toLowerCase();
      return list.filter((m) => !q || m.id.toLowerCase().includes(q) || (m.name || "").toLowerCase().includes(q));
    };
    function renderRows() {
      count.textContent = `(${list.length})`;
      const rows = visible();
      rowsBox.replaceChildren(...[...rows.map((m) => {
        const shown = on.has(m.id);
        const eye = el("button", { type: "button", class: `eye${shown ? " on" : ""}`, title: pick(shown ? "可用,点击隐藏" : "不可用,点击启用", shown ? "Available - click to hide" : "Unavailable - click to enable"),
          onclick: () => { if (shown) on.delete(m.id); else on.add(m.id); renderRows(); } });
        eye.innerHTML = shown ? EYE : EYE_OFF;
        return el("div", { class: `model-row${shown ? "" : " off"}` },
          el("div", { class: "model-name" }, el("strong", {}, m.name || m.id), m.name && m.name !== m.id ? el("span", { class: "small muted" }, m.id) : null),
          eye);
      }), rows.length ? null : el("p", { class: "muted" }, list.length ? T("没有匹配的模型。", "No models match.") : T("还没有模型。点击“获取可用模型”从厂商读取。", "No models yet. Use Fetch available models to read them from the vendor."))].filter(Boolean));
    }
    const fetchBtn = el("button", { type: "button", onclick: () => guarded(fetchBtn, async () => {
      status.textContent = "";
      try {
        const { models } = await api("GET", `/api/models/${provider}`);
        const seen = new Set(models.map((m) => m.id));
        list = newestFirst([...models, ...list.filter((m) => !seen.has(m.id) && on.has(m.id))]);
        status.textContent = pick(`获取到 ${models.length} 个模型`, `${models.length} models fetched`);
        renderRows();
      } catch (e) { toast(e.message, true); }
    }) }, T("获取可用模型", "Fetch available models"));
    const setVisible = (value) => () => { for (const m of visible()) { if (value) on.add(m.id); else on.delete(m.id); } renderRows(); };
    const doneBtn = el("button", { class: "primary", type: "button", onclick: () => guarded(doneBtn, async () => {
      s.model_catalog[provider] = list.map(({ id, name, created }) => ({ id, name, created: created || 0 }));
      s.enabled_models[provider] = list.filter((m) => on.has(m.id)).map((m) => m.id);
      await api("PUT", "/api/settings", { model_catalog: s.model_catalog, enabled_models: s.enabled_models });
      for (const m of modelSelects) if (m.provider === provider) m.refresh();
      refreshCounts();
      toast(pick("已保存可用模型", "Available models saved"));
      dlg.close();
    }) }, T("完成", "Done"));
    dlg.addEventListener("close", () => dlg.remove());
    dlg.append(el("div", { class: "models-head" },
      el("h3", { style: "margin:0" }, vendor, " ", T("可用模型", "Available models"), " ", count),
      el("span", { class: "spacer" }), fetchBtn, status),
      el("div", { class: "models-tools" },
        el("input", { type: "text", placeholder: pick("筛选模型…", "Filter models…"), oninput: (e) => { filter = e.target.value; renderRows(); } }),
        el("button", { type: "button", onclick: setVisible(false) }, T("全部隐藏", "Hide all")),
        el("button", { type: "button", onclick: setVisible(true) }, T("全部显示", "Show all"))),
      rowsBox,
      el("div", { class: "models-foot" }, el("button", { type: "button", onclick: () => dlg.close() }, T("取消", "Cancel")), doneBtn));
    document.body.append(dlg);
    renderRows();
    dlg.showModal();
  };
  const countLabels = [];
  const refreshCounts = () => countLabels.forEach((f) => f());
  const fetchModels = (provider, vendor) => {
    const summary = el("span", { class: "small muted", style: "margin-left:8px" });
    const update = () => { const n = (s.enabled_models[provider] || []).length; summary.textContent = pick(`已启用 ${n} 个模型`, `${n} models enabled`); };
    countLabels.push(update);
    update();
    return el("span", {}, el("button", { type: "button", onclick: () => openModels(provider, vendor) }, T("模型", "Models")), summary);
  };
  const saveBtn = el("button", { class: "primary", onclick: () => guarded(saveBtn, async () => {
    await api("PUT", "/api/settings", s); toast(pick("设置已保存", "Settings saved"));
  }) }, T("保存设置", "Save settings"));

  s.prices ||= {};
  const priceBox = el("div");
  const PRICE_COLS = [["input", "输入", "Input"], ["output", "输出", "Output"], ["cache_read", "缓存读取", "Cache read"],
    ["cache_write", "缓存写入", "Cache write"], ["peak_multiplier", "高峰倍率", "Peak multiplier"],
    ["long_threshold", "长上下文阈值（token）", "Long-context threshold (tokens)"], ["long_input", "长上下文输入", "Long-context input"],
    ["long_output", "长上下文输出", "Long-context output"]];
  const newModel = el("input", { type: "text", placeholder: T("型号名，如 claude-sonnet-5-5", "Model name, e.g. claude-sonnet-5-5"), style: "width:240px" });
  function renderPrices() {
    mount(priceBox,
      el("div", { class: "table-wrap" }, el("table", { class: "price-table" },
        el("thead", {}, el("tr", {}, el("th", {}, T("型号", "Model")), PRICE_COLS.map(([, zh, en]) => el("th", {}, T(zh, en))), el("th"))),
        el("tbody", {}, Object.entries(s.prices).map(([model, p]) => el("tr", {},
          el("td", { class: "model" }, model),
          PRICE_COLS.map(([k]) => el("td", {}, el("input", { type: "number", step: "any", min: "0", value: p[k] ?? "",
            oninput: (e) => { if (e.target.value === "") delete p[k]; else p[k] = Number(e.target.value); } }))),
          el("td", {}, el("button", { class: "link danger", onclick: () => { delete s.prices[model]; renderPrices(); } }, "✕"))))))),
      el("div", { class: "row", style: "margin-top:8px" }, newModel,
        el("button", { onclick: () => {
          const m = newModel.value.trim();
          if (!m) return;
          s.prices[m] ||= { input: 0, output: 0, cache_read: 0, cache_write: 0 };
          newModel.value = ""; renderPrices();
        } }, T("+ 添加型号", "+ Add model"))));
  }
  renderPrices();

  $app.replaceChildren(el("div", { class: "panel", style: "max-width:1100px" },
    el("h2", {}, T("设置", "Settings")),
    el("label", { class: "field" }, T("默认抽取模型（模板里选“默认”时使用）", "Default extraction model (used when a template is set to “Default”)"),
      el("select", { onchange: (e) => (s.default_provider = e.target.value) },
        opt("claude", "Claude", s.default_provider), opt("deepseek", "DeepSeek", s.default_provider),
        opt("custom", T("自定义 / Ollama", "Custom / Ollama"), s.default_provider))),
    el("label", { class: "field" }, T("AI 字段对应使用的模型（输出格式页的“AI 自动对应”）", "Model for AI field mapping (“AI auto-map” on the Output format page)"),
      el("select", { onchange: (e) => (s.mapping_model = e.target.value) },
        opt("default", T("跟随默认抽取模型", "Follow the default extraction model"), s.mapping_model),
        presets.map((p) => opt(p.key, L(() => loc(p.label)), s.mapping_model)))),
    el("label", { class: "field", style: "margin-top:10px" }, T("我方公司名称（每行一个；批量建模板识别供应商和生成关键词时会排除它们）",
      "Our own company names (one per line; excluded when batch templating works out vendors and keywords)"),
      el("textarea", { rows: 3, value: s.own_companies || "", oninput: (e) => (s.own_companies = e.target.value),
        placeholder: pick("如：Apex Logistics International Inc", "e.g. Apex Logistics International Inc") })),
    el("h3", {}, "Claude ", ok(providers.claude), " ", providers.claude ? fetchModels("claude", "Claude") : null),
    el("div", { class: "grid2" },
      el("label", { class: "field" }, T("抽取型号", "Extraction model"), modelInput("claude_model", "claude")),
      el("label", { class: "field" }, T("快速型号（识别模板、筛选页面）", "Fast model (template detection, page filtering)"), modelInput("claude_fast_model", "claude"))),
    el("h3", {}, "DeepSeek ", ok(providers.deepseek), " ", providers.deepseek ? fetchModels("deepseek", "DeepSeek") : null),
    el("label", { class: "field" }, T("型号（只能处理有文字层的电子版 PDF）", "Model (digital PDFs with a text layer only)"), modelInput("deepseek_model", "deepseek")),
    el("h3", {}, T("自定义 OpenAI 兼容接口（如 Ollama）", "Custom OpenAI-compatible endpoint (e.g. Ollama)")),
    el("div", { class: "grid3" },
      el("label", { class: "field" }, "Base URL", inp("custom_base_url")),
      el("label", { class: "field" }, "API Key", inp("custom_api_key")),
      el("label", { class: "field" }, T("型号", "Model"), inp("custom_model"))),
    el("label", { style: "display:block;margin-top:8px" }, el("input", { type: "checkbox", checked: s.custom_vision,
      onchange: (e) => (s.custom_vision = e.target.checked) }), T(" 该模型支持图片（视觉模型）", " This model accepts images (vision model)")),
    el("h3", {}, T("其他", "Other")),
    el("div", { class: "grid3" },
      el("label", { class: "field" }, T("页面渲染 DPI", "Page render DPI"), inp("render_dpi", "number")),
      el("label", { class: "field" }, T("参考样本数（已核对的结果）", "Reference examples (verified results)"), inp("few_shot_examples", "number")),
      el("label", { class: "field" }, T("同时处理文档数（0 = 自动，按待处理份数调整；固定值上限 32）", "Documents processed in parallel (0 = automatic, follows the number waiting; fixed values up to 32)"), inp("max_workers", "number")),
      el("label", { class: "field" }, T("自动模式的并行上限（服务商经常限流就调小）", "Parallel limit in automatic mode (lower it if the provider keeps rate-limiting)"), inp("max_workers_cap", "number"))),
    el("label", { style: "display:block;margin-top:8px" }, el("input", { type: "checkbox", checked: s.image_grayscale,
      onchange: (e) => (s.image_grayscale = e.target.checked) }),
      T(" 页面图片转灰度后再发送（更小更快；彩色标记或彩色笔迹分不清时关闭）",
        " Send page images in grayscale (smaller and faster; turn off if colored marks or ink matter)")),
    el("h3", {}, T("模型单价（美元 / 百万 token，用于估算成本）", "Model prices (USD per million tokens, used for cost estimates)")),
    priceBox,
    el("div", { class: "small muted", style: "margin-top:6px" }, T(
      "预填的是官方定价页上的价格（2026-10-08 查询），价格会调整，请定期核对。型号按前缀匹配（带日期后缀的型号会用同名前缀的价格）；自定义 / Ollama 型号没填单价时按 0 计算，其他没填单价的型号不计成本。" +
      "“高峰倍率”：DeepSeek 在 UTC 周一至周五 01:00–04:00、06:00–10:00 按 2 倍计价（这里没有排除中国法定假日，假日期间会略微高估）；" +
      "“长上下文”：Haiku 5.5 单次提示超过 10 万 token 时按更高价格计费，缓存价格随之同比调整。",
      "Prices are pre-filled from the official pricing pages (checked 2026-10-08); prices change, so check them regularly. Models are matched by prefix (a model with a date suffix uses the price of the same prefix). Custom / Ollama models without a price count as 0; other models without a price are left out of the cost. " +
      "“Peak multiplier”: DeepSeek charges 2× on weekdays (Mon–Fri) 01:00–04:00 and 06:00–10:00 UTC (Chinese public holidays are not excluded here, so holidays are slightly overestimated). " +
      "“Long context”: Haiku 5.5 charges a higher price when a single prompt exceeds 100k tokens, and cache prices scale accordingly.")),
    el("div", { class: "small muted", style: "margin-top:10px" }, T(
      "API Key 保存在项目根目录的 .env 文件中（ANTHROPIC_API_KEY / DEEPSEEK_API_KEY），修改后需重启程序。",
      "API keys are stored in the .env file in the project root (ANTHROPIC_API_KEY / DEEPSEEK_API_KEY); restart the app after changing them.")),
    el("div", { style: "margin-top:16px" }, saveBtn)));
}

document.getElementById("lang-btn").addEventListener("click", () => setLang(LANG === "zh" ? "en" : "zh"));
setLang(LANG);
route();
