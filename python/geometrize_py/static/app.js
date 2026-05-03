const form = document.querySelector("#run-form");
const imageInput = document.querySelector("#image-input");
const fileLabel = document.querySelector("#file-label");
const sourcePreview = document.querySelector("#source-preview");
const resultPreview = document.querySelector("#result-preview");
const runButton = document.querySelector("#run-button");
const statusText = document.querySelector("#status");
const nativeState = document.querySelector("#native-state");
const metrics = document.querySelector("#metrics");
const steps = document.querySelector("#steps");
const stepsOut = document.querySelector("#steps-out");
const maxSize = document.querySelector("#max-size");
const maxSizeOut = document.querySelector("#max-size-out");
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
    nativeState.textContent = data.native ? "Native core ready" : "Native core unavailable";
  })
  .catch(() => {
    nativeState.textContent = "Server unavailable";
  });

steps.addEventListener("input", () => {
  stepsOut.value = steps.value;
});

maxSize.addEventListener("input", () => {
  maxSizeOut.value = maxSize.value;
});

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
    clearDownloads();
  });
  reader.readAsDataURL(file);
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
