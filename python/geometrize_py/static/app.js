"use strict";

import { ApiError, postJson, readRunStream } from "./stream.js";
import { Preview } from "./preview.js";
import { FocusControls, FocusUpdates, PaintQueue } from "./focus.js";
import { Telemetry } from "./telemetry.js";
import { decodeImage, imageToPngDataUrl, openProjectFile, rasterDataUrl, readFileAsDataUrl } from "./project.js";

const byId = (id) => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing required UI element: #${id}`);
  return element;
};

const ui = {
  form: byId("run-form"), imageInput: byId("image-input"), fileLabel: byId("file-label"),
  imageName: byId("image-name"), sourceImage: byId("source-preview"), resultImage: byId("result-preview"),
  resultCanvas: byId("result-canvas"), run: byId("run-button"), pause: byId("pause-button"),
  restart: byId("restart-button"), sample: byId("sample-button"), projectInput: byId("project-input"),
  openProject: byId("load-project-button"), saveProject: byId("save-project-button"),
  status: byId("status"), nativeState: byId("native-state"), metrics: byId("metrics"),
  pipeline: byId("pipeline"), sourceMeta: byId("source-meta"), resultMeta: byId("result-meta"),
  preset: byId("preset"), steps: byId("steps"), stepsNumber: byId("steps-number"),
  shapeGrid: byId("shape-grid"),
  stepsOut: byId("steps-out"), maxSize: byId("max-size"), maxSizeNumber: byId("max-size-number"),
  maxSizeOut: byId("max-size-out"), exportSize: byId("export-size"),
  exportSizeNumber: byId("export-size-number"), exportSizeOut: byId("export-size-out"),
  alpha: byId("alpha"), seed: byId("seed"), shapeCount: byId("shape-count"),
  mutations: byId("mutations"), maxThreads: byId("max-threads"),
  stagnationLimit: byId("stagnation-limit"), settingsHelp: byId("settings-help"),
  downloads: {
    png: byId("download-png"), svg: byId("download-svg"), json: byId("download-json")
  }
};

const STOP_LABELS = {
  target_reached: "Target reached",
  adequate_fit: "Fit already adequate",
  no_further_improvement: "No further improvement",
  attempt_limit: "Attempt limit reached",
  paused: "Paused",
  error: "Render failed"
};

let contract;
let preview;
let telemetry;
let focus;
let focusUpdates;
let paintQueue;
let runIsPaint = false;
let focusAtRunStart = null;
let sourceDataUrl = "";
let sourceName = "";
let sessionId = "";
let phase = "idle";
let stableResult = null;
let workingResult = null;
let loadedPreviewOnlyResult = null;
let sceneVersion = 0;
let contentVersion = 0;
let exportVersion = 0;
let runVersion = 0;
let activeRunId = "";
let runStartedAt = 0;
let lastDurationMs = 0;
let lastArtifact = null;
let exportSetting = Number(ui.exportSize.value);
const exportCache = new Map();
const pendingExports = new Map();

function selectedShapeTypes() {
  return [...document.querySelectorAll("input[name='shape']:checked")].map((item) => item.value);
}

function renderShapeControls() {
  const defaults = new Set(contract.defaults.shape_types);
  const defaultOrder = new Map(contract.defaults.shape_types.map((type, index) => [type, index]));
  const shapes = [...contract.shapes].sort((left, right) =>
    (defaultOrder.get(left.type) ?? 100) - (defaultOrder.get(right.type) ?? 100));
  const labels = shapes.map((shape) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    const symbol = document.createElement("span");
    const name = document.createElement("span");
    input.type = "checkbox";
    input.name = "shape";
    input.value = shape.type;
    input.checked = defaults.has(shape.type);
    symbol.className = `shape-symbol ${shape.type.replaceAll("_", "-")}-symbol`;
    name.className = "shape-name";
    name.textContent = shape.label;
    label.replaceChildren(input, symbol, name);
    return label;
  });
  ui.shapeGrid.replaceChildren(ui.shapeGrid.querySelector("legend"), ...labels);
}

function normalizeInteger(input, key) {
  const bounds = contract.limits[key];
  const fallback = contract.defaults[key];
  const raw = input.valueAsNumber;
  const value = Number.isFinite(raw) ? Math.trunc(raw) : fallback;
  const normalized = Math.min(bounds.max, Math.max(bounds.min, value));
  input.value = String(normalized);
  return normalized;
}

