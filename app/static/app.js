/* Jobs dashboard — vanilla JS, no build step.
   All job-derived text goes through textContent (never innerHTML) so scraped data can't inject markup. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const LEVEL_LABEL = { intern: "Internship", entry: "Entry level", mid: "Mid level", senior: "Senior", unknown: "Level n/a" };
const SOURCE_LABEL = {
  greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", remotive: "Remotive",
  himalayas: "Himalayas", arbeitnow: "Arbeitnow", adzuna: "Adzuna",
  wellfound: "Wellfound", yc: "YC Work at a Startup",
};
const PAGE = 30;
const DEFAULTS = { q: "", min: "50", level: [], loc: [], src: [], status: "", days: "", sort: "score", company: "", lpa: "" };

const state = {
  f: { ...DEFAULTS, level: [], loc: [], src: [] },
  items: [], total: 0, loading: false, error: null, reqId: 0,
  stats: null, profile: null, sources: [],
  pollTimer: null, lastFocus: null,
};

/* ------------------------------------------------------------------ helpers */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) node.append(c.nodeType ? c : document.createTextNode(String(c)));
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = {
  bookmark: '<path class="fillable" d="M4.5 2.5h7a1 1 0 0 1 1 1v10l-4.5-3-4.5 3v-10a1 1 0 0 1 1-1z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>',
  check: '<path d="M3 8.5l3.2 3.2L13 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
  x: '<path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  arrow: '<path d="M5 11l6-6M6 5h5v5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
  undo: '<path d="M6 4L3 7l3 3M3 7h6a4 4 0 0 1 0 8H7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
};
function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.innerHTML = ICONS[name]; // static, trusted literals only
  return svg;
}

function safeUrl(u) {
  try {
    const url = new URL(u);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch { return null; }
}

function timeAgo(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  if (s < 86400 * 365) return `${Math.floor(s / (86400 * 30))}mo ago`;
  return `${Math.floor(s / (86400 * 365))}y ago`;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); if (j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* keep default */ }
    const err = new Error(detail); err.status = res.status; throw err;
  }
  return res.status === 204 ? null : res.json();
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

/* ------------------------------------------------------------ filter <-> URL */
function readUrl() {
  const p = new URLSearchParams(location.search);
  const list = (k) => (p.get(k) || "").split(",").filter(Boolean);
  const f = { ...DEFAULTS, level: [], loc: [], src: [] };
  if (p.has("q")) f.q = p.get("q");
  if (p.has("min")) f.min = String(parseInt(p.get("min"), 10) || 0);
  if (p.has("company")) f.company = p.get("company");
  if (p.get("lpa") === "listed" || Number(p.get("lpa")) > 0) f.lpa = p.get("lpa");
  f.level = list("level"); f.loc = list("loc"); f.src = list("src");
  if (["saved", "applied", "dismissed"].includes(p.get("status"))) f.status = p.get("status");
  if (["1", "7", "30"].includes(p.get("days"))) f.days = p.get("days");
  if (["score", "newest", "company", "salary"].includes(p.get("sort"))) f.sort = p.get("sort");
  state.f = f;
}

function writeUrl() {
  const f = state.f, p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.min !== DEFAULTS.min) p.set("min", f.min);
  if (f.company) p.set("company", f.company);
  if (f.lpa) p.set("lpa", f.lpa);
  if (f.level.length) p.set("level", f.level.join(","));
  if (f.loc.length) p.set("loc", f.loc.join(","));
  if (f.src.length) p.set("src", f.src.join(","));
  if (f.status) p.set("status", f.status);
  if (f.days) p.set("days", f.days);
  if (f.sort !== DEFAULTS.sort) p.set("sort", f.sort);
  const qs = p.toString();
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

function queryString(offset = 0) {
  const f = state.f, p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.min && f.min !== "0") p.set("min_score", f.min);
  if (f.company) p.set("company", f.company);
  if (f.lpa === "listed") p.set("has_salary", "true");
  else if (f.lpa) p.set("min_lpa", f.lpa);
  if (f.level.length) p.set("level", f.level.join(","));
  if (f.loc.length) p.set("location", f.loc.join(","));
  if (f.src.length) p.set("source", f.src.join(","));
  if (f.status) p.set("status", f.status);
  if (f.days) p.set("days", f.days);
  p.set("sort", f.sort); p.set("limit", PAGE); p.set("offset", offset);
  return p.toString();
}

