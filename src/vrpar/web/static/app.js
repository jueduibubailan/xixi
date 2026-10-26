/* vrpar dashboard — vanilla JS, no build step. */
"use strict";

const state = {
  run: "",
  runs: [],
  file: "",
  log: "pipeline.log",
  logs: [],
  timer: null,
  tab: "artifacts",
};

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    throw new Error(`${path} -> ${response.status}`);
  }
  return response.json();
}

function qs(extra) {
  const params = new URLSearchParams();
  if (state.run) params.set("run", state.run);
  for (const [key, value] of Object.entries(extra || {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, value);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

function fmtDuration(seconds) {
  if (seconds === undefined || seconds === null) return "—";
  if (seconds < 60) return `${Number(seconds).toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m${rest}s`;
}

function esc(text) {
  return String(text ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

/* ------------------------------- header -------------------------------- */

async function loadHealth() {
  try {
    const info = await api("/api/health");
    $("meta").textContent = `${info.task} · model=${info.model} · backend=${info.backend}`;
    $("v-coral").textContent = info.coral || "";
  } catch (error) {
    $("meta").textContent = `health failed: ${error.message}`;
  }
}

async function loadRuns() {
  const payload = await api("/api/runs");
  state.runs = payload.runs || [];
  const select = $("run-select");
  select.innerHTML = "";
  if (!state.runs.length) {
    const option = document.createElement("option");
    option.textContent = "no runs";
    option.value = "";
    select.appendChild(option);
    return;
  }
  for (const run of state.runs) {
    const option = document.createElement("option");
    option.value = run.path;
    option.textContent = `${run.task}/${run.run_id} · ${run.status || "?"}`;
    select.appendChild(option);
  }
  if (!state.run || !state.runs.some((r) => r.path === state.run)) {
    state.run = state.runs[0].path;
  }
  select.value = state.run;
}

/* ------------------------------- panels -------------------------------- */

async function loadStatus() {
  const status = await api(`/api/status${qs()}`);
  $("v-status").textContent = status.exists ? status.status || "?" : "none";
  $("v-round").textContent = status.round ?? "—";
  $("v-best").textContent = status.best_cost ?? "—";
  $("v-initial").textContent = status.initial_cost ?? "—";
  $("v-run-dir").textContent = status.run_dir || status.message || "—";
}

async function loadGrader() {
  try {
    const payload = await api(`/api/grader${qs()}`);
    const bundle = payload.grader;
    $("v-score").textContent = bundle ? bundle.score : "—";
    $("v-feasible").textContent = bundle ? String(bundle.feasible) : "—";
    if (bundle && bundle.best_cost !== null && bundle.best_cost !== undefined) {
      $("v-best").textContent = bundle.best_cost;
    }
  } catch (error) {
    $("v-score").textContent = "—";
    $("v-feasible").textContent = "—";
  }
}

async function loadStages() {
  const payload = await api(`/api/stages${qs()}`);
  const list = $("stages");
  list.innerHTML = "";
  (payload.stages || []).forEach((stage, index) => {
    const item = document.createElement("li");
    item.className = stage.status || "pending";
    const spinner = stage.status === "running" ? '<span class="spin">◐</span>' : "";
    item.innerHTML = `
      <span class="idx">${index + 1}</span>
      <span>
        <span class="name">${esc(stage.name)} <span class="path">[${esc(stage.does || stage.kind)}]</span></span>
        <div class="msg">${esc(stage.message || "")}</div>
        <div class="path">${esc((stage.present || []).join("  "))}</div>
      </span>
      <span class="msg">${fmtDuration(stage.duration_s)} · try ${stage.attempts ?? 0}</span>
      <span class="badge">${esc(stage.status)}${spinner}</span>`;
    list.appendChild(item);
  });
  if (!(payload.stages || []).length) {
    list.innerHTML = '<li><span class="idx">·</span><span class="msg">no stages yet</span></li>';
  }
}

// One browser, one source: everything the dashboard shows lives inside the run
// directory and is served by /api/file, whose path guard cannot reach outside a run.
async function loadBrowser() {
  return renderArtifacts();
}

async function renderArtifacts() {
  const payload = await api(`/api/artifacts${qs({ dir: "artifacts" })}`);
  renderList((payload.entries || []).map((entry) => ({
    path: entry.path,
    label: entry.path,
    size: entry.size,
    group: entry.path.includes("/") ? entry.path.split("/")[0] + "/" : "",
  })), "no artifacts yet");
}

function renderList(items, emptyText) {
  const list = $("files");
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = `<li class="msg">${esc(emptyText)}</li>`;
    return;
  }
  let currentGroup = null;
  for (const item of items) {
    if (item.group && item.group !== currentGroup) {
      currentGroup = item.group;
      const head = document.createElement("li");
      head.className = "group";
      head.textContent = currentGroup;
      list.appendChild(head);
    }
    const node = document.createElement("li");
    node.dataset.path = item.path;
    if (item.path === state.file) node.classList.add("active");
    const badge = item.badge ? `<span class="badge new">${esc(item.badge)}</span>` : "";
    const note = item.note ? `<div class="note">${esc(item.note)}</div>` : "";
    node.innerHTML = `${esc(item.label)}${badge}<span class="size">${item.size}B</span>${note}`;
    node.onclick = () => openFile(item.path);
    list.appendChild(node);
  }
}

async function openFile(path) {
  state.file = path;
  $("file-name").textContent = path;
  $("file-body").textContent = "loading…";
  try {
    const payload = await api(`/api/file${qs({ path })}`);
    $("file-body").textContent = payload.text ?? "";
  } catch (error) {
    $("file-body").textContent = `cannot display: ${error.message}`;
  }
  document.querySelectorAll("#files li").forEach((node) => {
    node.classList.toggle("active", node.dataset.path === path);
  });
}

function selectTab(tab) {
  state.tab = tab;
  state.file = "";
  $("file-name").textContent = "select a file";
  $("file-body").textContent = "—";
  document.querySelectorAll("#tabs .tab").forEach((node) => {
    node.classList.toggle("active", node.dataset.tab === tab);
  });
  loadBrowser().catch(() => {});
}

async function loadLogList() {
  const payload = await api(`/api/logs${qs()}`);
  state.logs = payload.logs || [];
  const select = $("log-select");
  select.innerHTML = "";
  if (!state.logs.length) {
    const option = document.createElement("option");
    option.textContent = "no logs yet";
    select.appendChild(option);
    return;
  }
  for (const log of state.logs) {
    const option = document.createElement("option");
    option.value = log.name;
    option.textContent = `${log.name} (${log.size}B)`;
    select.appendChild(option);
  }
  if (!state.logs.some((l) => l.name === state.log)) {
    state.log = state.logs[0].name;
  }
  select.value = state.log;
}

async function loadLog() {
  if (!state.log) return;
  const body = $("log-body");
  const atBottom = body.scrollTop + body.clientHeight >= body.scrollHeight - 40;
  try {
    const payload = await api(`/api/log${qs({ name: state.log, tail: 500 })}`);
    body.textContent = payload.text || "(empty)";
    $("log-path").textContent = `logs/${state.log}`;
    if (atBottom) body.scrollTop = body.scrollHeight;
  } catch (error) {
    body.textContent = `cannot read log: ${error.message}`;
  }
}

/* ------------------------------ lifecycle ------------------------------ */

async function refreshAll() {
  await Promise.allSettled([loadStatus(), loadGrader(), loadStages(), loadBrowser()]);
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(async () => {
    await Promise.allSettled([loadStatus(), loadGrader(), loadStages(), loadLog()]);
  }, 2000);
  setInterval(() => { loadBrowser().catch(() => {}); loadLogList().then(loadLog).catch(() => {}); }, 8000);
}

function startSSE() {
  const pill = $("live");
  if (!("EventSource" in window)) { pill.textContent = "polling"; return; }
  const source = new EventSource(`/api/events${qs()}`);
  source.addEventListener("state", () => { refreshAll(); loadLog(); });
  source.onopen = () => { pill.textContent = "live"; pill.classList.add("on"); };
  source.onerror = () => { pill.textContent = "reconnecting"; pill.classList.remove("on"); };
}

function wireControls() {
  $("run-select").onchange = async (event) => {
    state.run = event.target.value;
    state.file = "";
    state.tab = state.tab || "artifacts";
    await Promise.allSettled([refreshAll(), loadBrowser(), loadLogList().then(loadLog)]);
  };
  $("log-select").onchange = (event) => { state.log = event.target.value; loadLog(); };
  document.querySelectorAll("#tabs .tab").forEach((node) => {
    node.onclick = () => selectTab(node.dataset.tab);
  });
  $("log-refresh").onclick = () => loadLog();
  $("copy-btn").onclick = async () => {
    try { await navigator.clipboard.writeText($("file-body").textContent); $("copy-btn").textContent = "copied"; }
    catch (_) { $("copy-btn").textContent = "copy failed"; }
    setTimeout(() => { $("copy-btn").textContent = "copy"; }, 1200);
  };
  $("steer-btn").onclick = async () => {
    const text = $("steer-text").value.trim();
    if (!text) return;
    try {
      await api("/api/steer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, stage: $("steer-stage").value || null, run: state.run || null }),
      });
      $("steer-text").value = "";
      $("steer-btn").textContent = "queued";
      setTimeout(() => { $("steer-btn").textContent = "queue"; }, 1200);
    } catch (error) {
      $("steer-btn").textContent = "failed";
      setTimeout(() => { $("steer-btn").textContent = "queue"; }, 1500);
    }
  };
}

async function boot() {
  await loadHealth();
  await loadRuns();
  wireControls();
  await Promise.allSettled([refreshAll(), loadBrowser(), loadLogList().then(loadLog)]);
  startPolling();
  startSSE();
}

boot();
