"use strict";

const form = byId("run-form");
const imageInput = byId("image-input");
const fileLabel = byId("file-label");
const imageName = byId("image-name");
const sourcePreview = byId("source-preview");
const resultPreview = byId("result-preview");
const resultCanvas = byId("result-canvas");
const runButton = byId("run-button");
const pauseButton = byId("pause-button");
const sampleButton = byId("sample-button");
const statusText = byId("status");
const nativeState = byId("native-state");
const metrics = byId("metrics");
const steps = byId("steps");
const stepsOut = byId("steps-out");
const maxSize = byId("max-size");
const maxSizeOut = byId("max-size-out");
const maxSizeNumber = byId("max-size-number");
const sourceMeta = byId("source-meta");
const resultMeta = byId("result-meta");
const pipeline = byId("pipeline");
const alpha = byId("alpha");
const seed = byId("seed");
const shapeCount = byId("shape-count");
const mutations = byId("mutations");
const telemetryState = byId("telemetry-state");
const telemetryAcceptance = byId("telemetry-acceptance");
const telemetryImprovement = byId("telemetry-improvement");
const telemetryDuration = byId("telemetry-duration");
const telemetryScore = byId("telemetry-score");
const telemetryTotal = byId("telemetry-total");
const telemetryImpact = byId("telemetry-impact");
const scoreGraph = byId("score-graph");
const impactGraph = byId("impact-graph");
const primitiveMix = byId("primitive-mix");
const batchHistory = byId("batch-history");
const maxSizeBounds = {
  min: Number(maxSize.min),
  max: Number(maxSize.max),
  step: Number(maxSize.step)
};
const downloads = {
  png: document.querySelector("#download-png"),
  svg: document.querySelector("#download-svg"),
  json: document.querySelector("#download-json")
};

const LIVE_CANVAS_MAX = 1200;
const SHAPE_LABELS = {
  circle: "Circle",
  ellipse: "Ellipse",
  line: "Line",
  polyline: "Polyline",
  quadratic_bezier: "Bezier",
  rectangle: "Rectangle",
  rotated_ellipse: "Rotated ellipse",
  rotated_rectangle: "Rotated rect",
  triangle: "Triangle"
};

let sourceDataUrl = "";
let activeSessionId = "";
let activeUrls = [];
let runStartedAt = 0;
let runningController = null;
let currentBatch = null;
let runStats = createRunStats();
let renderSpace = {
  width: 0,
  height: 0,
  scale: 1
};

fetch("/health")
  .then((response) => response.json())
  .then((data) => {
    nativeState.textContent = data.native ? "Core ready" : "Core unavailable";
  })
  .catch(() => {
    nativeState.textContent = "Server unavailable";
  });

steps.addEventListener("input", () => {
  stepsOut.value = steps.value;
});

maxSize.addEventListener("input", () => syncMaxSize(maxSize.value));
maxSizeNumber.addEventListener("input", () => syncMaxSize(maxSizeNumber.value));

imageInput.addEventListener("change", () => {
  const file = imageInput.files[0];
  if (!file) {
    return;
  }
  const reader = new FileReader();
  reader.addEventListener("load", () => {
    setSourceImage(reader.result, file.name);
  });
  reader.readAsDataURL(file);
});

sampleButton.addEventListener("click", () => {
  const canvas = document.createElement("canvas");
  canvas.width = 220;
  canvas.height = 160;
  const context = canvas.getContext("2d");
  const fill = context.createLinearGradient(0, 0, 220, 160);
  fill.addColorStop(0, "#f1cf6a");
  fill.addColorStop(0.55, "#57935d");
  fill.addColorStop(1, "#9b5c3a");
  context.fillStyle = fill;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "rgba(252,245,226,0.84)";
  context.beginPath();
  context.arc(62, 54, 34, 0, Math.PI * 2);
  context.fill();
  context.fillStyle = "rgba(22,20,17,0.78)";
  context.beginPath();
  context.moveTo(72, 138);
  context.lineTo(138, 36);
  context.lineTo(190, 138);
  context.closePath();
  context.fill();
  setSourceImage(canvas.toDataURL("image/png"), "Generated sample");
});

