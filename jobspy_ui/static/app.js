/* Vanilla JS single-page UI. No build step.
 * RULE: scraped job data is untrusted. Only ever put it in the DOM via textContent / .value / setAttribute
 * (see h() below). Never use innerHTML with job data. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const state = {
  meta: null, profiles: [], selectedId: null, tab: "results",
  runs: [], resultsRunId: null, rows: [], filters: { q: "", site: "", newOnly: false },
  sort: { key: "date_posted", dir: -1 }, pollTimer: null, activeRuns: [],
};

// ---------- tiny helpers ----------
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}
const fmtTime = (iso) => iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
const duration = (a, b) => { if (!a || !b) return ""; const s = Math.round((new Date(b) - new Date(a)) / 1000); return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`; };
const selected = () => state.profiles.find((p) => p.id === state.selectedId);
const isActive = (r) => r && (r.status === "running" || r.status === "queued");

function salary(r) {
  if (!r.min_amount && !r.max_amount) return "";
  const n = (x) => (x ? Math.round(x).toLocaleString() : "?");
  const cur = r.currency && r.currency !== "USD" ? r.currency + " " : "$";
  return `${cur}${n(r.min_amount)}–${n(r.max_amount)}${r.interval ? "/" + r.interval.replace("ly", "").replace("year", "yr").replace("hour", "hr") : ""}`;
}

// ---------- boot ----------
async function boot() {
  state.meta = await api("/meta");
  if (!state.meta.jobspy_installed) {
    const b = $("#banner"); b.classList.remove("hidden");
    b.textContent = "python-jobspy isn't installed in this environment — run: pip install -U python-jobspy";
  }
  await loadProfiles();
  if (state.profiles.length) selectProfile(state.profiles[0].id);
  $("#btn-new").onclick = newProfile;
  $("#btn-import").onclick = openImport;
  $("#drawer-close").onclick = () => $("#drawer").classList.add("hidden");
  $("#import-cancel").onclick = () => $("#import-dialog").close();
  $("#import-go").onclick = doImport;
  pollActive();
}

async function loadProfiles() {
  state.profiles = await api("/profiles");
  renderSidebar();
}

function renderSidebar() {
  const list = $("#profile-list"); list.replaceChildren();
  for (const p of state.profiles) {
    const lr = p.last_run;
    const sub = lr ? (isActive(lr) ? lr.message || lr.status : `${fmtTime(lr.finished_at || lr.created_at)} · ${lr.job_count} jobs`) : "Never run";
    list.append(h("div", { class: "profile" + (p.id === state.selectedId ? " active" : ""), onclick: () => selectProfile(p.id) },
      h("div", { class: "name" }, h("span", { text: p.name }), h("span", { class: "dot " + (lr ? lr.status : "") })),
      h("div", { class: "sub", text: sub })));
  }
}

// ---------- profile view ----------
async function selectProfile(id, tab) {
  state.selectedId = id; state.tab = tab || "results";
  state.filters = { q: "", site: "", newOnly: false };
  $("#drawer").classList.add("hidden");
  renderSidebar();
  await refreshRuns();
  renderMain();
}

async function refreshRuns() {
  const p = selected(); if (!p) return;
  state.runs = await api(`/runs?profile_id=${p.id}&limit=30`);
  const latestDone = state.runs.find((r) => r.status === "done");
  if (latestDone && state.resultsRunId !== latestDone.id) {
    state.resultsRunId = latestDone.id;
    state.rows = (await api(`/runs/${latestDone.id}/results`)).rows;
  } else if (!latestDone) { state.resultsRunId = null; state.rows = []; }
}

function renderMain() {
  const p = selected(), main = $("#main");
  if (!p) { main.replaceChildren(h("div", { class: "empty", text: "Select a search on the left, or create a new one." })); return; }
  const c = p.config, active = state.runs.find(isActive);
  const pills = [`${c.search_terms.length} term${c.search_terms.length === 1 ? "" : "s"}`, c.location || "any location",
    c.sites.join(", "), c.hours_old ? `last ${c.hours_old}h` : "any age", `${c.results_wanted}/board`];
  if (c.is_remote) pills.push("remote"); if (c.only_new) pills.push("new only");

  const runBtn = h("button", { class: "btn primary run-btn", disabled: !!active || !state.meta.jobspy_installed, onclick: () => startRun(p.id) }, active ? "Running…" : "▶ Run now");
  main.replaceChildren(
    h("div", { class: "head" },
      h("div", {}, h("h2", { text: p.name }), p.description ? h("div", { class: "muted", text: p.description }) : null,
        h("div", { class: "summary" }, pills.map((t) => h("span", { class: "pill", text: t })))),
      runBtn),
    active ? renderProgress(active) : null,
    h("div", { class: "tabs" }, [["results", "Results"], ["history", "History"], ["settings", "Settings"]].map(([k, label]) =>
      h("div", { class: "tab" + (state.tab === k ? " active" : ""), text: label, onclick: () => { state.tab = k; renderMain(); } }))),
    h("div", { id: "tab-body" }));
  const body = $("#tab-body");
  if (state.tab === "results") renderResults(body);
  else if (state.tab === "history") renderHistory(body);
  else renderSettings(body, p);
}

function renderProgress(run) {
  const pct = run.progress_total ? Math.round((run.progress_done / run.progress_total) * 100) : 0;
  const logEl = h("pre", { class: "log", id: "live-log", text: "" });
  api(`/runs/${run.id}`).then((r) => { logEl.textContent = r.log || ""; logEl.scrollTop = logEl.scrollHeight; });
  return h("div", { class: "card progress" },
    h("div", {}, h("b", { text: run.status === "queued" ? "Queued…" : run.message || "Starting…" }),
      h("span", { class: "muted", text: `  ${run.progress_done}/${run.progress_total || "?"} searches` })),
    h("div", { class: "bar" + (run.status === "queued" ? " indeterminate" : "") }, h("i", { style: `width:${pct}%` })),
    h("div", { style: "display:flex;gap:10px;align-items:flex-start" },
      h("details", { class: "log", style: "flex:1" }, h("summary", { text: "Live log" }), logEl),
      h("button", { class: "btn", onclick: () => api(`/runs/${run.id}/cancel`, { method: "POST" }).then(pollActive) }, "Cancel")));
}

async function startRun(id) {
  try { await api(`/profiles/${id}/run`, { method: "POST" }); await refreshAll(); pollActive(); }
  catch (e) { alert(e.message); }
}
async function refreshAll() { await loadProfiles(); await refreshRuns(); renderMain(); }

// ---------- results ----------
function renderResults(body) {
  const run = state.runs.find((r) => r.status === "done");
  if (!run) { body.replaceChildren(h("div", { class: "empty", style: "margin-top:60px", text: "No results yet. Hit “Run now”." })); return; }
  const sites = [...new Set(state.rows.map((r) => r.site))];
  const tableHost = h("div");
  const f = state.filters;

  const toolbar = h("div", { class: "toolbar" },
    h("input", { type: "search", placeholder: "Filter title, company, location…", value: f.q, oninput: (e) => { f.q = e.target.value; drawTable(); } }),
    h("select", { onchange: (e) => { f.site = e.target.value; drawTable(); } },
      h("option", { value: "", text: "All boards" }), sites.map((s) => h("option", { value: s, text: s, selected: f.site === s }))),
    h("label", { class: "check", style: "display:flex;gap:6px;align-items:center" },
      h("input", { type: "checkbox", checked: f.newOnly, onchange: (e) => { f.newOnly = e.target.checked; drawTable(); } }), "New only"),
    h("span", { class: "spacer" }),
    h("span", { class: "muted", id: "count" }),
    run.has_files ? h("a", { class: "btn", href: `/api/runs/${run.id}/download/xlsx`, text: "⬇ Excel" }) : null,
    run.has_files ? h("a", { class: "btn", href: `/api/runs/${run.id}/download/csv`, text: "⬇ CSV" }) : null);
  body.replaceChildren(h("div", { class: "muted", style: "margin-bottom:8px", text: `Run #${run.id} · ${fmtTime(run.finished_at)} · ${run.job_count} jobs, ${run.new_count} new` }), toolbar, tableHost);

  function drawTable() {
    const q = f.q.toLowerCase();
    let rows = state.rows.filter((r) =>
      (!f.site || r.site === f.site) && (!f.newOnly || r.is_new) &&
      (!q || [r.title, r.company, r.location].some((x) => (x || "").toLowerCase().includes(q))));
    const { key, dir } = state.sort;
    rows = rows.sort((a, b) => ((a[key] ?? "") > (b[key] ?? "") ? 1 : (a[key] ?? "") < (b[key] ?? "") ? -1 : 0) * dir);
    $("#count").textContent = `${rows.length} shown`;
    const cols = [["title", "Title"], ["company", "Company"], ["location", "Location"], ["date_posted", "Posted"], ["site", "Board"]];
    const head = h("tr", {}, h("th", { text: "" }), cols.map(([k, l]) =>
      h("th", { text: l + (state.sort.key === k ? (dir > 0 ? " ▲" : " ▼") : ""), onclick: () => { state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : 1 }; drawTable(); } })),
      h("th", { text: "Pay" }), h("th", { text: "" }));
    tableHost.replaceChildren(h("table", { class: "jobs" }, h("thead", {}, head), h("tbody", {}, rows.map((r) =>
      h("tr", { class: "row", onclick: () => openDrawer(r) },
        h("td", {}, r.is_new ? h("span", { class: "badge", text: "NEW" }) : null),
        h("td", {}, h("b", { text: r.title || "" })), h("td", { text: r.company || "" }), h("td", { text: r.location || "" }),
        h("td", { text: (r.date_posted || "").slice(0, 10) }), h("td", {}, h("span", { class: "site", text: r.site })),
        h("td", { text: salary(r) }),
        h("td", {}, r.job_url ? h("a", { class: "link", href: r.job_url, target: "_blank", rel: "noopener noreferrer", text: "Open ↗", onclick: (e) => e.stopPropagation() }) : null))))));
  }
  drawTable();
}

function openDrawer(r) {
  $("#drawer-title").textContent = r.title || "";
  $("#drawer-sub").textContent = [r.company, r.location].filter(Boolean).join(" · ");
  const meta = $("#drawer-meta"); meta.replaceChildren();
  for (const t of [r.site, r.job_type, r.is_remote ? "remote" : null, salary(r), r.date_posted && `posted ${String(r.date_posted).slice(0, 10)}`, r.search_term && `term: ${r.search_term}`].filter(Boolean))
    meta.append(h("span", { class: "pill", text: t }));
  if (r.job_url) meta.append(h("a", { class: "link", href: r.job_url, target: "_blank", rel: "noopener noreferrer", text: "Open listing ↗" }));
  if (r.job_url_direct) meta.append(h("a", { class: "link", href: r.job_url_direct, target: "_blank", rel: "noopener noreferrer", text: "Apply directly ↗" }));
  $("#drawer-desc").textContent = r.description || "(No description. Turn on “Fetch full descriptions” in Settings.)";
  $("#drawer").classList.remove("hidden");
}

// ---------- history ----------
function renderHistory(body) {
  if (!state.runs.length) { body.replaceChildren(h("div", { class: "empty", style: "margin-top:60px", text: "No runs yet." })); return; }
  body.replaceChildren(h("table", { class: "jobs" },
    h("thead", {}, h("tr", {}, ["Run", "Started", "Status", "Jobs", "New", "Took", "", ""].map((t) => h("th", { text: t })))),
    h("tbody", {}, state.runs.map((r) => h("tr", {},
      h("td", { text: `#${r.id}` }), h("td", { text: fmtTime(r.started_at || r.created_at) + (r.trigger === "schedule" ? " (auto)" : "") }),
      h("td", {}, h("span", { class: "status " + r.status, text: r.status }), r.error ? h("div", { class: "muted", text: r.error.split("\n").pop().slice(0, 90) }) : null),
      h("td", { text: r.job_count ?? 0 }), h("td", { text: r.new_count ?? 0 }), h("td", { text: duration(r.started_at, r.finished_at) }),
      h("td", {}, r.has_files ? h("a", { class: "link", href: `/api/runs/${r.id}/download/xlsx`, text: "Excel" }) : null),
      h("td", {}, h("a", { class: "link", text: "Log", onclick: () => showLog(r.id) })))))));
}
async function showLog(id) {
  const r = await api(`/runs/${id}`);
  const dlg = h("dialog", {}, h("h3", { text: `Run #${id} log` }), h("pre", { class: "log", style: "max-height:60vh", text: r.log || "(empty)" }),
    h("div", { class: "dialog-actions" }, h("button", { class: "btn", onclick: () => { dlg.close(); dlg.remove(); } }, "Close")));
  document.body.append(dlg); dlg.showModal();
}

// ---------- settings form ----------
function renderSettings(body, p) { body.replaceChildren(buildForm(p)); }

function buildForm(p) {
  const f = $("#tpl-form").content.firstElementChild.cloneNode(true);
  const cfg = p ? p.config : state.meta.defaults;
  const m = state.meta;
  const set = (name, v) => { const el = f.elements[name]; if (el.type === "checkbox") el.checked = !!v; else el.value = v ?? ""; };

  m.countries.forEach((c) => f.elements.country_indeed.append(h("option", { value: c, text: c })));
  m.job_types.forEach((t) => f.elements.job_type.append(h("option", { value: t, text: t })));
  m.description_formats.forEach((t) => f.elements.description_format.append(h("option", { value: t, text: t })));

  set("name", p?.name); set("description", p?.description);
  set("search_terms", cfg.search_terms.join("\n")); set("location", cfg.location);
  const wantCountry = m.countries.find((c) => c.toLowerCase() === (cfg.country_indeed || "").toLowerCase()) || cfg.country_indeed;
  if (!m.countries.includes(wantCountry)) f.elements.country_indeed.append(h("option", { value: wantCountry, text: wantCountry }));
  set("country_indeed", wantCountry);
  for (const k of ["results_wanted", "hours_old", "distance", "job_type", "is_remote", "fetch_description", "only_new", "description_format", "easy_apply", "enforce_annual_salary"]) set(k, cfg[k]);
  set("proxies", (cfg.proxies || []).join("\n")); set("schedule_cron", p?.schedule_cron);

  const on = new Set(cfg.sites), chips = $("#site-chips", f);
  m.sites.forEach((s) => {
    const chip = h("span", { class: "chip" + (on.has(s.id) ? " on" : ""), title: s.note, text: s.label, onclick: () => { on.has(s.id) ? on.delete(s.id) : on.add(s.id); chip.classList.toggle("on"); } });
    chips.append(chip);
  });
  f.querySelectorAll(".presets a").forEach((a) => a.onclick = () => { f.elements.hours_old.value = a.dataset.v; });

  const errBox = $("#form-error", f);
  f.onsubmit = async (e) => {
    e.preventDefault(); errBox.classList.add("hidden");
    const g = (n) => f.elements[n];
    const payload = {
      name: g("name").value, description: g("description").value,
      config: {
        search_terms: g("search_terms").value, sites: [...on], location: g("location").value, country_indeed: g("country_indeed").value,
        results_wanted: g("results_wanted").value, hours_old: g("hours_old").value || null, distance: g("distance").value,
        job_type: g("job_type").value || null, is_remote: g("is_remote").checked, fetch_description: g("fetch_description").checked,
        only_new: g("only_new").checked, description_format: g("description_format").value, easy_apply: g("easy_apply").checked,
        enforce_annual_salary: g("enforce_annual_salary").checked, proxies: g("proxies").value,
      },
    };
    try {
      const saved = p ? await api(`/profiles/${p.id}`, { method: "PUT", body: payload }) : await api("/profiles", { method: "POST", body: payload });
      await loadProfiles(); await selectProfile(saved.id, "settings");
    } catch (ex) { errBox.textContent = ex.message; errBox.classList.remove("hidden"); }
  };
  const dup = $("#form-duplicate", f), del = $("#form-delete", f);
  if (!p) { dup.remove(); del.remove(); }
  else {
    dup.onclick = async () => { const c = await api(`/profiles/${p.id}/duplicate`, { method: "POST" }); await loadProfiles(); selectProfile(c.id, "settings"); };
    del.onclick = async () => {
      if (!confirm(`Delete “${p.name}” and all its run history and files?`)) return;
      try { await api(`/profiles/${p.id}`, { method: "DELETE" }); state.selectedId = null; await loadProfiles(); state.profiles.length ? selectProfile(state.profiles[0].id) : renderMain(); }
      catch (ex) { alert(ex.message); }
    };
  }
  return f;
}

function newProfile() {
  state.selectedId = null; renderSidebar();
  $("#drawer").classList.add("hidden");
  const main = $("#main");
  main.replaceChildren(h("div", { class: "head" }, h("h2", { text: "New search" })), buildForm(null));
}

// ---------- import ----------
function openImport() { $("#import-source").value = ""; $("#import-warnings").textContent = ""; $("#import-dialog").showModal(); }
async function doImport() {
  try {
    const { config, warnings } = await api("/import-script", { method: "POST", body: { source: $("#import-source").value } });
    $("#import-dialog").close();
    newProfile();
    // prefill the new-search form from the imported config
    const f = $("#main form"), cfg = { ...state.meta.defaults, ...config };
    f.elements.search_terms.value = (cfg.search_terms || []).join("\n"); f.elements.location.value = cfg.location || "";
    f.elements.results_wanted.value = cfg.results_wanted; f.elements.hours_old.value = cfg.hours_old ?? "";
    f.elements.is_remote.checked = !!cfg.is_remote; f.elements.fetch_description.checked = !!cfg.fetch_description;
    if (cfg.job_type) f.elements.job_type.value = cfg.job_type;
    const cc = state.meta.countries.find((c) => c.toLowerCase() === String(cfg.country_indeed).toLowerCase()); if (cc) f.elements.country_indeed.value = cc;
    const chips = [...f.querySelectorAll(".chip")]; const want = new Set(cfg.sites || []);
    chips.forEach((chip) => { const site = state.meta.sites.find((s) => s.label === chip.textContent)?.id; if (!!want.has(site) !== chip.classList.contains("on")) chip.click(); });
    f.elements.name.focus();
    if (warnings.length) alert("Imported with notes:\n• " + warnings.join("\n• "));
  } catch (e) { $("#import-warnings").textContent = e.message; }
}

// ---------- polling ----------
async function pollActive() {
  clearTimeout(state.pollTimer);
  const before = state.activeRuns.length;
  state.activeRuns = await api("/runs?active=1");
  const p = selected();
  if (state.activeRuns.length || before) {
    await loadProfiles();
    if (p && state.tab !== "settings") { await refreshRuns(); renderMain(); }   // don't clobber the form while editing
    else if (p) { await refreshRuns(); }
  }
  if (state.activeRuns.length) state.pollTimer = setTimeout(pollActive, 1500);
}

boot();
