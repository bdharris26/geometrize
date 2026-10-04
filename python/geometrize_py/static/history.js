"use strict";

const ID = /^[a-zA-Z0-9_-]{1,64}$/;
const copyOptions = options => ({ ...options, shape_types: [...options.shape_types],
  focus: options.focus ? { ...options.focus } : null });
const pointsIn = shapes => shapes.reduce((total, shape) => total +
  (shape?.type === "polyline" ? shape.data.points.length : 0), 0);
const copyTelemetry = telemetry => ({ ...telemetry, batches: [...telemetry.batches] });
const background = scene => Array.isArray(scene.background) ? scene.background : scene.background ?
  [scene.background.r, scene.background.g, scene.background.b, scene.background.a] : null;

export function experimentName(value) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > 80) {
    throw new Error("Experiment name must contain 1 to 80 characters");
  }
  return value.trim();
}

// Snapshots have already passed the scene/options validators in project.js.
// Check the complete graph and aggregate budgets before retaining any branch.
export function validateHistory(state, limits) {
  if (!Array.isArray(state.branches) || !state.branches.length || state.branches.length > limits.max_branches) {
    throw new Error(`Project history must contain 1 to ${limits.max_branches} experiments`);
  }
  const ids = new Map();
  const names = new Set();
  const branches = state.branches.map(branch => ({ ...branch, name: experimentName(branch.name) }));
  let shapes = 0;
  let points = 0;
  for (const branch of branches) {
    if (typeof branch.id !== "string" || !ID.test(branch.id) || ids.has(branch.id)) {
      throw new Error("Project experiment IDs must be unique and contain 1 to 64 letters, numbers, underscores or hyphens");
    }
    const name = branch.name.toLowerCase();
    if (names.has(name)) throw new Error("Project experiment names must be unique");
    names.add(name);
    ids.set(branch.id, branch);
    if (branch.parent_id !== null && (typeof branch.parent_id !== "string" || !ID.test(branch.parent_id))) {
      throw new Error("Project experiment parent must be an experiment ID or null");
    }
    if (!Number.isSafeInteger(branch.fork_shape_count) || branch.fork_shape_count < 0) {
      throw new Error("Project experiment fork shape count must be a nonnegative integer");
    }
    if (branch.fork_shape_count > branch.result.shapes.length) {
      throw new Error("Project experiment has fewer shapes than its fork prefix");
    }
    shapes += branch.result.shapes.length;
    points += pointsIn(branch.result.shapes);
    if (shapes > limits.max_history_shapes) {
      throw new Error(`Project experiments exceed ${limits.max_history_shapes} total shapes`);
    }
    if (points > limits.max_history_points) {
      throw new Error(`Project experiments exceed ${limits.max_history_points} total polyline points`);
    }
  }
  for (const branch of branches) {
    const parent = branch.parent_id === null ? null : ids.get(branch.parent_id);
    if (branch.parent_id !== null && !parent) throw new Error("Project experiment parent does not exist");
    if (branch.fork_shape_count > (parent?.result.shapes.length || 0)) {
      throw new Error("Project experiment fork shape count exceeds its parent");
    }
    if (parent && ((branch.result.render_width || branch.result.width) !== (parent.result.render_width || parent.result.width) ||
        (branch.result.render_height || branch.result.height) !== (parent.result.render_height || parent.result.height) ||
        JSON.stringify(background(branch.result)) !== JSON.stringify(background(parent.result)))) {
      throw new Error("Project fork scene dimensions and background must match its parent");
    }
    const seen = new Set([branch.id]);
    for (let ancestor = parent; ancestor; ancestor = ids.get(ancestor.parent_id)) {
      if (seen.has(ancestor.id)) throw new Error("Project experiment parents contain a cycle");
      seen.add(ancestor.id);
    }
  }
  const active = ids.get(state.active_branch);
  if (!active) throw new Error("Project active experiment does not exist");
  if (!Number.isSafeInteger(state.view_shape_count) || state.view_shape_count < 0 ||
      state.view_shape_count > active.result.shapes.length) {
    throw new Error("Project inspected shape count exceeds the active experiment");
  }
  return { ...state, branches };
}

