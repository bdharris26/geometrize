"use strict";

export function copyPalette(value) {
  return value == null ? null : { colors: value.colors.map(color => [...color]), strength: value.strength };
}

export function validatePalette(value, config = {}, label = "Palette") {
  if (value == null) return null;
  if (typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object or null`);
  if (Object.keys(value).some(key => !["colors", "strength"].includes(key))) throw new Error(`${label} contains an unknown field`);
  const max = config.max_colors ?? 32;
  if (!Array.isArray(value.colors) || !value.colors.length || value.colors.length > max) {
    throw new Error(`${label} must contain 1 to ${max} colors`);
  }
  const unique = new Map();
  value.colors.forEach(color => {
    if (!Array.isArray(color) || color.length !== 3 || color.some(channel =>
      !Number.isInteger(channel) || channel < 0 || channel > 255)) {
      throw new Error(`${label} colors must be RGB triplets of integers from 0 to 255`);
    }
    if (!unique.has(color.join(","))) unique.set(color.join(","), [...color]);
  });
  const strength = value.strength === undefined ? 1 : value.strength;
  if (typeof strength !== "number" || !Number.isFinite(strength) || strength < 0 || strength > 1) {
    throw new Error(`${label} strength must be a number from 0 to 1`);
  }
  return { colors: [...unique.values()], strength };
}

export const colorHex = color => "#" + color.map(channel => channel.toString(16).padStart(2, "0")).join("").toUpperCase();
export const paletteMode = value => value == null ? "off" : value.strength === 1 ? "exact" : "soft";
export const paletteLabel = value => value == null ? "Palette Off" :
  `Palette ${value.strength === 1 ? "Exact" : `Soft ${Math.round(value.strength * 100)}%`} · ${value.colors.length} colors`;

export function parseHexColors(text, max = 32) {
  const tokens = text.trim().split(/[\s,]+/).filter(Boolean);
  if (!tokens.length || tokens.length > max) throw new Error(`Enter 1 to ${max} hex colors`);
  const colors = new Map();
  for (const token of tokens) {
    let hex = token.replace(/^#/, "");
    if (!/^(?:[a-f0-9]{3}|[a-f0-9]{6})$/i.test(hex)) throw new Error(`Invalid hex color: ${token}. Use #RGB or #RRGGBB.`);
    if (hex.length === 3) hex = [...hex].map(channel => channel + channel).join("");
    const color = [0, 2, 4].map(index => parseInt(hex.slice(index, index + 2), 16));
    colors.set(colorHex(color), color);
  }
  return [...colors.values()];
}

// Draft text never becomes RunOptions until Use colors or extraction succeeds.
// Extraction owns its context; fitting the same source does not invalidate it.
export class PaletteControls {
  constructor(elements, config, { post, context, onChange = () => {} }) {
    this.el = elements;
    this.config = config || {};
    this.post = post;
    this.context = context;
    this.onChange = onChange;
    this.maxColors = this.config.max_colors ?? 32;
    this.defaultCount = this.config.defaults?.max_colors ?? 8;
    this.defaultSoft = this.config.defaults?.soft_strength ?? 0.75;
    this.available = false;
    this.locked = false;
    this.requestId = 0;
    this.revision = 0;
    this.extraction = null;
    this.swatchKey = null;
    elements.extractCount.min = "1";
    elements.extractCount.max = String(this.maxColors);
    elements.extractCount.value = String(this.defaultCount);
    elements.mode.addEventListener("change", () => this.setMode(elements.mode.value));
    elements.text.addEventListener("input", () => {
      this.invalidate();
      this.revision += 1;
      elements.text.setAttribute("aria-invalid", "false");
      this.feedback = "Draft colors are not applied. Use colors to update the palette.";
      this.render();
    });
    elements.apply.addEventListener("click", () => this.apply());
    elements.extract.addEventListener("click", () => { void this.extract(); });
    elements.extractCount.addEventListener("change", () => {
      this.invalidate();
      const count = elements.extractCount.valueAsNumber;
      elements.extractCount.value = String(Number.isFinite(count) ? Math.max(1, Math.min(this.maxColors, Math.trunc(count))) : this.defaultCount);
      this.render();
    });
    elements.strength.addEventListener("input", () => this.setStrength(elements.strength.value));
    elements.strengthNumber.addEventListener("input", () => this.setStrength(elements.strengthNumber.value));
    elements.strengthNumber.addEventListener("change", () => this.setStrength(elements.strengthNumber.value));
    this.reset();
  }

  get value() {
    return this.mode === "off" ? null : { colors: this.colors.map(color => [...color]),
      strength: this.mode === "exact" ? 1 : this.softStrength };
  }

  invalidate() {
    this.requestId += 1;
    this.extraction = null;
    if (this.feedback === "Extracting source colors…") this.feedback = "";
    if (this.colors) this.render();
  }

  reset() { this.replace(null); }

  replace(value) {
    const palette = validatePalette(value, this.config);
    this.invalidate();
    this.revision += 1;
    this.mode = paletteMode(palette);
    this.colors = palette?.colors || [];
    this.softStrength = this.mode === "soft" ? palette.strength : this.defaultSoft;
    this.feedback = "";
    this.el.text.value = this.colors.map(colorHex).join(", ");
    this.el.text.setAttribute("aria-invalid", "false");
    this.render({ syncNumber: true });
  }