function syncPair(slider, number, output, value) {
  const min = Number(slider.min);
  const max = Number(slider.max);
  const step = Number(slider.step) || 1;
  const raw = Number(value);
  const snapped = min + Math.round((Math.min(max, Math.max(min, Number.isFinite(raw) ? raw : min)) - min) / step) * step;
  slider.value = String(snapped);
  number.value = String(snapped);
  output.value = String(snapped);
  return snapped;
}

function commitExportSize(value) {
  const size = syncPair(ui.exportSize, ui.exportSizeNumber, ui.exportSizeOut, value);
  if (size !== exportSetting) {
    exportSetting = size;
    exportVersion += 1;
  }
  return size;
}

function currentOptions() {
  const steps = syncPair(ui.steps, ui.stepsNumber, ui.stepsOut, ui.stepsNumber.value);
  const maxSize = syncPair(ui.maxSize, ui.maxSizeNumber, ui.maxSizeOut, ui.maxSizeNumber.value);
  const exportSize = commitExportSize(ui.exportSizeNumber.value);
  return {
    steps,
    shape_types: selectedShapeTypes(),
    alpha: normalizeInteger(ui.alpha, "alpha"),
    seed: normalizeInteger(ui.seed, "seed"),
    shape_count: normalizeInteger(ui.shapeCount, "shape_count"),
    mutations: normalizeInteger(ui.mutations, "mutations"),
    max_threads: normalizeInteger(ui.maxThreads, "max_threads"),
    stagnation_limit: normalizeInteger(ui.stagnationLimit, "stagnation_limit"),
    max_size: maxSize,
    export_size: exportSize,
    focus: focus.value ? { ...focus.value } : null
  };
}

function applyOptions(options) {
  focus.replace(options.focus);
  syncPair(ui.steps, ui.stepsNumber, ui.stepsOut, options.steps);
  syncPair(ui.maxSize, ui.maxSizeNumber, ui.maxSizeOut, options.max_size);
  commitExportSize(options.export_size);
  for (const [key, input] of [["alpha", ui.alpha], ["seed", ui.seed],
    ["shape_count", ui.shapeCount], ["mutations", ui.mutations],
    ["max_threads", ui.maxThreads], ["stagnation_limit", ui.stagnationLimit]]) {
    input.value = String(options[key] ?? contract.defaults[key]);
  }
  document.querySelectorAll("input[name='shape']").forEach((input) => {
    input.checked = options.shape_types.includes(input.value);
  });
  ui.preset.value = "custom";
}

function setControls() {
  const busy = phase === "running" || phase === "pausing";
  ui.run.disabled = !contract || !sourceDataUrl || busy;
  ui.run.textContent = busy ? (phase === "pausing" ? "Pausing" : "Running") :
    (phase === "recovery" && sessionId ? "Recover result" : (sessionId ? "Continue" : "Run"));
  ui.pause.disabled = phase !== "running" || !sessionId || !activeRunId;
  ui.restart.disabled = !sourceDataUrl || busy;
  ui.imageInput.disabled = busy;
  ui.sample.disabled = busy;
  ui.projectInput.disabled = busy;
  ui.openProject.disabled = busy;
  ui.saveProject.disabled = busy || !sourceDataUrl;
  ui.maxSize.disabled = busy || Boolean(sessionId);
  ui.maxSizeNumber.disabled = busy || Boolean(sessionId);
  ui.settingsHelp.textContent = sessionId
    ? "Edits apply to the next batch. Working resolution is fixed until New render."
    : "Settings apply when you Run. Export resolution is used when a download is clicked.";
  focus?.setAvailability(Boolean(sourceDataUrl), busy);
  paintQueue?.setBusy(busy && !runIsPaint);
  for (const link of Object.values(ui.downloads)) {
    const available = Boolean(stableResult);
    link.setAttribute("aria-disabled", available ? "false" : "true");
    link.href = available ? "#" : "";
  }
}

function clearResult() {
  focusUpdates.end();
  paintQueue.clear();
  sessionId = "";
  activeRunId = "";
  stableResult = null;
  workingResult = null;
  loadedPreviewOnlyResult = null;
  lastDurationMs = 0;
  exportCache.clear();
  pendingExports.clear();
  lastArtifact = null;
  sceneVersion += 1;
  exportVersion += 1;
  preview.clearResult();
  telemetry.reset();
  ui.resultMeta.textContent = "";
  ui.metrics.textContent = "";
  ui.pipeline.textContent = "0 shapes";
  phase = "idle";
  runIsPaint = false;
  setControls();
}

