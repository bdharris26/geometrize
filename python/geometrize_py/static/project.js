"use strict";

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
  return value === null || value === undefined ? 0 :
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

export function imageToPngDataUrl(image) {
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  canvas.getContext("2d").drawImage(image, 0, 0);
  return canvas.toDataURL("image/png");
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
  if (!Array.isArray(input.shape_types)) throw new Error("Options shape types must be an array");
  const known = new Set(contract.shapes.map((item) => item.type));
  const types = [...new Set(input.shape_types.map((type) => {
    if (!known.has(type)) throw new Error(`Unsupported option shape type: ${String(type)}`);
    return type;
  }))];
  const result = { shape_types: types };
  for (const key of ["steps", "alpha", "seed", "shape_count", "mutations", "max_size", "export_size", "max_threads", "stagnation_limit"]) {
    const bounds = contract.limits[key];
    const value = input[key] ?? (key === "export_size" ? input.max_size : contract.defaults[key]);
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
    return result;
  });
}

export function validateProject(raw, contract) {
  if (Array.isArray(raw)) throw new Error("This is a Shapes JSON export, not a Geometrize project");
  const project = object(raw, "project");
  if (project.format !== contract.project.format) throw new Error("This JSON file is not a Geometrize project");
  if (project.version !== contract.project.version) throw new Error(`Project version ${String(project.version)} is not supported`);
  const source = object(project.source, "source");
  const result = object(project.result, "result");
  const telemetry = object(project.telemetry, "telemetry");
  if (!Array.isArray(result.shapes) || result.shapes.length > contract.project.max_shapes) {
    throw new Error(`A project can contain at most ${contract.project.max_shapes} shapes`);
  }
  let totalPoints = 0;
  result.shapes.forEach((item, index) => {
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
  const width = optionalDimension(result.width, "result width", contract);
  const height = optionalDimension(result.height, "result height", contract);
  const renderWidth = optionalDimension(result.render_width, "render width", contract);
  const renderHeight = optionalDimension(result.render_height, "render height", contract);
  const longest = Math.max(renderWidth || width, renderHeight || height);
  const geometryLimit = longest ? contract.project.max_geometry_factor * longest : contract.project.max_coordinate;
  return {
    source: {
      name: typeof source.name === "string" && source.name.length <= 512 && source.name ? source.name : "Source image",
      data_url: rasterDataUrl(source.data_url, "source image", contract),
      width: optionalDimension(source.width, "source width", contract),
      height: optionalDimension(source.height, "source height", contract)
    },
    options: options(project.options, contract),
    result: {
      preview_data_url: result.preview_data_url == null ? "" : rasterDataUrl(result.preview_data_url, "result preview", contract),
      width,
      height,
      render_width: renderWidth,
      render_height: renderHeight,
      background: result.background == null ? null : color(result.background, "result background"),
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

export async function openProjectFile(file, contract) {
  if (file.size > contract.project.max_bytes) throw new Error("Project files must be 64 MB or smaller");
  let raw;
  try {
    raw = JSON.parse(await file.text());
  } catch (error) {
    if (error instanceof SyntaxError) throw new Error("The file is not valid JSON");
    throw error;
  }
  const project = validateProject(raw, contract);
  const images = [decodeImage(project.source.data_url, "Project source image", contract)];
  if (project.result.preview_data_url) images.push(decodeImage(project.result.preview_data_url, "Project result preview", contract));
  await Promise.all(images);
  return project;
}