pauseButton.addEventListener("click", () => {
  if (!runningController) {
    return;
  }
  statusText.textContent = "Pausing";
  telemetryState.textContent = "Pausing";
  runningController.abort();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!sourceDataUrl && !activeSessionId) {
    statusText.textContent = "Choose an image";
    return;
  }

  const shapeTypes = selectedShapeTypes();
  if (shapeTypes.length === 0) {
    statusText.textContent = "Choose at least one shape";
    return;
  }

  const isContinuation = Boolean(activeSessionId);
  const options = currentOptions(shapeTypes);
  const payload = {
    options
  };
  if (isContinuation) {
    payload.session_id = activeSessionId;
  } else {
    payload.image = sourceDataUrl;
  }

  prepareRun(isContinuation, options);
  const controller = new AbortController();
  runningController = controller;

  try {
    await streamRun(payload, controller.signal);
  } catch (error) {
    if (isAbortError(error)) {
      recordCurrentBatch("Paused");
      statusText.textContent = "Paused";
      telemetryState.textContent = "Paused";
    } else {
      if (error.message.includes("Unknown render session")) {
        activeSessionId = "";
      }
      statusText.textContent = error.message;
      telemetryState.textContent = "Error";
    }
  } finally {
    runningController = null;
    setBusy(false);
  }
});

function setSourceImage(dataUrl, label) {
  sourceDataUrl = dataUrl;
  activeSessionId = "";
  currentBatch = null;
  sourcePreview.src = sourceDataUrl;
  fileLabel.textContent = "Change image";
  imageName.textContent = label;
  resetResultSurface();
  clearDownloads();
  resetTelemetry();
  statusText.textContent = "Ready";
  metrics.textContent = "";
  pipeline.textContent = "0 shapes";
  updateImageMeta(sourcePreview, sourceMeta);
  setBusy(false);
}

function selectedShapeTypes() {
  return [...document.querySelectorAll("input[name='shape']:checked")].map((item) => item.value);
}

function currentOptions(shapeTypes) {
  const longestDimension = Number(maxSize.value);
  return {
    steps: Number(steps.value),
    shape_types: shapeTypes,
    alpha: Number(alpha.value),
    seed: Number(seed.value),
    shape_count: Number(shapeCount.value),
    mutations: Number(mutations.value),
    max_size: longestDimension,
    export_size: longestDimension
  };
}

function prepareRun(isContinuation, options) {
  if (!isContinuation) {
    activeSessionId = "";
    resetTelemetry();
    resetResultSurface();
  }
  clearDownloads();
  setBusy(true);
  statusText.textContent = isContinuation ? "Continuing" : "Running";
  telemetryState.textContent = isContinuation ? "Continuing" : "Running";
  metrics.textContent = "";
  pipeline.textContent = "Iterating";
  runStartedAt = performance.now();
  currentBatch = {
    index: runStats.batches.length + 1,
    startedAtShapeCount: runStats.shapes.length,
    startedAtAttempts: runStats.attempts,
    target: options.steps,
    shapeTypes: options.shape_types,
    candidates: options.shape_count,
    mutations: options.mutations,
    alpha: options.alpha
  };
}

async function streamRun(payload, signal) {
  const response = await fetch("/api/run/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal
  });

  if (!response.ok) {
    const data = await response.json();
    throw new Error(data.error || "Render failed");
  }
  if (!response.body) {
    throw new Error("Streaming is unavailable");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      handleRunEvent(JSON.parse(line));
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) {
    handleRunEvent(JSON.parse(buffer));
  }
}

function handleRunEvent(event) {
  if (event.event === "start") {
    activeSessionId = event.session_id || activeSessionId;
    handleStartEvent(event);
    return;
  }

  if (event.event === "step") {
    appendStep(event);
    return;
  }

  if (event.event === "complete") {
    finishRun(event);
    return;
  }

  if (event.event === "error") {
    throw new Error(event.error || "Render failed");
  }
}

function handleStartEvent(event) {
  startLiveCanvas(event.width, event.height, event.background);
  if (event.continued && Array.isArray(event.shapes)) {
    rebuildFromSnapshot(event.shapes, event.attempts);
  }
  if (event.continued && currentBatch) {
    currentBatch.startedAtShapeCount = runStats.shapes.length;
    currentBatch.startedAtAttempts = event.attempts || runStats.attempts;
  }
  resultMeta.textContent = `${event.width} x ${event.height}`;
  telemetryAcceptance.textContent = `${runStats.shapes.length} accepted / ${event.attempts || runStats.attempts} attempts`;
  telemetryTotal.textContent = String(runStats.shapes.length);
  renderScoreGraph(runStats.scores);
  renderImpactGraph(runStats.improvements);
  renderPrimitiveMix();
}

function rebuildFromSnapshot(shapes, attempts) {
  const batches = runStats.batches;
  runStats = createRunStats(batches);
  runStats.attempts = attempts || 0;
  shapes.forEach((shape) => appendShape(shape, true));
}