/* ------------------------------------------------------------- sync UI <- state */
function syncControls() {
  const f = state.f;
  $("#q").value = f.q;
  $("#f-company").value = f.company;
  $("#f-days").value = f.days;
  $("#f-lpa").value = f.lpa;
  $("#sort").value = f.sort;
  $$("#f-score .seg__btn").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.value === f.min)));
  $$("#f-level input").forEach((i) => { i.checked = f.level.includes(i.value); });
  $$("#f-location input").forEach((i) => { i.checked = f.loc.includes(i.value); });
  $$("#f-source input").forEach((i) => { i.checked = f.src.includes(i.value); });
  $$("#status-tabs .tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.value === f.status)));
}

function buildSourceFilter() {
  const box = $("#f-source");
  box.replaceChildren();
  for (const [name, n] of state.sources) {
    const input = el("input", { type: "checkbox", value: name });
    input.checked = state.f.src.includes(name);
    input.addEventListener("change", () => {
      toggleIn(state.f.src, name, input.checked); refresh();
    });
    box.append(el("label", { class: "check" }, input, el("span", { text: SOURCE_LABEL[name] || name }), el("span", { class: "n", text: String(n) })));
  }
}

function toggleIn(arr, value, on) {
  const i = arr.indexOf(value);
  if (on && i < 0) arr.push(value);
  if (!on && i >= 0) arr.splice(i, 1);
}

function renderChips() {
  const f = state.f, box = $("#chips");
  box.replaceChildren();
  const add = (label, remove) => {
    const btn = el("button", { type: "button", "aria-label": `Remove filter: ${label}`, onclick: () => { remove(); refresh(); } });
    btn.append(icon("x"));
    box.append(el("span", { class: "chip", "data-testid": "chip" }, label, btn));
  };
  if (f.q) add(`“${f.q}”`, () => { f.q = ""; });
  if (f.min !== "0") add(`Match ≥ ${f.min}`, () => { f.min = "0"; });
  if (f.company) add(`Company: ${f.company}`, () => { f.company = ""; });
  if (f.lpa) add(f.lpa === "listed" ? "Salary listed" : `₹${f.lpa} LPA+`, () => { f.lpa = ""; });
  f.level.forEach((v) => add(LEVEL_LABEL[v] || v, () => toggleIn(f.level, v, false)));
  f.loc.forEach((v) => add(v === "india" ? "India" : "Remote", () => toggleIn(f.loc, v, false)));
  f.src.forEach((v) => add(SOURCE_LABEL[v] || v, () => toggleIn(f.src, v, false)));
  if (f.days) add(f.days === "1" ? "Last 24h" : `Last ${f.days} days`, () => { f.days = ""; });
}

/* --------------------------------------------------------------- rendering */
function statCard(label, value) {
  return el("div", { class: "stat" }, el("dt", { text: label }), el("dd", { text: String(value) }));
}

function renderStats() {
  const s = state.stats, box = $("#stats");
  box.replaceChildren();
  if (!s) return;
  box.append(
    statCard("Open jobs tracked", s.total.toLocaleString()),
    statCard("Strong matches (60+)", s.strong_matches.toLocaleString()),
    statCard("New in last 24h", s.new_last_24h.toLocaleString()),
    statCard("Applied", (s.by_status.applied || 0).toLocaleString()),
  );
  for (const k of ["saved", "applied", "dismissed"]) {
    const n = s.by_status[k] || 0;
    $(`[data-count="${k}"]`).textContent = n ? String(n) : "";
  }
  $("#salary-hint").textContent = s.total
    ? `${s.with_salary.toLocaleString()} of ${s.total.toLocaleString()} jobs list a salary. Jobs without one are hidden when you pick a minimum.`
    : "";
  const lr = s.last_run;
  const meta = $("#run-meta");
  if (s.crawling) meta.textContent = "Crawling sources…";
  else if (lr && lr.finished_at) meta.textContent = `Last refreshed ${timeAgo(lr.finished_at)}`;
  else meta.textContent = "Never refreshed";
}

