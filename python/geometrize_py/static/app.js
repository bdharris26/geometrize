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
const projectInput = byId("project-input");
const loadProjectButton = byId("load-project-button");
const saveProjectButton = byId("save-project-button");
const statusText = byId("status");
const nativeState = byId("native-state");
const metrics = byId("metrics");
const steps = byId("steps");
const stepsOut = byId("steps-out");
const maxSize = byId("max-size");
const maxSizeOut = byId("max-size-out");
const maxSizeNumber = byId("max-size-number");
const exportSize = byId("export-size");
const exportSizeOut = byId("export-size-out");
const exportSizeNumber = byId("export-size-number");
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
const exportSizeBounds = {
  min: Number(exportSize.min),
  max: Number(exportSize.max),
  step: Number(exportSize.step)
};
const downloads = {
  png: document.querySelector("#download-png"),
  svg: document.querySelector("#download-svg"),
  json: document.querySelector("#download-json")
};

const LIVE_CANVAS_MAX = 1200;
const PROJECT_FORMAT = "geometrize-project";
const PROJECT_VERSION = 1;
const PROJECT_FILE_MAX_BYTES = 64 * 1024 * 1024;
const PROJECT_SHAPE_MAX = 100000;
const SOURCE_MAX_DIMENSION = 16384;
const SOURCE_MAX_PIXELS = 8192 * 8192;
const RASTER_IMAGE_TYPES = new Set([
  "image/bmp",
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/tiff",
  "image/webp"
]);
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
const SHAPE_DATA_FIELDS = {
  circle: ["x", "y", "r"],
  ellipse: ["x", "y", "rx", "ry"],
  line: ["x1", "y1", "x2", "y2"],
  quadratic_bezier: ["x1", "y1", "cx", "cy", "x2", "y2"],
  rectangle: ["x1", "y1", "x2", "y2"],
  rotated_ellipse: ["x", "y", "rx", "ry", "angle"],
  rotated_rectangle: ["x1", "y1", "x2", "y2", "angle"],
  triangle: ["x1", "y1", "x2", "y2", "x3", "y3"]
};
const SHAPE_RADIUS_FIELDS = {
  circle: ["r"],
  ellipse: ["rx", "ry"],
  rotated_ellipse: ["rx", "ry"]
};
const FORM_SHAPE_TYPES = new Set(
  [...document.querySelectorAll("input[name='shape']")].map((item) => item.value)
);

let sourceDataUrl = "";
let activeSessionId = "";
let activeUrls = [];
let runStartedAt = 0;
let lastDurationMs = 0;
let runningController = null;
let currentBatch = null;
let runStats = createRunStats();
let renderBackground = null;
let sourceLoadVersion = 0;
let projectLoadVersion = 0;
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
exportSize.addEventListener("input", () => syncExportSize(exportSize.value));
exportSizeNumber.addEventListener("input", () => syncExportSize(exportSizeNumber.value));

