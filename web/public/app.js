/* Remote Monitor Lab dashboard */

const MARKER_ICON = {
  gateway: "📡",
  camera: "📷",
  sensor: "📟",
  spring: "💧",
  water: "🌊",
  trail: "🥾",
  interest: "📍",
  hazard: "⚠",
  camp: "⛺",
  other: "•",
};

let cachedStatus = null;
let cachedMetrics = [];
let map = null;
let gpsLayer = null;
let featureLayer = null;
let featureMarkers = new Map();
let mapReady = false;
let draftMarker = null;
let mapUserMoved = false;
let lastGatewayFocusKey = "";

const SERIES_COLORS = ["#7dcea0", "#e6c07b", "#7aa2ce", "#c4a35a", "#d08770", "#a3be8c", "#b48ead", "#88c0d0"];
const SERIES_PREF_KEY = "rmlab.series.layout";

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function badge(ok) {
  return ok
    ? '<span class="ok">ok</span>'
    : '<span class="bad">down</span>';
}

function card(title, rows) {
  const body = rows
    .map(
      ([k, v]) =>
        `<div class="row"><span class="muted">${k}</span><span>${v}</span></div>`
    )
    .join("");
  return `<article class="card"><h3>${title}</h3>${body}</article>`;
}

function fmtBytesGb(disk) {
  if (!disk || !disk.ok) return disk && disk.error ? escapeHtml(disk.error) : "—";
  return `${disk.free_gb} GB free / ${disk.total_gb} GB (${disk.used_pct}% used)`;
}

function formatLocation(loc) {
  if (!loc || loc.latitude == null || loc.longitude == null) return "—";
  const lat = Number(loc.latitude);
  const lon = Number(loc.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return "—";
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  let text = `${Math.abs(lat).toFixed(5)}° ${ns}, ${Math.abs(lon).toFixed(5)}° ${ew}`;
  if (loc.altitude_m != null && Number.isFinite(Number(loc.altitude_m))) {
    text += `, ${Number(loc.altitude_m).toFixed(0)} m`;
  }
  return escapeHtml(text);
}

function formatTemp(metric, fallback) {
  if (metric && metric.value != null) return `${Number(metric.value).toFixed(1)} °C`;
  if (fallback != null) return `${Number(fallback).toFixed(1)} °C`;
  return "—";
}

function formatVdd(metric, fallback) {
  if (metric && metric.value != null) return `${Number(metric.value).toFixed(2)} V`;
  if (fallback != null) return `${Number(fallback).toFixed(2)} V`;
  return "—";
}

function jetsonBody(status) {
  return (status && status.jetson && status.jetson.body) || {};
}

function tk1Body(status) {
  if (status && status.tk1 && typeof status.tk1 === "object") return status.tk1;
  return jetsonBody(status).tk1 || {};
}

/* -------- tabs -------- */

function bindTabs() {
  const tabs = Array.from(document.querySelectorAll(".tab"));
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const id = tab.dataset.tab;
      tabs.forEach((t) => {
        const on = t === tab;
        t.classList.toggle("active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        const on = panel.id === `tab-${id}`;
        panel.classList.toggle("active", on);
        panel.hidden = !on;
      });
      if (id === "map") ensureMap();
      if (id === "series") loadSeriesTab();
      if (id === "cameras") {
        loadCameras();
        if (cachedStatus) renderEdgeEvents(cachedStatus.edge_events || []);
      }
    });
  });
}

/* -------- charts -------- */

