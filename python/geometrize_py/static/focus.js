"use strict";

export function validateFocus(value, definition, label = "Focus") {
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object or null`);
  if (Object.keys(value).some(key => !["x", "y", "radius", "strength"].includes(key))) {
    throw new Error(`${label} contains an unknown field`);
  }
  const result = {};
  for (const key of ["x", "y", "radius", "strength"]) {
    const bounds = definition.limits[key];
    const field = key in value ? value[key] : definition.defaults[key];
    if (typeof field !== "number" || !Number.isFinite(field) || field < bounds.min || field > bounds.max) {
      throw new Error(`${label} ${key} must be a number from ${bounds.min} to ${bounds.max}`);
    }
    result[key] = field;
  }
  return result;
}

const copy = (value) => value ? { ...value } : null;
const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);

// One request at a time, including across run changes. Pending edits collapse to
// the latest position; old acknowledgements never replace the user's local edit.
export class FocusUpdates {
  constructor({ post, onState = () => {} }) {
    this.post = post;
    this.onState = onState;
    this.desired = null;
    this.context = null;
    this.pending = null;
    this.inFlight = false;
    this.revision = 0;
  }

  begin(context, initialFocus) {
    this.end();
    if (!context.runId || !context.sessionId) return;
    this.context = { ...context };
    if (!same(this.desired, initialFocus)) this.schedule();
    else this.onState("applied");
  }

  end() {
    this.context = null;
    this.pending = null;
  }

  update(value) {
    this.desired = copy(value);
    this.revision += 1;
    if (this.context) this.schedule();
  }

  schedule() {
    this.pending = { context: this.context, focus: copy(this.desired), revision: this.revision };
    this.onState("pending");
    void this.drain();
  }

  async drain() {
    if (this.inFlight) return;
    this.inFlight = true;
    try {
      while (this.pending) {
        const request = this.pending;
        this.pending = null;
        if (request.context !== this.context) continue;
        try {
          const reply = await this.post(`/api/sessions/${encodeURIComponent(request.context.sessionId)}/focus`, {
            run_id: request.context.runId, focus: request.focus
          });
          if (request.context !== this.context || request.revision !== this.revision) continue;
          if (reply.applies !== "next_attempt") throw new Error("The server did not confirm the focus update");
          this.onState("applied");
        } catch (error) {
          if (request.context === this.context && request.revision === this.revision) {
            this.onState("error", error.message || "Could not update focus");
          }
        }
      }
    } finally {
      this.inFlight = false;
    }
  }
}

// Clicks keep their captured targets. A hold keeps only its latest target and
// starts another stroke when fitting finishes, so no timer can build a backlog.
// Stopping discards future work; the current one-shape stroke finishes.
export class PaintQueue {
  constructor({ runStroke, onState = () => {}, canHold = () => true, capacity = 12 }) {
    this.runStroke = runStroke;
    this.onState = onState;
    this.canHold = canHold;
    this.capacity = capacity;
    this.enabled = false;
    this.busy = false;
    this.running = false;
    this.pending = [];
    this.generation = 0;
    this.behavior = "click";
    this.held = false;
    this.holdTarget = null;
    this.releaseGeneration = null;
  }

  setEnabled(value) {
    if (this.enabled === value) return;
    this.enabled = value;
    if (!value) this.clear();
    else this.report("ready");
  }

  setBusy(value) {
    this.busy = value;
    if (!value) void this.drain();
  }

  setBehavior(value) {
    if (this.behavior === value) return;
    this.behavior = value;
    this.clear();
  }

  clear() {
    this.generation += 1;
    this.pending = [];
    this.held = false;
    this.holdTarget = null;
    this.releaseGeneration = null;
    this.report("cleared");
  }

  enqueue(focus) {
    if (!this.enabled || this.behavior !== "click") return false;
    if (this.pending.length >= this.capacity) {
      this.report("full");
      return false;
    }
    this.pending.push(copy(focus));
    this.report("queued");
    void this.drain();
    return true;
  }

  startHold(focus) {
    if (!this.enabled || this.behavior !== "hold") return;
    this.generation += 1;
    this.pending = [];
    this.held = true;
    this.holdTarget = copy(focus);
    this.releaseGeneration = null;
    this.report("holding");
    void this.drain();
  }

  moveHold(focus) {
    if (!this.held) return;
    const wasInside = Boolean(this.holdTarget);
    this.holdTarget = copy(focus);
    if (!focus && wasInside) this.report("waiting");
    void this.drain();
  }

  updateHoldSettings(focus) {
    if (!this.holdTarget || !focus) return;
    this.holdTarget = { ...this.holdTarget, radius: focus.radius, strength: focus.strength };
  }

  stopHold() {
    if (!this.held) return;
    this.generation += 1;
    this.held = false;
    this.holdTarget = null;
    this.releaseGeneration = this.running ? this.generation : null;
    this.report("released");
  }

  report(state, detail = "") {
    this.onState(state, { pending: this.pending.length, running: this.running,
      holding: this.held, detail, capacity: this.capacity });
  }

  async drain() {
    if (this.running || this.busy || !this.enabled) return;
    const generation = this.generation;
    this.running = true;
    try {
      while (this.enabled && !this.busy && generation === this.generation &&
          (this.pending.length || (this.held && this.holdTarget))) {
        if (this.held && !this.canHold()) {
          this.stopHold();
          break;
        }
        const holding = this.held;
        const focus = holding ? copy(this.holdTarget) : this.pending.shift();
        this.report("running");
        try {
          const result = await this.runStroke(focus);
          if (generation !== this.generation && this.releaseGeneration !== this.generation) break;
          if (result.cancelled) {
            this.pending = [];
            this.held = false;
            this.holdTarget = null;
            this.releaseGeneration = null;
            this.report("stopped", result.reason || "Stroke interrupted");
            break;
          }
          if (generation !== this.generation) break;
          if (result.added > 0) this.report("accepted");
          else {
            // An adequate fit or exhausted attempts are a completed stroke,
            // not an instruction to repeatedly submit the same click.
            if (holding) {
              this.held = false;
              this.holdTarget = null;
              this.report("hold_stopped", result.reason || "No further improvement");
              break;
            }
            this.report("rejected", result.reason || "No further improvement");
          }
        } catch (error) {
          if (generation !== this.generation && this.releaseGeneration !== this.generation) break;
          this.pending = [];
          this.held = false;
          this.holdTarget = null;
          this.releaseGeneration = null;
          this.report("error", error.message || "Stroke failed");
          break;
        }
      }
    } finally {
      this.running = false;
      if (this.releaseGeneration === this.generation) {
        this.releaseGeneration = null;
        this.report("released");
      }
      if (generation !== this.generation) void this.drain();
    }
  }
}

export class FocusControls {
  constructor(elements, definition, onChange) {
    this.el = elements;
    this.definition = definition;
    this.onChange = onChange;
    this.mode = "pan";
    this.paintBehavior = "click";
    this.value = null;
    this.remembered = null;
    this.available = false;
    this.running = false;
    this.feedback = "";
    for (const key of ["radius", "strength"]) {
      for (const input of [elements[key], elements[`${key}Number`]]) {
        input.min = String(definition.limits[key].min * 100);
        input.max = String(definition.limits[key].max * 100);
      }
      elements[key].addEventListener("input", () => this.edit(key, elements[key].value));
      elements[`${key}Number`].addEventListener("change", () => this.edit(key, elements[`${key}Number`].value));
    }
    elements.toggle.addEventListener("click", () => this.setMode(this.mode === "focus" ? "pan" : "focus"));
    elements.paint.addEventListener("click", () => this.setMode(this.mode === "paint" ? "pan" : "paint"));
    elements.clear.addEventListener("click", () => this.reset());
    elements.behavior.addEventListener("change", () => {
      this.paintBehavior = elements.behavior.value;
      this.changed();
    });
    this.render();
  }

  defaults() {
    return { x: 0.5, y: 0.5, ...this.definition.defaults };
  }

  changed() {
    this.feedback = "";
    this.render();
    this.onChange(copy(this.value), this.mode, this.paintBehavior);
  }

  setMode(mode) {
    if (this.value) this.remembered = copy(this.value);
    this.mode = mode;
    this.value = mode === "pan" ? null : copy(this.remembered || this.defaults());
    this.changed();
  }

  replace(value) {
    this.value = validateFocus(value, this.definition);
    this.remembered = copy(this.value);
    this.mode = this.value ? "focus" : "pan";
    this.changed();
  }

  reset() {
    this.value = null;
    this.remembered = null;
    this.mode = "pan";
    this.changed();
  }

  move(point) {
    if (!this.value) return;
    this.value = { ...this.value, x: point.x, y: point.y };
    this.changed();
  }

  edit(key, percent) {
    if (!this.value) return;
    const bounds = this.definition.limits[key];
    const raw = Number(percent);
    const value = percent === "" || !Number.isFinite(raw) ? this.definition.defaults[key] : raw / 100;
    this.value = { ...this.value, [key]: Math.min(bounds.max, Math.max(bounds.min, value)) };
    this.changed();
  }

  setAvailability(available, running) {
    this.available = available;
    this.running = running;
    this.render();
  }

  setFeedback(message) {
    this.feedback = message;
    this.render();
  }

  render() {
    const value = this.value || this.remembered || this.defaults();
    this.el.toggle.disabled = !this.available;
    this.el.paint.disabled = !this.available;
    this.el.behavior.disabled = !this.available;
    this.el.behavior.value = this.paintBehavior;
    this.el.toggle.setAttribute("aria-pressed", String(this.mode === "focus"));
    this.el.paint.setAttribute("aria-pressed", String(this.mode === "paint"));
    this.el.clear.disabled = !this.available || (!this.value && !this.remembered);
    for (const key of ["radius", "strength"]) {
      const percent = String(Number((value[key] * 100).toFixed(4)));
      for (const input of [this.el[key], this.el[`${key}Number`]]) {
        input.value = percent;
        input.disabled = !this.value;
      }
    }
    this.el.position.textContent = this.value
      ? `Center ${Math.round(value.x * 100)}% / ${Math.round(value.y * 100)}%` : "Full image";
    this.el.status.textContent = this.feedback || (!this.available ? "Load an image to focus or paint." :
      this.mode === "pan" ? "Off · drag previews to pan" : this.mode === "paint" ?
        (this.paintBehavior === "hold" ? "Hold on the result to paint; release to stop." : "Click the result to add one shape.") :
        this.running ? "Focus changes apply to future attempts." : "Focus applies to the next batch.");
  }
}