export class HistoryControls {
  constructor(elements, history, actions) {
    this.el = elements;
    this.history = history;
    this.activeId = "";
    this.lastName = "";
    elements.branch.addEventListener("change", () => actions.select(elements.branch.value));
    elements.slider.addEventListener("input", () => actions.inspect(elements.slider.valueAsNumber));
    elements.count.addEventListener("change", () => actions.inspect(elements.count.valueAsNumber));
    elements.head.addEventListener("click", () => actions.inspect(history.length));
    elements.restore.addEventListener("click", () => actions.restore(false));
    elements.fork.addEventListener("click", () => actions.restore(true));
    elements.rename.addEventListener("click", () => actions.rename(elements.name.value));
    elements.name.addEventListener("input", () => { elements.rename.disabled = elements.name.value.trim() === history.active?.name; });
    elements.remove.addEventListener("click", () => actions.remove(elements.removeChoice.value));
    elements.removeChoice.addEventListener("change", () => this.renderRemoval());
  }

  childName(fork) {
    const name = this.el.name.value.trim();
    if (name && name !== this.history.active.name) return name;
    return this.history.uniqueName(fork ? `${name} copy` : `${name} at ${this.history.cursor}`);
  }

  renderRemoval() {
    const id = this.el.removeChoice.value;
    const child = this.history.branches.find(branch => branch.parent_id === id);
    this.el.remove.disabled = this.locked || !id || Boolean(child);
    this.el.removeHelp.textContent = child ? `Remove “${child.name}” first; it retains this parent.` :
      "Only inactive experiments can be removed.";
  }

  render({ busy = false, restoring = false } = {}) {
    const el = this.el;
    const history = this.history;
    const active = history.active;
    const hasScene = Boolean(active?.result.background && (active.result.render_width || active.result.width));
    this.locked = busy || restoring;
    const choice = el.removeChoice.value;
    const options = history.branches.map(branch => {
      const option = document.createElement("option");
      option.value = branch.id;
      option.textContent = `${branch.name} · ${branch.result.shapes.length} shapes`;
      return option;
    });
    el.branch.replaceChildren(...options);
    el.branch.value = history.activeId;
    el.branch.disabled = busy || !active || history.branches.length < 2;
    const others = history.branches.filter(branch => branch.id !== history.activeId).map(branch => {
      const option = document.createElement("option");
      option.value = branch.id;
      option.textContent = branch.name;
      return option;
    });
    el.removeChoice.replaceChildren(...others);
    if (history.branches.some(branch => branch.id === choice && branch.id !== history.activeId)) el.removeChoice.value = choice;
    el.removeChoice.disabled = this.locked || !others.length;
    this.renderRemoval();
    if (this.activeId !== history.activeId || el.name.value === this.lastName) el.name.value = active?.name || "";
    this.activeId = history.activeId;
    this.lastName = active?.name || "";
    el.name.disabled = this.locked || !active;
    el.rename.disabled = this.locked || !active || el.name.value.trim() === active.name;
    for (const input of [el.slider, el.count]) {
      input.max = String(history.length);
      input.value = String(history.cursor);
      input.disabled = this.locked || !hasScene || !history.length;
    }
    el.slider.setAttribute("aria-valuetext", `${history.cursor} of ${history.length} shapes`);
    el.position.textContent = `${history.cursor} / ${history.length}`;
    el.head.disabled = this.locked || !history.inspecting;
    el.restore.disabled = this.locked || !hasScene || (!history.inspecting && Boolean(active?.sessionId));
    el.restore.textContent = history.inspecting ? "Restore prefix" : "Restore head";
    el.fork.disabled = this.locked || !hasScene;
    el.help.textContent = busy ? "Timeline is locked until the batch is confirmed." : restoring ?
      "Creating a fresh experiment; the parent is retained." : history.inspecting ?
        "Inspecting a prefix. Restore it to a new experiment, or return to Head to continue." : hasScene && !active.sessionId ?
          "Saved head. Restore or fork to continue this experiment." : active?.result.preview_data_url ?
            "Preview-only experiment. New render starts a fresh fit and keeps this preview." :
              "Scrub to inspect; Restore and Fork keep the original experiment.";
    const settings = active?.options;
    el.settings.textContent = settings ?
      `${settings.shape_types.join(", ")} · alpha ${settings.alpha} · seed ${settings.seed} · ${settings.max_size}px working` : "";
  }
}

export class ReconstructionHistory {
  constructor(limits) {
    this.limits = limits;
    this.reset();
  }

  reset() {
    this.branches = [];
    this.activeId = "";
    this.cursor = 0;
  }

  get active() { return this.branches.find(branch => branch.id === this.activeId) || null; }
  get length() { return this.active?.result.shapes.length || 0; }
  get inspecting() { return this.cursor < this.length; }

  load(state) {
    this.branches = state.branches.map(branch => ({ ...branch, sessionId: "", options: copyOptions(branch.options),
      telemetry: copyTelemetry(branch.telemetry), pointCount: pointsIn(branch.result.shapes) }));
    this.activeId = state.active_branch;
    this.cursor = state.view_shape_count;
  }