function appendStep(event) {
  const shapes = event.shapes || [];
  shapes.forEach((shape) => appendShape(shape, true));

  runStats.attempts = event.attempts;
  const latestScore = runStats.scores.at(-1);
  const latestImpact = runStats.improvements.at(-1);
  statusText.textContent = "Running";
  pipeline.textContent = `${event.batch_shape_count} / ${event.batch_goal} shapes in batch`;
  metrics.textContent = `${runStats.shapes.length} shapes`;
  telemetryAcceptance.textContent = `${runStats.shapes.length} accepted / ${event.attempts} attempts`;
  telemetryTotal.textContent = String(runStats.shapes.length);
  telemetryDuration.textContent = formatDuration(performance.now() - runStartedAt);
  telemetryScore.textContent = formatScore(latestScore);
  telemetryImprovement.textContent = formatImpact(latestImpact);
  telemetryImpact.textContent = formatImpactValue(latestImpact);
  renderScoreGraph(runStats.scores);
  renderImpactGraph(runStats.improvements);
  renderPrimitiveMix();
}

function appendShape(shape, draw) {
  const previousScore = runStats.scores.at(-1);
  runStats.shapes.push(shape);
  runStats.primitiveCounts.set(shape.type, (runStats.primitiveCounts.get(shape.type) || 0) + 1);
  if (typeof shape.score === "number") {
    if (typeof previousScore === "number") {
      runStats.improvements.push(Math.max(0, previousScore - shape.score));
    }
    runStats.scores.push(shape.score);
  }
  if (draw) {
    drawShape(shape);
  }
}

function finishRun(data) {
  const elapsed = performance.now() - runStartedAt;
  activeSessionId = data.session_id || activeSessionId;
  resultCanvas.hidden = true;
  resultPreview.removeAttribute("aria-hidden");
  resultPreview.src = data.preview;
  recordCurrentBatch("Complete");
  statusText.textContent = "Complete";
  telemetryState.textContent = "Complete";
  metrics.textContent = `${data.shape_count} shapes, ${data.export_width || data.width} x ${data.export_height || data.height}`;
  pipeline.textContent = `${data.attempts} attempts`;
  resultMeta.textContent = `${data.export_width || data.width} x ${data.export_height || data.height}`;
  telemetryAcceptance.textContent = `${data.shape_count} accepted / ${data.attempts} attempts`;
  telemetryDuration.textContent = formatDuration(elapsed);
  telemetryTotal.textContent = String(data.shape_count);
  setDownload(downloads.png, data.preview, "geometrize.png");
  setDownload(downloads.svg, makeObjectUrl(data.svg, "image/svg+xml"), "geometrize.svg");
  setDownload(downloads.json, makeObjectUrl(JSON.stringify(data.shapes, null, 2), "application/json"), "geometrize.json");
}

function recordCurrentBatch(state) {
  if (!currentBatch) {
    return;
  }
  const added = Math.max(0, runStats.shapes.length - currentBatch.startedAtShapeCount);
  const attempts = Math.max(0, runStats.attempts - currentBatch.startedAtAttempts);
  runStats.batches.push({
    ...currentBatch,
    added,
    attempts,
    state
  });
  currentBatch = null;
  renderBatchHistory();
}

function renderBatchHistory() {
  const chips = runStats.batches.map((batch) => {
    const shapeLabel = batch.shapeTypes.map((type) => SHAPE_LABELS[type] || type).join(", ");
    const chip = document.createElement("span");
    const label = document.createElement("span");
    const added = document.createElement("strong");
    const state = document.createElement("span");

    chip.className = "batch-chip";
    chip.title = `${shapeLabel}; ${batch.candidates} candidates, ${batch.mutations} mutations, alpha ${batch.alpha}`;
    label.textContent = `Batch ${batch.index}`;
    added.textContent = `+${batch.added}`;
    state.textContent = batch.state;
    chip.replaceChildren(label, added, state);
    return chip;
  });
  batchHistory.replaceChildren(...chips);
}

function startLiveCanvas(width, height, background) {
  const longest = Math.max(width, height);
  const scale = Math.min(1, LIVE_CANVAS_MAX / longest);
  renderSpace = { width, height, scale };
  resultCanvas.width = Math.max(1, Math.round(width * scale));
  resultCanvas.height = Math.max(1, Math.round(height * scale));
  resultCanvas.style.aspectRatio = `${width} / ${height}`;
  resultCanvas.hidden = false;
  resultPreview.setAttribute("aria-hidden", "true");
  resultPreview.removeAttribute("src");
  const context = liveContext();
  context.fillStyle = rgba(background);
  context.fillRect(0, 0, width, height);
}

