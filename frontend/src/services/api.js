const API_BASE = import.meta.env.VITE_API_BASE || "";

async function responseError(res, fallback) {
  const payload = await res.json().catch(() => ({}));
  return new Error(payload.detail || fallback);
}

export async function openFolderPicker(signal) {
  const res = await fetch(`${API_BASE}/api/system/pick-folder`, {
    method: "POST",
    signal,
  });
  if (!res.ok) throw new Error("Failed to open folder picker");
  return res.json();
}

export async function browseDirectory(path = "") {
  const url = path ? `${API_BASE}/api/system/browse-dir?path=${encodeURIComponent(path)}` : `${API_BASE}/api/system/browse-dir`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("Failed to browse directory");
  return res.json();
}

export async function inspectFolder(directory, frameRate = 0.5, onProgress, adaptiveSampling = true) {
  const url = `${API_BASE}/api/folder/inspect/stream?directory=${encodeURIComponent(directory)}&frame_rate=${frameRate}&adaptive_sampling=${adaptiveSampling}`;
  const res = await fetch(url, { headers: { Accept: "text/event-stream" } });
  if (!res.ok) throw await responseError(res, "Failed to inspect directory");
  if (!res.body) throw new Error("This browser does not support streamed folder inspection");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  const consume = (block) => {
    const data = block.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim()).join("\n");
    if (!data) return;
    const event = JSON.parse(data);
    if (event.event === "progress") onProgress?.(event);
    if (event.event === "error") throw new Error(event.message || "Folder inspection failed");
    if (event.event === "complete") result = event.result;
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let separator;
    while ((separator = buffer.search(/\r?\n\r?\n/)) >= 0) {
      const block = buffer.slice(0, separator);
      const separatorLength = buffer.slice(separator).startsWith("\r\n\r\n") ? 4 : 2;
      buffer = buffer.slice(separator + separatorLength);
      consume(block);
    }
    if (done) break;
  }
  if (buffer.trim()) consume(buffer);
  if (!result) throw new Error("Folder inspection ended before a final result was received");
  return result;
}

export async function fetchSettings() {
  const res = await fetch(`${API_BASE}/api/settings`);
  if (!res.ok) throw await responseError(res, "Failed to load settings");
  return res.json();
}

export async function updateSettings(settings) {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw await responseError(res, "Failed to save settings");
  return res.json();
}

export async function fetchIndexingState() {
  const res = await fetch(`${API_BASE}/api/index/state`);
  if (!res.ok) throw new Error("Failed to fetch indexing state");
  return res.json();
}

export async function checkNewMedia() {
  const res = await fetch(`${API_BASE}/api/index/check-new`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to check for new media");
  return res.json();
}

export async function pauseIndexing() {
  const res = await fetch(`${API_BASE}/api/index/pause`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to pause indexing");
  return res.json();
}

export async function resumeIndexing() {
  const res = await fetch(`${API_BASE}/api/index/resume`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to resume indexing");
  return res.json();
}

export async function cancelIndexing() {
  const res = await fetch(`${API_BASE}/api/index/cancel`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to cancel indexing");
  return res.json();
}

export async function clearAllIndexing() {
  const res = await fetch(`${API_BASE}/api/index/clear`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to clear all indexing");
  }
  return res.json();
}

export async function fetchSystemStatus() {
  const res = await fetch(`${API_BASE}/api/system/status`);
  if (!res.ok) throw new Error("Failed to fetch system status");
  return res.json();
}

export async function auditImageProcessing(sampleSize = 8) {
  const res = await fetch(`${API_BASE}/api/system/audit?sample_size=${sampleSize}`);
  if (!res.ok) throw new Error("Image audit failed");
  return res.json();
}

export async function searchText(query, mrlDim = 768, limit = 30, signal) {
  const res = await fetch(`${API_BASE}/api/search/text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, mrl_dim: mrlDim, limit }),
    signal,
  });
  if (!res.ok) throw await responseError(res, "Search failed");
  return res.json();
}

export async function searchByImage(file, mrlDim = 768, limit = 30) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("mrl_dim", mrlDim);
  formData.append("limit", limit);

  const res = await fetch(`${API_BASE}/api/search/image`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) throw await responseError(res, "Visual search failed");
  return res.json();
}

export async function fetchSimilarPhotos(photoId, mrlDim = 768, limit = 12) {
  const res = await fetch(`${API_BASE}/api/photo/similar`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ photo_id: photoId, mrl_dim: mrlDim, limit }),
  });
  if (!res.ok) throw await responseError(res, "Failed to fetch related photos");
  return res.json();
}

export async function fetchVideoFrames(path, offset = 0, limit = 60, signal) {
  const query = new URLSearchParams({ path, offset: String(offset), limit: String(limit) });
  const res = await fetch(`${API_BASE}/api/video/frames?${query}`, { signal });
  if (!res.ok) throw await responseError(res, "Failed to load sampled video frames");
  return res.json();
}

export function getVideoFrameUrl(path, timestamp) {
  const query = new URLSearchParams({ path, timestamp: String(timestamp) });
  return `${API_BASE}/api/video/frame?${query}`;
}

export async function fetchMediaFaces(mediaId, mediaType) {
  const query = new URLSearchParams({ media_id: mediaId, media_type: mediaType });
  const res = await fetch(`${API_BASE}/api/faces/media?${query}`);
  if (!res.ok) throw await responseError(res, "Could not load detected faces");
  return res.json();
}

export function getFacePreviewUrl(mediaIdOrObj, mediaType, faceIndex) {
  let resolvedId = mediaIdOrObj;
  let resolvedType = mediaType;
  let resolvedIndex = faceIndex;
  if (mediaIdOrObj && typeof mediaIdOrObj === "object") {
    resolvedId = mediaIdOrObj.media_id ?? mediaIdOrObj.id ?? "";
    resolvedType = mediaIdOrObj.media_type ?? "image";
    resolvedIndex = mediaIdOrObj.face_index ?? 0;
  }
  const query = new URLSearchParams({
    media_id: String(resolvedId || ""),
    media_type: String(resolvedType || "image"),
    face_index: String(resolvedIndex ?? 0),
  });
  return `${API_BASE}/api/faces/preview?${query}`;
}

export async function searchSimilarFaces(mediaId, mediaType, faceIndex, limit = 40) {
  const res = await fetch(`${API_BASE}/api/faces/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_id: mediaId, media_type: mediaType, face_index: faceIndex, limit }),
  });
  if (!res.ok) throw await responseError(res, "Face search failed");
  return res.json();
}

export async function openMediaWith(mediaId, mediaType) {
  const res = await fetch(`${API_BASE}/api/media/open-with`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_id: mediaId, media_type: mediaType }),
  });
  if (!res.ok) throw await responseError(res, "Could not open the desktop app chooser");
  return res.json();
}