function renderHero() {
  const p = state.profile;
  if (!p) {
    $("#greeting").textContent = "Jobs for you";
    $("#hero-sub").textContent = "Upload your resume in Profile to start matching.";
    return;
  }
  const first = (p.name || "").split(" ")[0];
  $("#greeting").textContent = first ? `Jobs for ${first}` : "Jobs for you";
  const generic = new Set(["api", "agile", "oop", "dsa", "rest", "git", "jwt", "html", "css", "postman", "genai", "aws"]);
  const top = (p.skills || []).filter((s) => !generic.has(s)).slice(0, 5).join(", ");
  $("#hero-sub").textContent = `Ranked against your resume${top ? ` — ${top}` : ""}. You apply; we only find and rank.`;
}

function salaryEl(job) {
  if (!job.salary_lpa) return el("span", { class: "salary salary--none", "data-testid": "salary", text: "Salary not listed" });
  const title = job.salary_converted
    ? `Converted from ${job.salary} at approximate exchange rates`
    : `Listed as ${job.salary}`;
  return el("span", { class: "salary", "data-testid": "salary", title, text: job.salary_lpa });
}

function scoreClass(n) { return n >= 75 ? "score--high" : n < 50 ? "score--low" : ""; }

function renderJob(job) {
  const url = safeUrl(job.url);
  const applyBtn = url
    ? el("a", { class: "btn btn--primary btn--sm", href: url, target: "_blank", rel: "noopener noreferrer", "data-testid": "apply", "aria-label": `Apply to ${job.title} at ${job.company} (opens in new tab)`, onclick: () => onApplyClick(job) }, "Apply", icon("arrow"))
    : el("span", { class: "muted small", text: "No link" });

  const saved = job.status === "saved";
  const saveBtn = el("button", { class: "icon-btn", type: "button", "aria-pressed": String(saved), "aria-label": saved ? `Unsave ${job.title}` : `Save ${job.title}`, title: saved ? "Unsave" : "Save", "data-testid": "save", onclick: () => setStatus(job, saved ? "new" : "saved") }, icon("bookmark"));
  const dismissed = job.status === "dismissed";
  const dismissBtn = el("button", { class: "icon-btn", type: "button", "aria-label": dismissed ? `Restore ${job.title}` : `Dismiss ${job.title}`, title: dismissed ? "Restore" : "Dismiss", "data-testid": "dismiss", onclick: () => setStatus(job, dismissed ? "new" : "dismissed") }, icon(dismissed ? "undo" : "x"));
  const applied = job.status === "applied";
  const appliedBtn = el("button", { class: "icon-btn", type: "button", "aria-pressed": String(applied), "aria-label": applied ? `Unmark applied: ${job.title}` : `Mark as applied: ${job.title}`, title: applied ? "Unmark applied" : "Mark applied", "data-testid": "mark-applied", onclick: () => setStatus(job, applied ? "new" : "applied") }, icon("check"));

  const meta = el("div", { class: "job__meta" },
    el("span", { text: job.company }),
    job.location ? el("span", { text: job.location }) : null,
    timeAgo(job.posted_at || job.first_seen) ? el("span", { text: timeAgo(job.posted_at || job.first_seen) }) : null,
    salaryEl(job),
  );
  const tags = el("div", { class: "job__tags" },
    el("span", { class: "badge badge--level", text: LEVEL_LABEL[job.level] || job.level }),
    applied ? el("span", { class: "badge badge--applied", text: "Applied" }) : null,
    (job.matched || []).slice(0, 4).map((m) => el("span", { class: "badge", text: m })),
    (job.tags || []).includes("via-search") ? el("span", { class: "badge badge--preview", title: "Found through search; open the posting to confirm details and that it is still open", text: "Preview" }) : null,
    el("span", { class: "badge badge--src", text: SOURCE_LABEL[job.source] || job.source }),
  );
  const titleBtn = el("button", { class: "job__title", type: "button", "data-testid": "job-title", onclick: () => openJob(job.id) }, job.title);

  return el("li", { class: "job", "data-id": job.id, "data-status": job.status, "data-testid": "job" },
    el("div", { class: `score ${scoreClass(job.score)}`, title: "Match score", "aria-label": `Match score ${job.score} out of 100`, "data-testid": "score" }, String(job.score), el("small", { text: "match" })),
    el("div", { class: "job__main" }, titleBtn, meta, tags),
    el("div", { class: "job__actions" }, applyBtn, saveBtn, appliedBtn, dismissBtn),
  );
}