  uniqueName(base) {
    const names = new Set(this.branches.map(branch => branch.name.toLowerCase()));
    let name = base.slice(0, 80);
    for (let index = 2; names.has(name.toLowerCase()); index += 1) {
      const suffix = ` ${index}`;
      name = base.slice(0, 80 - suffix.length) + suffix;
    }
    return name;
  }

  checkChild(count, name, shapes = null) {
    name = experimentName(name);
    if (this.branches.length >= this.limits.max_branches) {
      throw new Error(`Keep at most ${this.limits.max_branches} experiments; remove an inactive one first`);
    }
    if (this.branches.some(branch => branch.name.toLowerCase() === name.toLowerCase())) {
      throw new Error("An experiment already uses that name");
    }
    const total = this.branches.reduce((sum, branch) => sum + branch.result.shapes.length, 0);
    if (total + count > this.limits.max_history_shapes) throw new Error("Experiment shape budget reached; remove an inactive experiment first");
    if (shapes) {
      const points = this.branches.reduce((sum, branch) => sum + pointsIn(branch.result.shapes), 0);
      let added = 0;
      for (let index = 0; index < count; index += 1) {
        const shape = shapes[index];
        if (shape.type === "polyline") added += shape.data.points.length;
      }
      if (points + added > this.limits.max_history_points) throw new Error("Experiment polyline budget reached; remove an inactive experiment first");
    }
    return name;
  }

  add(snapshot, { name = "Original", parent_id = null, fork_shape_count = 0 } = {}) {
    name = this.checkChild(snapshot.result.shapes.length, name, snapshot.result.shapes);
    let index = 1;
    while (this.branches.some(branch => branch.id === `experiment-${index}`)) index += 1;
    const branch = { id: `experiment-${index}`, name, parent_id, fork_shape_count,
      ...snapshot, options: copyOptions(snapshot.options), telemetry: copyTelemetry(snapshot.telemetry),
      pointCount: pointsIn(snapshot.result.shapes) };
    this.branches.push(branch);
    this.activeId = branch.id;
    this.cursor = branch.result.shapes.length;
    return branch;
  }

  checkpoint(snapshot) {
    if (!this.active) return this.add(snapshot);
    Object.assign(this.active, snapshot, { options: copyOptions(snapshot.options),
      telemetry: copyTelemetry(snapshot.telemetry), pointCount: pointsIn(snapshot.result.shapes) });
    this.cursor = this.length;
    return this.active;
  }

  rememberOptions(options) {
    if (this.active) this.active.options = copyOptions(options);
  }

  select(id) {
    if (!this.branches.some(branch => branch.id === id)) throw new Error("Experiment no longer exists");
    this.activeId = id;
    this.cursor = this.length;
    return this.active;
  }

  inspect(count) {
    this.cursor = Math.max(0, Math.min(this.length, Number.isFinite(count) ? Math.trunc(count) : this.length));
  }

  rename(name) {
    name = experimentName(name);
    if (this.branches.some(branch => branch.id !== this.activeId && branch.name.toLowerCase() === name.toLowerCase())) {
      throw new Error("An experiment already uses that name");
    }
    if (this.active) this.active.name = name;
  }

  remove(id) {
    if (id === this.activeId) throw new Error("Select another experiment before removing this one");
    const child = this.branches.find(branch => branch.parent_id === id);
    if (child) throw new Error(`Remove “${child.name}” first; it retains this experiment as its parent`);
    this.branches = this.branches.filter(branch => branch.id !== id);
  }

  expireSession(id = this.activeId) {
    const branch = this.branches.find(item => item.id === id);
    if (branch) branch.sessionId = "";
  }

  fitCapacity(shapeTypes = []) {
    const total = this.branches.reduce((sum, branch) => sum + branch.result.shapes.length, 0);
    const points = this.branches.reduce((sum, branch) => sum + branch.pointCount, 0);
    const pointCapacity = shapeTypes.includes("polyline") ? Math.floor((this.limits.max_history_points - points) / 4) : Infinity;
    return Math.max(0, Math.min(this.limits.max_shapes - this.length, this.limits.max_history_shapes - total, pointCapacity));
  }

  serialize(serializeResult) {
    return { active_branch: this.activeId, view_shape_count: this.cursor,
      branches: this.branches.map(branch => {
        const entry = { id: branch.id, name: branch.name, parent_id: branch.parent_id,
          fork_shape_count: branch.fork_shape_count, options: copyOptions(branch.options) };
        if (branch.id !== this.activeId) {
          entry.result = serializeResult(branch.result);
          entry.telemetry = branch.telemetry;
        }
        return entry;
      }) };
  }
}
