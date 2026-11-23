let source = "file";
let selected = null;
let lastFile = null;
let liveTimer = null;
let topicStatsByName = new Map();

const $ = (id) => document.getElementById(id);
const els = {
  statusBadge: $("statusBadge"),
  statusText: $("statusText"),
  sampleSelect: $("sampleSelect"),
  btnSample: $("btnSample"),
  btnUpload: $("btnUpload"),
  gapMode: $("gapMode"),
  gapFactor: $("gapFactor"),
  gapFactorField: $("gapFactorField"),
  gapMs: $("gapMs"),
  gapMsField: $("gapMsField"),
  gapTopic: $("gapTopic"),
  gapsOnly: $("gapsOnly"),
  gapBadge: $("gapBadge"),
  fileInput: $("fileInput"),
  mapTs: $("mapTs"),
  mapTopic: $("mapTopic"),
  mapQos: $("mapQos"),
  mapPayload: $("mapPayload"),
  btnReparse: $("btnReparse"),
  topicFilter: $("topicFilter"),
  payloadFilter: $("payloadFilter"),
  qosFilter: $("qosFilter"),
  timeFrom: $("timeFrom"),
  timeTo: $("timeTo"),
  btnApply: $("btnApply"),
  btnClearFilter: $("btnClearFilter"),
  topicStats: $("topicStats"),
  btnConnect: $("btnConnect"),
  btnDisconnect: $("btnDisconnect"),
  btnLiveClear: $("btnLiveClear"),
  liveError: $("liveError"),
  mqttHost: $("mqttHost"),
  mqttPort: $("mqttPort"),
  mqttTopic: $("mqttTopic"),
  mqttUser: $("mqttUser"),
  mqttPass: $("mqttPass"),
  countLabel: $("countLabel"),
  sourceLabel: $("sourceLabel"),
  btnRefresh: $("btnRefresh"),
  btnExportGraph: $("btnExportGraph"),
  btnExportCsv: $("btnExportCsv"),
  btnExportJsonl: $("btnExportJsonl"),
  dropZone: $("dropZone"),
  messageBody: $("messageBody"),
  windowSec: $("windowSec"),
  btnWindow: $("btnWindow"),
  btnCopy: $("btnCopy"),
  detailView: $("detailView"),
};

const MAPPING_FIELDS = { mapTs: "ts_key", mapTopic: "topic_key", mapQos: "qos_key", mapPayload: "payload_key" };
const STORAGE_KEY = "mqtt-log-viewer.mapping";
const GAP_FIELDS = ["gapMode", "gapFactor", "gapMs", "gapTopic"];
const GAP_STORAGE_KEY = "mqtt-log-viewer.gap";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function setStatus(mode, liveConnected = false) {
  source = mode;
  els.sourceLabel.textContent = mode === "live" ? " / ライブ購読" : lastFile ? ` / ${lastFile.name}` : " / ファイル";
  els.statusBadge.classList.toggle("live", liveConnected);
  els.statusText.textContent = liveConnected ? "live connected" : `${mode} mode`;
}

