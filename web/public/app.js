async function loadStatus() {
  const root = document.getElementById("cards");
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const camSummary =
      (data.cameras || [])
        .map((c) => `${c.name || c.id}:${c.running ? "live" : "off"}`)
        .join(", ") || "none";
    root.innerHTML = [
      card("Main", [
        ["API", data.main.api],
        ["Region", data.main.region],
        ["Uptime", `${data.main.uptime_s}s`],
        ["Cameras", camSummary],
      ]),
      card("Jetson", [
        ["URL", escapeHtml(data.jetson.url || "—")],
        ["SSH", escapeHtml(data.jetson.ssh || "—")],
        ["State", badge(!!data.jetson.ok)],
        ["Detail", detail(data.jetson)],
        [
          "Discovery",
          escapeHtml((data.jetson.discovery && data.jetson.discovery.note) || "—"),
        ],
      ]),
      card("ChirpStack", [
        ["URL", data.chirpstack.url],
        ["State", badge(!!data.chirpstack.ok)],
      ]),
      card("MQTT", [
        ["Broker", `${data.mqtt.host}:${data.mqtt.port}`],
      ]),
    ].join("");
  } catch (err) {
    root.innerHTML = `<p class="bad">Failed to load status: ${err.message}</p>`;
  }
}

function detail(obj) {
  if (obj.error) return obj.error;
  if (obj.body && typeof obj.body === "object" && obj.body.hostname) {
    return obj.body.hostname;
  }
  if (obj.status_code) return `HTTP ${obj.status_code}`;
  return "—";
}