function resetResultSurface() {
  resultPreview.setAttribute("aria-hidden", "true");
  resultPreview.removeAttribute("src");
  resultCanvas.hidden = true;
  resultCanvas.width = 1;
  resultCanvas.height = 1;
  resultMeta.textContent = "";
  renderSpace = { width: 0, height: 0, scale: 1 };
}

function drawShape(shape) {
  const context = liveContext();
  const data = shape.data || {};
  context.save();
  context.fillStyle = rgba(shape.color);
  context.strokeStyle = rgba(shape.color);
  context.lineWidth = 1;

  if (shape.type === "circle") {
    context.beginPath();
    context.arc(data.x, data.y, data.r, 0, Math.PI * 2);
    context.fill();
  } else if (shape.type === "ellipse") {
    context.beginPath();
    context.ellipse(data.x, data.y, data.rx, data.ry, 0, 0, Math.PI * 2);
    context.fill();
  } else if (shape.type === "rotated_ellipse") {
    context.beginPath();
    context.ellipse(data.x, data.y, data.rx, data.ry, degreesToRadians(data.angle), 0, Math.PI * 2);
    context.fill();
  } else if (shape.type === "rectangle") {
    drawRect(context, data.x1, data.y1, data.x2, data.y2);
  } else if (shape.type === "rotated_rectangle") {
    drawRotatedRect(context, data);
  } else if (shape.type === "triangle") {
    context.beginPath();
    context.moveTo(data.x1, data.y1);
    context.lineTo(data.x2, data.y2);
    context.lineTo(data.x3, data.y3);
    context.closePath();
    context.fill();
  } else if (shape.type === "line") {
    context.beginPath();
    context.moveTo(data.x1, data.y1);
    context.lineTo(data.x2, data.y2);
    context.stroke();
  } else if (shape.type === "quadratic_bezier") {
    context.beginPath();
    context.moveTo(data.x1, data.y1);
    context.quadraticCurveTo(data.cx, data.cy, data.x2, data.y2);
    context.stroke();
  } else if (shape.type === "polyline") {
    drawPolyline(context, data.points || []);
  }

  context.restore();
}

function liveContext() {
  const context = resultCanvas.getContext("2d");
  context.setTransform(renderSpace.scale, 0, 0, renderSpace.scale, 0, 0);
  return context;
}

function drawRect(context, x1, y1, x2, y2) {
  const x = Math.min(x1, x2);
  const y = Math.min(y1, y2);
  context.fillRect(x, y, Math.abs(x2 - x1), Math.abs(y2 - y1));
}

function drawRotatedRect(context, data) {
  const x = Math.min(data.x1, data.x2);
  const y = Math.min(data.y1, data.y2);
  const width = Math.abs(data.x2 - data.x1);
  const height = Math.abs(data.y2 - data.y1);
  const cx = x + width / 2;
  const cy = y + height / 2;
  context.translate(cx, cy);
  context.rotate(degreesToRadians(data.angle));
  context.fillRect(-width / 2, -height / 2, width, height);
}

function drawPolyline(context, points) {
  if (points.length === 0) {
    return;
  }
  context.beginPath();
  context.moveTo(points[0][0], points[0][1]);
  points.slice(1).forEach(([x, y]) => context.lineTo(x, y));
  context.stroke();
}

function renderScoreGraph(series) {
  const path = pathForSeries(series, 360, 120);
  const last = series.at(-1);
  scoreGraph.innerHTML = `
    <path class="graph-grid" d="M0 24H360M0 60H360M0 96H360" />
    <path class="score-path" d="${path}" />
    ${last === undefined ? "" : `<circle class="graph-dot" cx="348" cy="${pointY(series, last, 120)}" r="3" />`}
  `;
}

function renderImpactGraph(series) {
  const recent = series.slice(-96);
  if (recent.length === 0) {
    impactGraph.innerHTML = '<path class="graph-grid" d="M0 24H360M0 60H360M0 96H360" />';
    return;
  }
  const max = Math.max(...recent, 0.000001);
  const barWidth = Math.max(2, 360 / recent.length);
  const bars = recent.map((value, index) => {
    const normalized = Math.max(0, value) / max;
    const height = Math.max(1, normalized * 86);
    const x = index * barWidth;
    const y = 106 - height;
    return `<rect class="impact-bar" x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${Math.max(1, barWidth - 1).toFixed(2)}" height="${height.toFixed(2)}" />`;
  }).join("");
  impactGraph.innerHTML = `
    <path class="graph-grid" d="M0 24H360M0 60H360M0 96H360" />
    <path class="impact-axis" d="M0 106H360" />
    ${bars}
  `;
}

