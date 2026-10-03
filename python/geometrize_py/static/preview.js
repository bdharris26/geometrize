"use strict";

const LIVE_CANVAS_MAX = 1200;

function rgba(color) {
  const value = Array.isArray(color)
    ? { r: color[0], g: color[1], b: color[2], a: color[3] }
    : color || {};
  return `rgba(${Number(value.r) || 0}, ${Number(value.g) || 0}, ${Number(value.b) || 0}, ${value.a === undefined ? 1 : Number(value.a) / 255})`;
}

function drawShape(context, shape) {
  const data = shape.data || {};
  context.save();
  context.fillStyle = rgba(shape.color);
  context.strokeStyle = rgba(shape.color);
  context.lineWidth = 1;
  if (shape.type === "circle") {
    context.beginPath();
    context.arc(data.x, data.y, data.r, 0, Math.PI * 2);
    context.fill();
  } else if (shape.type === "ellipse" || shape.type === "rotated_ellipse") {
    context.beginPath();
    context.ellipse(data.x, data.y, data.rx, data.ry,
      shape.type === "rotated_ellipse" ? (Number(data.angle) || 0) * Math.PI / 180 : 0,
      0, Math.PI * 2);
    context.fill();
  } else if (shape.type === "rectangle") {
    context.fillRect(Math.min(data.x1, data.x2), Math.min(data.y1, data.y2),
      Math.abs(data.x2 - data.x1), Math.abs(data.y2 - data.y1));
  } else if (shape.type === "rotated_rectangle") {
    const x = Math.min(data.x1, data.x2);
    const y = Math.min(data.y1, data.y2);
    const width = Math.abs(data.x2 - data.x1);
    const height = Math.abs(data.y2 - data.y1);
    context.translate(x + width / 2, y + height / 2);
    context.rotate((Number(data.angle) || 0) * Math.PI / 180);
    context.fillRect(-width / 2, -height / 2, width, height);
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
  } else if (shape.type === "polyline" && data.points?.length) {
    context.beginPath();
    context.moveTo(data.points[0][0], data.points[0][1]);
    data.points.slice(1).forEach(([x, y]) => context.lineTo(x, y));
    context.stroke();
  }
  context.restore();
}

export class Preview {
  constructor({ sourceStage, resultStage, sourceImage, resultImage, resultCanvas, zoomOutput }) {
    this.sourceStage = sourceStage;
    this.resultStage = resultStage;
    this.sourceImage = sourceImage;
    this.resultImage = resultImage;
    this.resultCanvas = resultCanvas;
    this.zoomOutput = zoomOutput;
    this.sourceSize = null;
    this.resultSize = null;
    this.background = null;
    this.canvasScale = 1;
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.sourceImage.addEventListener("load", () => {
      this.sourceSize = { width: sourceImage.naturalWidth, height: sourceImage.naturalHeight };
      this.layout();
    });
    new ResizeObserver(() => this.layout()).observe(sourceStage);
    new ResizeObserver(() => this.layout()).observe(resultStage);
    for (const stage of [sourceStage, resultStage]) {
      stage.addEventListener("wheel", (event) => {
        event.preventDefault();
        this.setZoom(this.zoom * (event.deltaY < 0 ? 1.15 : 1 / 1.15));
      }, { passive: false });
      stage.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        stage.setPointerCapture(event.pointerId);
        stage.dataset.dragX = String(event.clientX);
        stage.dataset.dragY = String(event.clientY);
      });
      stage.addEventListener("pointermove", (event) => {
        if (!stage.hasPointerCapture(event.pointerId)) return;
        const dx = event.clientX - Number(stage.dataset.dragX);
        const dy = event.clientY - Number(stage.dataset.dragY);
        this.pan.x += dx / Math.max(1, stage.clientWidth);
        this.pan.y += dy / Math.max(1, stage.clientHeight);
        stage.dataset.dragX = String(event.clientX);
        stage.dataset.dragY = String(event.clientY);
        this.layout();
      });
    }
    this.layout();
  }

  setZoom(value) {
    this.zoom = Math.max(1, Math.min(8, Math.round(value * 100) / 100));
    this.layout();
  }

  fit() {
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.layout();
  }

  setSource(dataUrl) {
    this.sourceSize = null;
    this.sourceImage.src = dataUrl;
    this.fit();
  }

  layout() {
    this.zoomOutput.textContent = `${Math.round(this.zoom * 100)}%`;
    this.place(this.sourceStage, this.sourceImage, this.sourceSize);
    const visible = this.resultCanvas.hidden ? this.resultImage : this.resultCanvas;
    this.place(this.resultStage, visible, this.resultSize);
  }

  place(stage, media, size) {
    if (!size?.width || !size?.height) return;
    const scale = Math.min((stage.clientWidth - 24) / size.width, (stage.clientHeight - 24) / size.height);
    media.style.width = `${Math.max(1, size.width * scale)}px`;
    media.style.height = `${Math.max(1, size.height * scale)}px`;
    media.style.transform = `translate(-50%, -50%) translate(${this.pan.x * stage.clientWidth}px, ${this.pan.y * stage.clientHeight}px) scale(${this.zoom})`;
  }

  clearResult() {
    this.resultSize = null;
    this.background = null;
    this.resultImage.removeAttribute("src");
    this.resultImage.hidden = true;
    this.resultCanvas.hidden = true;
    this.resultCanvas.width = 1;
    this.resultCanvas.height = 1;
    this.layout();
  }

  beginResult(width, height, background) {
    this.resultSize = { width, height };
    this.background = background;
    this.canvasScale = Math.min(1, LIVE_CANVAS_MAX / Math.max(width, height));
    this.resultCanvas.width = Math.max(1, Math.round(width * this.canvasScale));
    this.resultCanvas.height = Math.max(1, Math.round(height * this.canvasScale));
    this.resultCanvas.hidden = false;
    this.resultImage.hidden = true;
    const context = this.context();
    context.fillStyle = rgba(background);
    context.fillRect(0, 0, width, height);
    this.layout();
  }

  draw(shape) {
    drawShape(this.context(), shape);
  }

  rebuild(width, height, background, shapes) {
    this.beginResult(width, height, background);
    shapes.forEach((shape) => this.draw(shape));
  }

  showImage(dataUrl, width, height) {
    this.resultSize = { width, height };
    this.resultImage.src = dataUrl;
    this.resultImage.hidden = false;
    this.resultCanvas.hidden = true;
    this.layout();
  }

  currentPreview() {
    if (!this.resultCanvas.hidden) return this.resultCanvas.toDataURL("image/png");
    const image = this.resultImage.getAttribute("src") || "";
    return image.startsWith("data:image/") ? image : "";
  }

  context() {
    const context = this.resultCanvas.getContext("2d");
    context.setTransform(this.canvasScale, 0, 0, this.canvasScale, 0, 0);
    return context;
  }
}
