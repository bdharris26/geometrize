const form = document.querySelector("#run-form");
const imageInput = document.querySelector("#image-input");
const fileLabel = document.querySelector("#file-label");
const sourcePreview = document.querySelector("#source-preview");
const resultPreview = document.querySelector("#result-preview");
const resultCanvas = document.querySelector("#result-canvas");
const runButton = document.querySelector("#run-button");
const sampleButton = document.querySelector("#sample-button");
const statusText = document.querySelector("#status");
const nativeState = document.querySelector("#native-state");
const metrics = document.querySelector("#metrics");
const steps = document.querySelector("#steps");
const stepsOut = document.querySelector("#steps-out");
const maxSize = document.querySelector("#max-size");
const maxSizeOut = document.querySelector("#max-size-out");
const maxSizeNumber = document.querySelector("#max-size-number");
const sourceMeta = document.querySelector("#source-meta");
const resultMeta = document.querySelector("#result-meta");
const pipeline = document.querySelector("#pipeline");
const telemetryState = document.querySelector("#telemetry-state");
const telemetryAcceptance = document.querySelector("#telemetry-acceptance");
const telemetryImprovement = document.querySelector("#telemetry-improvement");
const telemetryDuration = document.querySelector("#telemetry-duration");
const telemetryScore = document.querySelector("#telemetry-score");
const telemetryTotal = document.querySelector("#telemetry-total");
const telemetryResolution = document.querySelector("#telemetry-resolution");
const scoreGraph = document.querySelector("#score-graph");
const resolutionGraph = document.querySelector("#resolution-graph");
const primitiveMix = document.querySelector("#primitive-mix");
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
let activeUrls = [];
let runStartedAt = 0;
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

maxSize.addEventListener("input", () => syncMaxSize(maxSize.value, maxSizeNumber));
maxSizeNumber.addEventListener("input", () => syncMaxSize(maxSizeNumber.value, maxSize));

