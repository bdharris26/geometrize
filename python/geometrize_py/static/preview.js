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
  constructor({ sourceStage, resultStage, sourceImage, resultImage, resultCanvas, zoomOutput,
    focusOverlay = null, onFocusMove = () => {}, onFocusDisable = () => {}, onPaint = () => {} }) {
    this.sourceStage = sourceStage;
    this.resultStage = resultStage;
    this.sourceImage = sourceImage;
    this.resultImage = resultImage;
    this.resultCanvas = resultCanvas;
    this.zoomOutput = zoomOutput;
    this.focusOverlay = focusOverlay;
    this.onFocusMove = onFocusMove;
    this.onFocusDisable = onFocusDisable;
    this.onPaint = onPaint;
    this.focus = null;
    this.focusMode = "pan";
    this.blankResult = false;
    this.sourceSize = null;
    this.resultSize = null;
    this.canvasScale = 1;
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.sourceImage.addEventListener("load", () => {
      this.sourceSize = { width: sourceImage.naturalWidth, height: sourceImage.naturalHeight };
      this.ensurePaintTarget();
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
        if (stage === resultStage && this.focus && !event.shiftKey) {
          const point = this.resultPoint(event.clientX, event.clientY);
          if (!point) return;
          event.preventDefault();
          stage.focus({ preventScroll: true });
          stage.setPointerCapture(event.pointerId);
          stage.dataset.gesture = this.focusMode;
          if (this.focusMode === "paint") this.onPaint(point);
          else this.onFocusMove(point);
          return;
        }
        event.preventDefault();
        stage.setPointerCapture(event.pointerId);
        stage.dataset.gesture = "pan";
        stage.dataset.dragX = String(event.clientX);
        stage.dataset.dragY = String(event.clientY);
      });
      stage.addEventListener("pointermove", (event) => {
        if (!stage.hasPointerCapture(event.pointerId)) return;
        if (stage.dataset.gesture !== "pan") {
          if (stage.dataset.gesture === "focus" && this.focusMode === "focus") {
            const point = this.resultPoint(event.clientX, event.clientY);
            if (point) this.onFocusMove(point);
          }
          return;
        }
        const dx = event.clientX - Number(stage.dataset.dragX);
        const dy = event.clientY - Number(stage.dataset.dragY);
        this.pan.x += dx / Math.max(1, stage.clientWidth);
        this.pan.y += dy / Math.max(1, stage.clientHeight);
        stage.dataset.dragX = String(event.clientX);
        stage.dataset.dragY = String(event.clientY);
        this.layout();
      });
      const release = (event) => {
        if (stage.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId);
        delete stage.dataset.gesture;
      };
      stage.addEventListener("pointerup", release);
      stage.addEventListener("pointercancel", release);
      stage.addEventListener("lostpointercapture", () => { delete stage.dataset.gesture; });
    }
    resultStage.addEventListener("keydown", (event) => {
      if (!this.focus || !this.resultSize) return;
      if (event.key === "Escape") {
        event.preventDefault();
        this.onFocusDisable();
        return;
      }
      if (this.focusMode !== "focus") return;
      const direction = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
      if (!direction && event.key !== "Home") return;
      event.preventDefault();
      const step = event.shiftKey ? 0.05 : 0.01;
      this.onFocusMove(event.key === "Home" ? { x: 0.5, y: 0.5 } : {
        x: Math.max(0, Math.min(1, this.focus.x + direction[0] * step)),
        y: Math.max(0, Math.min(1, this.focus.y + direction[1] * step))
      });
    });
    this.layout();
  }

  setFocus(value, mode = value ? "focus" : "pan") {
    this.focus = value ? { ...value } : null;
    this.focusMode = mode;
    this.resultStage.dataset.focusMode = mode;
    this.resultStage.setAttribute("aria-label", mode === "paint" ? "Live result. Click to add one shape; Shift drag to pan." :
      mode === "focus" ? "Live result. Click or drag to move focus; arrow keys adjust it; Shift drag to pan." :
        "Live result. Drag to pan.");
    if (mode !== "paint" && this.blankResult) this.clearResult();
    this.ensurePaintTarget();
    this.layout();
  }

  ensurePaintTarget() {
    if (this.focusMode !== "paint" || this.resultSize || !this.sourceSize) return;
    this.beginResult(this.sourceSize.width, this.sourceSize.height, [18, 20, 22, 255]);
    this.blankResult = true;
  }

  resultPoint(clientX, clientY) {
    if (!this.resultSize) return null;
    const media = this.resultCanvas.hidden ? this.resultImage : this.resultCanvas;
    if (media.hidden) return null;
    const box = media.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0 || clientX < box.left || clientX > box.right ||
        clientY < box.top || clientY > box.bottom) return null;
    return { x: (clientX - box.left) / box.width, y: (clientY - box.top) / box.height };
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
    this.layoutFocus(visible);
  }

  layoutFocus(visible) {
    if (!this.focusOverlay) return;
    const shown = this.focus && this.resultSize && !visible.hidden;
    this.focusOverlay.toggleAttribute("hidden", !shown);
    if (!shown) return;
    const { width, height } = this.resultSize;
    this.focusOverlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
    this.place(this.resultStage, this.focusOverlay, this.resultSize);
    const cx = this.focus.x * width;
    const cy = this.focus.y * height;
    const radius = this.focus.radius * Math.min(width, height);
    for (const ring of this.focusOverlay.querySelectorAll(".focus-ring")) {
      ring.setAttribute("cx", cx);
      ring.setAttribute("cy", cy);
      ring.setAttribute("r", radius);
    }
    const pixelScale = visible.getBoundingClientRect().width / width || 1;
    const center = this.focusOverlay.querySelector(".focus-center");
    center.setAttribute("cx", cx);
    center.setAttribute("cy", cy);
    center.setAttribute("r", 3 / pixelScale);
  }

  place(stage, media, size) {
    if (!size?.width || !size?.height) return;
    const scale = Math.min((stage.clientWidth - 24) / size.width, (stage.clientHeight - 24) / size.height);
    media.style.width = `${Math.max(1, size.width * scale)}px`;
    media.style.height = `${Math.max(1, size.height * scale)}px`;
    media.style.transform = `translate(-50%, -50%) translate(${this.pan.x * stage.clientWidth}px, ${this.pan.y * stage.clientHeight}px) scale(${this.zoom})`;
  }

  clearResult() {
    this.blankResult = false;
    this.resultSize = null;
    this.resultImage.removeAttribute("src");
    this.resultImage.hidden = true;
    this.resultCanvas.hidden = true;
    this.resultCanvas.width = 1;
    this.resultCanvas.height = 1;
    this.ensurePaintTarget();
    this.layout();
  }

  beginResult(width, height, background) {
    this.blankResult = false;
    this.resultSize = { width, height };
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
    this.blankResult = false;
    this.resultSize = { width, height };
    this.resultImage.src = dataUrl;
    this.resultImage.hidden = false;
    this.resultCanvas.hidden = true;
    this.layout();
  }

  currentPreview() {
    if (this.blankResult) return "";
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
