"use strict";

import { validateFocus } from "./focus.js";
import { validateHistory } from "./history.js";
import { validatePalette } from "./palette.js";
import { validateSource, validateRgb } from "./source.js";

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Project ${label} must be an object`);
  }
  return value;
}

function integer(value, min, max, label) {
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    throw new Error(`Project ${label} must be an integer from ${min} to ${max}`);
  }
  return value;
}

function optionalDimension(value, label, contract) {
  return value === null || value === undefined ? null :
    integer(value, 1, contract.images.max_dimension, label);
}

function finite(value, label, contract, limit = contract.project.max_coordinate) {
  if (typeof value !== "number" || !Number.isFinite(value) ||
      Math.abs(value) > limit) {
    throw new Error(`Project ${label} must be a finite number`);
  }
  return value;
}

function color(value, label) {
  if (Array.isArray(value) && value.length !== 4) {
    throw new Error(`Project ${label} requires four RGBA channels`);
  }
  const raw = Array.isArray(value)
    ? { r: value[0], g: value[1], b: value[2], a: value[3] }
    : object(value, label);
  return {
    r: integer(raw.r, 0, 255, `${label} red channel`),
    g: integer(raw.g, 0, 255, `${label} green channel`),
    b: integer(raw.b, 0, 255, `${label} blue channel`),
    a: integer(raw.a, 0, 255, `${label} alpha channel`)
  };
}

export function rasterDataUrl(value, label, contract) {
  if (typeof value !== "string" || value.length > contract.project.max_bytes) {
    throw new Error(`Project ${label} is not a valid embedded image`);
  }
  const match = value.match(/^data:(image\/[a-z0-9.+-]+)(?:;[^,]*)?;base64,([a-z0-9+/=\r\n]+)$/i);
  if (!match || !contract.images.mime_types.includes(match[1].toLowerCase()) || !match[2]) {
    throw new Error(`Project ${label} must be an embedded supported raster image`);
  }
  return value;
}

export function decodeImage(url, label, contract) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.addEventListener("load", () => {
      const { naturalWidth: width, naturalHeight: height } = image;
      if (!width || !height || width > contract.images.max_dimension ||
          height > contract.images.max_dimension || width * height > contract.images.max_pixels) {
        reject(new Error(`${label} dimensions are too large`));
      } else {
        resolve(image);
      }
    }, { once: true });
    image.addEventListener("error", () => reject(new Error(`${label} could not be decoded`)), { once: true });
    image.src = url;
  });
}

export function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () =>
      typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("Could not read image")),
    { once: true });
    reader.addEventListener("error", () => reject(new Error("Could not read image")), { once: true });
    reader.readAsDataURL(file);
  });
}

function shape(raw, index, contract, geometryLimit) {
  const item = object(raw, `shape ${index + 1}`);
  const definition = contract.shapes.find((entry) => entry.type === item.type);
  if (!definition) throw new Error(`Shape ${index + 1} has an unsupported type`);
  const input = object(item.data, `shape ${index + 1} data`);
  const data = {};
  if (item.type === "polyline") {
    if (!Array.isArray(input.points) || input.points.length > contract.project.max_points) {
      throw new Error(`Shape ${index + 1} has invalid polyline points`);
    }
    data.points = input.points.map((point) => {
      if (!Array.isArray(point) || point.length !== 2) {
        throw new Error(`Shape ${index + 1} has an invalid polyline point`);
      }
      return [
        finite(point[0], `shape ${index + 1} point x`, contract, geometryLimit),
        finite(point[1], `shape ${index + 1} point y`, contract, geometryLimit)
      ];
    });
  } else {
    definition.data_fields.forEach((field) => {
      data[field] = finite(input[field], `shape ${index + 1} ${field}`, contract,
        field === "angle" ? contract.project.max_coordinate : geometryLimit);
    });
  }
  definition.radius_fields.forEach((field) => {
    if (data[field] < 0) throw new Error(`Shape ${index + 1} ${field} cannot be negative`);
  });
  const result = { type: item.type, color: color(item.color, `shape ${index + 1} color`), data };
  if (item.type_id !== undefined) result.type_id = integer(item.type_id, 0, 2 ** 31 - 1, `shape ${index + 1} type id`);
  if (item.score !== undefined) result.score = finite(item.score, `shape ${index + 1} score`, contract);
  return result;
}

function options(raw, contract) {
  const input = object(raw, "options");
  knownFields(input, [...Object.keys(contract.limits), "shape_types", "focus", "palette", "source", "background"], "options");
  if (!Array.isArray(input.shape_types)) throw new Error("Options shape types must be an array");
  const known = new Set(contract.shapes.map((item) => item.type));
  const types = [...new Set(input.shape_types.map((type) => {
    if (!known.has(type)) throw new Error(`Unsupported option shape type: ${String(type)}`);
    return type;
  }))];
  const result = { shape_types: types, focus: validateFocus(input.focus, contract.focus, "Project options focus"),
    palette: validatePalette(input.palette, contract.palette, "Project options palette"),
    source: validateSource(input.source, contract.source, "Project options source"),
    background: validateRgb(input.background, "Project options background") };
  for (const key of ["steps", "alpha", "seed", "shape_count", "mutations", "max_size", "export_size", "max_threads", "stagnation_limit"]) {
    const bounds = contract.limits[key];
    const value = input[key] === undefined ? contract.defaults[key] : input[key];
    result[key] = integer(value, bounds.min, bounds.max, key.replaceAll("_", " "));
  }
  return result;
}

function batches(raw, contract) {
  if (!Array.isArray(raw) || raw.length > contract.project.max_batches) {
    throw new Error(`Project telemetry batches must be an array of at most ${contract.project.max_batches} items`);
  }
  const known = new Set(contract.shapes.map((item) => item.type));
  return raw.map((rawBatch, position) => {
    const batch = object(rawBatch, `batch ${position + 1}`);
    if (!Array.isArray(batch.shapeTypes) || batch.shapeTypes.length === 0 ||
        batch.shapeTypes.some((type) => !known.has(type))) {
      throw new Error(`Batch ${position + 1} has invalid shape types`);
    }
    const result = {
      index: integer(batch.index, 1, Number.MAX_SAFE_INTEGER, "batch index"),
      target: integer(batch.target, 1, contract.limits.steps.max, "batch target"),
      shapeTypes: batch.shapeTypes,
      candidates: integer(batch.candidates, 1, contract.limits.shape_count.max, "batch candidates"),
      mutations: integer(batch.mutations, 1, contract.limits.mutations.max, "batch mutations"),
      alpha: integer(batch.alpha, 1, 255, "batch alpha"),
      added: integer(batch.added, 0, contract.project.max_shapes, "batch added"),
      attempts: integer(batch.attempts, 0, Number.MAX_SAFE_INTEGER, "batch attempts"),
      state: typeof batch.state === "string" ? batch.state.slice(0, 40) : "Complete"
    };
    for (const key of ["seed", "max_threads", "effective_threads", "start_shape_count", "start_attempts"]) {
      if (Number.isSafeInteger(batch[key]) && batch[key] >= 0) result[key] = batch[key];
    }
    if (typeof batch.reason === "string") result.reason = batch.reason.slice(0, 80);
    for (const key of ["focus", "initial_focus"]) {
      if (key in batch) result[key] = validateFocus(batch[key], contract.focus, `Project batch ${position + 1} ${key}`);
    }
    if ("palette" in batch) result.palette = validatePalette(batch.palette, contract.palette, `Project batch ${position + 1} palette`);
    if ("source" in batch) result.source = validateSource(batch.source, contract.source, `Project batch ${position + 1} source`);
    if ("background" in batch) result.background = validateRgb(batch.background, `Project batch ${position + 1} background`);
    return result;
  });
}

function shapeBudgets(shapes, contract) {
  if (!Array.isArray(shapes) || shapes.length > contract.project.max_shapes) {
    throw new Error(`A project can contain at most ${contract.project.max_shapes} shapes`);
  }
  let totalPoints = 0;
  shapes.forEach((item, index) => {
    if (item?.type !== "polyline") return;
    const points = item.data?.points;
    if (!Array.isArray(points) || points.length > contract.project.max_points) {
      throw new Error(`Shape ${index + 1} has invalid polyline points`);
    }
    totalPoints += points.length;
    if (totalPoints > contract.project.max_total_points) {
      throw new Error(`Project cannot exceed ${contract.project.max_total_points} polyline points`);
    }
  });
}

function scene(rawResult, rawTelemetry, contract) {
  const result = object(rawResult, "result");
  const telemetry = object(rawTelemetry, "telemetry");
  shapeBudgets(result.shapes, contract);
  const width = optionalDimension(result.width, "result width", contract);
  const height = optionalDimension(result.height, "result height", contract);
  const renderWidth = optionalDimension(result.render_width, "render width", contract);
  const renderHeight = optionalDimension(result.render_height, "render height", contract);
  const hasCanvas = (renderWidth || width) && (renderHeight || height) && result.background != null;
  if (!hasCanvas && (width || height || renderWidth || renderHeight || result.background != null ||
      result.shapes.length || result.preview_data_url != null)) {
    throw new Error("Project results require a fitting grid and background; an unfitted draft must be empty");
  }
  if (result.preview_data_url != null) rasterDataUrl(result.preview_data_url, "result preview", contract);
  const longest = Math.max(renderWidth || width, renderHeight || height);
  const geometryLimit = longest ? contract.project.max_geometry_factor * longest : contract.project.max_coordinate;
  if (result.target_digest !== undefined && (typeof result.target_digest !== "string" || !/^[a-f0-9]{64}$/i.test(result.target_digest))) {
    throw new Error("Project target digest must be 64 hexadecimal characters");
  }
  return {
    result: {
      preview_data_url: null,
      width,
      height,
      render_width: renderWidth,
      render_height: renderHeight,
      background: result.background == null ? null : color(result.background, "result background"),
      restored_shape_count: integer(result.restored_shape_count ?? 0, 0, result.shapes.length, "retained shape count"),
      ...(result.target_digest !== undefined ? { target_digest: result.target_digest.toLowerCase() } : {}),
      shapes: result.shapes.map((item, index) => shape(item, index, contract, geometryLimit))
    },
    telemetry: {
      attempts: integer(telemetry.attempts, 0, Number.MAX_SAFE_INTEGER, "telemetry attempts"),
      duration_ms: integer(telemetry.duration_ms, 0, Number.MAX_SAFE_INTEGER, "telemetry duration"),
      initial_score: telemetry.initial_score == null ? null : finite(telemetry.initial_score, "initial score", contract),
      batches: batches(telemetry.batches, contract)
    }
  };
}

function knownFields(value, fields, label) {
  if (Object.keys(value).some(key => !fields.includes(key))) throw new Error(`Project ${label} contains an unknown field`);
}

function history(raw, root, contract) {
  const input = object(raw, "history");
  knownFields(input, ["active_branch", "view_shape_count", "branches"], "history");
  if (!Array.isArray(input.branches) || !input.branches.length || input.branches.length > contract.project.max_branches) {
    throw new Error(`Project history must contain 1 to ${contract.project.max_branches} experiments`);
  }
  // Check raw array budgets and graph links before allocating validated scenes.
  const branches = input.branches.map(value => {
    const branch = object(value, "experiment");
    knownFields(branch, ["id", "name", "parent_id", "fork_shape_count", "options", "result", "telemetry"], "experiment");
    const active = branch.id === input.active_branch;
    if (active && ("result" in branch || "telemetry" in branch)) {
      throw new Error("Project active experiment uses the top-level result and telemetry");
    }
    const result = active ? root.result : object(branch.result, "experiment result");
    shapeBudgets(result.shapes, contract);
    const validatedOptions = options(branch.options, contract);
    return { ...branch, result, options: active ? root.options : validatedOptions };
  });
  const state = validateHistory({ active_branch: input.active_branch, view_shape_count: input.view_shape_count, branches }, contract.project);
  state.branches = state.branches.map(branch => ({
    id: branch.id, name: branch.name, parent_id: branch.parent_id, fork_shape_count: branch.fork_shape_count,
    options: branch.id === input.active_branch ? structuredClone(root.options) : branch.options,
    ...(branch.id === input.active_branch ? { result: root.result, telemetry: root.telemetry } :
      scene(branch.result, branch.telemetry, contract))
  }));
  return state;
}

export function validateProject(raw, contract) {
  if (Array.isArray(raw)) throw new Error("This is a Shapes JSON export, not a Geometrize project");
  const project = object(raw, "project");
  if (project.format !== contract.project.format) throw new Error("This JSON file is not a Geometrize project");
  if (project.version !== contract.project.version) {
    throw new Error(`Project version is not supported; only version ${contract.project.version} can be opened`);
  }
  const source = object(project.source, "source");
  const root = { options: options(project.options, contract), ...scene(project.result, project.telemetry, contract) };
  return {
    source: {
      name: typeof source.name === "string" && source.name.length <= 512 && source.name ? source.name : "Source image",
      data_url: rasterDataUrl(source.data_url, "source image", contract),
      width: optionalDimension(source.width, "source width", contract),
      height: optionalDimension(source.height, "source height", contract)
    },
    ...root, history: history(project.history, root, contract)
  };
}

export async function openProjectFile(file, contract, { decodeSource = true } = {}) {
  if (file.size > contract.project.max_bytes) throw new Error("Project files must be 64 MB or smaller");
  let raw;
  try {
    raw = JSON.parse(await file.text());
  } catch (error) {
    if (error instanceof SyntaxError) throw new Error("The file is not valid JSON");
    throw error;
  }
  const project = validateProject(raw, contract);
  if (decodeSource) await decodeImage(project.source.data_url, "Project source image", contract);
  return project;
}

export function projectContent(project, maxBytes) {
  const content = JSON.stringify(project, null, 2);
  if (new Blob([content]).size > maxBytes) {
    throw new Error("Project exceeds 64 MB; use a smaller source or remove an inactive experiment");
  }
  return content;
}
