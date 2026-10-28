import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft, ArrowRight, AlertCircle, Clock3, ExternalLink, Film, RefreshCw,
  Image as ImageIcon, Loader2, Maximize2, Minimize2, Minus, Plus, RotateCcw,
  RotateCw, Search, SkipBack, SkipForward, X, Play, Pause, Volume2, VolumeX,
  PictureInPicture2, Tv, RotateCcw as Rewind10, RotateCw as Forward10, Hand,
} from "lucide-react";
import {
  fetchMediaFaces, fetchVideoFrames, getFacePreviewUrl, getPhotoUrl,
  getVideoFrameUrl, getVideoUrl, isVideoMedia, openMediaWith,
} from "../services/api";

function formatTime(value = 0) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

/**
 * Viewport-aware Sampled Frame Card: Only mounts the <img> when scrolled into the horizontal strip viewport.
 */
function LazyFrameThumb({ path, timestamp, isMatched, isCurrent, onSeek }) {
  const ref = useRef(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return undefined;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          setInView(entry.isIntersecting);
        }
      },
      { rootMargin: "0px 240px 0px 240px", threshold: 0.01 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <button
      ref={ref}
      type="button"
      onClick={() => onSeek(timestamp)}
      title={`Seek to ${formatTime(timestamp)}${isMatched ? " (Search Match ⭐)" : ""}`}
      className={`group relative w-36 shrink-0 overflow-hidden rounded-xl text-left transition-all cursor-pointer ${
        isMatched
          ? "bg-amber-500/20 shadow-lg shadow-amber-500/25"
          : isCurrent
          ? "bg-red-500/20 shadow-lg shadow-red-500/25"
          : "bg-black/50 hover:bg-white/10"
      }`}
    >
      <div className="relative aspect-video w-full bg-slate-950 overflow-hidden">
        {inView ? (
          <img
            src={getVideoFrameUrl(path, timestamp)}
            loading="lazy"
            decoding="async"
            draggable={false}
            alt={`Sampled frame at ${formatTime(timestamp)}`}
            onError={(e) => {
              if (!e.currentTarget.dataset.retried) {
                e.currentTarget.dataset.retried = "1";
                const image = e.currentTarget;
                setTimeout(() => {
                  if (!image.isConnected) return;
                  const base = image.src.split("&_r=")[0];
                  image.src = `${base}&_r=${Date.now()}`;
                }, 600);
              }
            }}
            className="h-full w-full object-contain bg-black transition-transform duration-200 group-hover:scale-105 select-none"
          />
        ) : (
          <div className="h-full w-full flex items-center justify-center bg-slate-900/70 text-[10px] font-mono text-slate-500">
            {formatTime(timestamp)}
          </div>
        )}
        {isMatched && (
          <span className="absolute top-1 right-1 rounded bg-amber-500 px-1.5 py-0.5 text-[9px] font-bold text-slate-950 shadow">
            MATCH ⭐
          </span>
        )}
      </div>
      <div className="flex items-center justify-between px-2.5 py-1 bg-slate-900/95">
        <span className="text-[10px] font-mono text-slate-200">{formatTime(timestamp)}</span>
        {isCurrent && <span className="text-[9px] text-red-400 font-semibold">Playing</span>}
      </div>
    </button>
  );
}