function drawSeries(canvasId, points, opts = {}) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(232,239,233,0.15)";
  ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
  const seriesList = opts.series
    ? opts.series.filter((s) => s && Array.isArray(s.points) && s.points.length)
    : points && points.length
      ? [{ points, color: opts.color, scale: opts.scale }]
      : [];
  if (!seriesList.length) {
    ctx.fillStyle = "#9aafa3";
    ctx.fillText(opts.empty || "No samples yet", 16, 24);
    return;
  }
  const scaled = seriesList.map((s) => {
    const scale = s.scale || opts.scale || ((v) => v);
    return s.points.map((p) => scale(p.value));
  });
  let min = Math.min(...scaled.flat());
  let max = Math.max(...scaled.flat());
  if (opts.threshold != null) {
    min = Math.min(min, opts.threshold);
    max = Math.max(max, opts.threshold);
  }
  if (max - min < 1e-9) {
    min -= 0.5;
    max += 0.5;
  }
  const pad = (max - min) * 0.08;
  min -= pad;
  max += pad;
  const xAt = (i, n) => (i / Math.max(1, n - 1)) * (w - 20) + 10;
  const yAt = (v) => h - 10 - ((v - min) / (max - min)) * (h - 20);
  if (opts.threshold != null) {
    ctx.strokeStyle = "#d87a6a";
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(10, yAt(opts.threshold));
    ctx.lineTo(w - 10, yAt(opts.threshold));
    ctx.stroke();
    ctx.setLineDash([]);
  }
  const colors = ["#e6c07b", "#7aa2ce", "#c4a35a", "#7dcea0"];
  scaled.forEach((vals, si) => {
    ctx.strokeStyle = seriesList[si].color || opts.color || colors[si % colors.length];
    ctx.lineWidth = 2;
    ctx.beginPath();
    vals.forEach((v, i) => {
      const x = xAt(i, vals.length);
      const y = yAt(v);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
  if (opts.labelLatest) {
    const primary = scaled[0];
    ctx.fillStyle = "#9aafa3";
    ctx.fillText(opts.labelLatest(primary[primary.length - 1], scaled), 16, 24);
  }
}

function metricValue(point) {
  return point && point.value != null ? Number(point.value) : null;
}

function fmtAmbient(c) {
  return c == null || !Number.isFinite(c) ? "—" : `${c.toFixed(1)} °C`;
}

function preferMetrics(metrics) {
  const preferred = [
    "tk1.tboard_c",
    "jetson.tboard_c",
    "tk1.tcpu_c",
    "jetson.tcpu_c",
    "jetson.voltage_in",
    "tk1.supply_current_a",
  ];
  const rest = metrics.filter((m) => !preferred.includes(m)).sort();
  return preferred.filter((m) => metrics.includes(m)).concat(rest);
}

async function fetchSeries(metric, sinceS = 3600) {
  const data = await fetch(
    `/api/telemetry/series?metric=${encodeURIComponent(metric)}&since_s=${sinceS}`
  ).then((r) => r.json());
  return data.points || [];
}

/* -------- series tab -------- */

function readSeriesPrefs() {
  try {
    return JSON.parse(localStorage.getItem(SERIES_PREF_KEY) || "{}") || {};
  } catch (_) {
    return {};
  }
}

function writeSeriesPrefs(patch) {
  const next = { ...readSeriesPrefs(), ...patch };
  localStorage.setItem(SERIES_PREF_KEY, JSON.stringify(next));
}

function seriesLayout() {
  return document.getElementById("series-layout").value === "matrix" ? "matrix" : "single";
}

function applySeriesLayoutUi() {
  const matrix = seriesLayout() === "matrix";
  document.getElementById("series-metric-wrap").hidden = matrix;
  document.getElementById("series-cols-wrap").hidden = !matrix;
  document.getElementById("series-rows-wrap").hidden = !matrix;
  document.getElementById("series-single").hidden = matrix;
  document.getElementById("series-matrix").hidden = !matrix;
  document.getElementById("series-matrix-meta").hidden = !matrix;
  document.getElementById("series-cols-wrap").classList.toggle("compact", matrix);
  document.getElementById("series-rows-wrap").classList.toggle("compact", matrix);
}

function restoreSeriesPrefs() {
  const prefs = readSeriesPrefs();
  const layout = document.getElementById("series-layout");
  const cols = document.getElementById("series-cols");
  const rows = document.getElementById("series-rows");
  const windowSel = document.getElementById("series-window");
  if (prefs.layout === "matrix" || prefs.layout === "single") layout.value = prefs.layout;
  if (prefs.cols && [...cols.options].some((o) => o.value === String(prefs.cols))) {
    cols.value = String(prefs.cols);
  }
  if (prefs.rows != null && [...rows.options].some((o) => o.value === String(prefs.rows))) {
    rows.value = String(prefs.rows);
  }
  if (prefs.window && [...windowSel.options].some((o) => o.value === String(prefs.window))) {
    windowSel.value = String(prefs.window);
  }
  applySeriesLayoutUi();
}

async function loadSeriesTab() {
  const select = document.getElementById("series-metric");
  const current = select.value || readSeriesPrefs().metric || "";
  try {
    const data = await fetch("/api/telemetry/metrics").then((r) => r.json());
    const metrics = preferMetrics(data.metrics || []);
    cachedMetrics = metrics;
    select.innerHTML = metrics.length
      ? metrics.map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("")
      : `<option value="">No metrics yet</option>`;
    if (current && metrics.includes(current)) select.value = current;
    else if (metrics.includes("tk1.tboard_c")) select.value = "tk1.tboard_c";
    else if (metrics.includes("jetson.tboard_c")) select.value = "jetson.tboard_c";
    else if (metrics.includes("jetson.voltage_in")) select.value = "jetson.voltage_in";
  } catch (err) {
    cachedMetrics = [];
    select.innerHTML = `<option value="">${escapeHtml(err.message)}</option>`;
  }
  if (cachedStatus) renderAmbient(cachedStatus);
  await refreshSeriesView();
}

function renderAmbient(status) {
  const root = document.getElementById("ambient-cards");
  if (!root) return;
  const tel = (status && status.telemetry) || {};
  const jTherm = jetsonBody(status).thermal || {};
  const tk1 = tk1Body(status);
  const tkTherm = tk1.thermal || {};
  const jetBoard =
    metricValue(tel.jetson_tboard_c) ??
    (jTherm.tboard_c != null ? Number(jTherm.tboard_c) : null);
  const jetCpu =
    metricValue(tel.jetson_tcpu_c) ??
    (jTherm.tcpu_c != null ? Number(jTherm.tcpu_c) : null);
  const tkBoard =
    metricValue(tel.tk1_tboard_c) ??
    (tkTherm.tboard_c != null
      ? Number(tkTherm.tboard_c)
      : tk1.supply && tk1.supply.tboard_c != null
        ? Number(tk1.supply.tboard_c)
        : null);
  const tkCpu =
    metricValue(tel.tk1_tcpu_c) ??
    (tkTherm.tcpu_c != null
      ? Number(tkTherm.tcpu_c)
      : tk1.supply && tk1.supply.tcpu_c != null
        ? Number(tk1.supply.tcpu_c)
        : null);
  root.innerHTML = [
    card("Jetson ambient", [
      ["AO-therm", fmtAmbient(jetBoard)],
      ["CPU-therm", fmtAmbient(jetCpu)],
      ["Role", "furthest from CPU / room proxy"],
      [
        "Seen",
        tel.jetson_tboard_c
          ? new Date(tel.jetson_tboard_c.ts * 1000).toLocaleTimeString()
          : "—",
      ],
    ]),
    card("TK1 ambient", [
      ["Tboard_tegra", fmtAmbient(tkBoard)],
      ["CPU-therm", fmtAmbient(tkCpu)],
      ["Role", "furthest from CPU / room proxy"],
      [
        "Seen",
        tel.tk1_tboard_c
          ? new Date(tel.tk1_tboard_c.ts * 1000).toLocaleTimeString()
          : "—",
      ],
    ]),
  ].join("");
}

async function refreshSeriesView() {
  applySeriesLayoutUi();
  if (seriesLayout() === "matrix") return refreshSeriesMatrix();
  return refreshSeriesChart();
}

async function refreshSeriesChart() {
  const metric = document.getElementById("series-metric").value;
  const since = document.getElementById("series-window").value;
  const meta = document.getElementById("series-meta");
  writeSeriesPrefs({ layout: "single", metric, window: since });
  if (!metric) {
    drawSeries("series-chart", [], { empty: "No metric selected" });
    meta.textContent = "";
    return;
  }
  try {
    const data = await fetch(
      `/api/telemetry/series?metric=${encodeURIComponent(metric)}&since_s=${since}&limit=5000`
    ).then((r) => r.json());
    const points = data.points || [];
    drawSeries("series-chart", points, {
      color: "#7dcea0",
      empty: `No points for ${metric}`,
      labelLatest: (v) => `${metric}: ${Number(v).toPrecision(4)}`,
    });
    meta.textContent = points.length
      ? `${points.length} points · latest ${new Date(points[points.length - 1].ts * 1000).toLocaleString()}`
      : "No points in this window";
  } catch (err) {
    drawSeries("series-chart", [], { empty: err.message });
    meta.textContent = err.message;
  }
}

function matrixCanvasSize(cols) {
  const n = Math.max(1, Number(cols) || 2);
  if (n >= 4) return { w: 420, h: 140 };
  if (n === 3) return { w: 520, h: 150 };
  if (n === 1) return { w: 1100, h: 220 };
  return { w: 640, h: 160 };
}

async function refreshSeriesMatrix() {
  const root = document.getElementById("series-matrix");
  const meta = document.getElementById("series-matrix-meta");
  const since = document.getElementById("series-window").value;
  const cols = Number(document.getElementById("series-cols").value) || 2;
  const maxRows = Number(document.getElementById("series-rows").value);
  writeSeriesPrefs({ layout: "matrix", cols, rows: maxRows, window: since });
  root.dataset.cols = String(cols);
  const metrics = cachedMetrics.slice();
  if (!metrics.length) {
    root.innerHTML = `<p class="muted">No metrics yet.</p>`;
    meta.textContent = "";
    return;
  }
  const limit = maxRows > 0 ? cols * maxRows : metrics.length;
  const shown = metrics.slice(0, limit);
  const hidden = Math.max(0, metrics.length - shown.length);
  const { w, h } = matrixCanvasSize(cols);
  root.innerHTML = shown
    .map(
      (metric, i) => `
      <article class="series-tile" data-metric="${escapeHtml(metric)}" tabindex="0" role="button" aria-label="Open ${escapeHtml(metric)}">
        <div class="series-tile-head">
          <strong title="${escapeHtml(metric)}">${escapeHtml(metric)}</strong>
          <span class="muted" data-latest>—</span>
        </div>
        <canvas id="matrix-chart-${i}" width="${w}" height="${h}" aria-label="${escapeHtml(metric)} chart"></canvas>
      </article>`
    )
    .join("");

  root.querySelectorAll(".series-tile").forEach((tile) => {
    const open = () => {
      const metric = tile.dataset.metric;
      document.getElementById("series-layout").value = "single";
      document.getElementById("series-metric").value = metric;
      writeSeriesPrefs({ layout: "single", metric });
      applySeriesLayoutUi();
      refreshSeriesChart();
    };
    tile.addEventListener("click", open);
    tile.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        open();
      }
    });
  });

  meta.textContent = `Loading ${shown.length} of ${metrics.length} metrics…`;
  const results = await Promise.all(
    shown.map(async (metric, i) => {
      try {
        const data = await fetch(
          `/api/telemetry/series?metric=${encodeURIComponent(metric)}&since_s=${since}&limit=2500`
        ).then((r) => r.json());
        const points = data.points || [];
        drawSeries(`matrix-chart-${i}`, points, {
          color: SERIES_COLORS[i % SERIES_COLORS.length],
          empty: "No points",
          labelLatest: (v) => Number(v).toPrecision(4),
        });
        const latestEl = root.querySelector(`[data-metric="${CSS.escape(metric)}"] [data-latest]`);
        if (latestEl && points.length) {
          latestEl.textContent = Number(points[points.length - 1].value).toPrecision(4);
        }
        return points.length;
      } catch (err) {
        drawSeries(`matrix-chart-${i}`, [], { empty: err.message });
        return 0;
      }
    })
  );
  const withPoints = results.filter((n) => n > 0).length;
  meta.textContent = [
    `${shown.length} plots · ${cols}×${maxRows > 0 ? maxRows : "∞"} grid`,
    `${withPoints} with data`,
    hidden ? `${hidden} more hidden (raise max rows)` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function bindSeries() {
  restoreSeriesPrefs();
  document.getElementById("series-refresh").addEventListener("click", refreshSeriesView);
  document.getElementById("series-metric").addEventListener("change", () => {
    writeSeriesPrefs({ metric: document.getElementById("series-metric").value });
    refreshSeriesChart();
  });
  document.getElementById("series-window").addEventListener("change", refreshSeriesView);
  document.getElementById("series-layout").addEventListener("change", refreshSeriesView);
  document.getElementById("series-cols").addEventListener("change", refreshSeriesMatrix);
  document.getElementById("series-rows").addEventListener("change", refreshSeriesMatrix);
}

/* -------- cameras -------- */

async function loadCameras() {
  const root = document.getElementById("cameras");
  try {
    const res = await fetch("/api/cameras");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const cams = data.cameras || [];
    if (!cams.length) {
      root.innerHTML = `<p class="muted">No cameras configured.</p>`;
      return;
    }
    root.innerHTML = cams.map(renderCamera).join("");
    cams.forEach((c) => wireCamera(c));
  } catch (err) {
    root.innerHTML = `<p class="bad">Failed to load cameras: ${escapeHtml(err.message)}</p>`;
  }
}

function renderCamera(c) {
  const id = c.config.id;
  const buf = c.buffer || {};
  const illumOn = c.illuminator && c.illuminator.on;
  return `
  <article class="cam-card" data-id="${id}">
    <h3>${escapeHtml(c.name || id)}
      <span class="muted"> · ${c.running ? '<span class="ok">live</span>' : '<span class="bad">stopped</span>'}</span>
    </h3>
    <img class="preview" src="/api/cameras/${id}/stream" alt="Live ${escapeHtml(id)}" />
    <div class="controls">
      <label class="ctrl">Buffer (s)
        <input type="range" min="30" max="3600" step="30" data-field="buffer_seconds" value="${c.config.buffer_seconds}" />
        <span class="val" data-val="buffer_seconds">${c.config.buffer_seconds}</span>
      </label>
      <label class="ctrl">Max disk (GB)
        <input type="range" min="0.1" max="20" step="0.1" data-field="max_gb" value="${c.config.max_gb}" />
        <span class="val" data-val="max_gb">${c.config.max_gb}</span>
      </label>
      <label class="ctrl">Capture FPS
        <input type="range" min="1" max="30" step="1" data-field="fps" value="${c.config.fps}" />
        <span class="val" data-val="fps">${c.config.fps}</span>
      </label>
      <div class="row"><span class="muted">Location</span><span data-site-location>—</span></div>
      <div class="row"><span class="muted">Ring frames</span><span data-frames>${buf.frame_count ?? 0}</span></div>
      <div class="row"><span class="muted">Ring span</span><span data-span>${buf.span_s ?? "—"}s</span></div>
      <div class="btn-row">
        <button type="button" data-action="apply">Apply buffer settings</button>
        <button type="button" data-action="illum" class="${illumOn ? "active" : ""}">${illumOn ? "IR on" : "IR off"}</button>
        <button type="button" data-action="refresh">Refresh stats</button>
      </div>
      <div class="scrub">
        <label class="ctrl">Explore buffer
          <input type="range" min="0" max="1000" value="1000" data-scrub disabled />
          <span class="val" data-scrub-label>—</span>
        </label>
        <img data-scrub-img alt="Buffered frame" />
      </div>
    </div>
  </article>`;
}

function wireCamera(c) {
  const id = c.config.id;
  const cardEl = document.querySelector(`.cam-card[data-id="${id}"]`);
  if (!cardEl) return;

  cardEl.querySelectorAll('input[type="range"][data-field]').forEach((input) => {
    input.addEventListener("input", () => {
      const field = input.dataset.field;
      const valEl = cardEl.querySelector(`[data-val="${field}"]`);
      if (valEl) valEl.textContent = input.value;
    });
  });

  cardEl.querySelector('[data-action="apply"]').addEventListener("click", async () => {
    const patch = {};
    cardEl.querySelectorAll("[data-field]").forEach((input) => {
      const key = input.dataset.field;
      patch[key] = key === "max_gb" ? Number(input.value) : parseInt(input.value, 10);
    });
    const res = await fetch(`/api/cameras/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) {
      alert("Failed to update camera");
      return;
    }
    await refreshCameraCard(id);
  });

  cardEl.querySelector('[data-action="illum"]').addEventListener("click", async () => {
    const btn = cardEl.querySelector('[data-action="illum"]');
    const next = !btn.classList.contains("active");
    const res = await fetch(`/api/cameras/${id}/illuminator`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: next }),
    });
    if (!res.ok) return;
    const body = await res.json();
    btn.classList.toggle("active", !!body.on);
    btn.textContent = body.on ? "IR on" : "IR off";
  });

  cardEl.querySelector('[data-action="refresh"]').addEventListener("click", () => {
    refreshCameraCard(id);
  });

  setupScrub(id, cardEl);
}

async function refreshCameraCard(id) {
  const res = await fetch(`/api/cameras/${id}`);
  if (!res.ok) return;
  const c = await res.json();
  const cardEl = document.querySelector(`.cam-card[data-id="${id}"]`);
  if (!cardEl) return;
  cardEl.querySelector("[data-frames]").textContent = c.buffer.frame_count ?? 0;
  cardEl.querySelector("[data-span]").textContent =
    c.buffer.span_s == null ? "—" : `${c.buffer.span_s}s`;
  setupScrub(id, cardEl);
}

async function setupScrub(id, cardEl) {
  const scrub = cardEl.querySelector("[data-scrub]");
  const label = cardEl.querySelector("[data-scrub-label]");
  const img = cardEl.querySelector("[data-scrub-img]");
  const res = await fetch(`/api/cameras/${id}/buffer?limit=200`);
  if (!res.ok) return;
  const payload = await res.json();
  const frames = payload.frames || [];
  if (!frames.length) {
    scrub.disabled = true;
    label.textContent = "empty";
    img.removeAttribute("src");
    return;
  }
  scrub.disabled = false;
  scrub.min = 0;
  scrub.max = frames.length - 1;
  scrub.value = frames.length - 1;
  const show = (idx) => {
    const f = frames[idx];
    if (!f) return;
    label.textContent = new Date(f.ts * 1000).toLocaleTimeString();
    img.src = `/api/cameras/${id}/buffer/frame?ts=${f.ts}&_=${Date.now()}`;
  };
  scrub.oninput = () => show(parseInt(scrub.value, 10));
  show(frames.length - 1);
}

function objectSummary(meta) {
  if (!meta || typeof meta !== "object") return "";
  const list = Array.isArray(meta.objects) ? meta.objects : [];
  const detector = meta.detector ? String(meta.detector) : "";
  if (!list.length && !detector) return "";
  const best = new Map();
  list.forEach((obj) => {
    if (!obj || !obj.label) return;
    const conf = typeof obj.confidence === "number" ? obj.confidence : null;
    const prev = best.get(obj.label);
    if (!prev || (conf != null && (prev.conf == null || conf > prev.conf))) {
      best.set(obj.label, { conf });
    }
  });
  const names = Array.from(best.entries()).map(([label, info]) =>
    info.conf == null ? label : `${label} ${Math.round(info.conf * 100)}%`
  );
  const text = names.length ? names.join(", ") : "none identified";
  return escapeHtml(detector ? `${text} (${detector})` : text);
}

function renderEdgeEvents(events) {
  const root = document.getElementById("edge-events");
  if (!root) return;
  const sig = (events || []).map((e) => e.id).join(",");
  if (sig === root.dataset.sig) return;
  root.dataset.sig = sig;
  if (!events.length) {
    root.innerHTML = `<p class="muted">No motion windows yet.</p>`;
    return;
  }
  const groups = new Map();
  events.forEach((e) => {
    const meta = e.meta && typeof e.meta === "object" ? e.meta : {};
    const wid = meta.window_id || `single-${e.id}`;
    if (!groups.has(wid)) groups.set(wid, []);
    groups.get(wid).push(e);
  });
  root.innerHTML = Array.from(groups.values())
    .map((items) => {
      const audio = items.find((e) => e.kind === "audio" || (e.path || "").endsWith(".wav"));
      const stills = items.filter(
        (e) => e.kind === "object_still" || (e.meta && e.meta.kind === "object_still")
      );
      const frames = items.filter((e) => e !== audio && !stills.includes(e));
      frames.sort((a, b) => {
        const ai = (a.meta && a.meta.frame_index) || 0;
        const bi = (b.meta && b.meta.frame_index) || 0;
        return ai - bi;
      });
      const first = frames[0] || audio;
      if (!first) return "";
      const when = new Date(first.ts * 1000).toLocaleString();
      const pre = first.meta && first.meta.pre_s != null ? first.meta.pre_s : "—";
      const post = first.meta && first.meta.post_s != null ? first.meta.post_s : "—";
      const options = frames
        .map((f, i) => {
          const off = f.meta && f.meta.offset_s != null ? `${f.meta.offset_s}s` : `#${i + 1}`;
          return `<option value="${f.id}">${off}</option>`;
        })
        .join("");
      const player = audio
        ? `<audio controls preload="none" src="/api/edge/events/${audio.id}/audio" style="width:100%;margin-top:0.6rem"></audio>`
        : "";
      const scrub = frames.length
        ? `<div class="scrub">
          <img class="preview" data-motion-frame="${first.id}" src="/api/edge/events/${first.id}/frame" alt="motion frame" />
          <label class="ctrl" style="margin-top:0.6rem">Frame
            <select data-motion-select="${first.id}">${options}</select>
            <span class="val">${frames.length}</span>
          </label>
          ${player}
        </div>`
        : player;
      const objects = objectSummary(first.meta);
      const stillHtml = stills
        .map((still) => {
          const meta = still.meta || {};
          const box = Array.isArray(meta.objects) && meta.objects[0] ? meta.objects[0] : null;
          const name = (box && box.label) || meta.label || "object";
          const pct =
            box && typeof box.confidence === "number"
              ? ` ${Math.round(box.confidence * 100)}%`
              : "";
          return `<figure style="margin:0.6rem 0 0">
            <img class="preview" src="/api/edge/events/${still.id}/frame" alt="${escapeHtml(name)}" />
            <figcaption class="muted">${escapeHtml(name + pct)} · 16:9</figcaption>
          </figure>`;
        })
        .join("");
      return `<article class="card"><h3>${escapeHtml(first.camera_id || "edge")}</h3>
        <div class="row"><span class="muted">When</span><span>${when}</span></div>
        <div class="row"><span class="muted">Window</span><span>${pre}s before / ${post}s after</span></div>
        <div class="row"><span class="muted">Score</span><span>${first.score ?? "—"}</span></div>
        <div class="row"><span class="muted">Location</span><span>${formatLocation(
          first.meta && first.meta.location
        )}</span></div>
        ${objects ? `<div class="row"><span class="muted">Objects</span><span>${objects}</span></div>` : ""}
        ${scrub}
        ${stillHtml}
      </article>`;
    })
    .join("");
  root.querySelectorAll("[data-motion-select]").forEach((sel) => {
    sel.addEventListener("change", () => {
      const article = sel.closest("article");
      const img = article && article.querySelector("[data-motion-frame]");
      if (img) img.src = `/api/edge/events/${sel.value}/frame?_=${Date.now()}`;
    });
  });
}

/* -------- jetson / tk1 / status -------- */

function renderLoRaE5(status) {
  const root = document.getElementById("lora-e5");
  if (!root) return;
  const jetson = jetsonBody(status);
  const e5 = jetson.lora_e5;
  const sx = jetson.sx1302;
  const nodes = (status.telemetry && status.telemetry.nodes) || [];
  const gatewayRx = status.telemetry && status.telemetry.gateway_rx;
  const cards = [];
  if (e5 || nodes.some((n) => (n.tags || {}).transport === "usb-uart")) {
    const usb = nodes.find((n) => (n.tags || {}).transport === "usb-uart");
    const sample = (e5 && e5.last_sample && e5.last_sample.sensors) || {};
    const tempMetric = usb && usb.metrics && usb.metrics["node.temperature_c"];
    const vddMetric = usb && usb.metrics && usb.metrics["node.vdd_v"];
    cards.push(
      card("USB LoRa-E5", [
        ["Node", (e5 && e5.node_id) || (usb && usb.node_id) || "e5-usb0"],
        ["Port", (e5 && e5.port) || "/dev/ttyUSB0"],
        ["Temperature", formatTemp(tempMetric, sample.temperature_c)],
        ["Module VDD", formatVdd(vddMetric, sample.vdd_v)],
        [
          "State",
          e5 && e5.last_error
            ? `<span class="bad">${escapeHtml(e5.last_error)}</span>`
            : e5 && e5.running
              ? '<span class="ok">polling</span>'
              : "—",
        ],
      ])
    );
  }
  nodes
    .filter((n) => (n.tags || {}).transport === "lorawan")
    .forEach((n) => {
      cards.push(
        card(`LoRa ${n.node_id}`, [
          ["DevEUI", (n.tags && n.tags.dev_eui) || "—"],
          ["Temperature", formatTemp(n.metrics["node.temperature_c"], null)],
          ["Module VDD", formatVdd(n.metrics["node.vdd_v"], null)],
          [
            "Last uplink",
            n.metrics["node.uplink"]
              ? new Date(n.metrics["node.uplink"].ts * 1000).toLocaleString()
              : "—",
          ],
        ])
      );
    });
  cards.push(
    card("Waveshare gateway", [
      [
        "Concentrator",
        sx && sx.ok
          ? `<span class="ok">${escapeHtml(sx.chip_version || "up")}</span>`
          : `<span class="bad">${escapeHtml((sx && sx.note) || "not probed")}</span>`,
      ],
      [
        "Last uplink",
        gatewayRx
          ? new Date(gatewayRx.ts * 1000).toLocaleString()
          : '<span class="muted">none yet</span>',
      ],
      [
        "RSSI",
        status.telemetry && status.telemetry.gateway_rssi
          ? `${status.telemetry.gateway_rssi.value.toFixed(0)} dBm`
          : "—",
      ],
      ["Location", formatLocation(status.telemetry && status.telemetry.location)],
    ])
  );
  root.innerHTML = cards.join("");
}

function renderJetson(status) {
  const root = document.getElementById("jetson-cards");
  if (!root) return;
  const j = status.jetson || {};
  const body = jetsonBody(status);
  const volt = body.voltage || {};
  const therm = body.thermal || {};
  const motion = body.motion_camera || {};
  const hub = body.hub || {};
  const thr =
    (status.telemetry && status.telemetry.alert_config && status.telemetry.alert_config.voltage_min_v) ||
    "4.60";
  const latest = status.telemetry && status.telemetry.jetson_voltage_in;
  const tboard = status.telemetry && status.telemetry.jetson_tboard_c;
  const tcpu = status.telemetry && status.telemetry.jetson_tcpu_c;
  root.innerHTML = [
    card("Reachability", [
      ["URL", escapeHtml(j.url || "—")],
      ["SSH", escapeHtml(j.ssh || "—")],
      ["State", badge(!!j.ok)],
      ["Discovery", escapeHtml((j.discovery && j.discovery.note) || "—")],
      ["Hostname", escapeHtml(body.hostname || "—")],
      ["Uptime", body.uptime_s != null ? `${Math.round(body.uptime_s)}s` : "—"],
    ]),
    card("Disk & hub link", [
      ["Disk", fmtBytesGb(body.disk)],
      ["Main URL", escapeHtml(body.main_url || "—")],
      ["Hub note", escapeHtml(hub.note || "—")],
      ["Gateway server", escapeHtml((body.gateway && body.gateway.server) || "—")],
    ]),
    card("Power", [
      [
        "POM_5V_IN",
        latest ? `${latest.value.toFixed(3)} V` : volt.voltage_in_v != null ? `${volt.voltage_in_v} V` : "—",
      ],
      ["Threshold", `${Number(thr).toFixed(2)} V`],
      [
        "State",
        latest
          ? latest.value < Number(thr)
            ? '<span class="bad">low</span>'
            : '<span class="ok">ok</span>'
          : "—",
      ],
      ["Upload", volt.upload_error ? `<span class="bad">${escapeHtml(volt.upload_error)}</span>` : '<span class="ok">ok</span>'],
    ]),
    card("Ambient temperature", [
      [
        "AO-therm",
        tboard
          ? `${tboard.value.toFixed(1)} °C`
          : therm.tboard_c != null
            ? `${Number(therm.tboard_c).toFixed(1)} °C`
            : "waiting…",
      ],
      [
        "CPU-therm",
        tcpu
          ? `${tcpu.value.toFixed(1)} °C`
          : therm.tcpu_c != null
            ? `${Number(therm.tcpu_c).toFixed(1)} °C`
            : "—",
      ],
      ["Sensor", escapeHtml((therm.sensor || (tboard && tboard.tags && tboard.tags.sensor) || "AO-therm"))],
      ["Role", "furthest from CPU / room proxy"],
      [
        "Seen",
        tboard ? new Date(tboard.ts * 1000).toLocaleTimeString() : "—",
      ],
    ]),
    card("Motion camera", [
      ["Running", motion.running ? '<span class="ok">yes</span>' : '<span class="bad">no</span>'],
      ["Device", escapeHtml(motion.device_open || motion.device || "—")],
      ["Frames", motion.frames_seen != null ? String(motion.frames_seen) : "—"],
      ["Triggers", motion.triggers != null ? String(motion.triggers) : "—"],
      ["Window", `${motion.pre_s ?? "—"}s / ${motion.post_s ?? "—"}s`],
    ]),
  ].join("");

  fetchSeries("jetson.voltage_in")
    .then((points) =>
      drawSeries("voltage-chart", points, {
        threshold: Number(thr),
        color: "#c4a35a",
        empty: "No voltage samples yet",
      })
    )
    .catch(() => drawSeries("voltage-chart", []));
  Promise.all([fetchSeries("jetson.tboard_c"), fetchSeries("jetson.tcpu_c")])
    .then(([board, cpu]) =>
      drawSeries("jetson-tboard-chart", board, {
        empty: "No AO-therm samples yet",
        series: [
          { points: board, color: "#e6c07b" },
          { points: cpu, color: "#7aa2ce" },
        ],
        labelLatest: (v, all) => {
          const cpuLatest = all && all[1] ? all[1][all[1].length - 1] : null;
          return `AO ${v.toFixed(1)} °C` + (cpuLatest != null ? ` · CPU ${cpuLatest.toFixed(1)} °C` : "");
        },
      })
    )
    .catch(() => drawSeries("jetson-tboard-chart", []));
  renderLoRaE5(status);
}

function renderTk1(status) {
  const root = document.getElementById("tk1-cards");
  if (!root) return;
  const tk1 = tk1Body(status);
  const telemetry = (status && status.telemetry) || {};
  const current = telemetry.tk1_supply_current_a;
  const voltage = telemetry.tk1_supply_voltage_v;
  const watts = telemetry.tk1_supply_power_w;
  const tboard = telemetry.tk1_tboard_c;
  const tcpu = telemetry.tk1_tcpu_c;
  const motion = tk1.motion_camera || {};
  const hub = tk1.hub || {};
  const supply = tk1.supply || {};
  const therm = tk1.thermal || {};
  root.innerHTML = [
    card("Reachability", [
      ["State", badge(!!tk1.ok)],
      ["Hostname", escapeHtml(tk1.hostname || "—")],
      ["Uptime", tk1.uptime_s != null ? `${Math.round(tk1.uptime_s)}s` : "—"],
      ["Main URL", escapeHtml(tk1.main_url || "—")],
      ["Hub note", escapeHtml(hub.note || tk1.error || "—")],
      ["Disk", fmtBytesGb(tk1.disk)],
    ]),
    card("12 V input", [
      ["Current", current ? `${(current.value * 1000).toFixed(0)} mA` : "waiting…"],
      ["Voltage", voltage ? `${voltage.value.toFixed(2)} V` : "—"],
      ["Power", watts ? `${watts.value.toFixed(2)} W` : "—"],
      [
        "INA3221",
        supply.ok === false
          ? `<span class="bad">${escapeHtml(supply.error || "missing")}</span>`
          : supply.ok
            ? '<span class="ok">ok</span>'
            : "—",
      ],
    ]),
    card("Ambient temperature", [
      [
        "Tboard_tegra",
        tboard
          ? `${tboard.value.toFixed(1)} °C`
          : therm.tboard_c != null
            ? `${Number(therm.tboard_c).toFixed(1)} °C`
            : supply.tboard_c != null
              ? `${Number(supply.tboard_c).toFixed(1)} °C`
              : "waiting…",
      ],
      [
        "CPU-therm",
        tcpu
          ? `${tcpu.value.toFixed(1)} °C`
          : therm.tcpu_c != null
            ? `${Number(therm.tcpu_c).toFixed(1)} °C`
            : supply.tcpu_c != null
              ? `${Number(supply.tcpu_c).toFixed(1)} °C`
              : "—",
      ],
      ["Sensor", "NCT72 / Tboard_tegra"],
      ["Role", "furthest from CPU / room proxy"],
      ["Camera", motion.running ? '<span class="ok">running</span>' : '<span class="bad">stopped</span>'],
      ["Triggers", motion.triggers != null ? String(motion.triggers) : "—"],
    ]),
  ].join("");

  fetchSeries("tk1.supply_current_a")
    .then((points) =>
      drawSeries("tk1-current-chart", points, {
        scale: (v) => v * 1000,
        color: "#7dcea0",
        empty: "No TK1 current samples yet",
        labelLatest: (v) => `${v.toFixed(0)} mA`,
      })
    )
    .catch(() => drawSeries("tk1-current-chart", []));
  Promise.all([fetchSeries("tk1.tboard_c"), fetchSeries("tk1.tcpu_c")])
    .then(([board, cpu]) =>
      drawSeries("tk1-tboard-chart", board, {
        empty: "No Tboard_tegra samples yet",
        series: [
          { points: board, color: "#e6c07b" },
          { points: cpu, color: "#7aa2ce" },
        ],
        labelLatest: (v, all) => {
          const cpuLatest = all && all[1] ? all[1][all[1].length - 1] : null;
          return `Board ${v.toFixed(1)} °C` + (cpuLatest != null ? ` · CPU ${cpuLatest.toFixed(1)} °C` : "");
        },
      })
    )
    .catch(() => drawSeries("tk1-tboard-chart", []));
}

async function loadStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cachedStatus = data;
    const hubDisk = data.main && data.main.disk;
    const tel = data.telemetry || {};
    const jetAmb = metricValue(tel.jetson_tboard_c);
    const tkAmb = metricValue(tel.tk1_tboard_c);
    document.getElementById("header-status").innerHTML = [
      `Hub ${escapeHtml(data.main.advertise_host || "")}`,
      badge(!!data.jetson.ok) + " Jetson" + (jetAmb != null ? ` ${jetAmb.toFixed(1)}°C` : ""),
      badge(!!(data.tk1 && data.tk1.ok)) + " TK1" + (tkAmb != null ? ` ${tkAmb.toFixed(1)}°C` : ""),
      hubDisk && hubDisk.ok ? `${hubDisk.free_gb} GB free` : "",
    ]
      .filter(Boolean)
      .join(" · ");
    renderAmbient(data);
    renderJetson(data);
    renderTk1(data);
    renderEdgeEvents(data.edge_events || []);
    applySiteLocation(data.telemetry && data.telemetry.location);
    if (mapReady && validLatLng(data.telemetry && data.telemetry.location)) {
      const key = gatewayFocusKey(data.telemetry.location);
      if (key && key !== lastGatewayFocusKey) {
        focusMapOnGatewayGps(data.telemetry.location, [], { force: false });
      }
    }
    renderAlerts(data.telemetry && data.telemetry.recent_alerts);
    fillNotifyForm(data.telemetry && data.telemetry.alert_config);
    const thr = (data.telemetry && data.telemetry.alert_config && data.telemetry.alert_config.voltage_min_v) || "4.60";
    const vmin = document.getElementById("voltage-min");
    if (vmin && !vmin.matches(":active")) {
      vmin.value = thr;
      document.getElementById("voltage-min-val").textContent = Number(thr).toFixed(2);
    }
  } catch (err) {
    document.getElementById("header-status").innerHTML = `<span class="bad">${escapeHtml(
      err.message
    )}</span>`;
  }
}