function pretty(value) {
  if (value !== null && typeof value === "object") return JSON.stringify(value, null, 2);
  const text = String(value ?? "");
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

const JST_OFFSET_MS = 9 * 60 * 60 * 1000;

function formatJst(epochSec) {
  return new Date(epochSec * 1000 + JST_OFFSET_MS).toISOString().replace("T", " ").slice(0, 23);
}

function formatDt(dt) {
  if (dt === null || dt === undefined) return "";
  return dt >= 1000 ? `${(dt / 1000).toFixed(2)} s` : dt.toFixed(1);
}

function thresholdFor(topic) {
  return topicStatsByName.get(topic)?.threshold_ms ?? null;
}

function isGap(msg) {
  const limit = thresholdFor(msg.topic);
  return limit !== null && msg.dt_ms !== null && msg.dt_ms !== undefined && msg.dt_ms > limit;
}

function intervalNote(msg) {
  if (msg.dt_ms === null || msg.dt_ms === undefined) return "前回受信: なし（このトピックの最初のメッセージ）";
  const stat = topicStatsByName.get(msg.topic);
  const parts = [`前回受信から ${msg.dt_ms.toFixed(1)} ms`];
  if (stat?.median_ms != null) parts.push(`通常 ${stat.median_ms.toFixed(1)} ms`);
  if (stat?.threshold_ms != null) parts.push(`判定基準 ${stat.threshold_ms.toFixed(1)} ms`);
  const text = parts.join(" / ");
  return isGap(msg) ? `[周期抜け] ${text}` : text;
}

function selectMessage(msg, row) {
  selected = msg;
  els.messageBody.querySelectorAll("tr.active").forEach((tr) => tr.classList.remove("active"));
  row?.classList.add("active");
  els.detailView.textContent =
    `${msg.ts}  ${msg.topic}  (QoS ${msg.qos})\n${intervalNote(msg)}\n\n${pretty(msg.payload)}`;
  els.btnCopy.disabled = false;
  els.btnWindow.disabled = msg.ts_epoch === null || msg.ts_epoch === undefined;
}

function clearSelection() {
  selected = null;
  els.detailView.textContent = "メッセージを選択してください";
  els.btnCopy.disabled = true;
  els.btnWindow.disabled = true;
}

function statTooltip(item) {
  const lines = [item.topic, `受信 ${item.count} 件`];
  if (item.rate_hz) lines.push(`平均 ${item.rate_hz} Hz`);
  if (item.median_ms != null) lines.push(`通常間隔 ${item.median_ms.toFixed(1)} ms`);
  if (item.max_dt_ms != null) lines.push(`最大間隔 ${item.max_dt_ms.toFixed(1)} ms`);
  if (item.threshold_ms != null) lines.push(`判定基準 ${item.threshold_ms.toFixed(1)} ms / 周期抜け ${item.gap_count} 件`);
  else lines.push("周期抜け判定: 対象外");
  lines.push("", "クリックでこのトピックに絞り込み");
  return lines.join("\n");
}

function renderStats(stats) {
  els.topicStats.replaceChildren();
  for (const item of stats) {
    const row = el("div", "stat-item");
    row.title = statTooltip(item);
    row.append(el("span", "stat-topic", item.topic));
    const meta = el("span", "stat-count", item.rate_hz ? `${item.count} / ${item.rate_hz} Hz` : `${item.count}`);
    if (item.gap_count > 0) meta.prepend(el("span", "stat-gap", `抜け ${item.gap_count}`));
    row.append(meta);
    row.addEventListener("click", () => {
      els.topicFilter.value = item.topic;
      refreshMessages();
    });
    els.topicStats.append(row);
  }
}

function renderMessages(result) {
  const label = result.shown < result.total ? `${result.total} 件（先頭 ${result.shown} 件を表示）` : `${result.total} 件`;
  els.countLabel.textContent = label;
  topicStatsByName = new Map(result.topic_stats.map((s) => [s.topic, s]));
  renderStats(result.topic_stats);
  renderGapBadge(result.gap_total);

  const fragment = document.createDocumentFragment();
  for (const msg of result.messages) {
    const tr = document.createElement("tr");
    if (selected && msg.id === selected.id && msg.topic === selected.topic) tr.classList.add("active");
    const dtCell = el("td", "mono num", formatDt(msg.dt_ms));
    if (isGap(msg)) {
      tr.classList.add("gap");
      dtCell.title = intervalNote(msg);
    }
    tr.append(
      el("td", "mono nowrap", msg.ts),
      dtCell,
      el("td", "mono", msg.topic),
      el("td", "num", msg.qos),
      el("td", "mono payload-cell", msg.payload_text),
    );
    tr.addEventListener("click", () => selectMessage(msg, tr));
    fragment.append(tr);
  }
  els.messageBody.replaceChildren(fragment);
  if (!result.messages.length) {
    const tr = el("tr");
    const td = el("td", "empty", "該当するメッセージがありません");
    td.colSpan = 5;
    tr.append(td);
    els.messageBody.append(tr);
  }
}

function renderGapBadge(total) {
  const on = els.gapMode.value !== "off";
  els.gapBadge.hidden = !on;
  els.gapBadge.textContent = els.gapsOnly.checked ? `周期抜け ${total} 件（抜けのみ表示中）` : `周期抜け ${total} 件`;
  els.gapBadge.classList.toggle("has-gaps", total > 0);
  els.gapBadge.classList.toggle("pressed", els.gapsOnly.checked);
}

function filterParams() {
  const params = new URLSearchParams({ source });
  const add = (key, value) => value.trim() && params.set(key, value.trim());
  add("topic", els.topicFilter.value);
  add("payload", els.payloadFilter.value);
  add("time_from", els.timeFrom.value);
  add("time_to", els.timeTo.value);
  if (els.qosFilter.value !== "") params.set("qos", els.qosFilter.value);
  params.set("gap_mode", els.gapMode.value);
  add("gap_factor", els.gapFactor.value);
  add("gap_ms", els.gapMs.value);
  add("gap_topic", els.gapTopic.value);
  if (els.gapsOnly.checked && els.gapMode.value !== "off") params.set("gaps_only", "true");
  return params;
}

async function requestJson(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail;
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return data;
}

let latestRequest = 0;

async function refreshMessages({ quiet = false } = {}) {
  const requestId = ++latestRequest;
  try {
    const data = await requestJson(`/api/messages?${filterParams()}`);
    // A slower, older response must not overwrite the view the user asked for last.
    if (requestId === latestRequest) renderMessages(data);
  } catch (err) {
    if (!quiet) alert(err.message);
  }
}

function resetFilters() {
  els.topicFilter.value = "";
  els.payloadFilter.value = "";
  els.qosFilter.value = "";
  els.timeFrom.value = "";
  els.timeTo.value = "";
  els.gapsOnly.checked = false;
}

async function loadSample() {
  stopLivePolling();
  try {
    await requestJson(`/api/sample?${new URLSearchParams({ name: els.sampleSelect.value })}`);
    lastFile = null;
    resetFilters();
    setStatus("file", false);
    clearSelection();
    await refreshMessages();
  } catch (err) {
    alert(err.message);
  }
}

async function uploadFile(file) {
  stopLivePolling();
  const body = new FormData();
  body.append("file", file);
  for (const [id, key] of Object.entries(MAPPING_FIELDS)) body.append(key, els[id].value.trim());
  try {
    await requestJson("/api/upload", { method: "POST", body });
    lastFile = file;
    resetFilters();
    setStatus("file", false);
    clearSelection();
    await refreshMessages();
  } catch (err) {
    alert(`読み込みに失敗しました: ${err.message}`);
  }
}

function exportFiltered(format) {
  const params = filterParams();
  params.set("format", format);
  window.location.href = `/api/export?${params}`;
}

function narrowAroundSelected() {
  if (!selected || selected.ts_epoch == null) return;
  const sec = Math.max(0, Number(els.windowSec.value) || 0);
  els.timeFrom.value = formatJst(selected.ts_epoch - sec);
  els.timeTo.value = formatJst(selected.ts_epoch + sec);
  els.topicFilter.value = "";
  els.payloadFilter.value = "";
  els.qosFilter.value = "";
  refreshMessages();
}

async function copyPayload() {
  if (!selected) return;
  try {
    await navigator.clipboard.writeText(pretty(selected.payload));
    els.btnCopy.textContent = "コピーしました";
    setTimeout(() => (els.btnCopy.textContent = "Payloadコピー"), 1200);
  } catch {
    alert("クリップボードにコピーできませんでした");
  }
}

async function connectLive() {
  els.liveError.textContent = "";
  const payload = {
    host: els.mqttHost.value.trim() || "localhost",
    port: Number(els.mqttPort.value || 1883),
    topic: els.mqttTopic.value.trim() || "#",
    username: els.mqttUser.value.trim() || null,
    password: els.mqttPass.value || null,
  };
  try {
    const data = await requestJson("/api/live/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (data.error) els.liveError.textContent = data.error;
    clearSelection();
    setStatus("live", !!data.connected);
    startLivePolling();
    await refreshMessages();
  } catch (err) {
    els.liveError.textContent = err.message;
  }
}

async function disconnectLive() {
  await fetch("/api/live/disconnect", { method: "POST" });
  stopLivePolling();
  setStatus("live", false);
  await refreshMessages();
}

async function clearLive() {
  await fetch("/api/live/clear", { method: "POST" });
  clearSelection();
  await refreshMessages();
}

function startLivePolling() {
  stopLivePolling();
  liveTimer = setInterval(async () => {
    try {
      const status = await requestJson("/api/live/status");
      setStatus("live", !!status.connected);
      if (status.error) els.liveError.textContent = status.error;
      await refreshMessages({ quiet: true });
    } catch {
      /* keep polling; transient errors are expected while the broker reconnects */
    }
  }, 1000);
}

function stopLivePolling() {
  if (liveTimer) {
    clearInterval(liveTimer);
    liveTimer = null;
  }
}

function loadMapping() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    for (const id of Object.keys(MAPPING_FIELDS)) els[id].value = saved[id] || "";
    if (Object.values(saved).some(Boolean)) $("mappingBox").open = true;
  } catch {
    /* ignore broken storage */
  }
}