function setSource(dataUrl, name) {
  focus.reset();
  contentVersion += 1;
  runVersion += 1;
  sourceDataUrl = dataUrl;
  sourceName = name;
  preview.setSource(dataUrl);
  ui.sourceImage.alt = name;
  ui.fileLabel.textContent = "Change image";
  ui.imageName.textContent = name;
  ui.sourceMeta.textContent = "";
  ui.sourceImage.addEventListener("load", () => {
    if (ui.sourceImage.src === dataUrl) ui.sourceMeta.textContent = `${ui.sourceImage.naturalWidth} x ${ui.sourceImage.naturalHeight}`;
  }, { once: true });
  clearResult();
  ui.status.textContent = "Ready";
}

async function loadImage(file) {
  const version = ++contentVersion;
  const type = file.type.toLowerCase();
  const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] || "";
  if (extension === ".svg" || extension === ".tif" || extension === ".tiff" ||
      (type && type !== "application/octet-stream" && !contract.images.mime_types.includes(type))) {
    throw new Error("Choose a PNG, JPEG, WebP, BMP, or GIF image");
  }
  let dataUrl;
  if (contract.images.mime_types.includes(type)) {
    dataUrl = await readFileAsDataUrl(file);
    await decodeImage(dataUrl, "Source image", contract);
    rasterDataUrl(dataUrl, "source image", contract);
  } else {
    const url = URL.createObjectURL(file);
    try {
      dataUrl = imageToPngDataUrl(await decodeImage(url, "Source image", contract));
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  if (version === contentVersion && phase !== "running" && phase !== "pausing") setSource(dataUrl, file.name);
}

function sampleImage() {
  const canvas = document.createElement("canvas");
  canvas.width = 220;
  canvas.height = 160;
  const context = canvas.getContext("2d");
  const fill = context.createLinearGradient(0, 0, 220, 160);
  fill.addColorStop(0, "#f1cf6a");
  fill.addColorStop(0.55, "#57935d");
  fill.addColorStop(1, "#9b5c3a");
  context.fillStyle = fill;
  context.fillRect(0, 0, 220, 160);
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
  setSource(canvas.toDataURL("image/png"), "Generated sample");
}

function stopLabel(reason, eventName) {
  return STOP_LABELS[reason] || (eventName === "paused" ? "Paused" : "Complete");
}

function onRunEvent(event, version) {
  if (version !== runVersion) return;
  if (event.event === "start") {
    sessionId = event.session_id || sessionId;
    activeRunId = event.run_id || "";
    if (!runIsPaint && focus.mode !== "paint") {
      focusUpdates.begin({ sessionId, runId: activeRunId, runVersion: version, contentVersion }, focusAtRunStart);
    }
    if (!event.continued) telemetry.reset();
    workingResult = {
      width: event.width, height: event.height, background: event.background,
      shapes: [...(event.shapes || [])], attempts: event.attempts || 0,
      revision: event.revision || 0
    };
    telemetry.replace(workingResult.shapes, workingResult.attempts, event.initial_score ?? telemetry.initialScore);
    telemetry.setState("Running");
    preview.rebuild(event.width, event.height, event.background, workingResult.shapes);
    ui.resultMeta.textContent = `${event.width} x ${event.height}`;
    setControls();
    return;
  }
  if (event.event === "step") {
    if (!workingResult) throw new ApiError("The render stream sent a step before its start", "stream_invalid");
    const shapes = event.shapes || [];
    workingResult.shapes.push(...shapes);
    workingResult.attempts = event.attempts ?? workingResult.attempts;
    workingResult.revision = event.revision ?? workingResult.revision;
    telemetry.append(shapes, workingResult.attempts);
    shapes.forEach((shape) => preview.draw(shape));
    ui.pipeline.textContent = `${event.batch_shape_count ?? workingResult.shapes.length} / ${event.batch_goal ?? "?"} shapes in batch`;
    ui.metrics.textContent = `${workingResult.shapes.length} shapes`;
    if (phase !== "pausing") ui.status.textContent = "Running";
    telemetry.setState(phase === "pausing" ? "Pausing" : "Running", performance.now() - runStartedAt);
    return;
  }
  if (event.event === "complete" || event.event === "paused") {
    acceptSnapshot(event, event.event);
  }
}

function acceptSnapshot(event, eventName) {
  if (!Array.isArray(event.shapes) || !event.width || !event.height || !event.background) {
    throw new ApiError("The server did not provide a complete result snapshot", "snapshot_invalid");
  }
  const alreadyDrawn = workingResult && workingResult.width === event.width &&
    workingResult.height === event.height && workingResult.revision === event.revision &&
    workingResult.shapes.length === event.shapes.length;
  sessionId = event.session_id || sessionId;
  activeRunId = "";
  focusUpdates.end();
  focus.setFeedback("");
  stableResult = {
    width: event.width, height: event.height, background: event.background,
    shapes: event.shapes, attempts: event.attempts || 0,
    revision: event.revision || 0
  };
  workingResult = null;
  loadedPreviewOnlyResult = null;
  sceneVersion += 1;
  exportVersion += 1;
  lastDurationMs = Math.max(0, performance.now() - runStartedAt);
  telemetry.replace(stableResult.shapes, stableResult.attempts, event.initial_score ?? telemetry.initialScore);
  telemetry.addBatch(event.batch_summary);
  const label = stopLabel(event.stop_reason, eventName);
  telemetry.setState(label, lastDurationMs);
  if (!alreadyDrawn) preview.rebuild(event.width, event.height, event.background, event.shapes);
  ui.resultMeta.textContent = `${event.width} x ${event.height}`;
  ui.metrics.textContent = `${event.shapes.length} shapes · export on download`;
  ui.pipeline.textContent = `${stableResult.attempts} attempts`;
  ui.status.textContent = label;
  phase = "ready";
  setControls();
}

function captureConfirmedView() {
  return {
    sceneVersion,
    previewImage: stableResult ? "" : preview.currentPreview(),
    previewSize: stableResult ? null : preview.resultSize && { ...preview.resultSize },
    shapes: [...telemetry.shapes],
    attempts: telemetry.attempts,
    initialScore: telemetry.initialScore,
    batches: [...telemetry.batches],
    elapsed: telemetry.elapsed,
    resultMeta: ui.resultMeta.textContent,
    metrics: ui.metrics.textContent,
    pipeline: ui.pipeline.textContent
  };
}

function restoreConfirmedView(confirmed) {
  if (sceneVersion !== confirmed.sceneVersion) return;
  workingResult = null;
  activeRunId = "";
  focusUpdates.end();
  focus.setFeedback("");
  if (stableResult) {
    preview.rebuild(stableResult.width, stableResult.height, stableResult.background, stableResult.shapes);
  } else if (confirmed.previewImage && confirmed.previewSize) {
    preview.showImage(confirmed.previewImage, confirmed.previewSize.width, confirmed.previewSize.height);
  } else {
    preview.clearResult();
  }
  telemetry.replace(confirmed.shapes, confirmed.attempts, confirmed.initialScore);
  telemetry.batches = confirmed.batches;
  telemetry.setState("Error", confirmed.elapsed);
  ui.resultMeta.textContent = confirmed.resultMeta;
  ui.metrics.textContent = confirmed.metrics;
  ui.pipeline.textContent = confirmed.pipeline;
}

async function recoverSnapshot(version) {
  if (!sessionId) return false;
  ui.status.textContent = "Recovering final result";
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (version !== runVersion) return false;
    try {
      const snapshot = await postJson(`/api/sessions/${encodeURIComponent(sessionId)}/snapshot`, {});
      if (version !== runVersion) return false;
      acceptSnapshot(snapshot, "snapshot");
      ui.status.textContent = snapshot.stop_reason === "error"
        ? "Render failed; recovered the last confirmed result"
        : "Stream interrupted; recovered final result";
      return true;
    } catch (error) {
      if (error.code === "session_busy" || error.code === "renderer_busy") {
        await new Promise((resolve) => setTimeout(resolve, 500));
        continue;
      }
      if (error.code === "unknown_session") sessionId = "";
      return false;
    }
  }
  return false;
}