function badge(ok) {
  return ok
    ? '<span class="ok">reachable</span>'
    : '<span class="bad">unreachable</span>';
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
    root.innerHTML = `<p class="bad">Failed to load cameras: ${err.message}</p>`;
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

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadVoltage() {
  const root = document.getElementById("voltage");
  try {
    const status = await fetch("/api/status").then((r) => r.json());
    const series = await fetch("/api/telemetry/series?metric=jetson.voltage_in&since_s=3600").then((r) =>
      r.json()
    );
    const latest = status.telemetry && status.telemetry.jetson_voltage_in;
    const cfg = (status.telemetry && status.telemetry.alert_config) || {};
    const alerts = (status.telemetry && status.telemetry.recent_alerts) || [];
    const v = latest ? latest.value.toFixed(3) : "—";
    const thr = cfg.voltage_min_v || "4.60";
    document.getElementById("voltage-min").value = thr;
    document.getElementById("voltage-min-val").textContent = Number(thr).toFixed(2);
    root.innerHTML = [
      card("POM_5V_IN", [
        ["Latest", latest ? `${v} V` : "waiting for Jetson…"],
        ["Threshold", `${Number(thr).toFixed(2)} V`],
        [
          "State",
          latest
            ? latest.value < Number(thr)
              ? '<span class="bad">low</span>'
              : '<span class="ok">ok</span>'
            : "—",
        ],
      ]),
      card("Recent alerts", [
        [
          "Latest",
          alerts[0]
            ? `${alerts[0].kind}: ${alerts[0].message}`
            : '<span class="muted">none</span>',
        ],
      ]),
    ].join("");
    drawVoltageChart(series.points || [], Number(thr));
    applySiteLocation(status.telemetry && status.telemetry.location);
    renderEdgeEvents(status.edge_events || []);
    renderLoRaE5(status);
    renderTk1Supply(status);
  } catch (err) {
    root.innerHTML = `<p class="bad">${err.message}</p>`;
  }
}

function renderTk1Supply(status) {
  const root = document.getElementById("tk1-power");
  if (!root) return;
  const telemetry = (status && status.telemetry) || {};
  const current = telemetry.tk1_supply_current_a;
  const voltage = telemetry.tk1_supply_voltage_v;
  const watts = telemetry.tk1_supply_power_w;
  const tboard = telemetry.tk1_tboard_c;
  const shunt = current && current.tags && current.tags.shunt_ohm;
  root.innerHTML = [
    card("12 V input", [
      ["Current", current ? `${(current.value * 1000).toFixed(0)} mA` : "waiting for TK1…"],
      ["Voltage", voltage ? `${voltage.value.toFixed(2)} V` : "—"],
      ["Power", watts ? `${watts.value.toFixed(2)} W` : "—"],
      ["Shunt", shunt != null ? `${(Number(shunt) * 1000).toFixed(0)} mΩ` : "—"],
      [
        "Seen",
        current ? new Date(current.ts * 1000).toLocaleTimeString() : "—",
      ],
    ]),
    card("Tboard_tegra", [
      ["Temperature", tboard ? `${tboard.value.toFixed(1)} °C` : "waiting for TK1…"],
      ["Seen", tboard ? new Date(tboard.ts * 1000).toLocaleTimeString() : "—"],
    ]),
  ].join("");
  fetch("/api/telemetry/series?metric=tk1.supply_current_a&since_s=3600")
    .then((r) => r.json())
    .then((series) => drawCurrentChart(series.points || []))
    .catch(() => drawCurrentChart([]));
  fetch("/api/telemetry/series?metric=tk1.tboard_c&since_s=3600")
    .then((r) => r.json())
    .then((series) => drawBoardChart(series.points || []))
    .catch(() => drawBoardChart([]));
}

function drawCurrentChart(points) {
  const canvas = document.getElementById("tk1-current-chart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(232,239,233,0.15)";
  ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
  if (!points.length) {
    ctx.fillStyle = "#9aafa3";
    ctx.fillText("No TK1 current samples yet", 16, 24);
    return;
  }
  const vals = points.map((p) => p.value * 1000);
  const min = Math.min(...vals, 0);
  const max = Math.max(...vals, 1);
  const span = max - min || 1;
  const xAt = (i) => (i / Math.max(1, points.length - 1)) * (w - 20) + 10;
  const yAt = (v) => h - 10 - ((v - min) / span) * (h - 20);
  ctx.strokeStyle = "#7dcea0";
  ctx.lineWidth = 2;
  ctx.beginPath();
  vals.forEach((v, i) => {
    const x = xAt(i);
    const y = yAt(v);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function drawBoardChart(points) {
  const canvas = document.getElementById("tk1-tboard-chart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(232,239,233,0.15)";
  ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
  if (!points.length) {
    ctx.fillStyle = "#9aafa3";
    ctx.fillText("No Tboard_tegra samples yet", 16, 24);
    return;
  }
  const vals = points.map((p) => p.value);
  let min = Math.min(...vals);
  let max = Math.max(...vals);
  if (max - min < 1) {
    const mid = (min + max) / 2;
    min = mid - 0.5;
    max = mid + 0.5;
  }
  const span = max - min || 1;
  const xAt = (i) => (i / Math.max(1, points.length - 1)) * (w - 20) + 10;
  const yAt = (v) => h - 10 - ((v - min) / span) * (h - 20);
  ctx.strokeStyle = "#e6c07b";
  ctx.lineWidth = 2;
  ctx.beginPath();
  vals.forEach((v, i) => {
    const x = xAt(i);
    const y = yAt(v);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = "#9aafa3";
  ctx.fillText(`${vals[vals.length - 1].toFixed(1)} °C`, 16, 24);
}

function renderLoRaE5(status) {
  const root = document.getElementById("lora-e5");
  if (!root) return;
  const jetson = status.jetson && status.jetson.body;
  const e5 = jetson && jetson.lora_e5;
  const sx = jetson && jetson.sx1302;
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
        ["Profile", (e5 && e5.profile) || "temp_v1"],
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
        ["Samples", e5 ? String(e5.samples || 0) : "—"],
      ])
    );
  }
  nodes
    .filter((n) => (n.tags || {}).transport === "lorawan")
    .forEach((n) => {
      const tempMetric = n.metrics["node.temperature_c"];
      const vddMetric = n.metrics["node.vdd_v"];
      cards.push(
        card(`LoRa ${n.node_id}`, [
          ["DevEUI", (n.tags && n.tags.dev_eui) || "—"],
          ["Profile", (n.tags && n.tags.profile) || "—"],
          ["Temperature", formatTemp(tempMetric, null)],
          ["Module VDD", formatVdd(vddMetric, null)],
          [
            "Last uplink",
            n.metrics["node.uplink"]
              ? new Date(n.metrics["node.uplink"].ts * 1000).toLocaleString()
              : "—",
          ],
          ["Location", formatLocation(n.tags && n.tags.location)],
        ])
      );
    });
  cards.push(
    card("Waveshare gateway", [
      ["SPI", (sx && sx.spidev && sx.spidev.path) || "/dev/spidev1.0"],
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
      [
        "Location",
        formatLocation(status.telemetry && status.telemetry.location),
      ],
    ])
  );
  root.innerHTML = cards.join("");
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

function applySiteLocation(loc) {
  const text = formatLocation(loc);
  document.querySelectorAll("[data-site-location]").forEach((el) => {
    el.innerHTML = text;
  });
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

function drawVoltageChart(points, threshold) {
  const canvas = document.getElementById("voltage-chart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(232,239,233,0.15)";
  ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
  if (!points.length) {
    ctx.fillStyle = "#9aafa3";
    ctx.fillText("No voltage samples yet", 16, 24);
    return;
  }
  const vals = points.map((p) => p.value);
  const min = Math.min(...vals, threshold) - 0.05;
  const max = Math.max(...vals, threshold) + 0.05;
  const xAt = (i) => (i / Math.max(1, points.length - 1)) * (w - 20) + 10;
  const yAt = (v) => h - 10 - ((v - min) / (max - min)) * (h - 20);
  // threshold
  ctx.strokeStyle = "#d87a6a";
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(10, yAt(threshold));
  ctx.lineTo(w - 10, yAt(threshold));
  ctx.stroke();
  ctx.setLineDash([]);
  // series
  ctx.strokeStyle = "#c4a35a";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = xAt(i);
    const y = yAt(p.value);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
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
    root.innerHTML = `<p class="muted">No motion windows yet. An edge camera keeps a short pre-roll in memory and uploads the clip when something moves.</p>`;
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
      const stills = items.filter((e) => e.kind === "object_still" || (e.meta && e.meta.kind === "object_still"));
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
      const label = frames.length
        ? `${frames.length} frame${frames.length === 1 ? "" : "s"}`
        : "audio";
      const objects = objectSummary(first.meta);
      const stillHtml = stills
        .map((still) => {
          const meta = still.meta || {};
          const box = Array.isArray(meta.objects) && meta.objects[0] ? meta.objects[0] : null;
          const name = (box && box.label) || meta.label || "object";
          const pct = box && typeof box.confidence === "number" ? ` ${Math.round(box.confidence * 100)}%` : "";
          return `<figure style="margin:0.6rem 0 0">
            <img class="preview" src="/api/edge/events/${still.id}/frame" alt="${escapeHtml(name)}" />
            <figcaption class="muted">${escapeHtml(name + pct)} · 16:9</figcaption>
          </figure>`;
        })
        .join("");
      return `<article class="card"><h3>${escapeHtml(first.camera_id || "edge")} · ${label}${
        audio && frames.length ? " + audio" : ""
      }${stills.length ? ` + ${stills.length} still${stills.length === 1 ? "" : "s"}` : ""}</h3>
        <div class="row"><span class="muted">When</span><span>${when}</span></div>
        <div class="row"><span class="muted">Window</span><span>${pre}s before / ${post}s after</span></div>
        <div class="row"><span class="muted">Score</span><span>${first.score ?? "—"}</span></div>
        <div class="row"><span class="muted">Location</span><span>${formatLocation(first.meta && first.meta.location)}</span></div>
        ${objects ? `<div class="row"><span class="muted">Objects</span><span>${objects}</span></div>` : ""}
        ${scrub}
        ${stillHtml}
      </article>`;
    })
    .join("");
  root.querySelectorAll("[data-motion-select]").forEach((sel) => {
    sel.addEventListener("change", () => {
      const card = sel.closest("article");
      const img = card && card.querySelector("[data-motion-frame]");
      if (img) img.src = `/api/edge/events/${sel.value}/frame?_=${Date.now()}`;
    });
  });
}

function bindMotionControls() {
  const pre = document.getElementById("motion-pre");
  const post = document.getElementById("motion-post");
  const thr = document.getElementById("motion-threshold");
  if (!pre || pre.dataset.bound) return;
  pre.dataset.bound = "1";
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
  bindMotionControls();
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
    /* jetson may be offline */
  }
}

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
  loadVoltage();
});

loadStatus();
loadCameras();
loadVoltage();
loadMotionConfig();
bindJetsonDiscover();
setInterval(loadStatus, 5000);
setInterval(loadVoltage, 10000);

function bindJetsonDiscover() {
  const btn = document.getElementById("jetson-discover");
  if (!btn || btn.dataset.bound) return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", async () => {
    const status = document.getElementById("jetson-discover-status");
    btn.disabled = true;
    status.textContent = "Searching the local network…";
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