function renderList() {
  const list = $("#joblist"), stateBox = $("#state"), more = $("#load-more");
  list.replaceChildren();
  stateBox.hidden = true; more.hidden = true;

  if (state.loading && !state.items.length) {
    for (let i = 0; i < 6; i++) list.append(el("li", { class: "skeleton", "aria-hidden": "true" }));
    list.setAttribute("aria-busy", "true");
    $("#count").textContent = "Loading…";
    return;
  }
  list.setAttribute("aria-busy", "false");

  if (state.error) {
    stateBox.hidden = false; stateBox.replaceChildren(
      el("h3", { text: "Couldn’t load jobs" }), el("p", { text: state.error }),
      el("button", { class: "btn btn--secondary", type: "button", onclick: () => refresh(), text: "Try again" }));
    $("#count").textContent = "";
    return;
  }
  if (!state.items.length) {
    const hasFilters = JSON.stringify(state.f) !== JSON.stringify({ ...DEFAULTS, level: [], loc: [], src: [] });
    stateBox.hidden = false; stateBox.replaceChildren(
      el("h3", { text: state.stats && state.stats.total === 0 ? "No jobs yet" : "No jobs match these filters" }),
      el("p", { text: state.stats && state.stats.total === 0 ? "Run a refresh to crawl the job boards and rank them against your resume." : "Try lowering the match score or removing a filter." }),
      state.stats && state.stats.total === 0
        ? el("button", { class: "btn btn--primary", type: "button", onclick: startCrawl, text: "Refresh jobs" })
        : hasFilters ? el("button", { class: "btn btn--secondary", type: "button", onclick: resetFilters, text: "Reset filters" }) : null);
    $("#count").textContent = "0 jobs";
    return;
  }
  for (const job of state.items) list.append(renderJob(job));
  $("#count").textContent = `Showing ${state.items.length} of ${state.total.toLocaleString()} job${state.total === 1 ? "" : "s"}`;
  more.hidden = state.items.length >= state.total;
}

/* ----------------------------------------------------------------- data flow */
async function loadJobs({ append = false } = {}) {
  const id = ++state.reqId;
  state.loading = true; state.error = null;
  if (!append) { state.items = []; renderList(); }
  try {
    const data = await api(`/api/jobs?${queryString(append ? state.items.length : 0)}`);
    if (id !== state.reqId) return; // a newer request superseded this one
    state.items = append ? state.items.concat(data.items) : data.items;
    state.total = data.total;
  } catch (e) {
    if (id !== state.reqId) return;
    state.error = e.message;
  } finally {
    if (id === state.reqId) { state.loading = false; renderList(); }
  }
}

async function loadStats() {
  try {
    state.stats = await api("/api/stats");
    state.sources = Object.entries(state.stats.by_source).sort((a, b) => b[1] - a[1]);
    renderStats();
    if ($$("#f-source input").length !== state.sources.length) { buildSourceFilter(); }
    if (state.stats.crawling) pollCrawl();
  } catch (e) { toast(`Stats unavailable: ${e.message}`); }
}

async function loadProfile() {
  try { state.profile = await api("/api/profile"); } catch { state.profile = null; }
  renderHero();
}

function refresh() {
  writeUrl(); syncControls(); renderChips(); loadJobs();
}

function resetFilters() {
  state.f = { ...DEFAULTS, level: [], loc: [], src: [] };
  refresh();
}