export default function MediaViewer({
  media, items, onClose, onSelect, onFindSimilar, onFindSimilarFace,
  faceSearchEnabled = { photos: false, videos: false }, showFilenames = false,
}) {
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [handToolActive, setHandToolActive] = useState(false);
  const [isDraggingPan, setIsDraggingPan] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [bufferedPct, setBufferedPct] = useState(0);
  const [theaterMode, setTheaterMode] = useState(true);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [drag, setDrag] = useState({ x: 0, y: 0 });
  const [framesOpen, setFramesOpen] = useState(false);
  const [frames, setFrames] = useState([]);
  const frameRequestRef = useRef(null);
  const [frameTotal, setFrameTotal] = useState(0);
  const [frameLoading, setFrameLoading] = useState(false);
  const [frameError, setFrameError] = useState("");
  const [sourceFps, setSourceFps] = useState(30);
  const [duration, setDuration] = useState(0);
  const [playerTime, setPlayerTime] = useState(0);
  const [jumpValue, setJumpValue] = useState("");
  const [hoverInfo, setHoverInfo] = useState(null); // { pct, time, nearestSampleTs }
  const [isScrubbing, setIsScrubbing] = useState(false);
  const [facePanelOpen, setFacePanelOpen] = useState(false);
  const [faces, setFaces] = useState([]);
  const [faceLoading, setFaceLoading] = useState(false);
  const [faceSearchLoading, setFaceSearchLoading] = useState(null);
  const [faceError, setFaceError] = useState("");
  const [openNotice, setOpenNotice] = useState("");
  const [videoError, setVideoError] = useState(null);
  const [imageError, setImageError] = useState(false);

  // Netflix-style auto-hide controls state
  const [controlsVisible, setControlsVisible] = useState(true);
  const hideControlsTimerRef = useRef(null);
  const controlsHoveredRef = useRef(false);
  const controlsFocusedRef = useRef(false);

  const videoRef = useRef(null);
  const containerRef = useRef(null);
  const timelineRef = useRef(null);
  const pointerRef = useRef(null);
  const didPanMoveRef = useRef(false);

  const video = isVideoMedia(media);
  const mediaType = video ? "video" : "image";
  const currentIndex = items.findIndex((item) => item.id === media?.id);
  const canFindFaces = Boolean(faceSearchEnabled?.[video ? "videos" : "photos"]);
  const canPan = handToolActive || zoom > 1 || drag.x !== 0 || drag.y !== 0;

  // Keep controls visible whenever user is interacting with any tool, drawer, input, or hovering the HUD
  const shouldKeepControlsPinned = useCallback(() => {
    return Boolean(
      controlsHoveredRef.current ||
      controlsFocusedRef.current ||
      isScrubbing ||
      facePanelOpen ||
      framesOpen ||
      isDraggingPan ||
      pointerRef.current ||
      (video && !isPlaying)
    );
  }, [isScrubbing, facePanelOpen, framesOpen, isDraggingPan, video, isPlaying]);

  // Reset and schedule auto-hide only when cursor is idle on the media stage and no tool is active
  const revealControls = useCallback(() => {
    setControlsVisible(true);
    if (hideControlsTimerRef.current) {
      clearTimeout(hideControlsTimerRef.current);
    }
    hideControlsTimerRef.current = setTimeout(() => {
      if (!shouldKeepControlsPinned()) {
        setControlsVisible(false);
      }
    }, 3200);
  }, [shouldKeepControlsPinned]);

  useEffect(() => {
    if (!media) return undefined;
    revealControls();
    return () => {
      if (hideControlsTimerRef.current) clearTimeout(hideControlsTimerRef.current);
    };
  }, [media?.id, isScrubbing, facePanelOpen, framesOpen, isPlaying, revealControls]);

  useEffect(() => {
    setZoom(1);
    setRotation(0);
    setDrag({ x: 0, y: 0 });
    setHandToolActive(false);
    setIsDraggingPan(false);
    setPlaybackRate(1);
    setIsPlaying(false);
    setBufferedPct(0);
    setHoverInfo(null);
    setFrames([]);
    setFrameTotal(0);
    setFrameError("");
    setFaces([]);
    setFacePanelOpen(false);
    setFaceError("");
    setOpenNotice("");
    setVideoError(null);
    setImageError(false);
    const initialTime = Number(media?.matched_timestamp ?? media?.timestamp ?? 0) || 0;
    setPlayerTime(initialTime);
    setDuration(Number(media?.duration || 0) || 0);
    setJumpValue(formatTime(initialTime));
  }, [media?.id, media?.path]);

  const loadFrames = useCallback(async (offset = 0, batchSize = 60) => {
    if (!media?.path) return;
    frameRequestRef.current?.abort();
    const controller = new AbortController();
    frameRequestRef.current = controller;
    setFrameLoading(true);
    setFrameError("");
    try {
      const response = await fetchVideoFrames(media.path, offset, batchSize, controller.signal);
      if (controller.signal.aborted) return;
      setFrames((current) => offset === 0 ? response.frames || [] : [...current, ...(response.frames || [])]);
      setFrameTotal(response.total || 0);
      if (response.source_fps > 0) setSourceFps(response.source_fps);
      if (response.duration > 0) setDuration(response.duration);
    } catch (error) {
      if (error.name !== "AbortError") setFrameError(error.message);
    } finally {
      if (frameRequestRef.current === controller) setFrameLoading(false);
    }
  }, [media?.path]);

  useEffect(() => {
    if (video && media?.path) {
      loadFrames(0, 2000);
    }
    return () => frameRequestRef.current?.abort();
  }, [video, media?.path, loadFrames]);

  useEffect(() => {
    if (!video || !videoRef.current) return undefined;
    const player = videoRef.current;
    const startAt = Number(media?.matched_timestamp ?? media?.timestamp ?? 0) || 0;
    const seek = () => {
      if (startAt > 0) player.currentTime = startAt;
      if (Number.isFinite(player.duration) && player.duration > 0) {
        setDuration(Number(player.duration));
      }
      setPlayerTime(startAt);
      player.play().catch(() => {});
    };
    if (player.readyState >= 1) seek();
    else player.addEventListener("loadedmetadata", seek, { once: true });
    return () => player.removeEventListener("loadedmetadata", seek);
  }, [video, media?.id, media?.timestamp, media?.matched_timestamp]);

  const togglePlay = useCallback(() => {
    const player = videoRef.current;
    if (!player) return;
    if (player.paused) {
      player.play().catch(() => {});
    } else {
      player.pause();
    }
  }, []);

  const toggleMute = useCallback(() => {
    const player = videoRef.current;
    if (!player) return;
    const nextMuted = !player.muted;
    player.muted = nextMuted;
    setIsMuted(nextMuted);
  }, []);

  const handleVolumeChange = useCallback((nextVol) => {
    const player = videoRef.current;
    const clamped = Math.max(0, Math.min(1, Number(nextVol)));
    setVolume(clamped);
    if (player) {
      player.volume = clamped;
      player.muted = clamped === 0;
      setIsMuted(clamped === 0);
    }
  }, []);

  const seekTo = useCallback((time) => {
    const player = videoRef.current;
    if (!player) return;
    const maxDur = Number.isFinite(duration) && duration > 0 ? duration : (Number(player.duration) || Number.MAX_SAFE_INTEGER);
    const target = Math.max(0, Math.min(maxDur, Number(time) || 0));
    player.currentTime = target;
    setPlayerTime(target);
    setJumpValue(formatTime(target));
  }, [duration]);

  const stepFrame = useCallback((direction) => {
    const player = videoRef.current;
    if (player && !player.paused) player.pause();
    seekTo((player?.currentTime || 0) + direction / Math.max(1, sourceFps));
  }, [seekTo, sourceFps]);

  const toggleFullscreen = useCallback(() => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen?.().catch(() => {});
    } else {
      document.exitFullscreen?.().catch(() => {});
    }
  }, []);

  useEffect(() => {
    const onFsChange = () => {
      setIsFullscreen(Boolean(document.fullscreenElement));
      revealControls();
    };
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, [revealControls]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLSelectElement ||
        event.target instanceof HTMLTextAreaElement ||
        event.target?.isContentEditable ||
        event.target?.tagName === "BUTTON"
      ) return;
      revealControls();
      if (event.key === "Escape" && !document.fullscreenElement) {
        onClose();
        return;
      }
      if (event.key === "f" || event.key === "F") {
        event.preventDefault();
        toggleFullscreen();
        return;
      }
      if (event.key === "h" || event.key === "H") {
        event.preventDefault();
        setHandToolActive((prev) => !prev);
        return;
      }
      if (video) {
        if (event.key === " " || event.key === "k" || event.key === "K") {
          event.preventDefault();
          togglePlay();
          return;
        }
        if (event.key === "j" || event.key === "J" || event.key === "ArrowLeft") {
          event.preventDefault();
          seekTo((videoRef.current?.currentTime || 0) - 10);
          return;
        }
        if (event.key === "l" || event.key === "L" || event.key === "ArrowRight") {
          event.preventDefault();
          seekTo((videoRef.current?.currentTime || 0) + 10);
          return;
        }
        if (event.key === "m" || event.key === "M") {
          event.preventDefault();
          toggleMute();
          return;
        }
        if (event.key === "t" || event.key === "T") {
          event.preventDefault();
          setTheaterMode((v) => !v);
          return;
        }
        if (event.key === ",") {
          event.preventDefault();
          stepFrame(-1);
          return;
        }
        if (event.key === ".") {
          event.preventDefault();
          stepFrame(1);
          return;
        }
      } else {
        if (event.key === "ArrowLeft" && currentIndex > 0) onSelect(items[currentIndex - 1]);
        if (event.key === "ArrowRight" && currentIndex >= 0 && currentIndex < items.length - 1) onSelect(items[currentIndex + 1]);
        if (event.key === "+" || event.key === "=") setZoom((value) => Math.min(5, value + 0.25));
        if (event.key === "-") setZoom((value) => Math.max(0.25, value - 0.25));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [currentIndex, items, onClose, onSelect, video, togglePlay, seekTo, toggleMute, toggleFullscreen, stepFrame, revealControls]);

  // Smooth 60fps Hand/Move Tool pointer handlers
  const handleStagePointerDown = (event) => {
    if (event.button !== 0 && event.button !== 1) return;
    if (!canPan && event.button !== 1) return;
    event.preventDefault();
    didPanMoveRef.current = false;
    pointerRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: drag.x,
      originY: drag.y,
    };
    setIsDraggingPan(true);
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const handleStagePointerMove = (event) => {
    revealControls();
    if (!pointerRef.current) return;
    const dx = event.clientX - pointerRef.current.startX;
    const dy = event.clientY - pointerRef.current.startY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
      didPanMoveRef.current = true;
    }
    setDrag({
      x: pointerRef.current.originX + dx,
      y: pointerRef.current.originY + dy,
    });
  };

  const handleStagePointerUp = (event) => {
    if (!pointerRef.current) return;
    pointerRef.current = null;
    setIsDraggingPan(false);
    try {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    } catch {
      // Ignore if pointer capture already released
    }
  };

  // Nearest sampled frame lookup for Timeline Peek on hover
  const findNearestSample = useCallback((targetSec) => {
    if (!frames || frames.length === 0) return null;
    let best = frames[0];
    let bestDiff = Math.abs(targetSec - best);
    for (let i = 1; i < frames.length; i++) {
      const diff = Math.abs(targetSec - frames[i]);
      if (diff < bestDiff) {
        best = frames[i];
        bestDiff = diff;
      }
    }
    return best;
  }, [frames]);

  const computeTimelinePosition = useCallback((clientX) => {
    const bar = timelineRef.current;
    if (!bar || duration <= 0) return null;
    const rect = bar.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)));
    const time = pct * duration;
    const nearestSampleTs = findNearestSample(time);
    return { pct, time, nearestSampleTs };
  }, [duration, findNearestSample]);

  const handleTimelinePointerMove = (e) => {
    revealControls();
    const info = computeTimelinePosition(e.clientX);
    if (info) {
      setHoverInfo(info);
      if (isScrubbing) {
        seekTo(info.time);
      }
    }
  };

  const handleTimelinePointerDown = (e) => {
    revealControls();
    const info = computeTimelinePosition(e.clientX);
    if (info) {
      setIsScrubbing(true);
      seekTo(info.time);
      e.currentTarget.setPointerCapture?.(e.pointerId);
    }
  };

  const handleTimelinePointerUp = (e) => {
    if (isScrubbing) {
      setIsScrubbing(false);
      e.currentTarget.releasePointerCapture?.(e.pointerId);
    }
  };

  const toggleFrames = () => {
    const next = !framesOpen;
    setFramesOpen(next);
    if (next && frames.length === 0) loadFrames(0);
  };

  const togglePiP = async () => {
    try {
      if (document.pictureInPictureElement) {
        await document.exitPictureInPicture();
      } else if (videoRef.current) {
        await videoRef.current.requestPictureInPicture();
      }
    } catch {
      // Ignore if PiP is unsupported
    }
  };

  const showFaceChoices = async () => {
    if (!canFindFaces) return;
    if (facePanelOpen) {
      setFacePanelOpen(false);
      return;
    }
    setFacePanelOpen(true);
    setFaceLoading(true);
    setFaceError("");
    try {
      const result = await fetchMediaFaces(media.id, mediaType);
      setFaces(result.faces || []);
      if (!result.faces?.length) setFaceError("No indexed faces found for this item yet. Check the face-index progress in Settings.");
    } catch (error) {
      setFaceError(error.message);
      setFaces([]);
    } finally {
      setFaceLoading(false);
    }
  };

  const runFaceSearch = async (faceIndex) => {
    setFaceSearchLoading(faceIndex);
    setFaceError("");
    try {
      await onFindSimilarFace?.(media, faceIndex);
    } catch (error) {
      setFaceError(error.message || "Face search failed");
    } finally {
      setFaceSearchLoading(null);
    }
  };

  const openDesktopChooser = async () => {
    setOpenNotice("");
    try {
      await openMediaWith(media.id, mediaType);
      setOpenNotice("Windows app chooser opened.");
    } catch (error) {
      setOpenNotice(error.message);
    }
  };

  const progressPct = useMemo(() => {
    if (!duration || duration <= 0) return 0;
    return Math.min(100, Math.max(0, (playerTime / duration) * 100));
  }, [playerTime, duration]);

  if (!media) return null;
  const title = showFilenames ? media.filename : (video ? "Cinema Video Player" : "Cinema Image Viewer");
  const subtitle = showFilenames
    ? (video ? `${media.path} · match at ${formatTime(media.matched_timestamp ?? media.timestamp)}` : media.path)
    : (video ? `Best sampled-frame match at ${formatTime(media.matched_timestamp ?? media.timestamp)} · Total ${formatTime(duration)}` : "Scroll to zoom · Hand tool (H) to pan");

  return (
    <div
      className="fixed inset-0 z-50 bg-black/95 p-0 sm:p-2 backdrop-blur-xl animate-fade-in flex items-center justify-center"
      onClick={onClose}
      onMouseMove={revealControls}
    >
      <section
        ref={containerRef}
        onClick={(event) => event.stopPropagation()}
        onMouseMove={revealControls}
        className={`relative mx-auto flex flex-col overflow-hidden bg-black shadow-2xl transition-all duration-200 ${
          isFullscreen
            ? "h-screen w-screen max-w-none rounded-none"
            : theaterMode
            ? "h-[96vh] w-[97vw] max-w-[1780px] rounded-2xl"
            : "h-[90vh] w-[90vw] max-w-6xl rounded-2xl"
        } ${!controlsVisible ? "cursor-none" : ""}`}
      >
        {/* Full-Bleed Locked-Aspect Cinema Viewport (Never shifts when controls auto-hide!) */}
        <div
          onPointerDown={handleStagePointerDown}
          onPointerMove={handleStagePointerMove}
          onPointerUp={handleStagePointerUp}
          onPointerCancel={handleStagePointerUp}
          onWheel={(event) => {
            revealControls();
            if (!video || zoom > 1 || handToolActive) {
              event.preventDefault();
              if (event.deltaY < 0) setZoom((value) => Math.min(5, +(value + 0.15).toFixed(2)));
              else setZoom((value) => Math.max(0.25, +(value - 0.15).toFixed(2)));
            }
          }}
          style={{
            cursor: !controlsVisible
              ? "none"
              : isDraggingPan
              ? "grabbing"
              : canPan
              ? "grab"
              : video
              ? "pointer"
              : "default",
          }}
          className="relative flex h-full w-full flex-1 items-center justify-center overflow-hidden bg-black select-none"
        >
          {video ? (
            <video
              ref={videoRef}
              key={media.id}
              src={getVideoUrl(media)}
              playsInline
              preload="metadata"
              draggable={false}
              onClick={() => {
                if (didPanMoveRef.current) {
                  didPanMoveRef.current = false;
                  return;
                }
                togglePlay();
              }}
              onDoubleClick={toggleFullscreen}
              onPlay={() => setIsPlaying(true)}
              onPause={() => setIsPlaying(false)}
              onVolumeChange={(e) => {
                setVolume(e.currentTarget.volume);
                setIsMuted(e.currentTarget.muted);
              }}
              onProgress={(e) => {
                const v = e.currentTarget;
                if (v.buffered.length > 0 && v.duration > 0) {
                  const end = v.buffered.end(v.buffered.length - 1);
                  setBufferedPct(Math.min(100, (end / v.duration) * 100));
                }
              }}
              onTimeUpdate={(event) => {
                if (!isScrubbing) {
                  const value = event.currentTarget.currentTime;
                  setPlayerTime(value);
                  setJumpValue(formatTime(value));
                }
              }}
              onLoadedMetadata={(event) => {
                const d = Number(event.currentTarget.duration) || 0;
                if (d > 0) setDuration(d);
              }}
              onError={(e) => {
                const err = e.currentTarget.error;
                let msg = "Video playback failed.";
                if (err?.code === 4) {
                  msg = "This video container or codec (e.g. MKV/AVI) is not supported natively by web browsers.";
                } else if (err?.code === 3) {
                  msg = "Video decoding error or corrupted media file.";
                } else if (err?.code === 2) {
                  msg = "Network or file reading error.";
                }
                setVideoError(msg);
              }}
              style={{
                aspectRatio: media.width && media.height ? `${media.width} / ${media.height}` : "16 / 9",
                transform: `translate3d(${drag.x}px, ${drag.y}px, 0) scale(${zoom}) rotate(${rotation}deg)`,
                transition: isDraggingPan ? "none" : "transform 120ms ease-out",
              }}
              className="h-full w-full max-h-full max-w-full object-contain bg-black select-none"
            />
          ) : (
            <img
              src={getPhotoUrl(media)}
              alt={showFilenames ? (media.filename || "Selected image") : "Selected image"}
              draggable={false}
              onDragStart={(e) => e.preventDefault()}
              onError={() => setImageError(true)}
              onDoubleClick={() => {
                if (zoom > 1 || drag.x !== 0 || drag.y !== 0) {
                  setZoom(1);
                  setDrag({ x: 0, y: 0 });
                } else {
                  setZoom(2);
                  setHandToolActive(true);
                }
              }}
              style={{
                transform: `translate3d(${drag.x}px, ${drag.y}px, 0) scale(${zoom}) rotate(${rotation}deg)`,
                transition: isDraggingPan ? "none" : "transform 120ms ease-out",
              }}
              className="max-h-full max-w-full select-none object-contain pointer-events-none"
            />
          )}

          {/* Video Playback Fallback Overlay for unsupported browser codecs (MKV, AVI, etc.) */}
          {video && videoError && (
            <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-black/85 p-6 text-center backdrop-blur-md">
              <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-amber-500/20 text-amber-300">
                <AlertCircle size={28} />
              </div>
              <h3 className="text-base font-bold text-white mb-1">Cannot Play Video in Browser</h3>
              <p className="max-w-md text-xs text-slate-300 mb-5 leading-relaxed">{videoError}</p>
              <div className="flex flex-wrap items-center justify-center gap-3">
                <button
                  type="button"
                  onClick={openDesktopChooser}
                  className="rounded-xl bg-blue-600 hover:bg-blue-500 px-4 py-2 text-xs font-semibold text-white transition flex items-center gap-1.5 shadow-lg cursor-pointer"
                >
                  <ExternalLink size={14} /> Open in Desktop Player
                </button>
                {frames.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setFramesOpen(true)}
                    className="rounded-xl bg-white/10 hover:bg-white/20 px-4 py-2 text-xs font-semibold text-slate-200 transition flex items-center gap-1.5 cursor-pointer"
                  >
                    <Film size={14} /> View {frames.length} Sampled Frames
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Image Loading Fallback Overlay */}
          {!video && imageError && (
            <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-black/85 p-6 text-center backdrop-blur-md">
              <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-rose-500/20 text-rose-300">
                <AlertCircle size={28} />
              </div>
              <h3 className="text-base font-bold text-white mb-1">Image Not Available</h3>
              <p className="max-w-md text-xs text-slate-300 mb-5 leading-relaxed">
                The image file could not be loaded. It may have been moved, renamed, or corrupted.
              </p>
              <button
                type="button"
                onClick={openDesktopChooser}
                className="rounded-xl bg-white/10 hover:bg-white/20 px-4 py-2 text-xs font-semibold text-slate-200 transition flex items-center gap-1.5 cursor-pointer"
              >
                <ExternalLink size={14} /> Open with Desktop App
              </button>
            </div>
          )}
        </div>

        {/* Netflix-Style Top Overlay Header (Never auto-hides while hovering or using tools) */}
        <header
          onMouseEnter={() => {
            controlsHoveredRef.current = true;
            setControlsVisible(true);
            if (hideControlsTimerRef.current) clearTimeout(hideControlsTimerRef.current);
          }}
          onMouseLeave={() => {
            controlsHoveredRef.current = false;
            revealControls();
          }}
          onPointerDown={() => {
            controlsHoveredRef.current = true;
            setControlsVisible(true);
            if (hideControlsTimerRef.current) clearTimeout(hideControlsTimerRef.current);
          }}
          onClick={() => {
            setControlsVisible(true);
            revealControls();
          }}
          onFocusCapture={() => {
            controlsFocusedRef.current = true;
            setControlsVisible(true);
            if (hideControlsTimerRef.current) clearTimeout(hideControlsTimerRef.current);
          }}
          onBlurCapture={() => {
            controlsFocusedRef.current = false;
            revealControls();
          }}
          className={`absolute top-0 inset-x-0 z-30 flex flex-wrap items-center justify-between gap-3 bg-gradient-to-b from-black/90 via-black/55 to-transparent px-4 pt-3.5 pb-8 sm:px-6 transition-all duration-300 ${
            controlsVisible ? "opacity-100 translate-y-0" : "opacity-0 -translate-y-3 pointer-events-none"
          }`}
        >
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-sm font-bold text-white drop-shadow">{title}</h2>
              {video && duration > 0 && (
                <span className="shrink-0 rounded-md bg-red-500/20 px-2 py-0.5 text-[10px] font-mono font-semibold text-red-200">
                  {formatTime(duration)}
                </span>
              )}
              {media.matched_person_name && (
                <span className="shrink-0 rounded-full bg-blue-500/25 px-2.5 py-0.5 text-[10px] font-bold text-blue-200">
                  👤 {media.matched_person_name}
                </span>
              )}
            </div>
            <p className="mt-0.5 truncate text-[11px] text-slate-300/90 drop-shadow" title={showFilenames ? media.path : undefined}>{subtitle}</p>
          </div>

          <div className="flex shrink-0 flex-wrap items-center gap-1.5">
            {currentIndex > 0 && (
              <button
                type="button"
                aria-label="Previous media in gallery"
                title="Previous item in gallery"
                onClick={() => onSelect(items[currentIndex - 1])}
                className="viewer-button w-auto gap-1 px-2.5 text-xs bg-black/50 backdrop-blur-md"
              >
                <ArrowLeft size={14} /> Prev
              </button>
            )}
            {currentIndex >= 0 && currentIndex < items.length - 1 && (
              <button
                type="button"
                aria-label="Next media in gallery"
                title="Next item in gallery"
                onClick={() => onSelect(items[currentIndex + 1])}
                className="viewer-button w-auto gap-1 px-2.5 text-xs bg-black/50 backdrop-blur-md"
              >
                Next <ArrowRight size={14} />
              </button>
            )}

            {/* Dedicated Hand / Move Tool (Works on both Photos & Videos at any zoom level!) */}
            <button
              type="button"
              aria-label="Hand move tool"
              aria-pressed={handToolActive}
              title="Hand / Move Tool (H) — Drag to pan anywhere"
              onClick={() => {
                setHandToolActive((prev) => {
                  const next = !prev;
                  if (next && zoom === 1 && !video) {
                    setZoom(1.5);
                  }
                  return next;
                });
              }}
              className={`viewer-button w-auto gap-1.5 px-2.5 text-xs backdrop-blur-md ${
                handToolActive
                  ? "bg-amber-500/25 text-amber-200 shadow-md shadow-amber-500/20"
                  : "bg-black/50"
              }`}
            >
              <Hand size={14} />
              <span className="hidden sm:inline">Move</span>
            </button>

            <button
              type="button"
              aria-label="Zoom out"
              title="Zoom out (-)"
              onClick={() => setZoom((value) => Math.max(0.25, +(value - 0.25).toFixed(2)))}
              className="viewer-button bg-black/50 backdrop-blur-md"
            >
              <Minus size={15} />
            </button>
            <button
              type="button"
              aria-label="Reset zoom and pan"
              title="Reset zoom & position to 100%"
              onClick={() => {
                setZoom(1);
                setDrag({ x: 0, y: 0 });
                setHandToolActive(false);
              }}
              className="min-w-12 rounded-lg bg-black/50 px-2 py-1.5 text-center text-xs font-mono text-slate-200 hover:bg-white/10 backdrop-blur-md cursor-pointer"
            >
              {Math.round(zoom * 100)}%
            </button>
            <button
              type="button"
              aria-label="Zoom in"
              title="Zoom in (+)"
              onClick={() => setZoom((value) => Math.min(5, +(value + 0.25).toFixed(2)))}
              className="viewer-button bg-black/50 backdrop-blur-md"
            >
              <Plus size={15} />
            </button>

            {!video && (
              <>
                <button type="button" aria-label="Rotate image left" title="Rotate left" onClick={() => setRotation((value) => (value - 90 + 360) % 360)} className="viewer-button bg-black/50 backdrop-blur-md"><RotateCcw size={15} /></button>
                <button type="button" aria-label="Rotate image right" title="Rotate right" onClick={() => setRotation((value) => (value + 90) % 360)} className="viewer-button bg-black/50 backdrop-blur-md"><RotateCw size={15} /></button>
              </>
            )}

            {video && (
              <>
                <button
                  type="button"
                  onClick={toggleFrames}
                  aria-pressed={framesOpen}
                  title="Toggle sampled frames strip"
                  className={`viewer-button w-auto gap-1.5 px-3 text-xs backdrop-blur-md ${
                    framesOpen ? "bg-red-500/25 text-red-200" : "bg-black/50"
                  }`}
                >
                  <Film size={14} />
                  <span>Frames ({frames.length})</span>
                </button>
                <button
                  type="button"
                  aria-label="Theater mode"
                  title="Toggle wide theater mode (T)"
                  onClick={() => setTheaterMode((v) => !v)}
                  className={`viewer-button bg-black/50 backdrop-blur-md ${theaterMode ? "bg-blue-500/20 text-blue-300" : ""}`}
                >
                  <Tv size={15} />
                </button>
                <button
                  type="button"
                  aria-label="Picture in Picture"
                  title="Picture-in-Picture"
                  onClick={togglePiP}
                  className="viewer-button bg-black/50 backdrop-blur-md"
                >
                  <PictureInPicture2 size={15} />
                </button>
              </>
            )}

            <button type="button" aria-label="Reset view" title="Reset zoom, pan, and rotation" onClick={() => { setZoom(1); setDrag({ x: 0, y: 0 }); setRotation(0); setHandToolActive(false); }} className="viewer-button bg-black/50 backdrop-blur-md"><RefreshCw size={14} /></button>
            <button type="button" aria-label="Open in desktop application" title="Open with…" onClick={openDesktopChooser} className="viewer-button bg-black/50 backdrop-blur-md"><ExternalLink size={15} /></button>
            <button type="button" aria-label="Fullscreen" title="Fullscreen (F)" onClick={toggleFullscreen} className="viewer-button bg-black/50 backdrop-blur-md">
              {isFullscreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
            </button>
            <button type="button" aria-label="Close viewer" title="Close (Esc)" onClick={onClose} className="viewer-button bg-black/50 backdrop-blur-md hover:bg-rose-500/30 hover:text-rose-200"><X size={18} /></button>
          </div>
        </header>

        {/* Netflix-Style Bottom Overlay Stack (Timeline, Transport Controls, Frames Strip, Face Drawer) */}
        <div
          onMouseEnter={() => {
            controlsHoveredRef.current = true;
            setControlsVisible(true);
            if (hideControlsTimerRef.current) clearTimeout(hideControlsTimerRef.current);
          }}
          onMouseLeave={() => {
            controlsHoveredRef.current = false;
            revealControls();
          }}
          onPointerDown={() => {
            controlsHoveredRef.current = true;
            setControlsVisible(true);
            if (hideControlsTimerRef.current) clearTimeout(hideControlsTimerRef.current);
          }}
          onClick={() => {
            setControlsVisible(true);
            revealControls();
          }}
          onFocusCapture={() => {
            controlsFocusedRef.current = true;
            setControlsVisible(true);
            if (hideControlsTimerRef.current) clearTimeout(hideControlsTimerRef.current);
          }}
          onBlurCapture={() => {
            controlsFocusedRef.current = false;
            revealControls();
          }}
          className={`absolute bottom-0 inset-x-0 z-30 flex flex-col bg-gradient-to-t from-black/95 via-black/80 to-transparent pt-10 transition-all duration-300 ${
            controlsVisible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4 pointer-events-none"
          }`}
        >
          {/* Viewport-Windowed Sampled Frames Strip (When toggled open) */}
          {video && framesOpen && (
            <section className="mx-4 mb-2 rounded-2xl bg-black/85 px-4 py-2.5 backdrop-blur-xl shadow-2xl sm:mx-6">
              <div className="mb-1.5 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <h3 className="text-xs font-semibold text-white flex items-center gap-1.5">
                    <Film size={13} className="text-red-400" />
                    Indexed Sampled Frames ({frames.length})
                  </h3>
                  <span className="text-[10px] text-slate-400 hidden sm:inline">
                    Click any sampled moment to jump directly · Hover timeline below to peek
                  </span>
                </div>
                <span className="shrink-0 text-[10px] font-mono text-slate-400">
                  {frames.length} / {frameTotal} moments {duration > 0 ? `· covers up to ${formatTime(frames[frames.length - 1] || 0)} of ${formatTime(duration)}` : ""}
                </span>
              </div>
              {frameError && <p role="alert" className="mb-2 flex items-center gap-1.5 text-[11px] text-rose-300"><AlertCircle size={13} />{frameError}</p>}
              <div className="flex gap-2.5 overflow-x-auto pb-1.5 scrollbar-thin">
                {frames.map((timestamp) => {
                  const isMatched = Math.abs((media.matched_timestamp ?? -999) - timestamp) < 0.5;
                  const isCurrent = Math.abs(playerTime - timestamp) < 2.0;
                  return (
                    <LazyFrameThumb
                      key={timestamp}
                      path={media.path}
                      timestamp={timestamp}
                      isMatched={isMatched}
                      isCurrent={isCurrent}
                      onSeek={seekTo}
                    />
                  );
                })}
                {frameLoading && <div className="flex w-36 shrink-0 items-center justify-center text-red-300"><Loader2 size={18} className="animate-spin" /></div>}
              </div>
              {!frameLoading && frames.length < frameTotal && (
                <button onClick={() => loadFrames(frames.length, 60)} className="mt-1 rounded-lg bg-white/5 px-3 py-1 text-[11px] font-semibold text-slate-300 hover:bg-white/10 cursor-pointer">
                  Load more sampled frames ({Math.min(60, frameTotal - frames.length)} of {frameTotal - frames.length} remaining)
                </button>
              )}
            </section>
          )}

          {/* Netflix / YouTube-Grade Custom Timeline Scrubber with Hover Thumbnail Peek & Control Deck */}
          {video && (
            <div className="flex flex-col px-4 pb-3.5 sm:px-6">
              {/* Interactive Scrubber with Floating Thumbnail Peek */}
              <div className="relative mb-3 pt-1">
                {/* Floating Timeline Peek Card on Hover */}
                {hoverInfo && duration > 0 && (
                  <div
                    className="pointer-events-none absolute bottom-7 z-30 -translate-x-1/2 flex flex-col items-center"
                    style={{
                      left: `${Math.min(93, Math.max(7, hoverInfo.pct * 100))}%`,
                    }}
                  >
                    <div className="overflow-hidden rounded-xl bg-[#0b0e14]/95 shadow-2xl backdrop-blur-md">
                      {hoverInfo.nearestSampleTs !== null && (
                        <div className="relative h-24 w-44 bg-black">
                          <img
                            src={getVideoFrameUrl(media.path, hoverInfo.nearestSampleTs)}
                            alt="Timeline peek preview"
                            onError={(e) => {
                              if (!e.currentTarget.dataset.retried) {
                                e.currentTarget.dataset.retried = "1";
                                setTimeout(() => {
                                  const base = e.currentTarget.src.split("&_r=")[0];
                                  e.currentTarget.src = `${base}&_r=${Date.now()}`;
                                }, 600);
                              }
                            }}
                            className="h-full w-full object-contain bg-black"
                          />
                          <span className="absolute bottom-1 right-1 rounded bg-black/80 px-1.5 py-0.5 text-[9px] font-mono text-red-300">
                            Sample @ {formatTime(hoverInfo.nearestSampleTs)}
                          </span>
                        </div>
                      )}
                      <div className="flex items-center justify-center gap-2 px-2.5 py-1 text-[11px] font-mono font-bold text-white bg-slate-900">
                        <span>{formatTime(hoverInfo.time)}</span>
                        {Math.abs((media.matched_timestamp ?? -999) - hoverInfo.time) < 5 && (
                          <span className="text-[9px] text-amber-400">⭐ Match</span>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                {/* Scrubber Track */}
                <div
                  ref={timelineRef}
                  role="slider"
                  aria-label="Video timeline scrubber"
                  aria-valuemin={0}
                  aria-valuemax={Math.round(duration || 0)}
                  aria-valuenow={Math.round(playerTime || 0)}
                  tabIndex={0}
                  onPointerMove={handleTimelinePointerMove}
                  onPointerLeave={() => { if (!isScrubbing) setHoverInfo(null); }}
                  onPointerDown={handleTimelinePointerDown}
                  onPointerUp={handleTimelinePointerUp}
                  className="group relative h-2 hover:h-3.5 w-full rounded-full bg-white/20 cursor-pointer transition-all flex items-center"
                >
                  {/* Buffered Bar */}
                  <div
                    className="pointer-events-none absolute top-0 left-0 h-full rounded-full bg-white/30 transition-all"
                    style={{ width: `${bufferedPct}%` }}
                  />
                  {/* Hover Ghost Bar */}
                  {hoverInfo && (
                    <div
                      className="pointer-events-none absolute top-0 left-0 h-full rounded-full bg-white/35"
                      style={{ width: `${hoverInfo.pct * 100}%` }}
                    />
                  )}
                  {/* Netflix Red Active Playhead Fill */}
                  <div
                    className="pointer-events-none absolute top-0 left-0 h-full rounded-full bg-[#e50914]"
                    style={{ width: `${progressPct}%` }}
                  />

                  {/* Sampled Frame Markers along Timeline */}
                  {duration > 0 && frames.map((ts) => {
                    const pct = Math.min(100, Math.max(0, (ts / duration) * 100));
                    const isMatched = Math.abs((media.matched_timestamp ?? -999) - ts) < 0.5;
                    return (
                      <div
                        key={ts}
                        title={`Sampled frame at ${formatTime(ts)}${isMatched ? " (Search Match ⭐)" : ""}`}
                        onClick={(e) => { e.stopPropagation(); seekTo(ts); }}
                        className={`absolute top-1/2 -translate-y-1/2 -translate-x-1/2 rounded-full transition-transform hover:scale-150 ${
                          isMatched
                            ? "w-3 h-3 bg-amber-400 z-20 shadow-md shadow-amber-400/60"
                            : "w-1.5 h-1.5 bg-white/75 hover:bg-white z-10"
                        }`}
                        style={{ left: `${pct}%` }}
                      />
                    );
                  })}

                  {/* Playhead Scrub Knob */}
                  <div
                    className="pointer-events-none absolute top-1/2 -translate-y-1/2 -translate-x-1/2 h-4 w-4 rounded-full bg-[#e50914] shadow-lg transition-transform group-hover:scale-125 z-20"
                    style={{ left: `${progressPct}%` }}
                  />
                </div>
              </div>

              {/* Primary Transport & Audio/Speed Controls */}
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={togglePlay}
                    aria-label={isPlaying ? "Pause" : "Play"}
                    title={isPlaying ? "Pause (K / Space)" : "Play (K / Space)"}
                    className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#e50914] hover:bg-[#f6121d] text-white shadow-md shadow-red-600/30 transition cursor-pointer"
                  >
                    {isPlaying ? <Pause size={16} className="fill-current" /> : <Play size={16} className="fill-current ml-0.5" />}
                  </button>

                  <button
                    type="button"
                    onClick={() => seekTo((videoRef.current?.currentTime || 0) - 10)}
                    title="Rewind 10 seconds (J / ←)"
                    className="rounded-xl bg-white/10 px-2.5 py-1.5 text-xs font-semibold text-slate-100 hover:bg-white/20 flex items-center gap-1 cursor-pointer"
                  >
                    <Rewind10 size={13} /> 10s
                  </button>
                  <button
                    type="button"
                    onClick={() => seekTo((videoRef.current?.currentTime || 0) + 10)}
                    title="Forward 10 seconds (L / →)"
                    className="rounded-xl bg-white/10 px-2.5 py-1.5 text-xs font-semibold text-slate-100 hover:bg-white/20 flex items-center gap-1 cursor-pointer"
                  >
                    10s <Forward10 size={13} />
                  </button>

                  <button type="button" aria-label="Step back one frame" title="Previous frame (,)" onClick={() => stepFrame(-1)} className="viewer-button h-8 w-8 bg-white/10"><SkipBack size={14} /></button>
                  <button type="button" aria-label="Step forward one frame" title="Next frame (.)" onClick={() => stepFrame(1)} className="viewer-button h-8 w-8 bg-white/10"><SkipForward size={14} /></button>

                  {/* Volume Control */}
                  <div className="flex items-center gap-1.5 rounded-xl bg-white/10 px-2.5 py-1">
                    <button type="button" onClick={toggleMute} title="Mute / Unmute (M)" className="text-slate-200 hover:text-white cursor-pointer">
                      {isMuted || volume === 0 ? <VolumeX size={15} className="text-rose-400" /> : <Volume2 size={15} />}
                    </button>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      value={isMuted ? 0 : volume}
                      onChange={(e) => handleVolumeChange(e.target.value)}
                      aria-label="Volume"
                      className="w-16 accent-[#e50914] cursor-pointer h-1"
                    />
                  </div>

                  {/* Current Time / Total Duration */}
                  <span className="inline-flex items-center gap-1.5 text-xs font-mono font-semibold text-slate-100 ml-1">
                    <Clock3 size={13} className="text-red-400" />
                    <span>{formatTime(playerTime)}</span>
                    <span className="text-slate-500">/</span>
                    <span className="text-slate-300">{duration ? formatTime(duration) : "--:--"}</span>
                  </span>
                </div>

                {/* Right-Side Controls: Jump Timestamp, Playback Speed, Similar Face Search, Fullscreen */}
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex items-center gap-1">
                    <span className="text-[10px] text-slate-300">Jump:</span>
                    <input
                      aria-label="Jump to video timestamp"
                      value={jumpValue}
                      onChange={(event) => setJumpValue(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          const parts = jumpValue.split(":").map(Number);
                          const seconds = parts.reduce((total, part) => total * 60 + (Number.isFinite(part) ? part : 0), 0);
                          seekTo(seconds);
                        }
                      }}
                      className="w-20 rounded-lg bg-black/60 px-2 py-1 text-center text-xs font-mono text-white focus:outline-none"
                      title="Enter time (e.g. 31:00 or 1:05:00) and press Enter"
                    />
                  </div>

                  <select
                    aria-label="Playback speed"
                    value={playbackRate}
                    onChange={(event) => {
                      const rate = Number(event.target.value);
                      setPlaybackRate(rate);
                      if (videoRef.current) videoRef.current.playbackRate = rate;
                    }}
                    className="h-8 rounded-xl bg-black/70 px-2.5 text-xs font-semibold text-slate-100 cursor-pointer focus:outline-none"
                  >
                    {[0.5, 0.75, 1, 1.25, 1.5, 2].map((rate) => <option key={rate} value={rate}>{rate}× Speed</option>)}
                  </select>

                  <button
                    type="button"
                    onClick={showFaceChoices}
                    disabled={!canFindFaces}
                    title={canFindFaces ? "Find media with a similar face" : "Enable face matching for videos in Settings"}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-white/10 px-3 py-1.5 text-xs font-semibold text-slate-100 transition hover:bg-white/20 disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer"
                  >
                    <Search size={13} /> Similar Face
                  </button>

                  <button
                    type="button"
                    onClick={toggleFullscreen}
                    title="Fullscreen (F)"
                    className="viewer-button h-8 w-8 bg-white/10"
                  >
                    {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Image Footer (when viewing photos — also auto-hides after cursor inactivity) */}
          {!video && (
            <footer className="flex flex-wrap items-center justify-between gap-3 px-4 pb-4 sm:px-6">
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
                <span className="rounded-full bg-blue-500/20 px-2.5 py-1 text-blue-200 font-semibold">Photo</span>
                {media.width && media.height && <span>{media.width} × {media.height}</span>}
                {media.accuracy_percent !== undefined && <span title="Ranking guidance, not a probability">Relevance {media.accuracy_percent}%</span>}
                {handToolActive && <span className="rounded-full bg-amber-500/20 px-2.5 py-1 text-amber-200 font-semibold">✋ Hand Tool Active — Drag to move</span>}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" onClick={() => onFindSimilar(media)} className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-blue-500 cursor-pointer shadow-lg"><Search size={14} /> Find similar</button>
                <button type="button" onClick={showFaceChoices} disabled={!canFindFaces} title={canFindFaces ? "Find media with a similar face" : "Enable face matching for photos in Settings"} className="inline-flex items-center gap-2 rounded-xl bg-white/10 px-3 py-2 text-xs font-semibold text-slate-100 transition hover:bg-white/20 disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer"><Search size={14} /> Find similar face</button>
                {openNotice && <span role="status" className="max-w-44 truncate text-[10px] text-slate-300">{openNotice}</span>}
              </div>
            </footer>
          )}

          {facePanelOpen && (
            <section className="mx-4 mb-3 rounded-2xl bg-[#0d1117]/95 px-4 py-3 backdrop-blur-xl shadow-2xl sm:mx-6">
              <div className="flex items-center justify-between gap-3">
                <div><h3 className="text-xs font-semibold text-white">Choose a face to search</h3><p className="mt-0.5 text-[10px] text-slate-400">Face vectors stay on this device. Similarity is a ranking hint.</p></div>
                <button type="button" aria-label="Close face choices" onClick={() => setFacePanelOpen(false)} className="viewer-button h-7 w-7"><X size={14} /></button>
              </div>
              {faceLoading && <p className="mt-3 flex items-center gap-2 text-xs text-slate-300"><Loader2 size={14} className="animate-spin" />Loading detected faces…</p>}
              {faceError && <p role="alert" className="mt-3 flex items-center gap-1.5 text-[11px] text-amber-200"><AlertCircle size={13} />{faceError}</p>}
              {faces.length > 0 && <div className="mt-3 flex gap-3 overflow-x-auto pb-1">
                {faces.map((face) => (
                  <div key={face.face_index} className="w-32 shrink-0 rounded-xl bg-white/[0.04] p-2">
                    <img src={getFacePreviewUrl(media.id, mediaType, face.face_index)} alt={`Detected face ${face.face_index + 1}`} className="h-24 w-full rounded-lg bg-black object-cover" />
                    <button type="button" disabled={faceSearchLoading !== null} onClick={() => runFaceSearch(face.face_index)} className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-2 py-2 text-[10px] font-semibold text-white hover:bg-blue-500 disabled:opacity-60 cursor-pointer">
                      {faceSearchLoading === face.face_index ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
                      Search face {face.face_index + 1}
                    </button>
                  </div>
                ))}
              </div>}
            </section>
          )}
        </div>
      </section>
    </div>
  );
}