imageInput.addEventListener("change", () => {
  const file = imageInput.files[0];
  if (!file) {
    return;
  }
  fileLabel.textContent = file.name;
  const reader = new FileReader();
  reader.addEventListener("load", () => {
    sourceDataUrl = reader.result;
    sourcePreview.src = sourceDataUrl;
    resetResultSurface();
    clearDownloads();
    resetTelemetry();
    updateImageMeta(sourcePreview, sourceMeta);
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
  sourceDataUrl = canvas.toDataURL("image/png");
  sourcePreview.src = sourceDataUrl;
  fileLabel.textContent = "Generated sample";
  clearDownloads();
  resetResultSurface();
  resetTelemetry();
  statusText.textContent = "Ready";
  metrics.textContent = "";
  pipeline.textContent = "0 shapes";
  updateImageMeta(sourcePreview, sourceMeta);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!sourceDataUrl) {
    statusText.textContent = "Choose an image";
    return;
  }

  const shapeTypes = [...document.querySelectorAll("input[name='shape']:checked")].map((item) => item.value);
  if (shapeTypes.length === 0) {
    statusText.textContent = "Choose at least one shape";
    return;
  }

  setBusy(true);
  clearDownloads();
  resetTelemetry();
  resetResultSurface();
  statusText.textContent = "Running";
  telemetryState.textContent = "Running";
  metrics.textContent = "";
  pipeline.textContent = "Iterating";
  runStartedAt = performance.now();

  const payload = {
    image: sourceDataUrl,
    options: {
      steps: Number(steps.value),
      shape_types: shapeTypes,
      alpha: Number(document.querySelector("#alpha").value),
      seed: Number(document.querySelector("#seed").value),
      shape_count: Number(document.querySelector("#shape-count").value),
      mutations: Number(document.querySelector("#mutations").value),
      max_size: Number(maxSize.value)
    }
  };

  try {
    await streamRun(payload);
  } catch (error) {
    statusText.textContent = error.message;
    telemetryState.textContent = "Error";
  } finally {
    setBusy(false);
  }
});

async function streamRun(payload) {
  const response = await fetch("/api/run/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
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
    startLiveCanvas(event.width, event.height, event.background);
    resultMeta.textContent = `${event.width} x ${event.height}`;
    telemetryResolution.textContent = `${event.width} x ${event.height}`;
    renderResolutionGraph(event.width, event.height, 0, Number(steps.value));
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

function appendStep(event) {
  const shapes = event.shapes || [];
  shapes.forEach((shape) => {
    runStats.shapes.push(shape);
    runStats.primitiveCounts.set(shape.type, (runStats.primitiveCounts.get(shape.type) || 0) + 1);
    if (typeof shape.score === "number") {
      runStats.scores.push(shape.score);
    }
    drawShape(shape);
  });

  runStats.attempts = event.attempts;
  const latestScore = runStats.scores.at(-1);
  statusText.textContent = "Running";
  pipeline.textContent = `${event.attempts} / ${steps.value} steps`;
  metrics.textContent = `${runStats.shapes.length} shapes`;
  telemetryAcceptance.textContent = `${runStats.shapes.length} accepted / ${event.attempts} attempts`;
  telemetryTotal.textContent = String(runStats.shapes.length);
  telemetryDuration.textContent = formatDuration(performance.now() - runStartedAt);
  telemetryScore.textContent = formatScore(latestScore);
  telemetryImprovement.textContent = formatScoreDelta(runStats.scores);
  renderScoreGraph(runStats.scores);
  renderPrimitiveMix();
  renderResolutionGraph(renderSpace.width, renderSpace.height, runStats.shapes.length, Number(steps.value));
}

function finishRun(data) {
  const elapsed = performance.now() - runStartedAt;
  resultPreview.src = data.preview;
  statusText.textContent = "Complete";
  telemetryState.textContent = "Complete";
  metrics.textContent = `${data.shape_count} shapes, ${data.width} x ${data.height}`;
  pipeline.textContent = `${data.attempts} steps`;
  resultMeta.textContent = `${data.width} x ${data.height}`;
  telemetryAcceptance.textContent = `${data.shape_count} accepted / ${data.attempts} attempts`;
  telemetryDuration.textContent = formatDuration(elapsed);
  telemetryTotal.textContent = String(data.shape_count);
  setDownload(downloads.png, data.preview, "geometrize.png");
  setDownload(downloads.svg, makeObjectUrl(data.svg, "image/svg+xml"), "geometrize.svg");
  setDownload(downloads.json, makeObjectUrl(JSON.stringify(data.shapes, null, 2), "application/json"), "geometrize.json");
}

function startLiveCanvas(width, height, background) {
  const longest = Math.max(width, height);
  const scale = Math.min(1, LIVE_CANVAS_MAX / longest);
  renderSpace = { width, height, scale };
  resultCanvas.width = Math.max(1, Math.round(width * scale));
  resultCanvas.height = Math.max(1, Math.round(height * scale));
  resultCanvas.style.aspectRatio = `${width} / ${height}`;
  resultCanvas.hidden = false;
  const context = liveContext();
  context.fillStyle = rgba(background);
  context.fillRect(0, 0, width, height);
}

function resetResultSurface() {
  resultPreview.removeAttribute("src");
  resultCanvas.hidden = true;
  resultCanvas.width = 1;
  resultCanvas.height = 1;
  resultMeta.textContent = "";
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

function renderResolutionGraph(width, height, shapeCount, targetSteps) {
  const area = width && height ? width * height : 0;
  const shapeLevel = targetSteps > 0 ? Math.min(1, shapeCount / targetSteps) : 0;
  const yArea = area ? 34 : 96;
  const yShapes = 104 - shapeLevel * 70;
  resolutionGraph.innerHTML = `
    <path class="graph-grid" d="M0 24H360M0 60H360M0 96H360" />
    <path class="area-path" d="M0 ${yArea}H360" />
    <path class="shape-path" d="M0 104L360 ${yShapes}" />
  `;
}

function renderPrimitiveMix() {
  const rows = [...runStats.primitiveCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  const total = Math.max(1, runStats.shapes.length);
  primitiveMix.innerHTML = rows.map(([type, count]) => {
    const percent = count / total;
    return `
      <div class="mix-row">
        <span>${SHAPE_LABELS[type] || type}</span>
        <div class="mix-track"><span style="inline-size:${Math.max(2, percent * 100)}%"></span></div>
        <output>${count}</output>
      </div>
    `;
  }).join("");
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
  telemetryResolution.textContent = "--";
  primitiveMix.innerHTML = "";
  renderScoreGraph([]);
  renderResolutionGraph(0, 0, 0, Number(steps.value));
}

function createRunStats() {
  return {
    attempts: 0,
    shapes: [],
    scores: [],
    primitiveCounts: new Map()
  };
}

function setBusy(busy) {
  runButton.disabled = busy;
  runButton.textContent = busy ? "Running" : "Run";
}

function syncMaxSize(value, mirror) {
  const numericValue = Math.max(maxSizeBounds.min, Math.min(maxSizeBounds.max, Number(value) || maxSizeBounds.min));
  const snapped = Math.round(numericValue / maxSizeBounds.step) * maxSizeBounds.step;
  maxSize.value = String(snapped);
  maxSizeNumber.value = String(snapped);
  maxSizeOut.value = String(snapped);
  mirror.value = String(snapped);
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
  return typeof value === "number" ? value.toFixed(4) : "--";
}

function formatScoreDelta(series) {
  if (series.length < 2) {
    return "Score --";
  }
  const delta = series.at(-1) - series.at(-2);
  return `Score ${delta >= 0 ? "+" : ""}${delta.toFixed(4)}`;
}

function formatDuration(ms) {
  const seconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = String(seconds % 60).padStart(2, "0");
  return `${minutes}:${remainder}`;
}

function degreesToRadians(value) {
  return (Number(value) || 0) * Math.PI / 180;
}

resetResultSurface();
resetTelemetry();