function applySiteLocation(loc) {
  const text = formatLocation(loc);
  document.querySelectorAll("[data-site-location]").forEach((el) => {
    el.innerHTML = text;
  });
  const gps = document.getElementById("gps-latest");
  if (gps) {
    gps.innerHTML = loc
      ? `${formatLocation(loc)}<br/><span class="tiny">${
          loc.ts ? new Date(loc.ts * 1000).toLocaleString() : ""
        } · ${escapeHtml(loc.fix_source || loc.source || "gateway")}</span>`
      : "No GPS fix stored yet.";
  }
}

function validLatLng(loc) {
  if (!loc) return null;
  const lat = Number(loc.latitude);
  const lon = Number(loc.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  if (Math.abs(lat) < 1e-8 && Math.abs(lon) < 1e-8) return null;
  return [lat, lon];
}

function gatewayFocusKey(loc) {
  const ll = validLatLng(loc);
  if (!ll) return "";
  return `${ll[0].toFixed(5)},${ll[1].toFixed(5)}`;
}

function focusMapOnGatewayGps(latest, trackPoints = [], { force = false } = {}) {
  if (!mapReady || !map) return false;
  const latestLl = validLatLng(latest);
  const track = (trackPoints || [])
    .map((p) => validLatLng(p))
    .filter(Boolean);
  if (!latestLl && !track.length) return false;
  const key = gatewayFocusKey(latest) || `${track[track.length - 1][0].toFixed(5)},${track[track.length - 1][1].toFixed(5)}`;
  if (!force && mapUserMoved && key === lastGatewayFocusKey) return false;
  lastGatewayFocusKey = key;
  mapUserMoved = false;
  const run = () => {
    map.invalidateSize();
    if (track.length >= 2) {
      const bounds = L.latLngBounds(track);
      if (latestLl) bounds.extend(latestLl);
      if (bounds.isValid()) {
        map.fitBounds(bounds, { padding: [36, 36], maxZoom: 17 });
        return;
      }
    }
    map.setView(latestLl || track[track.length - 1], 16, { animate: true });
  };
  run();
  setTimeout(run, 80);
  return true;
}

/* -------- map -------- */

function ensureMap() {
  if (mapReady) {
    mapUserMoved = false;
    setTimeout(() => map && map.invalidateSize(), 50);
    refreshMapData({ focus: true });
    return;
  }
  const bootstrap =
    validLatLng(cachedStatus && cachedStatus.telemetry && cachedStatus.telemetry.location) ||
    [29.6, -95.6];
  map = L.map("map", { zoomControl: true }).setView(bootstrap, bootstrap[0] === 29.6 ? 11 : 16);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);
  gpsLayer = L.layerGroup().addTo(map);
  featureLayer = L.layerGroup().addTo(map);
  map.on("dragstart zoomstart", () => {
    mapUserMoved = true;
  });
  map.on("click", (ev) => {
    document.getElementById("feature-lat").value = ev.latlng.lat.toFixed(6);
    document.getElementById("feature-lon").value = ev.latlng.lng.toFixed(6);
    if (draftMarker) map.removeLayer(draftMarker);
    draftMarker = L.circleMarker(ev.latlng, {
      radius: 7,
      color: "#c4a35a",
      fillColor: "#c4a35a",
      fillOpacity: 0.7,
    }).addTo(map);
  });
  mapReady = true;
  refreshMapData({ focus: true });
}