async function startRun({ paintFocus = null } = {}) {
  if (!sourceDataUrl || phase === "running" || phase === "pausing") return;
  if (phase === "recovery") {
    if (!await recoverSnapshot(runVersion)) {
      phase = sessionId ? "recovery" : "error";
      ui.status.textContent = sessionId
        ? "Could not confirm the final result. Retry recovery or start a New render."
        : "Render session expired. Start a New render.";
      setControls();
    }
    return;
  }
  const options = currentOptions();
  if (paintFocus) {
    options.steps = 1;
    options.focus = { ...paintFocus };
  }
  if (!options.shape_types.length) {
    ui.status.textContent = "Choose at least one shape";
    return;
  }
  const continuing = Boolean(sessionId);
  const initialShapeCount = continuing ? stableResult?.shapes.length || 0 : 0;
  runIsPaint = Boolean(paintFocus);
  focusAtRunStart = options.focus;
  focusUpdates.end();
  activeRunId = "";
  const payload = continuing
    ? { session_id: sessionId, options }
    : { image: sourceDataUrl, options };
  const confirmedView = captureConfirmedView();
  const version = ++runVersion;
  contentVersion += 1;
  phase = "running";
  runStartedAt = performance.now();
  ui.status.textContent = continuing ? "Continuing" : "Running";
  ui.pipeline.textContent = "Iterating";
  telemetry.setState(continuing ? "Continuing" : "Running");
  setControls();
  let sawStart = false;
  let terminal = null;
  try {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      try {
        terminal = await readRunStream(payload, (event) => {
          if (event.event === "start") sawStart = true;
          onRunEvent(event, version);
        });
        break;
      } catch (error) {
        if (continuing && (error.code === "session_busy" || error.code === "renderer_busy") && attempt < 7) {
          await new Promise((resolve) => setTimeout(resolve, 200));
          continue;
        }
        throw error;
      }
    }
    return {
      confirmed: Boolean(terminal),
      added: terminal?.batch_summary?.added ?? Math.max(0, (stableResult?.shapes.length || 0) - initialShapeCount),
      reason: stopLabel(terminal?.stop_reason, terminal?.event),
      cancelled: ["paused", "error"].includes(terminal?.stop_reason) || terminal?.event === "paused"
    };
  } catch (error) {
    if (version !== runVersion) return;
    if (sessionId && sawStart && await recoverSnapshot(version)) {
      return {
        confirmed: true,
        added: Math.max(0, (stableResult?.shapes.length || 0) - initialShapeCount),
        reason: "Stream interrupted; result recovered",
        cancelled: true
      };
    }
    restoreConfirmedView(confirmedView);
    const code = error.code || "request_failed";
    if (code === "unknown_session") sessionId = "";
    if (sessionId && sawStart) {
      phase = "recovery";
      ui.status.textContent = `${error.message}. Recover the result before continuing.`;
    } else {
      phase = stableResult ? "ready" : "error";
      ui.status.textContent = error.message || "Render failed";
    }
    telemetry.setState("Error", lastDurationMs);
    return { confirmed: false, message: ui.status.textContent };
  } finally {
    if (version === runVersion) {
      runIsPaint = false;
      setControls();
    }
  }
}