export async function fetchRecentPhotos(offset = 0, limit = 50, options = {}) {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  if (options.shuffle !== false) {
    params.append("shuffle", "1");
  } else {
    params.append("shuffle", "0");
  }
  if (options.seed != null) {
    params.append("seed", String(options.seed));
  }
  const res = await fetch(`${API_BASE}/api/photos?${params.toString()}`);
  if (!res.ok) throw new Error("Failed to fetch photos");
  return res.json();
}

const VIDEO_EXTENSIONS = new Set([".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"]);

export function isVideoMedia(media) {
  const extension = media?.path?.match(/\.[^./\\]+$/)?.[0]?.toLowerCase();
  return media?.media_type === "video" && VIDEO_EXTENSIONS.has(extension);
}

export function getPhotoUrl(photo, thumb = false) {
  if (!photo) return "";
  if (isVideoMedia(photo)) return "";
  const base = photo.path
    ? `${API_BASE}/api/photo?path=${encodeURIComponent(photo.path)}`
    : photo.id
      ? `${API_BASE}/api/photo?id=${encodeURIComponent(photo.id)}`
      : "";
  if (!base) return "";
  return thumb ? `${base}&thumb=1` : base;
}

export function createIndexEventSource(directoryPath, resume = false, frameRate = 0.5, adaptiveSampling = true) {
  const url = `${API_BASE}/api/index/stream?directory=${encodeURIComponent(directoryPath)}&resume=${resume}&frame_rate=${frameRate}&adaptive_sampling=${adaptiveSampling}`;
  return new EventSource(url);
}

export function getVideoUrl(media) {
  return isVideoMedia(media) ? `${API_BASE}/api/video?path=${encodeURIComponent(media.path)}` : "";
}

export async function fetchNewlyAddedMedia(limit = 200) {
  const res = await fetch(`${API_BASE}/api/media/newly-added?limit=${limit}`);
  if (!res.ok) throw await responseError(res, "Failed to load newly added files");
  return res.json();
}

export async function clearNewlyAddedMedia() {
  const res = await fetch(`${API_BASE}/api/media/newly-added/clear`, { method: "POST" });
  if (!res.ok) throw await responseError(res, "Failed to clear newly added list");
  return res.json();
}

export async function fetchAlbums(folder = null, options = {}) {
  const params = new URLSearchParams();
  if (folder) params.append("folder", folder);
  if (options.include_subfolders) params.append("include_subfolders", "1");
  if (options.offset != null) params.append("offset", String(options.offset));
  if (options.limit != null) params.append("limit", String(options.limit));
  if (options.sort_by) params.append("sort_by", options.sort_by);
  if (options.sort_order) params.append("sort_order", options.sort_order);

  const res = await fetch(`${API_BASE}/api/albums?${params.toString()}`);
  if (!res.ok) throw await responseError(res, "Failed to load album data");
  return res.json();
}

export async function openInExplorer(path) {
  const res = await fetch(`${API_BASE}/api/system/open-explorer?path=${encodeURIComponent(path)}`, {
    method: "POST",
  });
  if (!res.ok) throw await responseError(res, "Failed to open folder in explorer");
  return res.json();
}