function featureDivIcon(type) {
  const glyph = MARKER_ICON[type] || MARKER_ICON.other;
  return L.divIcon({
    className: "rmlab-marker",
    html: `<span class="marker-chip">${glyph}</span>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}

async function refreshMapData({ focus = false } = {}) {
  if (!mapReady) return;
  const [locData, featData] = await Promise.all([
    fetch("/api/locations?since_s=2592000&limit=5000").then((r) => r.json()),
    fetch("/api/map/features").then((r) => r.json()),
  ]);
  gpsLayer.clearLayers();
  const points = (locData.points || [])
    .slice()
    .sort((a, b) => Number(a.ts || 0) - Number(b.ts || 0));
  const latest = locData.latest || (points.length ? points[points.length - 1] : null);
  const latestLl = validLatLng(latest);
  if (points.length) {
    const latlngs = points.map((p) => validLatLng(p)).filter(Boolean);
    if (latlngs.length >= 2) {
      L.polyline(latlngs, { color: "#6fbf7a", weight: 3, opacity: 0.85 }).addTo(gpsLayer);
    }
  }
  if (latestLl) {
    L.circleMarker(latestLl, {
      radius: 9,
      color: "#6fbf7a",
      fillColor: "#6fbf7a",
      fillOpacity: 0.95,
    })
      .bindPopup(
        `LoRa gateway GPS<br/>${formatLocation(latest)}<br/><span class="tiny">${escapeHtml(
          latest.fix_source || latest.source || ""
        )}</span>`
      )
      .addTo(gpsLayer);
  }
  applySiteLocation(latest);

  featureLayer.clearLayers();
  featureMarkers.clear();
  const features = featData.features || [];
  features.forEach((f) => {
    const marker = L.marker([f.latitude, f.longitude], {
      icon: featureDivIcon(f.marker_type),
      draggable: true,
    });
    marker.bindPopup(
      `<strong>${escapeHtml(f.name)}</strong><br/>${escapeHtml(f.marker_type)}<br/>${escapeHtml(
        f.description || ""
      )}`
    );
    marker.on("click", () => selectFeature(f));
    marker.on("dragend", async () => {
      const ll = marker.getLatLng();
      await fetch(`/api/map/features/${f.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ latitude: ll.lat, longitude: ll.lng }),
      });
      refreshMapData();
    });
    marker.addTo(featureLayer);
    featureMarkers.set(f.id, marker);
  });

  const list = document.getElementById("feature-list");
  list.innerHTML = features.length
    ? features
        .map(
          (f) => `<button type="button" class="feature-item" data-id="${f.id}">
            <span><span class="marker-chip">${MARKER_ICON[f.marker_type] || "•"}</span>${escapeHtml(
              f.name
            )}</span>
            <span class="muted">${escapeHtml(f.marker_type)}</span>
          </button>`
        )
        .join("")
    : `<p class="muted">None yet. Click the map and save a feature.</p>`;
  list.querySelectorAll(".feature-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const f = features.find((x) => String(x.id) === btn.dataset.id);
      if (f) selectFeature(f);
    });
  });

  // Prefer LoRa gateway GPS for map focus; saved features stay visible but don't pull the view away.
  if (latestLl || points.length) {
    focusMapOnGatewayGps(latest, points, { force: focus });
  } else if (focus && features.length) {
    const bounds = features.map((f) => [f.latitude, f.longitude]);
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
  }
}