async function pauseRun() {
  if (phase !== "running" || !sessionId || !activeRunId) return;
  const version = runVersion;
  preview.stopPaintGesture();
  paintQueue.clear();
  phase = "pausing";
  ui.status.textContent = "Pausing after the current attempt";
  telemetry.setState("Pausing", performance.now() - runStartedAt);
  setControls();
  try {
    await postJson(`/api/sessions/${encodeURIComponent(sessionId)}/pause`, { run_id: activeRunId });
  } catch (error) {
    if (version !== runVersion || phase !== "pausing") return;
    phase = "running";
    ui.status.textContent = `Could not request pause: ${error.message}`;
    telemetry.setState("Running", performance.now() - runStartedAt);
    setControls();
  }
}

function createProject() {
  const result = stableResult;
  const previewOnly = result ? null : loadedPreviewOnlyResult;
  return {
    format: contract.project.format,
    version: contract.project.version,
    saved_at: new Date().toISOString(),
    source: {
      name: sourceName || "Source image", data_url: sourceDataUrl,
      width: ui.sourceImage.naturalWidth || null, height: ui.sourceImage.naturalHeight || null
    },
    options: currentOptions(),
    result: {
      preview_data_url: previewOnly?.preview_data_url || (result ? preview.currentPreview() || null : null),
      width: result?.width || previewOnly?.width || null,
      height: result?.height || previewOnly?.height || null,
      render_width: result?.width || previewOnly?.render_width || null,
      render_height: result?.height || previewOnly?.render_height || null,
      background: result?.background || previewOnly?.background || null,
      shapes: result?.shapes || previewOnly?.shapes || []
    },
    telemetry: {
      attempts: result?.attempts ?? (previewOnly ? telemetry.attempts : 0),
      duration_ms: Math.round(lastDurationMs),
      initial_score: telemetry.initialScore, batches: telemetry.batches
    }
  };
}

