"use strict";

export class ApiError extends Error {
  constructor(message, code = "request_failed") {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

export async function postJson(path, payload) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
  } catch (error) {
    throw new ApiError(error.message || "Could not reach the server", "network_error");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(body.error || `Request failed (${response.status})`, body.code || "request_failed");
  }
  return body;
}

export async function readRunStream(payload, onEvent) {
  const response = await fetch("/api/run/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body.error || "Render failed", body.code || "request_failed");
  }
  if (!response.body) {
    throw new ApiError("Streaming is unavailable", "stream_unavailable");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal = null;
  const consume = (line) => {
    if (!line.trim()) return;
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      throw new ApiError("The render stream contained invalid data", "stream_invalid");
    }
    if (event.event === "error") {
      throw new ApiError(event.error || "Render failed", event.code || "render_failed");
    }
    if (terminal) {
      throw new ApiError("The render stream sent data after completion", "stream_invalid");
    }
    if (event.event === "complete" || event.event === "paused") terminal = event;
    onEvent(event);
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      lines.forEach(consume);
    }
    buffer += decoder.decode();
    consume(buffer);
  } finally {
    reader.releaseLock();
  }
  if (!terminal) {
    throw new ApiError("The render stream ended before a final result was confirmed", "stream_incomplete");
  }
  return terminal;
}