function selectFeature(f) {
  document.getElementById("feature-id").value = f.id;
  document.getElementById("feature-name").value = f.name || "";
  document.getElementById("feature-type").value = f.marker_type || "interest";
  document.getElementById("feature-lat").value = Number(f.latitude).toFixed(6);
  document.getElementById("feature-lon").value = Number(f.longitude).toFixed(6);
  document.getElementById("feature-desc").value = f.description || "";
  document.getElementById("feature-delete").hidden = false;
  document.querySelectorAll(".feature-item").forEach((el) => {
    el.classList.toggle("active", String(el.dataset.id) === String(f.id));
  });
  const marker = featureMarkers.get(f.id);
  if (marker) {
    map.panTo(marker.getLatLng());
    marker.openPopup();
  }
}

function clearFeatureForm() {
  document.getElementById("feature-form").reset();
  document.getElementById("feature-id").value = "";
  document.getElementById("feature-delete").hidden = true;
  document.getElementById("feature-status").textContent = "";
  if (draftMarker) {
    map.removeLayer(draftMarker);
    draftMarker = null;
  }
}

function bindMapForm() {
  document.getElementById("feature-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const status = document.getElementById("feature-status");
    const id = document.getElementById("feature-id").value;
    const payload = {
      name: document.getElementById("feature-name").value.trim(),
      marker_type: document.getElementById("feature-type").value,
      latitude: Number(document.getElementById("feature-lat").value),
      longitude: Number(document.getElementById("feature-lon").value),
      description: document.getElementById("feature-desc").value,
    };
    status.textContent = "Saving…";
    try {
      const res = await fetch(id ? `/api/map/features/${id}` : "/api/map/features", {
        method: id ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      status.textContent = "Saved";
      clearFeatureForm();
      refreshMapData();
    } catch (err) {
      status.textContent = err.message;
    }
  });
  document.getElementById("feature-clear").addEventListener("click", clearFeatureForm);
  document.getElementById("feature-delete").addEventListener("click", async () => {
    const id = document.getElementById("feature-id").value;
    if (!id) return;
    if (!confirm("Delete this map feature?")) return;
    await fetch(`/api/map/features/${id}`, { method: "DELETE" });
    clearFeatureForm();
    refreshMapData();
  });
  document.getElementById("feature-use-gps").addEventListener("click", async () => {
    const data = await fetch("/api/locations?since_s=2592000&limit=10").then((r) => r.json());
    const loc = data.latest;
    if (!loc || !validLatLng(loc)) {
      document.getElementById("feature-status").textContent = "No GPS fix available";
      return;
    }
    document.getElementById("feature-lat").value = Number(loc.latitude).toFixed(6);
    document.getElementById("feature-lon").value = Number(loc.longitude).toFixed(6);
    document.getElementById("feature-status").textContent = "Filled from latest GPS";
  });
  document.getElementById("map-recenter-gps").addEventListener("click", async () => {
    mapUserMoved = false;
    const data = await fetch("/api/locations?since_s=2592000&limit=5000").then((r) => r.json());
    const points = (data.points || []).slice().sort((a, b) => Number(a.ts || 0) - Number(b.ts || 0));
    const latest = data.latest || (points.length ? points[points.length - 1] : null);
    if (!focusMapOnGatewayGps(latest, points, { force: true })) {
      document.getElementById("feature-status").textContent = "No gateway GPS fix to center on";
    }
  });
}

/* -------- config / alerts -------- */

function renderAlerts(alerts) {
  const root = document.getElementById("alerts-list");
  if (!root) return;
  if (!alerts || !alerts.length) {
    root.innerHTML = `<p class="muted">No alerts yet.</p>`;
    return;
  }
  root.innerHTML = alerts
    .map(
      (a) => `<article class="card"><h3>${escapeHtml(a.kind)}</h3>
        <div class="row"><span class="muted">When</span><span>${new Date(
          a.ts * 1000
        ).toLocaleString()}</span></div>
        <div class="row"><span class="muted">Message</span><span>${escapeHtml(a.message)}</span></div>
      </article>`
    )
    .join("");
}

function fillNotifyForm(cfg) {
  if (!cfg) return;
  const wh = document.getElementById("notify-webhook");
  const em = document.getElementById("notify-email");
  if (wh && document.activeElement !== wh) wh.value = cfg.notify_webhook_url || "";
  if (em && document.activeElement !== em) em.value = cfg.notify_email || "";
}

function bindConfig() {
  document.getElementById("voltage-min").addEventListener("input", (ev) => {
    document.getElementById("voltage-min-val").textContent = Number(ev.target.value).toFixed(2);
  });
  document.getElementById("voltage-min-apply").addEventListener("click", async () => {
    const voltage_min_v = Number(document.getElementById("voltage-min").value);
    await fetch("/api/alerts/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voltage_min_v }),
    });
    loadStatus();
  });
  document.getElementById("notify-apply").addEventListener("click", async () => {
    await fetch("/api/alerts/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        notify_webhook_url: document.getElementById("notify-webhook").value.trim(),
        notify_email: document.getElementById("notify-email").value.trim(),
      }),
    });
    loadStatus();
  });

  const pre = document.getElementById("motion-pre");
  const post = document.getElementById("motion-post");
  const thr = document.getElementById("motion-threshold");
  const show = () => {
    document.getElementById("motion-pre-val").textContent = Number(pre.value).toFixed(1);
    document.getElementById("motion-post-val").textContent = Number(post.value).toFixed(1);
    document.getElementById("motion-threshold-val").textContent = Number(thr.value).toFixed(1);
  };
  pre.addEventListener("input", show);
  post.addEventListener("input", show);
  thr.addEventListener("input", show);
  document.getElementById("motion-apply").addEventListener("click", async () => {
    const status = document.getElementById("motion-status");
    status.textContent = "Saving…";
    try {
      const res = await fetch("/api/jetson/motion", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pre_s: Number(pre.value),
          post_s: Number(post.value),
          threshold: Number(thr.value),
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      status.textContent = "Applied on the Jetson";
    } catch (err) {
      status.textContent = err.message;
    }
  });
}