function renderPrimitiveMix() {
  const rows = [...runStats.primitiveCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  const total = Math.max(1, runStats.shapes.length);
  const elements = rows.map(([type, count]) => {
    const percent = count / total;
    const row = document.createElement("div");
    const label = document.createElement("span");
    const track = document.createElement("div");
    const fill = document.createElement("span");
    const output = document.createElement("output");

    row.className = "mix-row";
    track.className = "mix-track";
    fill.style.inlineSize = `${Math.max(2, percent * 100)}%`;
    label.textContent = SHAPE_LABELS[type] || type;
    output.textContent = String(count);
    track.append(fill);
    row.replaceChildren(label, track, output);
    return row;
  });
  primitiveMix.replaceChildren(...elements);
}

function pathForSeries(series, width, height) {
  if (series.length === 0) {
    return "";
  }
  if (series.length === 1) {
    return `M0 ${height / 2}H${width}`;
  }
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  return series.map((value, index) => {
    const x = (index / (series.length - 1)) * width;
    const y = 12 + ((max - value) / range) * (height - 24);
    return `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join("");
}

function pointY(series, value, height) {
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  return (12 + ((max - value) / range) * (height - 24)).toFixed(2);
}

function resetTelemetry() {
  runStats = createRunStats();
  telemetryState.textContent = "Idle";
  telemetryAcceptance.textContent = "0 accepted / 0 attempts";
  telemetryImprovement.textContent = "Score --";
  telemetryDuration.textContent = "--";
  telemetryScore.textContent = "--";
  telemetryTotal.textContent = "0";
  telemetryImpact.textContent = "--";
  primitiveMix.replaceChildren();
  batchHistory.replaceChildren();
  renderScoreGraph([]);
  renderImpactGraph([]);
}

function createRunStats(batches = []) {
  return {
    attempts: 0,
    shapes: [],
    scores: [],
    improvements: [],
    primitiveCounts: new Map(),
    batches
  };
}

function setBusy(busy) {
  runButton.disabled = busy;
  pauseButton.disabled = !busy;
  runButton.textContent = busy ? "Running" : (activeSessionId ? "Continue" : "Run");
}

function syncMaxSize(value) {
  const numericValue = Math.max(maxSizeBounds.min, Math.min(maxSizeBounds.max, Number(value) || maxSizeBounds.min));
  const snapped = Math.round(numericValue / maxSizeBounds.step) * maxSizeBounds.step;
  maxSize.value = String(snapped);
  maxSizeNumber.value = String(snapped);
  maxSizeOut.value = String(snapped);
}

function clearDownloads() {
  activeUrls.forEach((url) => URL.revokeObjectURL(url));
  activeUrls = [];
  Object.values(downloads).forEach((link) => {
    link.removeAttribute("href");
    link.removeAttribute("download");
    link.setAttribute("aria-disabled", "true");
  });
}

function makeObjectUrl(content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  activeUrls.push(url);
  return url;
}

function setDownload(link, href, filename) {
  link.href = href;
  link.download = filename;
  link.setAttribute("aria-disabled", "false");
}

function updateImageMeta(image, target) {
  if (image.complete && image.naturalWidth) {
    target.textContent = `${image.naturalWidth} x ${image.naturalHeight}`;
    return;
  }
  image.addEventListener("load", () => {
    target.textContent = `${image.naturalWidth} x ${image.naturalHeight}`;
  }, { once: true });
}

function rgba(color) {
  const value = Array.isArray(color) ? { r: color[0], g: color[1], b: color[2], a: color[3] } : color || {};
  const alpha = value.a === undefined ? 1 : Number(value.a) / 255;
  return `rgba(${Number(value.r) || 0}, ${Number(value.g) || 0}, ${Number(value.b) || 0}, ${alpha})`;
}

function formatScore(value) {
  return typeof value === "number" ? formatFixed(value) : "--";
}

function formatImpact(value) {
  return typeof value === "number" ? `Impact +${formatFixed(Math.max(0, value))}` : "Impact --";
}

function formatImpactValue(value) {
  return typeof value === "number" ? `+${formatFixed(Math.max(0, value))}` : "--";
}

function formatFixed(value) {
  const normalized = Math.abs(value) < 0.00005 ? 0 : value;
  return normalized.toFixed(4);
}

function formatDuration(ms) {
  const seconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = String(seconds % 60).padStart(2, "0");
  return `${minutes}:${remainder}`;
}

function isAbortError(error) {
  return error && error.name === "AbortError";
}

function degreesToRadians(value) {
  return (Number(value) || 0) * Math.PI / 180;
}

function byId(id) {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing required UI element: #${id}`);
  }
  return element;
}

resetResultSurface();
resetTelemetry();