function saveMapping() {
  const data = {};
  for (const id of Object.keys(MAPPING_FIELDS)) data[id] = els[id].value.trim();
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

function syncGapFields() {
  els.gapFactorField.hidden = els.gapMode.value !== "auto";
  els.gapMsField.hidden = els.gapMode.value !== "fixed";
}

function loadGapSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(GAP_STORAGE_KEY) || "{}");
    for (const id of GAP_FIELDS) if (saved[id]) els[id].value = saved[id];
  } catch {
    /* ignore broken storage */
  }
  syncGapFields();
}

function onGapSettingsChange() {
  const data = {};
  for (const id of GAP_FIELDS) data[id] = els[id].value.trim();
  localStorage.setItem(GAP_STORAGE_KEY, JSON.stringify(data));
  syncGapFields();
  refreshMessages();
}

els.btnSample.addEventListener("click", loadSample);
els.btnUpload.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", () => {
  const file = els.fileInput.files?.[0];
  if (file) uploadFile(file);
  els.fileInput.value = "";
});
els.btnReparse.addEventListener("click", () => {
  if (lastFile) uploadFile(lastFile);
  else alert("先にログファイルを開いてください");
});
for (const id of Object.keys(MAPPING_FIELDS)) els[id].addEventListener("change", saveMapping);

