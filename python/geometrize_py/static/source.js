"use strict";

import { colorHex, parseHexColors } from "./palette.js";

export function copyRgb(value) { return value == null ? null : [...value]; }
export function validateRgb(value, label = "Color") {
  if (value == null) return null;
  if (!Array.isArray(value) || value.length !== 3 ||
      [...value].some(channel => !Number.isInteger(channel) || channel < 0 || channel > 255)) {
    throw new Error(`${label} must be an RGB triplet of integers from 0 to 255 or null`);
  }
  return [...value];
}
export function validateSource(value, config = {}, label = "Source options") {
  if (value == null) return { frame: 0, matte: null };
  if (typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(key => !["frame", "matte"].includes(key))) {
    throw new Error(`${label} must contain only frame and matte`);
  }
  const frame = value.frame === undefined ? 0 : value.frame, max = config.limits?.frame?.max ?? 255;
  if (!Number.isSafeInteger(frame) || frame < 0 || frame > max) throw new Error(`${label} frame must be an integer from 0 to ${max}`);
  return { frame, matte: validateRgb(value.matte, `${label} matte`) };
}
export function copySource(value) { return { frame: value?.frame ?? 0, matte: copyRgb(value?.matte) }; }
export const sourceKey = value => `${value?.frame ?? 0}:${value?.matte?.join(",") ?? "none"}`;
export function sourceLabel(value) {
  return `Frame ${value?.frame ?? 0} · ${value?.matte ? `${colorHex(value.matte)} matte` : "Keep transparency"}`;
}
export function canonicalImageUrl(value, mime) {
  const match = typeof value === "string" && value.match(/^data:[^,]*;base64,([a-z0-9+/=\r\n]+)$/i);
  if (!match) throw new Error("Source must be an embedded raster image");
  return `data:${mime};base64,${match[1]}`;
}

// This owns preparation requests independently of render attempts. Call begin
// before reading an upload/project so both early failures and replies are owned.
export class SourcePreparation {
  constructor(contract, post) { this.contract = contract; this.post = post; this.serial = 0; }
  invalidate() { this.serial += 1; }
  begin() { return { id: ++this.serial }; }
  owns(token) { return token.id === this.serial; }
  async prepare(image, source, token) {
    const policy = validateSource(source, this.contract.source);
    const reply = await this.post("/api/source/prepare", { image, source: policy });
    if (!this.owns(token)) return null;
    const normalized = validateSource(reply.source, this.contract.source, "Prepared source");
    const images = this.contract.images, edge = this.contract.source?.preview_size ?? 1024;
    const count = this.contract.source?.max_frames ?? 256;
    if (sourceKey(normalized) !== sourceKey(policy) ||
        !Number.isInteger(reply.width) || !Number.isInteger(reply.height) || reply.width < 1 || reply.height < 1 ||
        reply.width > images.max_dimension || reply.height > images.max_dimension || reply.width * reply.height > images.max_pixels ||
        !images.mime_types.includes(reply.mime_type) || !Number.isInteger(reply.frame_count) ||
        reply.frame_count < 1 || reply.frame_count > count || normalized.frame >= reply.frame_count ||
        typeof reply.frames_truncated !== "boolean" || typeof reply.default_image !== "boolean" ||
        (reply.frames_truncated && reply.frame_count !== count) ||
        !(reply.frame_duration_ms === null || (Number.isFinite(reply.frame_duration_ms) && reply.frame_duration_ms >= 0)) ||
        !Number.isInteger(reply.preview_width) || !Number.isInteger(reply.preview_height) ||
        reply.preview_width < 1 || reply.preview_height < 1 || reply.preview_width > edge || reply.preview_height > edge ||
        typeof reply.preview_data_url !== "string" || !/^data:image\/png;base64,[a-z0-9+/=]+$/i.test(reply.preview_data_url) ||
        reply.preview_data_url.length > this.contract.project.max_bytes) {
      throw new Error("The server did not confirm the prepared source");
    }
    return { ...reply, source: normalized, image: canonicalImageUrl(image, reply.mime_type) };
  }
}

const rgbMode = (value, empty) => value == null ? empty :
  value.every(channel => channel === 255) ? "white" : value.every(channel => channel === 0) ? "black" : "custom";
const readColor = (mode, text, empty) => mode === empty ? null : mode === "white" ? [255,255,255] :
  mode === "black" ? [0,0,0] : parseHexColors(text, 1)[0];

export class SourceControls {
  constructor(elements, config, apply) {
    this.el = elements; this.config = config || {}; this.apply = apply;
    this.locked = true; this.metadata = null;
    for (const input of [elements.frame, elements.matte, elements.matteColor, elements.background, elements.backgroundColor]) {
      input.addEventListener("input", () => {
        if (this.locked) return;
        this.feedback = "Target changes are drafts. Apply to new render keeps this experiment.";
        this.render();
      });
    }
    elements.apply.addEventListener("click", () => { if (!this.locked) void apply(); });
    this.replace({ source: null, background: null });
  }
  get value() { return { source: copySource(this.applied.source), background: copyRgb(this.applied.background) }; }
  draft() {
    const source = validateSource({ frame: this.el.frame.valueAsNumber,
      matte: readColor(this.el.matte.value, this.el.matteColor.value, "none") }, this.config);
    if (this.metadata && source.frame >= this.metadata.frame_count) {
      throw new Error(`Choose a frame index from 0 to ${this.metadata.frame_count - 1}`);
    }
    return { source,
      background: validateRgb(readColor(this.el.background.value, this.el.backgroundColor.value, "average"), "Canvas background") };
  }
  replace(options, metadata = this.metadata) {
    this.applied = { source: validateSource(options.source, this.config), background: validateRgb(options.background) };
    this.metadata = metadata;
    this.el.frame.value = String(this.applied.source.frame);
    this.el.matte.value = rgbMode(this.applied.source.matte, "none");
    this.el.matteColor.value = colorHex(this.applied.source.matte || [255,255,255]);
    this.el.background.value = rgbMode(this.applied.background, "average");
    this.el.backgroundColor.value = colorHex(this.applied.background || [255,255,255]);
    this.feedback = "";
    this.render();
  }
  setAvailability(available, locked) { this.available = available; this.locked = locked; this.render(); }
  render() {
    const el = this.el, metadata = this.metadata;
    el.summary.textContent = `Source · ${sourceLabel(this.applied.source)}`;
    el.frame.max = String(Math.max(0, Math.min(this.config.limits?.frame?.max ?? 255, (metadata?.frame_count ?? 1) - 1)));
    el.metadata.textContent = metadata ? `${metadata.width} × ${metadata.height} · ${metadata.frames_truncated ? `first ${metadata.frame_count} frames` : `${metadata.frame_count} frame${metadata.frame_count === 1 ? "" : "s"}`}${metadata.default_image ? " · APNG default image is index 0" : ""}` : "";
    el.frameLabel.textContent = metadata?.default_image && el.frame.value === "0" ? "Frame index · default image" : "Frame index (starts at 0)";
    el.matteColor.hidden = el.matte.value !== "custom";
    el.backgroundColor.hidden = el.background.value !== "custom";
    for (const input of [el.frame, el.matte, el.matteColor, el.background, el.backgroundColor, el.apply]) {
      input.disabled = this.locked || !this.available;
    }
    el.frame.disabled ||= !metadata || metadata.frame_count < 2;
    el.status.textContent = this.feedback;
  }
}