async function setStatus(job, status) {
  const prev = job.status;
  try {
    await api(`/api/jobs/${job.id}/status`, { method: "POST", body: JSON.stringify({ status }) });
    job.status = status;
    // The drawer holds its own copy of the job; keep the list row in sync too.
    const listed = state.items.find((j) => j.id === job.id);
    if (listed) listed.status = status;
    const labels = { saved: "Saved", applied: "Marked as applied", dismissed: "Dismissed", new: "Restored" };
    toast(`${labels[status]} — ${job.title}`);
    // Jobs that no longer belong in the current tab disappear from the list.
    const tab = state.f.status;
    const stays = tab ? status === tab : status !== "dismissed";
    if (!stays) {
      state.items = state.items.filter((j) => j.id !== job.id);
      state.total = Math.max(0, state.total - 1);
    }
    renderList();
    loadStats();
    if (!$("#job-drawer").hidden && Number($("#job-drawer").dataset.id) === job.id) openJob(job.id, { keepFocus: true });
  } catch (e) { job.status = prev; toast(`Couldn’t update: ${e.message}`); }
}

function onApplyClick(job) {
  // Opening the link is the main action; offer to track it without changing anything automatically.
  if (job.status === "new" || job.status === "saved") toast("Opened in a new tab — use ✓ to mark it applied when you’re done");
}

/* ------------------------------------------------------------------- crawl */
async function startCrawl() {
  const btn = $("#refresh");
  try {
    await api("/api/crawl", { method: "POST" });
    setCrawling(true);
    pollCrawl();
  } catch (e) {
    if (e.status === 409) { setCrawling(true); pollCrawl(); toast("A refresh is already running"); }
    else toast(`Couldn’t start refresh: ${e.message}`);
  }
  btn.blur();
}

function setCrawling(on) {
  const btn = $("#refresh");
  btn.disabled = on;
  btn.replaceChildren(...(on ? [el("span", { class: "spinner", "aria-hidden": "true" }), el("span", { class: "btn__label", text: "Crawling…" })] : [el("span", { class: "btn__label", text: "Refresh jobs" })]));
  btn.setAttribute("aria-busy", String(on));
  if (on) $("#run-meta").textContent = "Crawling sources…";
}

function pollCrawl() {
  clearTimeout(state.pollTimer);
  setCrawling(true);
  const tick = async () => {
    try {
      const s = await api("/api/crawl/status");
      if (s.running) { state.pollTimer = setTimeout(tick, 2000); return; }
      setCrawling(false);
      await loadStats(); await loadProfile(); loadJobs();
      if (s.error || (s.last_run && s.last_run.status === "failed")) toast("Refresh finished with errors — see last run");
      else {
        const inserted = Object.values((s.last_run && s.last_run.stats && s.last_run.stats.sources) || {}).reduce((a, x) => a + (x.inserted || 0), 0);
        toast(`Refresh complete — ${inserted} new job${inserted === 1 ? "" : "s"}`);
      }
    } catch { state.pollTimer = setTimeout(tick, 4000); }
  };
  state.pollTimer = setTimeout(tick, 1200);
}

/* ---------------------------------------------------------------- drawers */
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