  changed() {
    this.invalidate();
    this.revision += 1;
    this.render();
    this.onChange(this.value);
  }

  setMode(mode) {
    if (this.locked) return;
    if (mode !== "off" && !this.colors.length) {
      this.feedback = "Use colors or Extract source before enabling a palette.";
      this.changed();
      return;
    }
    this.mode = mode;
    this.feedback = "";
    this.changed();
  }

  setStrength(percent) {
    if (this.locked) return;
    const raw = percent === "" ? this.defaultSoft * 100 : Number(percent);
    const value = Number.isFinite(raw) ? Math.max(0, Math.min(100, raw)) / 100 : this.defaultSoft;
    if (value === 1) this.mode = "exact";
    else { this.mode = "soft"; this.softStrength = value; }
    this.feedback = "";
    this.changed();
  }

  commitColors(colors, feedback) {
    this.colors = colors.map(color => [...color]);
    if (this.mode === "off") this.mode = "exact";
    this.el.text.value = this.colors.map(colorHex).join(", ");
    this.el.text.setAttribute("aria-invalid", "false");
    this.feedback = feedback;
    this.changed();
  }

  apply() {
    if (this.locked) return;
    try {
      const colors = parseHexColors(this.el.text.value, this.maxColors);
      this.commitColors(colors, `Using ${colors.length} colors for the next batch or stroke.`);
    } catch (error) {
      this.el.text.setAttribute("aria-invalid", "true");
      this.feedback = error.message;
      this.render();
    }
  }

  owns(token) {
    const context = this.context();
    return this.extraction === token && this.requestId === token.id && this.revision === token.revision &&
      context.image === token.image && context.branchId === token.branchId &&
      JSON.stringify(context.source ?? null) === token.sourceKey && !this.locked;
  }

  async extract() {
    if (!this.available || this.locked || this.extraction) return;
    const context = this.context();
    const raw = this.el.extractCount.valueAsNumber;
    const count = Number.isFinite(raw) ? Math.max(1, Math.min(this.maxColors, Math.trunc(raw))) : this.defaultCount;
    this.el.extractCount.value = String(count);
    const source = context.source ? { frame: context.source.frame, matte: context.source.matte ? [...context.source.matte] : null } : null;
    const token = { id: ++this.requestId, revision: this.revision, image: context.image, branchId: context.branchId,
      source, sourceKey: JSON.stringify(source) };
    this.extraction = token;
    this.feedback = "Extracting source colors…";
    this.render();
    try {
      const reply = await this.post("/api/palette", { image: token.image, max_colors: count, ...(source ? { source } : {}) });
      if (!this.owns(token)) return;
      const palette = validatePalette({ colors: reply.colors }, this.config, "Extracted palette");
      const sampleSize = this.config.sample_size ?? 256;
      if (reply.requested_max_colors !== count || palette.colors.length > count ||
          !Number.isInteger(reply.sample_width) || reply.sample_width < 1 ||
          reply.sample_width > sampleSize || !Number.isInteger(reply.sample_height) ||
          reply.sample_height < 1 || reply.sample_height > sampleSize) {
        throw new Error("The server did not confirm the extracted palette");
      }
      this.commitColors(palette.colors, `Extracted ${palette.colors.length} colors from the source; applies to the next batch or stroke.`);
    } catch (error) {
      if (this.owns(token)) this.feedback = `Could not extract palette: ${error.message}`;
    } finally {
      if (this.extraction === token) this.extraction = null;
      this.render();
    }
  }

  setAvailability(available, locked) {
    this.available = available;
    this.locked = locked;
    if (locked) this.invalidate();
    this.render();
  }

  render({ syncNumber = false } = {}) {
    const el = this.el;
    el.mode.value = this.mode;
    el.summary.textContent = paletteLabel(this.value);
    el.status.textContent = this.feedback;
    el.soft.hidden = this.mode !== "soft";
    const percent = this.mode === "exact" ? "100" : String(Math.round(this.softStrength * 1000) / 10);
    el.strength.value = percent;
    if (syncNumber || document.activeElement !== el.strengthNumber) el.strengthNumber.value = percent;
    for (const input of [el.mode, el.text, el.apply, el.strength, el.strengthNumber, el.extractCount]) {
      input.disabled = this.locked || !this.available;
    }
    el.extract.disabled = this.locked || !this.available || Boolean(this.extraction);
    const key = this.colors.map(colorHex).join(",");
    if (key !== this.swatchKey) {
      const swatches = this.colors.map(color => {
        const item = document.createElement("li"), chip = document.createElement("span"), label = document.createElement("span");
        chip.className = "palette-swatch";
        chip.style.backgroundColor = colorHex(color);
        chip.setAttribute("aria-hidden", "true");
        label.textContent = colorHex(color);
        item.replaceChildren(chip, label);
        return item;
      });
      el.swatches.replaceChildren(...swatches);
      this.swatchKey = key;
    }
  }
}