function triggerDownload(content, type, filename) {
  const url = content.startsWith("data:") ? content : URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  if (!content.startsWith("data:")) setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function saveProject() {
  if (!sourceDataUrl) return;
  const content = JSON.stringify(createProject(), null, 2);
  if (new Blob([content]).size > contract.project.max_bytes) {
    throw new Error("Project is larger than 64 MB; lower the preview resolution before saving");
  }
  const base = sourceName.replace(/\.[^.]+$/, "").replace(/[^a-z0-9_-]+/gi, "-").replace(/^-+|-+$/g, "") || "geometrize";
  triggerDownload(content, "application/json", `${base}.geometrize-project.json`);
  ui.status.textContent = "Project saved";
}

function applyProject(project) {
  setSource(project.source.data_url, project.source.name);
  applyOptions(project.options);
  const result = project.result;
  const width = result.render_width || result.width;
  const height = result.render_height || result.height;
  if (width && height && result.background) {
    stableResult = {
      width, height, background: result.background,
      shapes: result.shapes, attempts: project.telemetry.attempts, revision: 0
    };
    preview.rebuild(width, height, result.background, result.shapes);
    ui.resultMeta.textContent = `${width} x ${height}`;
    sceneVersion += 1;
  } else {
    loadedPreviewOnlyResult = result;
    if (width && height && result.preview_data_url) {
      preview.showImage(result.preview_data_url, width, height);
      ui.resultMeta.textContent = `${width} x ${height}`;
    }
  }
  telemetry.replace(result.shapes, project.telemetry.attempts, project.telemetry.initial_score);
  telemetry.batches = project.telemetry.batches;
  lastDurationMs = project.telemetry.duration_ms;
  telemetry.setState("Loaded project", lastDurationMs);
  ui.metrics.textContent = `${result.shapes.length} shapes`;
  ui.pipeline.textContent = `${project.telemetry.attempts} attempts`;
  ui.status.textContent = "Project loaded — Run starts a new render";
  phase = "loaded";
  setControls();
}

async function downloadResult(type) {
  if (!stableResult) return;
  if (type === "json") {
    triggerDownload(JSON.stringify(stableResult.shapes, null, 2), "application/json", "geometrize-shapes.json");
    return;
  }
  const size = commitExportSize(ui.exportSizeNumber.value);
  const version = exportVersion;
  const scene = sceneVersion;
  const cacheKey = `${scene}:${size}`;
  try {
    let artifact = exportCache.get(cacheKey);
    if (!artifact) {
      ui.status.textContent = `Preparing ${size}px export`;
      let request = pendingExports.get(cacheKey);
      if (!request) {
        request = postJson("/api/export", {
          result: {
            width: stableResult.width, height: stableResult.height,
            background: stableResult.background, shapes: stableResult.shapes,
            attempts: stableResult.attempts
          },
          export_size: size
        });
        pendingExports.set(cacheKey, request);
        request.finally(() => pendingExports.delete(cacheKey)).catch(() => {});
      }
      artifact = await request;
      if (version !== exportVersion || scene !== sceneVersion) {
        return;
      }
      exportCache.set(cacheKey, artifact);
      while (exportCache.size > 3) exportCache.delete(exportCache.keys().next().value);
    }
    lastArtifact = { size, artifact };
    if (type === "png") triggerDownload(artifact.preview, "image/png", "geometrize.png");
    else triggerDownload(artifact.svg, "image/svg+xml", "geometrize.svg");
    ui.status.textContent = `Exported ${artifact.export_width} x ${artifact.export_height}`;
  } catch (error) {
    if (version !== exportVersion || scene !== sceneVersion) return;
    if (lastArtifact?.size === size) {
      const old = lastArtifact.artifact;
      if (type === "png") triggerDownload(old.preview, "image/png", "geometrize-previous.png");
      else triggerDownload(old.svg, "image/svg+xml", "geometrize-previous.svg");
      ui.status.textContent = `Export failed; downloaded the previous completed result: ${error.message}`;
    } else {
      ui.status.textContent = `Export failed: ${error.message}`;
    }
  }
}

function bindEvents() {
  for (const [slider, number, output] of [
    [ui.steps, ui.stepsNumber, ui.stepsOut],
    [ui.maxSize, ui.maxSizeNumber, ui.maxSizeOut]
  ]) {
    const update = (value) => {
      syncPair(slider, number, output, value);
      ui.preset.value = "custom";
    };
    slider.addEventListener("input", () => update(slider.value));
    number.addEventListener("change", () => update(number.value));
  }
  ui.exportSize.addEventListener("input", () => commitExportSize(ui.exportSize.value));
  ui.exportSizeNumber.addEventListener("change", () => commitExportSize(ui.exportSizeNumber.value));
  ui.preset.addEventListener("change", () => {
    const preset = contract.presets[ui.preset.value];
    if (!preset) return;
    const values = preset.options;
    if (values.steps != null) syncPair(ui.steps, ui.stepsNumber, ui.stepsOut, values.steps);
    if (values.max_size != null && !sessionId) syncPair(ui.maxSize, ui.maxSizeNumber, ui.maxSizeOut, values.max_size);
    if (values.shape_count != null) ui.shapeCount.value = String(values.shape_count);
    if (values.mutations != null) ui.mutations.value = String(values.mutations);
    ui.status.textContent = `${preset.label} settings selected for the next ${sessionId ? "batch" : "render"}`;
  });
  for (const input of [ui.alpha, ui.seed, ui.shapeCount, ui.mutations, ui.maxThreads,
    ui.stagnationLimit, ...document.querySelectorAll("input[name='shape']")]) {
    input.addEventListener("change", () => { ui.preset.value = "custom"; });
  }
  ui.imageInput.addEventListener("change", async () => {
    const file = ui.imageInput.files[0];
    if (!file) return;
    try { await loadImage(file); }
    catch (error) {
      ui.imageInput.value = "";
      ui.status.textContent = `Could not load image: ${error.message}`;
    }
  });
  ui.sample.addEventListener("click", sampleImage);
  ui.openProject.addEventListener("click", () => ui.projectInput.click());
  ui.projectInput.addEventListener("change", async () => {
    const file = ui.projectInput.files[0];
    ui.projectInput.value = "";
    if (!file) return;
    const version = ++contentVersion;
    try {
      const project = await openProjectFile(file, contract);
      if (version === contentVersion && phase !== "running" && phase !== "pausing") applyProject(project);
    } catch (error) {
      if (version === contentVersion) ui.status.textContent = `Could not open project: ${error.message}`;
    }
  });
  ui.saveProject.addEventListener("click", () => {
    try { saveProject(); }
    catch (error) { ui.status.textContent = error.message; }
  });
  ui.form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (phase === "running" || phase === "pausing") return;
    if (focus.mode === "paint") focus.setMode("focus");
    paintQueue.clear();
    void startRun();
  });
  ui.pause.addEventListener("click", () => { void pauseRun(); });
  ui.restart.addEventListener("click", () => {
    if (phase === "running" || phase === "pausing") return;
    runVersion += 1;
    clearResult();
    ui.status.textContent = "New render ready";
  });
  for (const [type, link] of Object.entries(ui.downloads)) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      if (link.getAttribute("aria-disabled") !== "true") void downloadResult(type);
    });
  }
  byId("zoom-out").addEventListener("click", () => preview.setZoom(preview.zoom / 1.25));
  byId("zoom-in").addEventListener("click", () => preview.setZoom(preview.zoom * 1.25));
  byId("zoom-fit").addEventListener("click", () => preview.fit());
}