async function loadMotionConfig() {
  try {
    const res = await fetch("/api/jetson/motion");
    if (!res.ok) return;
    const data = await res.json();
    const cam = data.motion_camera || data;
    if (cam.pre_s != null) document.getElementById("motion-pre").value = cam.pre_s;
    if (cam.post_s != null) document.getElementById("motion-post").value = cam.post_s;
    if (cam.threshold != null) document.getElementById("motion-threshold").value = cam.threshold;
    document.getElementById("motion-pre").dispatchEvent(new Event("input"));
  } catch (_err) {
    /* offline */
  }
}

function bindJetsonDiscover() {
  const btn = document.getElementById("jetson-discover");
  btn.addEventListener("click", async () => {
    const status = document.getElementById("jetson-discover-status");
    btn.disabled = true;
    status.textContent = "Searching…";
    try {
      const res = await fetch("/api/jetson/discover", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      status.textContent = data.note || data.url || "done";
      loadStatus();
    } catch (err) {
      status.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });
}

/* -------- boot -------- */

bindTabs();
bindSeries();
bindConfig();
bindMapForm();
bindJetsonDiscover();
loadSeriesTab();
loadCameras();
loadStatus();
loadMotionConfig();
setInterval(loadStatus, 5000);
setInterval(() => {
  if (document.getElementById("tab-series").classList.contains("active")) {
    refreshSeriesChart();
  }
}, 15000);
