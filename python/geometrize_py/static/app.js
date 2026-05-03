const form = document.querySelector("#run-form");
const imageInput = document.querySelector("#image-input");
const fileLabel = document.querySelector("#file-label");
const sourcePreview = document.querySelector("#source-preview");
const resultPreview = document.querySelector("#result-preview");
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

let sourceDataUrl = "";
let activeUrls = [];

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
    resultPreview.removeAttribute("src");
    resultMeta.textContent = "";
    clearDownloads();
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
  fill.addColorStop(0, "#f3d46b");
  fill.addColorStop(0.55, "#4aa386");
  fill.addColorStop(1, "#2f6eb3");
  context.fillStyle = fill;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "rgba(255,255,255,0.82)";
  context.beginPath();
  context.arc(62, 54, 34, 0, Math.PI * 2);
  context.fill();
  context.fillStyle = "rgba(31,37,40,0.74)";
  context.beginPath();
  context.moveTo(72, 138);
  context.lineTo(138, 36);
  context.lineTo(190, 138);
  context.closePath();
  context.fill();
  sourceDataUrl = canvas.toDataURL("image/png");
  sourcePreview.src = sourceDataUrl;
  resultPreview.removeAttribute("src");
  resultMeta.textContent = "";
  fileLabel.textContent = "Generated sample";
  clearDownloads();
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
  statusText.textContent = "Running";
  metrics.textContent = "";
  pipeline.textContent = "Iterating";

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
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Render failed");
    }
    resultPreview.src = data.preview;
    statusText.textContent = "Complete";
    metrics.textContent = `${data.shape_count} shapes, ${data.width} x ${data.height}`;
    pipeline.textContent = `${data.attempts} steps`;
    resultMeta.textContent = `${data.width} x ${data.height}`;
    setDownload(downloads.png, data.preview, "geometrize.png");
    setDownload(downloads.svg, makeObjectUrl(data.svg, "image/svg+xml"), "geometrize.svg");
    setDownload(downloads.json, makeObjectUrl(JSON.stringify(data.shapes, null, 2), "application/json"), "geometrize.json");
  } catch (error) {
    statusText.textContent = error.message;
  } finally {
    setBusy(false);
  }
});

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