imageInput.addEventListener("change", async () => {
  const file = imageInput.files[0];
  if (!file) {
    return;
  }
  const loadVersion = ++sourceLoadVersion;
  const fileType = file.type.toLowerCase();
  if (
    (fileType && fileType !== "application/octet-stream" && !RASTER_IMAGE_TYPES.has(fileType)) ||
    file.name.toLowerCase().endsWith(".svg")
  ) {
    imageInput.value = "";
    statusText.textContent = "Choose a PNG, JPEG, WebP, BMP, GIF, or TIFF image";
    return;
  }
  try {
    let dataUrl;
    if (RASTER_IMAGE_TYPES.has(fileType)) {
      dataUrl = await readFileAsDataUrl(file);
      await decodeProjectImage(dataUrl, "source image");
      dataUrl = validateRasterDataUrl(dataUrl, "source image");
    } else {
      const objectUrl = URL.createObjectURL(file);
      try {
        const image = await decodeProjectImage(objectUrl, "source image");
        dataUrl = imageToPngDataUrl(image);
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
    }
    if (loadVersion !== sourceLoadVersion || runningController) {
      return;
    }
    setSourceImage(dataUrl, file.name);
  } catch (error) {
    if (loadVersion === sourceLoadVersion && !runningController) {
      imageInput.value = "";
      const message = error instanceof Error ? error.message : "Could not decode image";
      statusText.textContent = `Could not load image: ${message}`;
    }
  }
});

loadProjectButton.addEventListener("click", () => {
  projectInput.click();
});

projectInput.addEventListener("change", async () => {
  const file = projectInput.files[0];
  projectInput.value = "";
  if (!file) {
    return;
  }
  const loadVersion = ++projectLoadVersion;
  try {
    const project = await openProjectFile(file);
    if (loadVersion !== projectLoadVersion || runningController) {
      return;
    }
    applyProject(project);
  } catch (error) {
    if (loadVersion === projectLoadVersion && !runningController) {
      const message = error instanceof Error ? error.message : "Invalid project file";
      statusText.textContent = `Could not open project: ${message}`;
    }
  }
});

saveProjectButton.addEventListener("click", () => {
  try {
    saveProject();
  } catch (error) {
    const message = error instanceof Error ? error.message : "Could not create project file";
    statusText.textContent = message;
  }
});

sampleButton.addEventListener("click", () => {
  sourceLoadVersion += 1;
  projectLoadVersion += 1;
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
      lastDurationMs = performance.now() - runStartedAt;
      statusText.textContent = "Paused";
      telemetryState.textContent = "Paused";
      telemetryDuration.textContent = formatDuration(lastDurationMs);
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
  lastDurationMs = 0;
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

function saveProject() {
  if (!sourceDataUrl) {
    throw new Error("Choose an image before saving a project");
  }

  const previewDataUrl = currentResultPreview();
  const project = {
    format: PROJECT_FORMAT,
    version: PROJECT_VERSION,
    saved_at: new Date().toISOString(),
    source: {
      name: imageName.textContent || "Source image",
      data_url: sourceDataUrl,
      width: sourcePreview.naturalWidth || null,
      height: sourcePreview.naturalHeight || null
    },
    options: currentOptions(selectedShapeTypes()),
    result: {
      preview_data_url: previewDataUrl || null,
      width: resultPreview.naturalWidth || renderSpace.width || null,
      height: resultPreview.naturalHeight || renderSpace.height || null,
      render_width: renderSpace.width || null,
      render_height: renderSpace.height || null,
      background: renderBackground,
      shapes: runStats.shapes
    },
    telemetry: {
      attempts: runStats.attempts,
      duration_ms: Math.round(lastDurationMs),
      batches: runStats.batches
    }
  };
  const content = JSON.stringify(project, null, 2);
  const projectBlob = new Blob([content], { type: "application/json" });
  if (projectBlob.size > PROJECT_FILE_MAX_BYTES) {
    throw new Error("Project is larger than 64 MB; lower the export resolution before saving");
  }
  const url = URL.createObjectURL(projectBlob);
  const link = document.createElement("a");
  const baseName = project.source.name
    .replace(/\.[^.]+$/, "")
    .replace(/[^a-z0-9_-]+/gi, "-")
    .replace(/^-+|-+$/g, "") || "geometrize";
  link.href = url;
  link.download = `${baseName}.geometrize-project.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  statusText.textContent = "Project saved";
}

function currentResultPreview() {
  const preview = resultPreview.getAttribute("src") || "";
  if (preview.startsWith("data:image/")) {
    return preview;
  }
  if (!resultCanvas.hidden && renderSpace.width > 0 && renderSpace.height > 0) {
    return resultCanvas.toDataURL("image/png");
  }
  return "";
}

async function openProjectFile(file) {
  if (file.size > PROJECT_FILE_MAX_BYTES) {
    throw new Error("Project files must be 64 MB or smaller");
  }

  let raw;
  try {
    raw = JSON.parse(await file.text());
  } catch (error) {
    if (error instanceof SyntaxError) {
      throw new Error("The file is not valid JSON");
    }
    throw error;
  }
  const project = validateProject(raw);
  const images = [decodeProjectImage(project.source.data_url, "source image")];
  if (project.result.preview_data_url) {
    images.push(decodeProjectImage(project.result.preview_data_url, "result preview"));
  }
  await Promise.all(images);
  return project;
}

function applyProject(project) {
  setSourceImage(project.source.data_url, project.source.name);
  applyProjectOptions(project.options);

  runStats = createRunStats(project.telemetry.batches);
  runStats.attempts = project.telemetry.attempts;
  project.result.shapes.forEach((shape) => appendShape(shape, false));
  lastDurationMs = project.telemetry.duration_ms;
  renderBackground = project.result.background;
  renderSpace = {
    width: project.result.render_width,
    height: project.result.render_height,
    scale: 1
  };

  if (project.result.preview_data_url) {
    resultCanvas.hidden = true;
    resultPreview.removeAttribute("aria-hidden");
    resultPreview.src = project.result.preview_data_url;
  } else if (
    project.result.shapes.length > 0 &&
    project.result.background &&
    project.result.render_width > 0 &&
    project.result.render_height > 0
  ) {
    startLiveCanvas(
      project.result.render_width,
      project.result.render_height,
      project.result.background
    );
    project.result.shapes.forEach((shape) => drawShape(shape));
  }

  const width = project.result.width || project.result.render_width;
  const height = project.result.height || project.result.render_height;
  resultMeta.textContent = width && height ? `${width} x ${height}` : "";
  renderRestoredTelemetry();
  clearDownloads();
  if (project.result.preview_data_url) {
    setDownload(downloads.png, project.result.preview_data_url, "geometrize.png");
  }
  if (project.result.shapes.length > 0) {
    setDownload(
      downloads.json,
      makeObjectUrl(JSON.stringify(project.result.shapes, null, 2), "application/json"),
      "geometrize-shapes.json"
    );
  }

  activeSessionId = "";
  currentBatch = null;
  statusText.textContent = "Project loaded — Run starts a new render";
  pipeline.textContent = `${runStats.attempts} attempts`;
  metrics.textContent = width && height
    ? `${runStats.shapes.length} shapes, ${width} x ${height}`
    : `${runStats.shapes.length} shapes`;
  setBusy(false);
}

function applyProjectOptions(options) {
  steps.value = String(options.steps);
  stepsOut.value = String(options.steps);
  alpha.value = String(options.alpha);
  seed.value = String(options.seed);
  shapeCount.value = String(options.shape_count);
  mutations.value = String(options.mutations);
  syncMaxSize(options.max_size);
  syncExportSize(options.export_size);
  document.querySelectorAll("input[name='shape']").forEach((item) => {
    item.checked = options.shape_types.includes(item.value);
  });
}

function renderRestoredTelemetry() {
  const latestScore = runStats.scores.at(-1);
  const latestImpact = runStats.improvements.at(-1);
  telemetryState.textContent = "Loaded project";
  telemetryAcceptance.textContent = `${runStats.shapes.length} accepted / ${runStats.attempts} attempts`;
  telemetryImprovement.textContent = formatImpact(latestImpact);
  telemetryDuration.textContent = formatDuration(lastDurationMs);
  telemetryScore.textContent = formatScore(latestScore);
  telemetryTotal.textContent = String(runStats.shapes.length);
  telemetryImpact.textContent = formatImpactValue(latestImpact);
  renderScoreGraph(runStats.scores);
  renderImpactGraph(runStats.improvements);
  renderPrimitiveMix();
  renderBatchHistory();
}

function validateProject(raw) {
  if (Array.isArray(raw)) {
    throw new Error("This is a Shapes JSON export, not a Geometrize project");
  }
  const project = requireObject(raw, "project");
  if (project.format !== PROJECT_FORMAT) {
    throw new Error("This JSON file is not a Geometrize project");
  }
  if (project.version !== PROJECT_VERSION) {
    throw new Error(`Project version ${String(project.version)} is not supported`);
  }

  const source = requireObject(project.source, "source");
  const result = requireObject(project.result, "result");
  const telemetry = requireObject(project.telemetry, "telemetry");
  return {
    source: {
      name: optionalText(source.name, "Source image", 512),
      data_url: validateRasterDataUrl(source.data_url, "source image"),
      width: optionalDimension(source.width, "source width"),
      height: optionalDimension(source.height, "source height")
    },
    options: validateProjectOptions(project.options),
    result: {
      preview_data_url: result.preview_data_url === null || result.preview_data_url === undefined
        ? ""
        : validateRasterDataUrl(result.preview_data_url, "result preview"),
      width: optionalDimension(result.width, "result width"),
      height: optionalDimension(result.height, "result height"),
      render_width: optionalDimension(result.render_width, "render width"),
      render_height: optionalDimension(result.render_height, "render height"),
      background: result.background === null || result.background === undefined
        ? null
        : validateColor(result.background, "result background"),
      shapes: validateShapes(result.shapes)
    },
    telemetry: {
      attempts: integerBetween(telemetry.attempts, 0, Number.MAX_SAFE_INTEGER, "telemetry attempts"),
      duration_ms: integerBetween(telemetry.duration_ms, 0, Number.MAX_SAFE_INTEGER, "telemetry duration"),
      batches: validateBatches(telemetry.batches)
    }
  };
}

function validateProjectOptions(raw) {
  const options = requireObject(raw, "options");
  if (!Array.isArray(options.shape_types)) {
    throw new Error("Options shape types must be an array");
  }
  const shapeTypes = [...new Set(options.shape_types.map((type) => {
    if (typeof type !== "string" || !FORM_SHAPE_TYPES.has(type)) {
      throw new Error(`Unsupported option shape type: ${String(type)}`);
    }
    return type;
  }))];
  const maxSize = steppedInteger(options.max_size, 64, 2048, 64, "optimizer size");
  const exportSize = options.export_size === undefined
    ? maxSize
    : steppedInteger(options.export_size, 64, 4096, 64, "export size");
  return {
    steps: integerBetween(options.steps, 1, 4096, "shapes to add"),
    shape_types: shapeTypes,
    alpha: integerBetween(options.alpha, 1, 255, "alpha"),
    seed: integerBetween(options.seed, 0, 2147483647, "seed"),
    shape_count: integerBetween(options.shape_count, 1, 512, "candidate count"),
    mutations: integerBetween(options.mutations, 1, 2048, "mutation count"),
    max_size: maxSize,
    export_size: exportSize
  };
}

function validateShapes(raw) {
  if (!Array.isArray(raw)) {
    throw new Error("Project result shapes must be an array");
  }
  if (raw.length > PROJECT_SHAPE_MAX) {
    throw new Error(`A project can contain at most ${PROJECT_SHAPE_MAX} shapes`);
  }
  return raw.map((shape, index) => validateShape(shape, index));
}

function validateShape(raw, index) {
  const shape = requireObject(raw, `shape ${index + 1}`);
  if (
    typeof shape.type !== "string" ||
    !Object.prototype.hasOwnProperty.call(SHAPE_LABELS, shape.type)
  ) {
    throw new Error(`Shape ${index + 1} has an unsupported type`);
  }
  const rawData = requireObject(shape.data, `shape ${index + 1} data`);
  const data = {};
  if (shape.type === "polyline") {
    if (!Array.isArray(rawData.points) || rawData.points.length > 10000) {
      throw new Error(`Shape ${index + 1} has invalid polyline points`);
    }
    data.points = rawData.points.map((point) => {
      if (!Array.isArray(point) || point.length !== 2) {
        throw new Error(`Shape ${index + 1} has an invalid polyline point`);
      }
      return [
        finiteShapeNumber(point[0], `shape ${index + 1} point x`),
        finiteShapeNumber(point[1], `shape ${index + 1} point y`)
      ];
    });
  } else {
    for (const field of SHAPE_DATA_FIELDS[shape.type]) {
      data[field] = finiteShapeNumber(rawData[field], `shape ${index + 1} ${field}`);
    }
  }
  for (const field of SHAPE_RADIUS_FIELDS[shape.type] || []) {
    if (data[field] < 0) {
      throw new Error(`Shape ${index + 1} ${field} cannot be negative`);
    }
  }
  const normalized = {
    type: shape.type,
    color: validateColor(shape.color, `shape ${index + 1} color`),
    data
  };
  if (shape.type_id !== undefined) {
    normalized.type_id = integerBetween(shape.type_id, 0, 2147483647, `shape ${index + 1} type id`);
  }
  if (shape.score !== undefined) {
    normalized.score = finiteShapeNumber(shape.score, `shape ${index + 1} score`);
  }
  return normalized;
}

function validateBatches(raw) {
  if (!Array.isArray(raw) || raw.length > 10000) {
    throw new Error("Project telemetry batches must be an array of at most 10000 items");
  }
  return raw.map((rawBatch, position) => {
    const batch = requireObject(rawBatch, `batch ${position + 1}`);
    if (!Array.isArray(batch.shapeTypes) || batch.shapeTypes.length === 0) {
      throw new Error(`Batch ${position + 1} must include shape types`);
    }
    const shapeTypes = batch.shapeTypes.map((type) => {
      if (
        typeof type !== "string" ||
        !Object.prototype.hasOwnProperty.call(SHAPE_LABELS, type)
      ) {
        throw new Error(`Batch ${position + 1} has an unsupported shape type`);
      }
      return type;
    });
    return {
      index: integerBetween(batch.index, 1, Number.MAX_SAFE_INTEGER, `batch ${position + 1} index`),
      target: integerBetween(batch.target, 1, 4096, `batch ${position + 1} target`),
      shapeTypes,
      candidates: integerBetween(batch.candidates, 1, 512, `batch ${position + 1} candidates`),
      mutations: integerBetween(batch.mutations, 1, 2048, `batch ${position + 1} mutations`),
      alpha: integerBetween(batch.alpha, 1, 255, `batch ${position + 1} alpha`),
      added: integerBetween(batch.added, 0, PROJECT_SHAPE_MAX, `batch ${position + 1} added shapes`),
      attempts: integerBetween(batch.attempts, 0, Number.MAX_SAFE_INTEGER, `batch ${position + 1} attempts`),
      state: optionalText(batch.state, "Complete", 40)
    };
  });
}

function validateColor(raw, label) {
  const color = Array.isArray(raw)
    ? { r: raw[0], g: raw[1], b: raw[2], a: raw[3] }
    : requireObject(raw, label);
  return {
    r: integerBetween(color.r, 0, 255, `${label} red channel`),
    g: integerBetween(color.g, 0, 255, `${label} green channel`),
    b: integerBetween(color.b, 0, 255, `${label} blue channel`),
    a: integerBetween(color.a, 0, 255, `${label} alpha channel`)
  };
}

function validateRasterDataUrl(value, label) {
  if (typeof value !== "string" || value.length > PROJECT_FILE_MAX_BYTES) {
    throw new Error(`Project ${label} is not a valid embedded image`);
  }
  const match = value.match(/^data:(image\/[a-z0-9.+-]+)(?:;[^,]*)?;base64,([a-z0-9+/=\r\n]+)$/i);
  if (!match || match[1].toLowerCase() === "image/svg+xml" || match[2].length === 0) {
    throw new Error(`Project ${label} must be an embedded raster image`);
  }
  return value;
}

function decodeProjectImage(dataUrl, label) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.addEventListener("load", () => {
      if (
        image.naturalWidth <= 0 ||
        image.naturalHeight <= 0 ||
        image.naturalWidth > SOURCE_MAX_DIMENSION ||
        image.naturalHeight > SOURCE_MAX_DIMENSION ||
        image.naturalWidth * image.naturalHeight > SOURCE_MAX_PIXELS
      ) {
        reject(new Error(`Project ${label} dimensions are too large`));
        return;
      }
      resolve(image);
    }, { once: true });
    image.addEventListener("error", () => reject(new Error(`Project ${label} could not be decoded`)), { once: true });
    image.src = dataUrl;
  });
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      if (typeof reader.result !== "string") {
        reject(new Error("Could not read image"));
        return;
      }
      resolve(reader.result);
    }, { once: true });
    reader.addEventListener("error", () => reject(new Error("Could not read image")), { once: true });
    reader.readAsDataURL(file);
  });
}

function imageToPngDataUrl(image) {
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const context = canvas.getContext("2d");
  context.drawImage(image, 0, 0);
  return canvas.toDataURL("image/png");
}

function requireObject(value, label) {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null)
  ) {
    throw new Error(`Project ${label} must be an object`);
  }
  return value;
}

function optionalText(value, fallback, maxLength) {
  if (value === undefined || value === null || value === "") {
    return fallback;
  }
  if (typeof value !== "string" || value.length > maxLength) {
    throw new Error(`Project text values must be ${maxLength} characters or fewer`);
  }
  return value;
}

function optionalDimension(value, label) {
  if (value === undefined || value === null) {
    return 0;
  }
  return integerBetween(value, 1, 32768, label);
}

function steppedInteger(value, min, max, step, label) {
  const normalized = integerBetween(value, min, max, label);
  if (normalized % step !== 0) {
    throw new Error(`Project ${label} must use increments of ${step}`);
  }
  return normalized;
}

function integerBetween(value, min, max, label) {
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    throw new Error(`Project ${label} must be an integer from ${min} to ${max}`);
  }
  return value;
}

function finiteShapeNumber(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) > 1000000000) {
    throw new Error(`Project ${label} must be a finite number`);
  }
  return value;
}

function selectedShapeTypes() {
  return [...document.querySelectorAll("input[name='shape']:checked")].map((item) => item.value);
}

function currentOptions(shapeTypes) {
  return {
    steps: normalizedIntegerInput(steps, 128, 1, 4096),
    shape_types: shapeTypes,
    alpha: normalizedIntegerInput(alpha, 128, 1, 255),
    seed: normalizedIntegerInput(seed, 9001, 0, 2147483647),
    shape_count: normalizedIntegerInput(shapeCount, 64, 1, 512),
    mutations: normalizedIntegerInput(mutations, 128, 1, 2048),
    max_size: Number(maxSize.value),
    export_size: Number(exportSize.value)
  };
}

function normalizedIntegerInput(input, defaultValue, min, max) {
  const raw = input.valueAsNumber;
  const value = Number.isFinite(raw) ? Math.trunc(raw) : defaultValue;
  const normalized = Math.max(min, Math.min(max, value));
  input.value = String(normalized);
  return normalized;
}

function prepareRun(isContinuation, options) {
  sourceLoadVersion += 1;
  projectLoadVersion += 1;
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
  lastDurationMs = 0;
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
  lastDurationMs = performance.now() - runStartedAt;
  telemetryDuration.textContent = formatDuration(lastDurationMs);
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
  lastDurationMs = elapsed;
  activeSessionId = data.session_id || activeSessionId;
  runStats.attempts = data.attempts;
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
  renderBackground = background;
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
  renderBackground = null;
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
  const plotted = series.slice(-512);
  const path = pathForSeries(plotted, 360, 120);
  const last = plotted.at(-1);
  scoreGraph.innerHTML = `
    <path class="graph-grid" d="M0 24H360M0 60H360M0 96H360" />
    <path class="score-path" d="${path}" />
    ${last === undefined ? "" : `<circle class="graph-dot" cx="348" cy="${pointY(plotted, last, 120)}" r="3" />`}
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
  lastDurationMs = 0;
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
  const hasActiveSession = Boolean(activeSessionId);
  runButton.disabled = busy;
  pauseButton.disabled = !busy;
  loadProjectButton.disabled = busy;
  projectInput.disabled = busy;
  imageInput.disabled = busy;
  sampleButton.disabled = busy;
  saveProjectButton.disabled = busy || !sourceDataUrl;
  maxSize.disabled = busy || hasActiveSession;
  maxSizeNumber.disabled = busy || hasActiveSession;
  runButton.textContent = busy ? "Running" : (activeSessionId ? "Continue" : "Run");
}

function syncMaxSize(value) {
  const snapped = snapRangeValue(value, maxSizeBounds);
  maxSize.value = String(snapped);
  maxSizeNumber.value = String(snapped);
  maxSizeOut.value = String(snapped);
}

function syncExportSize(value) {
  const snapped = snapRangeValue(value, exportSizeBounds);
  exportSize.value = String(snapped);
  exportSizeNumber.value = String(snapped);
  exportSizeOut.value = String(snapped);
}

function snapRangeValue(value, bounds) {
  const numericValue = Math.max(bounds.min, Math.min(bounds.max, Number(value) || bounds.min));
  return Math.round(numericValue / bounds.step) * bounds.step;
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
