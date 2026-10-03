"use strict";

function fixed(value) {
  return typeof value === "number" && Number.isFinite(value) ?
    (Math.abs(value) < 0.00005 ? 0 : value).toFixed(4) : "--";
}

function duration(ms) {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function graph(series) {
  const recent = series.slice(-512);
  const values = recent.map((entry) => entry.score);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  const range = max - min || 1;
  const y = (value) => 8 + ((max - value) / range) * 88;
  const path = recent.map((entry, index) => {
    const x = recent.length === 1 ? 36 : 36 + index / (recent.length - 1) * 312;
    return `${index ? "L" : "M"}${x.toFixed(2)} ${y(entry.score).toFixed(2)}`;
  }).join("");
  const first = recent[0];
  const last = recent.at(-1);
  return `
    <path class="graph-grid" d="M36 8V102H348M36 55H348" />
    <text class="graph-label" x="1" y="12">${fixed(max)}</text>
    <text class="graph-label" x="1" y="99">${fixed(min)}</text>
    <text class="graph-label" x="36" y="119">${first ? first.index : 0}</text>
    <text class="graph-label" x="348" y="119" text-anchor="end">${last ? last.index : 0} shapes</text>
    <path class="score-path" d="${path}" />
    ${last ? `<circle class="graph-dot" cx="${recent.length === 1 ? 36 : 348}" cy="${y(last.score).toFixed(2)}" r="3" />` : ""}`;
}

function impactGraph(series) {
  const recent = series.slice(-96);
  const max = Math.max(0.000001, ...recent);
  const width = recent.length ? 312 / recent.length : 312;
  const bars = recent.map((value, index) => {
    const height = Math.max(1, value / max * 82);
    return `<rect class="impact-bar" x="${(36 + index * width).toFixed(2)}" y="${(102 - height).toFixed(2)}" width="${Math.max(1, width - 1).toFixed(2)}" height="${height.toFixed(2)}" />`;
  }).join("");
  return `<path class="graph-grid" d="M36 8V102H348" />
    <text class="graph-label" x="1" y="12">${fixed(max)}</text>
    <text class="graph-label" x="1" y="102">0</text>
    <text class="graph-label" x="36" y="119">${recent.length ? `Last ${recent.length} shapes` : "No shapes"}</text>
    ${bars}`;
}

export class Telemetry {
  constructor(elements, shapeLabels) {
    this.el = elements;
    this.shapeLabels = shapeLabels;
    this.reset();
  }

  reset() {
    this.shapes = [];
    this.counts = new Map();
    this.scoreSeries = [];
    this.impacts = [];
    this.attempts = 0;
    this.initialScore = null;
    this.batches = [];
    this.state = "Idle";
    this.elapsed = 0;
    this.render();
  }

  setBaseline(score) {
    if (typeof score === "number" && Number.isFinite(score)) {
      this.initialScore = score;
      this.rebuildSeries();
    }
    this.render();
  }

  replace(shapes, attempts, initialScore = this.initialScore) {
    this.shapes = [...shapes];
    this.attempts = attempts || 0;
    this.initialScore = typeof initialScore === "number" ? initialScore : null;
    this.rebuildSeries();
    this.render();
  }

  append(shapes, attempts) {
    shapes.forEach((shape) => {
      this.shapes.push(shape);
      this.ingestShape(shape, this.shapes.length);
    });
    this.attempts = attempts || this.attempts;
    this.render();
  }

  rebuildSeries() {
    this.counts = new Map();
    this.scoreSeries = [];
    this.impacts = [];
    if (this.initialScore !== null) this.scoreSeries.push({ index: 0, score: this.initialScore });
    this.shapes.forEach((shape, index) => this.ingestShape(shape, index + 1));
  }

  ingestShape(shape, index) {
    this.counts.set(shape.type, (this.counts.get(shape.type) || 0) + 1);
    if (typeof shape.score !== "number" || !Number.isFinite(shape.score)) return;
    const previous = this.scoreSeries.at(-1)?.score;
    this.scoreSeries.push({ index, score: shape.score });
    if (typeof previous === "number") this.impacts.push(Math.max(0, previous - shape.score));
  }

  addBatch(summary) {
    if (!summary) return;
    const batch = {
      index: summary.index ?? this.batches.length + 1,
      target: summary.target ?? 0,
      shapeTypes: summary.shapeTypes ?? summary.shape_types ?? [],
      candidates: summary.candidates ?? summary.shape_count ?? 0,
      mutations: summary.mutations ?? 0,
      alpha: summary.alpha ?? 0,
      seed: summary.seed ?? 0,
      max_threads: summary.max_threads ?? 0,
      effective_threads: summary.effective_threads ?? 0,
      start_shape_count: summary.start_shape_count ?? 0,
      start_attempts: summary.start_attempts ?? 0,
      added: summary.added ?? 0,
      attempts: summary.attempts ?? 0,
      state: summary.state ?? "Complete",
      reason: summary.reason ?? ""
    };
    const previous = this.batches.findIndex((item) => item.index === batch.index);
    if (previous >= 0) this.batches[previous] = batch;
    else this.batches.push(batch);
    this.render();
  }

  setState(state, elapsed = this.elapsed) {
    this.state = state;
    this.elapsed = elapsed;
    this.render();
  }

  render() {
    const el = this.el;
    const latestScore = this.scoreSeries.at(-1)?.score;
    const latestImpact = this.impacts.at(-1);
    el.state.textContent = this.state;
    el.acceptance.textContent = `${this.shapes.length} accepted / ${this.attempts} attempts`;
    el.improvement.textContent = `Error ${fixed(latestScore)} · lower is better`;
    el.duration.textContent = this.elapsed ? duration(this.elapsed) : "--";
    el.score.textContent = fixed(latestScore);
    el.baseline.textContent = `Initial error ${fixed(this.initialScore)} · recent ${Math.min(this.scoreSeries.length, 512)} points`;
    el.total.textContent = String(this.shapes.length);
    el.impact.textContent = latestImpact === undefined ? "--" : `+${fixed(latestImpact)}`;
    el.scoreGraph.innerHTML = graph(this.scoreSeries);
    el.impactGraph.innerHTML = impactGraph(this.impacts);

    const rows = [...this.counts].sort((a, b) => b[1] - a[1]).map(([type, count]) => {
      const row = document.createElement("div");
      const label = document.createElement("span");
      const track = document.createElement("div");
      const fill = document.createElement("span");
      const output = document.createElement("output");
      row.className = "mix-row";
      track.className = "mix-track";
      fill.style.inlineSize = `${Math.max(2, count / Math.max(1, this.shapes.length) * 100)}%`;
      label.textContent = this.shapeLabels[type] || type;
      output.textContent = String(count);
      track.append(fill);
      row.replaceChildren(label, track, output);
      return row;
    });
    el.mix.replaceChildren(...rows);

    const chips = this.batches.map((batch) => {
      const chip = document.createElement("span");
      const label = document.createElement("span");
      const added = document.createElement("strong");
      const state = document.createElement("span");
      chip.className = "batch-chip";
      chip.title = `${batch.shapeTypes.map((type) => this.shapeLabels[type] || type).join(", ")}; ${batch.candidates} candidates, ${batch.mutations} mutations, alpha ${batch.alpha}, seed ${batch.seed}, ${batch.effective_threads || "auto"} workers`;
      label.textContent = `Batch ${batch.index}`;
      added.textContent = `+${batch.added}`;
      state.textContent = batch.state;
      chip.replaceChildren(label, added, state);
      return chip;
    });
    el.history.replaceChildren(...chips);
  }
}