els.btnApply.addEventListener("click", refreshMessages);
els.btnRefresh.addEventListener("click", refreshMessages);
els.btnClearFilter.addEventListener("click", () => {
  resetFilters();
  refreshMessages();
});
els.btnExportGraph.addEventListener("click", () => exportFiltered("graph"));
els.btnExportCsv.addEventListener("click", () => exportFiltered("csv"));
els.btnExportJsonl.addEventListener("click", () => exportFiltered("jsonl"));
els.btnWindow.addEventListener("click", narrowAroundSelected);
els.btnCopy.addEventListener("click", copyPayload);
els.btnConnect.addEventListener("click", connectLive);
els.btnDisconnect.addEventListener("click", disconnectLive);
els.btnLiveClear.addEventListener("click", clearLive);

for (const id of GAP_FIELDS) els[id].addEventListener("change", onGapSettingsChange);
els.gapTopic.addEventListener("keydown", (e) => {
  if (e.key === "Enter") onGapSettingsChange();
});
els.gapsOnly.addEventListener("change", refreshMessages);
els.gapBadge.addEventListener("click", () => {
  els.gapsOnly.checked = !els.gapsOnly.checked;
  refreshMessages();
});

for (const id of ["topicFilter", "payloadFilter", "timeFrom", "timeTo"]) {
  els[id].addEventListener("keydown", (e) => {
    if (e.key === "Enter") refreshMessages();
  });
}

["dragenter", "dragover"].forEach((type) =>
  els.dropZone.addEventListener(type, (e) => {
    e.preventDefault();
    els.dropZone.classList.add("dragging");
  }),
);
["dragleave", "drop"].forEach((type) =>
  els.dropZone.addEventListener(type, (e) => {
    e.preventDefault();
    els.dropZone.classList.remove("dragging");
  }),
);
els.dropZone.addEventListener("drop", (e) => {
  const file = e.dataTransfer?.files?.[0];
  if (file) uploadFile(file);
});

loadMapping();
loadGapSettings();
loadSample();