function openPanel(panel) {
  state.lastFocus = document.activeElement;
  $("#scrim").hidden = false; panel.hidden = false;
  document.body.style.overflow = "hidden";
  const first = $("[data-close]", panel);
  (first || panel).focus();
}
function closePanels() {
  $$(".drawer").forEach((d) => { d.hidden = true; });
  $("#scrim").hidden = true;
  document.body.style.overflow = "";
  if (state.lastFocus && document.contains(state.lastFocus)) state.lastFocus.focus();
}
function trapFocus(e) {
  const open = $$(".drawer").find((d) => !d.hidden);
  if (!open) return;
  if (e.key === "Escape") { e.preventDefault(); closePanels(); return; }
  if (e.key !== "Tab") return;
  const items = $$(FOCUSABLE, open).filter((n) => n.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

async function openJob(id, { keepFocus = false } = {}) {
  const drawer = $("#job-drawer");
  try {
    const job = await api(`/api/jobs/${id}`);
    drawer.dataset.id = String(id);
    $("#job-company").textContent = job.company;
    $("#job-title").textContent = job.title;
    const body = $("#job-body"); body.replaceChildren();
    const facts = el("dl", { class: "kv" });
    const row = (k, v) => { if (v) facts.append(el("dt", { text: k }), el("dd", { text: v })); };
    row("Match", `${job.score} / 100`);
    row("Level", LEVEL_LABEL[job.level] || job.level);
    row("Location", job.location + (job.remote ? " · remote-eligible" : ""));
    row("Posted", job.posted_at ? `${timeAgo(job.posted_at)} (${job.posted_at.slice(0, 10)})` : `first seen ${timeAgo(job.first_seen)}`);
    row("Team", job.department);
    row("Type", job.employment_type);
    row("Salary", job.salary_lpa ? `${job.salary_lpa}${job.salary_converted ? `  (listed as ${job.salary}; converted at approximate rates)` : ""}` : "Not listed");
    row("Source", SOURCE_LABEL[job.source] || job.source);
    body.append(facts);
    if (job.reasons.length) body.append(el("h3", { class: "section-title", text: "Why it matches" }), el("ul", { class: "why" }, job.reasons.map((r) => el("li", { text: r }))));
    if (job.matched.length) body.append(el("div", { class: "job__tags", style: "margin:12px 0 0" }, job.matched.map((m) => el("span", { class: "badge", text: m }))));
    body.append(el("h3", { class: "section-title", text: "Description" }), el("div", { class: "desc", "data-testid": "job-desc", text: job.description || "No description provided — open the posting for details." }));

    const foot = $("#job-foot"); foot.replaceChildren();
    const url = safeUrl(job.url);
    const applied = job.status === "applied", saved = job.status === "saved";
    foot.append(
      el("button", { class: "btn btn--secondary", type: "button", onclick: () => setStatus(job, saved ? "new" : "saved"), text: saved ? "Unsave" : "Save" }),
      el("button", { class: "btn btn--secondary", type: "button", onclick: () => setStatus(job, applied ? "new" : "applied"), text: applied ? "Unmark applied" : "Mark applied" }),
      url ? el("a", { class: "btn btn--primary", href: url, target: "_blank", rel: "noopener noreferrer", "data-testid": "drawer-apply" }, "Apply on site", icon("arrow")) : null,
    );
    if (!keepFocus) openPanel(drawer);
  } catch (e) { toast(`Couldn’t open job: ${e.message}`); }
}

/* ---------------------------------------------------------------- profile */
const draft = { skills: [], exclude: [] };

function renderTags(boxId, list, labelPrefix) {
  const box = $(boxId); box.replaceChildren();
  list.forEach((t, i) => {
    const btn = el("button", { type: "button", "aria-label": `Remove ${labelPrefix} ${t}`, onclick: () => { list.splice(i, 1); renderTags(boxId, list, labelPrefix); } });
    btn.append(icon("x"));
    box.append(el("span", { class: "tag", "data-testid": "tag" }, t, btn));
  });
}

async function openProfile() {
  await loadProfile();
  const p = state.profile || { skills: [], exclude_keywords: [], locations: [], level: "entry", wants_internships: true };
  draft.skills = [...(p.skills || [])];
  draft.exclude = [...(p.exclude_keywords || [])];
  $("#resume-file").textContent = p.source_file ? `${p.name ? p.name + " — " : ""}${p.source_file.split("/").pop()}` : "No resume uploaded yet";
  $("#p-interns").checked = p.wants_internships !== false;
  $("#p-level").value = p.level || "entry";
  $("#p-loc-india").checked = (p.locations || []).includes("india");
  $("#p-loc-remote").checked = (p.locations || []).includes("remote");
  $("#profile-msg").textContent = "";
  renderTags("#p-skills", draft.skills, "skill");
  renderTags("#p-exclude", draft.exclude, "keyword");
  openPanel($("#profile-drawer"));
}

async function saveProfile() {
  const btn = $("#save-profile"), msg = $("#profile-msg");
  const locations = ["india", "remote"].filter((l) => $(`#p-loc-${l}`).checked);
  btn.disabled = true; msg.textContent = "Re-matching jobs…";
  try {
    const res = await api("/api/profile", { method: "PUT", body: JSON.stringify({
      skills: draft.skills, exclude_keywords: draft.exclude, locations,
      level: $("#p-level").value, wants_internships: $("#p-interns").checked,
    }) });
    state.profile = res.profile;
    toast(`Profile saved — ${res.rescored} jobs re-matched`);
    closePanels(); renderHero(); loadStats(); loadJobs();
  } catch (e) { msg.textContent = `Couldn’t save: ${e.message}`; }
  finally { btn.disabled = false; }
}

async function uploadResume(file) {
  const msg = $("#profile-msg");
  if (!file) return;
  const fd = new FormData(); fd.append("file", file);
  msg.textContent = "Reading resume…";
  try {
    const res = await api("/api/profile/resume", { method: "POST", body: fd });
    state.profile = res.profile;
    toast(`Resume processed — ${res.profile.skills.length} skills found, ${res.rescored} jobs re-matched`);
    closePanels(); renderHero(); loadStats(); loadJobs();
  } catch (e) { msg.textContent = `Couldn’t read resume: ${e.message}`; }
}

function addTag(inputId, list, boxId, prefix, lower) {
  const input = $(inputId), v = input.value.trim();
  if (!v) return;
  const val = lower ? v.toLowerCase() : v;
  if (!list.includes(val)) list.push(val);
  input.value = "";
  renderTags(boxId, list, prefix);
}

/* ------------------------------------------------------------------- wiring */
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

function wire() {
  $("#q").addEventListener("input", debounce((e) => { state.f.q = e.target.value.trim(); refresh(); }, 250));
  $("#f-company").addEventListener("input", debounce((e) => { state.f.company = e.target.value.trim(); refresh(); }, 250));
  $("#f-days").addEventListener("change", (e) => { state.f.days = e.target.value; refresh(); });
  $("#f-lpa").addEventListener("change", (e) => { state.f.lpa = e.target.value; refresh(); });
  $("#sort").addEventListener("change", (e) => { state.f.sort = e.target.value; refresh(); });
  $$("#f-score .seg__btn").forEach((b) => b.addEventListener("click", () => { state.f.min = b.dataset.value; refresh(); }));
  $$("#f-level input").forEach((i) => i.addEventListener("change", () => { toggleIn(state.f.level, i.value, i.checked); refresh(); }));
  $$("#f-location input").forEach((i) => i.addEventListener("change", () => { toggleIn(state.f.loc, i.value, i.checked); refresh(); }));
  $$("#status-tabs .tab").forEach((t) => t.addEventListener("click", () => { state.f.status = t.dataset.value; refresh(); }));
  $("#clear-filters").addEventListener("click", resetFilters);
  $("#load-more").addEventListener("click", () => loadJobs({ append: true }));
  $("#refresh").addEventListener("click", startCrawl);
  $("#open-profile").addEventListener("click", openProfile);
  $("#save-profile").addEventListener("click", saveProfile);
  $("#resume-file-input").addEventListener("change", (e) => { uploadResume(e.target.files[0]); e.target.value = ""; });
  $("#p-skill-form").addEventListener("submit", (e) => { e.preventDefault(); addTag("#p-skill-input", draft.skills, "#p-skills", "skill", true); });
  $("#p-exclude-form").addEventListener("submit", (e) => { e.preventDefault(); addTag("#p-exclude-input", draft.exclude, "#p-exclude", "keyword", true); });
  $$("[data-close]").forEach((b) => b.addEventListener("click", closePanels));
  $("#scrim").addEventListener("click", closePanels);
  document.addEventListener("keydown", trapFocus);
  $("#filters-toggle").addEventListener("click", (e) => {
    const open = $("#filters").classList.toggle("is-open");
    e.currentTarget.setAttribute("aria-expanded", String(open));
  });
}

async function init() {
  readUrl(); wire(); syncControls(); renderChips(); renderList();
  await Promise.all([loadStats(), loadProfile()]);
  syncControls();
  loadJobs();
}

init();