async function init() {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error(`Server returned ${response.status}`);
    contract = await response.json();
    renderShapeControls();
    for (const [key, input] of [["steps", ui.stepsNumber], ["alpha", ui.alpha],
      ["seed", ui.seed], ["shape_count", ui.shapeCount], ["mutations", ui.mutations],
      ["max_threads", ui.maxThreads], ["stagnation_limit", ui.stagnationLimit]]) {
      input.min = String(contract.limits[key].min);
      input.max = String(contract.limits[key].max);
    }
    ui.maxThreads.max = String(Math.min(contract.limits.max_threads.max, contract.resources?.worker_budget || contract.limits.max_threads.max));
    preview = new Preview({
      sourceStage: byId("source-stage"), resultStage: byId("result-stage"),
      sourceImage: ui.sourceImage, resultImage: ui.resultImage,
      resultCanvas: ui.resultCanvas, zoomOutput: byId("zoom-value"),
      focusOverlay: byId("focus-overlay"),
      onFocusMove: (point) => focus.move(point),
      onFocusDisable: () => focus.setMode("pan"),
      onPaint: (point) => {
        focus.move(point);
        paintQueue.enqueue(focus.value);
      },
      onPaintHoldStart: (point) => {
        focus.move(point);
        paintQueue.startHold(focus.value);
      },
      onPaintHoldMove: (point) => {
        if (point) focus.move(point);
        paintQueue.moveHold(point ? focus.value : null);
      },
      onPaintHoldStop: () => paintQueue.stopHold()
    });
    preview.clearResult();
    focusUpdates = new FocusUpdates({
      post: postJson,
      onState: (state, message) => focus.setFeedback(state === "pending" ? "Updating focus…" :
        state === "error" ? `Focus update failed: ${message}. Next batch uses the selected focus.` :
          "Focus applies from the next attempt.")
    });
    paintQueue = new PaintQueue({
      canHold: () => preview.paintGestureActive(),
      runStroke: async (paintFocus) => {
        const outcome = await startRun({ paintFocus });
        if (!outcome?.confirmed) throw new Error(outcome?.message || "Could not complete the stroke");
        return outcome;
      },
      onState: (state, { pending, running, holding, detail, capacity }) => {
        if (["error", "stopped", "hold_stopped", "released"].includes(state)) preview.stopPaintGesture();
        const queue = pending ? ` · ${pending} queued` : "";
        byId("paint-status").textContent = state === "full" ? `Queue full (${capacity}) · wait for a stroke` :
          state === "error" || state === "stopped" ? `Paint stopped: ${detail}` : state === "rejected" ? `No shape added: ${detail}${queue}` :
            state === "hold_stopped" ? `Hold stopped: no shape added · ${detail}` :
              state === "waiting" ? "Move over the result to keep painting" :
                state === "released" ? (running ? "Released · finishing current shape" : "Hold released") :
                  holding ? "Painting while held · one shape at a time" :
                    state === "accepted" ? `Added one shape${queue}` : state === "running" || state === "queued" ?
                      `${running ? "Painting one shape" : "Paint ready"}${queue}` : "";
      }
    });
    focus = new FocusControls({
      toggle: byId("focus-toggle"), paint: byId("paint-toggle"), clear: byId("focus-clear"),
      behavior: byId("paint-behavior"),
      radius: byId("focus-radius"), radiusNumber: byId("focus-radius-number"),
      strength: byId("focus-strength"), strengthNumber: byId("focus-strength-number"),
      position: byId("focus-position"), status: byId("focus-status")
    }, contract.focus, (value, mode, paintBehavior) => {
      if (mode === "paint") focusUpdates.end();
      focusUpdates.update(value);
      if (mode !== "paint" && !runIsPaint && activeRunId && !focusUpdates.context &&
          (phase === "running" || phase === "pausing")) {
        focusUpdates.begin({ sessionId, runId: activeRunId, runVersion, contentVersion }, undefined);
      }
      preview.setFocus(value, mode, paintBehavior);
      paintQueue.setBehavior(paintBehavior);
      paintQueue.setEnabled(mode === "paint");
      paintQueue.updateHoldSettings(value);
      ui.preset.value = "custom";
    });
    telemetry = new Telemetry({
      state: byId("telemetry-state"), acceptance: byId("telemetry-acceptance"),
      improvement: byId("telemetry-improvement"), duration: byId("telemetry-duration"),
      score: byId("telemetry-score"), baseline: byId("telemetry-baseline"),
      total: byId("telemetry-total"), impact: byId("telemetry-impact"),
      scoreGraph: byId("score-graph"), impactGraph: byId("impact-graph"),
      mix: byId("primitive-mix"), history: byId("batch-history"),
      historySummary: byId("batch-history-summary"), historyWindow: byId("batch-history-window"),
      historyOlder: byId("batch-history-older"), historyNewer: byId("batch-history-newer"),
      historyLatest: byId("batch-history-latest")
    }, Object.fromEntries(contract.shapes.map((shape) => [shape.type, shape.label])));
    bindEvents();
    setControls();
  } catch (error) {
    ui.status.textContent = `Could not load app settings: ${error.message}`;
  }
  fetch("/health").then((response) => response.json()).then((data) => {
    ui.nativeState.textContent = data.native ? "Core ready" : "Core unavailable";
  }).catch(() => { ui.nativeState.textContent = "Server unavailable"; });
}

void init();
