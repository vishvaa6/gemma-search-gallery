import React, { lazy, Suspense, useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  Search,
  Camera,
  FolderOpen,
  X,
  Zap,
  Sparkles,
  Sliders,
  CheckCircle2,
  Loader2,
  HardDrive,
  ChevronRight,
  Play,
  Pause,
  AlertCircle,
  Clock,
  Layers,
  StopCircle,
  Folder,
  ArrowLeft,
  Check,
  Settings,
  Trash2,
  Database,
  RefreshCw,
  Video as VideoIcon,
  ShieldCheck,
  Cpu,
  Eye,
  FolderTree,
  Shuffle,
  ExternalLink,
  Grid,
  ArrowUp,
} from "lucide-react";

import {
  fetchSystemStatus,
  searchText,
  searchByImage,
  fetchRecentPhotos,
  fetchSimilarPhotos,
  getPhotoUrl,
  createIndexEventSource,
  openFolderPicker,
  browseDirectory,
  inspectFolder,
  fetchIndexingState,
  checkNewMedia,
  pauseIndexing,
  resumeIndexing,
  cancelIndexing,
  clearAllIndexing,
  auditImageProcessing,
  isVideoMedia,
  fetchSettings,
  updateSettings,
  searchSimilarFaces,
  getFacePreviewUrl,
  fetchNewlyAddedMedia,
  clearNewlyAddedMedia,
  fetchAlbums,
  openInExplorer,
} from "./services/api";
import { getVideoUrl, getVideoFrameUrl } from "./services/api";
const MediaViewer = lazy(() => import("./components/MediaViewer"));

const MRL_OPTIONS = [
  { dim: 128, label: "128d", desc: "Smallest reranking vectors" },
  { dim: 256, label: "256d", desc: "Compact reranking vectors" },
  { dim: 512, label: "512d", desc: "Larger reranking vectors" },
  { dim: 768, label: "768d", desc: "Full Fidelity & Maximum Accuracy", default: true },
];

function formatTime(value = 0) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

function PreferenceSwitch({ label, description, checked, disabled, onChange }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl bg-[#1b222c]/70 backdrop-blur-md p-4 shadow-inner">
      <div className="min-w-0">
        <p className="text-xs font-semibold text-slate-100">{label}</p>
        <p className="mt-1 text-[11px] leading-relaxed text-slate-400">{description}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 shrink-0 rounded-full transition focus-visible:outline-none disabled:cursor-wait disabled:opacity-50 ${checked ? "bg-blue-500/45 shadow-[0_0_14px_rgba(59,130,246,0.35)]" : "bg-black/55 backdrop-blur-md"}`}
      >
        <span className={`absolute top-1 h-4 w-4 rounded-full shadow transition-all ${checked ? "left-[23px] bg-blue-200" : "left-1 bg-slate-300"}`} />
      </button>
    </div>
  );
}

/**
 * Viewport-only Media Card:
 * Uses IntersectionObserver to only mount <img> / <video> elements when the card
 * is inside or near the viewport, preventing the browser from decoding off-screen
 * images or opening dozens of concurrent video streams.
 * Wrapped with React.memo and uses a 350ms hover-preview debounce to prevent
 * UI stutter/lag when scrolling or moving the mouse across cards.
 */
const ViewportMediaCard = React.memo(function ViewportMediaCard({
  photo,
  onSelect,
  showFilenames,
  videoDuration,
  onHoverMediaChange,
}) {
  const cardRef = useRef(null);
  const [inView, setInView] = useState(false);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [showVideoPreview, setShowVideoPreview] = useState(false);
  const hoverTimerRef = useRef(null);

  useEffect(() => {
    const el = cardRef.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]) {
          setInView(entries[0].isIntersecting);
        }
      },
      { rootMargin: "250px 0px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const isVideo = isVideoMedia(photo);

  useEffect(() => {
    return () => {
      if (hoverTimerRef.current) {
        clearTimeout(hoverTimerRef.current);
        hoverTimerRef.current = null;
      }
    };
  }, []);

  const handleMouseEnter = () => {
    setIsHovered(true);
    onHoverMediaChange?.(photo, true);
    if (isVideo) {
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
      // 350ms hover delay prevents accidental video decoding/streaming when just brushing past
      hoverTimerRef.current = setTimeout(() => {
        setShowVideoPreview(true);
      }, 350);
    }
  };

  const handleMouseLeave = () => {
    setIsHovered(false);
    onHoverMediaChange?.(photo, false);
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
    setShowVideoPreview(false);
  };

  const accuracy = photo.accuracy_percent;
  let badgeColor = "bg-black/60 backdrop-blur-md text-slate-200";
  if (accuracy >= 90) badgeColor = "bg-emerald-500/25 text-emerald-200 backdrop-blur-md font-bold shadow-[0_0_16px_rgba(16,185,129,0.25)]";
  else if (accuracy >= 80) badgeColor = "bg-blue-500/25 text-blue-200 backdrop-blur-md font-bold shadow-[0_0_16px_rgba(59,130,246,0.25)]";
  else if (accuracy >= 65) badgeColor = "bg-amber-500/25 text-amber-200 backdrop-blur-md font-bold shadow-[0_0_14px_rgba(245,158,11,0.2)]";

  const matchedTs = photo.matched_timestamp ?? photo.timestamp ?? 0;
  const resolvedDuration = photo.duration || videoDuration;

  return (
    <div
      ref={cardRef}
      onClick={() => onSelect(photo)}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          onSelect(photo);
        }
      }}
      style={{ contentVisibility: "auto", containIntrinsicSize: "auto 240px" }}
      className="group relative flex flex-col bg-[#141a22]/85 rounded-2xl overflow-hidden hover:bg-[#1b222c]/95 shadow-[0_8px_28px_rgba(0,0,0,0.45)] hover:shadow-[0_14px_40px_rgba(0,0,0,0.7)] transition-all duration-200 cursor-pointer"
    >
      <div className="relative aspect-video w-full overflow-hidden bg-[#0d0f12]">
        {!inView ? (
          <div className="h-full w-full bg-[#12161c]/80 animate-pulse flex items-center justify-center text-slate-600">
            {isVideo ? <VideoIcon size={22} /> : <Sparkles size={18} />}
          </div>
        ) : isVideo ? (
          <>
            {!imgLoaded && (
              <div className="absolute inset-0 bg-[#12161c] animate-pulse flex items-center justify-center text-slate-600 z-0">
                <VideoIcon size={22} className="opacity-40" />
              </div>
            )}
            <img
              src={getVideoFrameUrl(photo.path, matchedTs)}
              alt={photo.filename || "Video thumbnail"}
              loading="lazy"
              decoding="async"
              onLoad={() => setImgLoaded(true)}
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
              className={`h-full w-full object-cover transition-all duration-300 group-hover:scale-105 ${imgLoaded ? 'opacity-100' : 'opacity-0'}`}
            />
            {showVideoPreview && (
              <video
                src={`${getVideoUrl(photo)}#t=${Math.max(0, matchedTs)}`}
                muted
                autoPlay
                playsInline
                preload="metadata"
                className="absolute inset-0 h-full w-full object-cover z-10"
              />
            )}
          </>
        ) : (
          <>
            {!imgLoaded && (
              <div className="absolute inset-0 bg-[#12161c] animate-pulse flex items-center justify-center text-slate-600 z-0">
                <Sparkles size={18} className="opacity-40" />
              </div>
            )}
            <img
              src={getPhotoUrl(photo, true)}
              alt={photo.filename || "Photo"}
              loading="lazy"
              decoding="async"
              onLoad={() => setImgLoaded(true)}
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
              className={`h-full w-full object-cover transition-all duration-300 group-hover:scale-105 ${imgLoaded ? 'opacity-100' : 'opacity-0'}`}
            />
          </>
        )}

        {isVideo && (
          <span className="absolute bottom-2 left-2 inline-flex items-center gap-1.5 rounded-full bg-black/55 backdrop-blur-md px-2.5 py-1 text-[10px] font-mono font-medium text-white shadow-md">
            <VideoIcon size={12} className="text-blue-300" />
            <span>{formatTime(matchedTs)}</span>
            {resolvedDuration ? (
              <>
                <span className="text-slate-400">/</span>
                <span className="text-slate-200">{formatTime(resolvedDuration)}</span>
              </>
            ) : null}
          </span>
        )}

        {isHovered && (
          <span className="pointer-events-none absolute bottom-2 right-2 hidden sm:inline-flex items-center gap-1 rounded-full bg-black/55 backdrop-blur-md px-2.5 py-0.5 text-[10px] font-medium text-blue-200 shadow-sm">
            <Eye size={11} className="text-blue-300" />
            <span>Hold Space</span>
          </span>
        )}

        {accuracy !== undefined && (
          <div className="absolute top-2.5 right-2.5">
            <span className={`px-2.5 py-1 rounded-full text-xs flex items-center gap-1 ${badgeColor}`}>
              <Sparkles className="w-3 h-3" />
              <span title="Ranking guidance based on similarity; this is not a probability.">{accuracy}% relevance</span>
            </span>
          </div>
        )}
      </div>

      <div className="p-3 bg-[#12161c]/65">
        {showFilenames && (
          <p title={photo.filename} className="text-xs font-semibold text-slate-200 truncate group-hover:text-blue-300 transition-colors">
            {photo.filename}
          </p>
        )}
        <div className="flex items-center justify-between text-[11px] text-slate-400 mt-1">
          <span>
            {isVideo
              ? `Video · ${resolvedDuration ? `${formatTime(resolvedDuration)} total · ` : ""}match at ${formatTime(matchedTs)}`
              : photo.width && photo.height
              ? `${photo.width}×${photo.height}`
              : "Photo"}
          </span>
          {photo.similarity !== undefined && (
            <span className="font-mono text-emerald-300 text-[10px] bg-emerald-500/15 px-1.5 py-0.2 rounded-full">
              Sim: {photo.similarity}
            </span>
          )}
        </div>
      </div>
    </div>
  );
});

export default function App() {
  const [activePage, setActivePage] = useState("gallery"); // "gallery" | "albums" | "newly_added" | "settings"
  // Session seed for deterministic infinite-scrolling with random shuffling on every page refresh/load
  const sessionSeedRef = useRef(Math.floor(Math.random() * 1000000));
  const [isShuffling, setIsShuffling] = useState(false);
  const [query, setQuery] = useState("");
  const [activeSearch, setActiveSearch] = useState(null);
  const [searchError, setSearchError] = useState("");
  // Default to 768d MRL for highest accuracy
  const [activeMrl, setActiveMrl] = useState(768);
  const [minAccuracy, setMinAccuracy] = useState(0);
  const [photos, setPhotos] = useState([]);
  const [hasMoreMedia, setHasMoreMedia] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [latencyMs, setLatencyMs] = useState(null);
  const [system, setSystem] = useState(null);
  const [isServerDisconnected, setIsServerDisconnected] = useState(false);
  const consecutiveFailuresRef = useRef(0);
  const [selectedPhoto, setSelectedPhoto] = useState(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [frameRate, setFrameRate] = useState(1 / 30);
  const [adaptiveSampling, setAdaptiveSampling] = useState(true);
  const [videoDurations, setVideoDurations] = useState({});
  const [imageAudit, setImageAudit] = useState(null);
  const [isAuditingImages, setIsAuditingImages] = useState(false);
  const [settings, setSettings] = useState({
    show_filenames: false,
    face_search_photos_enabled: false,
    face_search_videos_enabled: false,
  });
  const [faceIndexState, setFaceIndexState] = useState({ photos: null, videos: null });
  const [settingsNotice, setSettingsNotice] = useState("");
  const [isSavingSettings, setIsSavingSettings] = useState(false);

  const loadMoreSentinelRef = useRef(null);

  // Spacebar + Hover Quick Peek Popup State
  const [hoveredMedia, setHoveredMedia] = useState(null);
  const [isSpaceHeld, setIsSpaceHeld] = useState(false);
  const [isPointerInPeek, setIsPointerInPeek] = useState(false);
  const hoveredMediaRef = useRef(null);
  const isSpaceHeldRef = useRef(false);
  const isPointerInPeekRef = useRef(false);
  const isCardHoveredRef = useRef(false);
  const searchPreviewObjectUrlRef = useRef(null);

  const closeQuickPeek = useCallback(() => {
    isSpaceHeldRef.current = false;
    isPointerInPeekRef.current = false;
    setIsSpaceHeld(false);
    setIsPointerInPeek(false);
    if (!isCardHoveredRef.current) {
      hoveredMediaRef.current = null;
      setHoveredMedia(null);
    }
  }, []);

  const handleHoverMediaChange = useCallback((item, isEntering) => {
    if (isEntering) {
      isCardHoveredRef.current = true;
      hoveredMediaRef.current = item;
      // Only trigger React state update if Quick Peek is actively open
      if (isSpaceHeldRef.current || isPointerInPeekRef.current) {
        setHoveredMedia(item);
      }
    } else if (hoveredMediaRef.current?.id === item?.id) {
      isCardHoveredRef.current = false;
      // Do NOT close or clear hoveredMedia if the Quick Peek popup is open or cursor is entering the popup window!
      if (!isSpaceHeldRef.current && !isPointerInPeekRef.current) {
        hoveredMediaRef.current = null;
        setHoveredMedia(null);
      }
    }
  }, []);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape" && (isSpaceHeldRef.current || isPointerInPeekRef.current)) {
        closeQuickPeek();
        return;
      }
      if (event.code !== "Space" && event.key !== " ") return;
      if (selectedPhoto) return;
      const tag = event.target?.tagName;
      if (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        event.target?.isContentEditable ||
        event.target?.closest("input, textarea, select, [contenteditable='true'], [role='slider'], button")
      ) return;
      if (!hoveredMediaRef.current) return;
      event.preventDefault();
      if (!event.repeat) {
        isSpaceHeldRef.current = true;
        setHoveredMedia(hoveredMediaRef.current);
        setIsSpaceHeld(true);
      }
    };
    const onKeyUp = (event) => {
      if (event.code === "Space" || event.key === " ") {
        isSpaceHeldRef.current = false;
        setIsSpaceHeld(false);
        // Only clear hoveredMedia if cursor is neither inside the Quick Peek window nor on the card
        if (!isPointerInPeekRef.current && !isCardHoveredRef.current) {
          hoveredMediaRef.current = null;
          setHoveredMedia(null);
        }
      }
    };
    const onBlur = () => {
      isSpaceHeldRef.current = false;
      setIsSpaceHeld(false);
    };

    window.addEventListener("keydown", onKeyDown, { passive: false });
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
  }, [selectedPhoto, closeQuickPeek]);

  // Clear Indexing State
  const [isClearingIndex, setIsClearingIndex] = useState(false);
  const [clearNotice, setClearNotice] = useState(null);

  // Folder & Indexing State
  const [folderModalOpen, setFolderModalOpen] = useState(false);
  const [folderInput, setFolderInput] = useState("");
  const [folderNotice, setFolderNotice] = useState("");
  const [isPickingFolder, setIsPickingFolder] = useState(false);

  // In-App Directory Navigator State
  const [browseData, setBrowseData] = useState({
    current_path: "",
    parent_path: null,
    drives: [],
    presets: [],
    subdirectories: [],
    image_count: 0,
  });
  const [isBrowsingDir, setIsBrowsingDir] = useState(false);

  // Instant Folder Inspection & Estimate State
  const [inspectedFolder, setInspectedFolder] = useState(null);
  const [isInspecting, setIsInspecting] = useState(false);
  const [inspectionProgress, setInspectionProgress] = useState(null);

  // Live Indexing State
  const [indexingState, setIndexingState] = useState(null); // { active, folder, current, indexed, total, percent, speed, eta, isPaused, done }

  // App Restart Resumable Session State
  const [resumableSession, setResumableSession] = useState(null);
  const autoResumeStartedRef = useRef(false);
  const pendingAutoResumeFolderRef = useRef(null);

  // Detected New Media Prompt State (Asks user to start scan or remind later)
  const [newMediaDetected, setNewMediaDetected] = useState(null);
  const [isNewMediaSnoozed, setIsNewMediaSnoozed] = useState(false);
  const lastSeenNewMediaCountRef = useRef(0);

  // Newly Added Media Tab State
  const [newlyAddedList, setNewlyAddedList] = useState([]);
  const [isLoadingNewlyAdded, setIsLoadingNewlyAdded] = useState(false);
  const [newlyAddedFilter, setNewlyAddedFilter] = useState("all"); // "all" | "photos" | "videos"

  const loadNewlyAdded = useCallback(async () => {
    setIsLoadingNewlyAdded(true);
    try {
      const res = await fetchNewlyAddedMedia(200);
      setNewlyAddedList(res.items || []);
    } catch (err) {
      console.error("Failed to load newly added media:", err);
    } finally {
      setIsLoadingNewlyAdded(false);
    }
  }, []);

  const handleClearNewlyAdded = useCallback(async () => {
    try {
      await clearNewlyAddedMedia();
      setNewlyAddedList([]);
    } catch (err) {
      console.error("Failed to clear newly added media:", err);
    }
  }, []);

  // Album & Folder Explorer State
  const [currentAlbumFolder, setCurrentAlbumFolder] = useState(null);
  const [albumData, setAlbumData] = useState(null);
  const [isLoadingAlbum, setIsLoadingAlbum] = useState(false);
  const [albumViewMode, setAlbumViewMode] = useState("explorer"); // "explorer" | "all_albums"
  const [albumIncludeSubfolders, setAlbumIncludeSubfolders] = useState(false);
  const [albumSortBy, setAlbumSortBy] = useState("name"); // "name" | "date" | "size" | "type"
  const [albumSortOrder, setAlbumSortOrder] = useState("asc"); // "asc" | "desc"
  const [albumFilterText, setAlbumFilterText] = useState("");
  const [albumMediaFilter, setAlbumMediaFilter] = useState("all"); // "all" | "photos" | "videos"
  const [isLoadingMoreAlbum, setIsLoadingMoreAlbum] = useState(false);

  const loadAlbumFolder = useCallback(async (targetFolder = null, includeSub = albumIncludeSubfolders, sortBy = albumSortBy, sortOrder = albumSortOrder) => {
    setIsLoadingAlbum(true);
    try {
      const data = await fetchAlbums(targetFolder, {
        include_subfolders: includeSub,
        offset: 0,
        limit: 120,
        sort_by: sortBy,
        sort_order: sortOrder,
      });
      setAlbumData(data);
      setCurrentAlbumFolder(data.current_path || null);
    } catch (err) {
      console.error("Failed to load album data:", err);
    } finally {
      setIsLoadingAlbum(false);
    }
  }, [albumIncludeSubfolders, albumSortBy, albumSortOrder]);

  const loadMoreAlbumMedia = useCallback(async () => {
    if (!albumData || isLoadingMoreAlbum || !albumData.media_has_more) return;
    setIsLoadingMoreAlbum(true);
    try {
      const res = await fetchAlbums(currentAlbumFolder, {
        include_subfolders: albumIncludeSubfolders,
        offset: albumData.media.length,
        limit: 100,
        sort_by: albumSortBy,
        sort_order: albumSortOrder,
      });
      setAlbumData((prev) => ({
        ...prev,
        media: [...(prev.media || []), ...(res.media || [])],
        media_has_more: res.media_has_more,
      }));
    } catch (err) {
      console.error("Failed to load more album media:", err);
    } finally {
      setIsLoadingMoreAlbum(false);
    }
  }, [albumData, isLoadingMoreAlbum, currentAlbumFolder, albumIncludeSubfolders, albumSortBy, albumSortOrder]);

  const handleOpenFolderInExplorer = useCallback(async (folderPath) => {
    const target = folderPath || currentAlbumFolder || albumData?.current_path;
    if (!target) return;
    try {
      await openInExplorer(target);
    } catch (err) {
      console.error("Failed to open folder in explorer:", err);
    }
  }, [currentAlbumFolder, albumData?.current_path]);

  const handleReshuffle = useCallback(async () => {
    sessionSeedRef.current = Math.floor(Math.random() * 1000000);
    setIsShuffling(true);
    setSearchError("");
    try {
      const res = await fetchRecentPhotos(0, 100, { shuffle: true, seed: sessionSeedRef.current });
      setPhotos(res.photos || []);
      setHasMoreMedia(Boolean(res.has_more));
    } catch (err) {
      setSearchError(`Could not shuffle feed: ${err.message}`);
    } finally {
      setIsShuffling(false);
    }
  }, []);

  const handleStartNewMediaScan = useCallback(() => {
    if (!newMediaDetected?.directory) return;
    const targetDir = newMediaDetected.directory;
    const targetRate = newMediaDetected.frameRate || frameRate;
    setNewMediaDetected(null);
    setIsNewMediaSnoozed(false);
    startIndexingFolder(targetDir, true, targetRate, adaptiveSampling);
  }, [newMediaDetected, frameRate, adaptiveSampling]);

  const handleRemindLaterNewMedia = useCallback(() => {
    setIsNewMediaSnoozed(true);
  }, []);

  const cameraInputRef = useRef(null);
  const activeEventSourceRef = useRef(null);
  const lastQueryRef = useRef("");
  const lastImageFileRef = useRef(null);
  const lastSimilarPhotoRef = useRef(null);
  const searchRequestRef = useRef(0);

  const clearActiveSearch = async () => {
    const requestId = ++searchRequestRef.current;
    if (searchPreviewObjectUrlRef.current) {
      URL.revokeObjectURL(searchPreviewObjectUrlRef.current);
      searchPreviewObjectUrlRef.current = null;
    }
    setQuery("");
    lastQueryRef.current = "";
    lastImageFileRef.current = null;
    lastSimilarPhotoRef.current = null;
    setActiveSearch(null);
    setSearchError("");
    setLatencyMs(null);
    setIsSearching(true);
    sessionSeedRef.current = Math.floor(Math.random() * 1000000);
    try {
      const result = await fetchRecentPhotos(0, 100, { shuffle: true, seed: sessionSeedRef.current });
      if (requestId !== searchRequestRef.current) return;
      setPhotos(result.photos || []);
      setHasMoreMedia(Boolean(result.has_more));
    } catch (err) {
      if (requestId !== searchRequestRef.current) return;
      setSearchError(`Could not load the library: ${err.message}`);
    } finally {
      if (requestId === searchRequestRef.current) setIsSearching(false);
    }
  };

  const loadMoreLibrary = useCallback(async () => {
    if (isLoadingMore || !hasMoreMedia || activeSearch || query.trim()) return;
    setIsLoadingMore(true);
    const requestId = searchRequestRef.current;
    try {
      const result = await fetchRecentPhotos(photos.length, 100, { shuffle: true, seed: sessionSeedRef.current });
      if (requestId !== searchRequestRef.current) return;
      setPhotos((current) => [...current, ...(result.photos || [])]);
      setHasMoreMedia(Boolean(result.has_more));
    } catch (err) {
      setSearchError(`Could not load more media: ${err.message}`);
    } finally {
      setIsLoadingMore(false);
    }
  }, [isLoadingMore, hasMoreMedia, activeSearch, photos.length, query]);

  // Viewport-based automatic pagination (only loads next batch when user scrolls near bottom)
  useEffect(() => {
    const el = loadMoreSentinelRef.current;
    if (!el || activePage !== "gallery" || !hasMoreMedia || activeSearch || isLoadingMore) return;
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          loadMoreLibrary();
        }
      },
      { rootMargin: "400px 0px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [activePage, hasMoreMedia, activeSearch, isLoadingMore, loadMoreLibrary]);

  // Load in-app directory browsing
  const loadDirectoryBrowse = useCallback(async (path = "") => {
    setIsBrowsingDir(true);
    try {
      const data = await browseDirectory(path);
      setBrowseData(data);
      if (data.current_path) {
        setFolderInput(data.current_path);
      }
    } catch (err) {
      console.error("Browse dir error:", err);
    } finally {
      setIsBrowsingDir(false);
    }
  }, []);

  // Load initial system stats, gallery photos, and check for paused/resumable session
  const loadInitialData = useCallback(async () => {
    const requestId = searchRequestRef.current;
    try {
      const [sys, savedSettingsResult, data, initialState] = await Promise.all([
        fetchSystemStatus(), fetchSettings().catch(() => null),
        fetchRecentPhotos(0, 100, { shuffle: true, seed: sessionSeedRef.current }), fetchIndexingState(),
      ]);
      setSystem(sys);
      if (sys?.face_index) setFaceIndexState(sys.face_index);
      try {
        const savedSettings = savedSettingsResult || {};
        setSettings((current) => ({ ...current, ...(savedSettings.settings || {}) }));
        setFaceIndexState(savedSettings.face_index || sys?.face_index || { photos: null, videos: null });
      } catch (settingsError) {
        console.error("Settings load failed:", settingsError);
      }
      if (requestId === searchRequestRef.current) {
        setPhotos(data.photos || []);
        setHasMoreMedia(Boolean(data.has_more));
      }
      loadNewlyAdded();

      // Check for saved / interrupted indexing session from disk
      let savedState = initialState;
      const rememberedFolder = savedState?.last_selected_directory || savedState?.directory || "";
      if (rememberedFolder) setFolderInput(rememberedFolder);
      if (rememberedFolder && savedState?.last_selected_directory_available === false) {
        setFolderNotice(`The saved folder is unavailable: ${rememberedFolder}. Reconnect the drive or choose another folder.`);
      }

      if (
        savedState &&
        Number(savedState.new_files_detected || 0) > 0 &&
        savedState.directory &&
        savedState.status !== "paused" &&
        savedState.status !== "in_progress" &&
        !activeEventSourceRef.current
      ) {
        const newCount = Number(savedState.new_files_detected || 0);
        setNewMediaDetected({
          directory: savedState.directory,
          total_new: newCount,
          new_images: savedState.new_images_detected || 0,
          new_videos: savedState.new_videos_detected || 0,
          frameRate: savedState.frame_rate || (1 / 30),
        });
        lastSeenNewMediaCountRef.current = newCount;
      }

      if (
        savedState &&
        (savedState.status === "paused" || savedState.status === "in_progress") &&
        savedState.directory && !autoResumeStartedRef.current
      ) {
        autoResumeStartedRef.current = true;
        if (savedState.directory_available === false) {
          setIndexingState({
            active: false,
            folder: savedState.directory,
            current: "Saved folder is unavailable",
            indexed: savedState.indexed_count || 0,
            total: savedState.total_files || 0,
            percent: savedState.percent || 0,
            isPaused: false,
            done: false,
            error: `Cannot resume because this folder is unavailable: ${savedState.directory}. Reconnect its drive or choose another folder.`,
          });
        } else {
          const savedDone = savedState.processed_files || savedState.indexed_count || 0;
          setResumableSession(savedState);
          setIndexingState({
            active: false,
            folder: savedState.directory,
            current: `Saved checkpoint ready at file ${savedDone} of ${savedState.total_files || 0} (Photos First → Videos After). Click Resume to continue where you left off.`,
            indexed: savedState.indexed_count || 0,
            indexedFrames: savedState.indexed_frames || 0,
            total: savedState.total_files || 0,
            percent: savedState.percent || 0,
            processedFiles: savedDone,
            frameRate: savedState.frame_rate || (1 / 30),
            isPaused: true,
            done: false,
            error: null,
          });
        }
      }
    } catch (err) {
      console.error("Initial load error:", err);
    }
  }, []);

  useEffect(() => {
    loadInitialData();

    // Periodic GPU telemetry & background new-media watcher polling
    const timer = setInterval(() => {
      fetchSystemStatus().then((status) => {
        consecutiveFailuresRef.current = 0;
        setIsServerDisconnected(false);
        setSystem(status);
        if (status?.face_index) setFaceIndexState(status.face_index);
        const activeSession = status?.active_indexing;
        const newDetected = Number(activeSession?.new_files_detected || 0);
        if (
          newDetected > 0 &&
          activeSession?.directory &&
          activeSession?.status !== "in_progress" &&
          activeSession?.status !== "paused" &&
          !activeEventSourceRef.current
        ) {
          setNewMediaDetected({
            directory: activeSession.directory,
            total_new: newDetected,
            new_images: activeSession.new_images_detected || 0,
            new_videos: activeSession.new_videos_detected || 0,
            frameRate: activeSession.frame_rate || (1 / 30),
          });
          if (newDetected !== lastSeenNewMediaCountRef.current) {
            lastSeenNewMediaCountRef.current = newDetected;
            setIsNewMediaSnoozed(false);
          }
        } else if (newDetected === 0 && !activeSession?.status) {
          setNewMediaDetected(null);
          lastSeenNewMediaCountRef.current = 0;
        }
      }).catch(() => {
        consecutiveFailuresRef.current += 1;
        if (consecutiveFailuresRef.current >= 2) {
          setIsServerDisconnected(true);
        }
      });
    }, 5000);
    return () => clearInterval(timer);
  }, [loadInitialData]);

  // Auto-resume / auto-delta-sync indexing once the model has finished loading
  useEffect(() => {
    if (system?.model_status === "ready" && pendingAutoResumeFolderRef.current) {
      const pending = pendingAutoResumeFolderRef.current;
      pendingAutoResumeFolderRef.current = null;
      startIndexingFolder(pending.directory, true, pending.frameRate, pending.adaptiveSampling);
    }
  }, [system?.model_status]);

  // Load directory navigation when modal opens
  useEffect(() => {
    if (folderModalOpen) {
      loadDirectoryBrowse("");
    }
  }, [folderModalOpen, loadDirectoryBrowse]);

  // Real-time Debounced Search
  useEffect(() => {
    if (!query.trim()) {
      setIsSearching(false);
      if (lastQueryRef.current !== "") {
        const requestId = ++searchRequestRef.current;
        lastQueryRef.current = "";
        setActiveSearch(null);
        sessionSeedRef.current = Math.floor(Math.random() * 1000000);
        fetchRecentPhotos(0, 100, { shuffle: true, seed: sessionSeedRef.current }).then((res) => {
          if (requestId === searchRequestRef.current) {
            setPhotos(res.photos || []);
            setHasMoreMedia(Boolean(res.has_more));
          }
        }).catch((err) => {
          if (requestId === searchRequestRef.current) setSearchError(`Could not load the library: ${err.message}`);
        });
        setLatencyMs(null);
      }
      return;
    }

    const requestId = ++searchRequestRef.current;
    lastQueryRef.current = query;
    lastImageFileRef.current = null;
    lastSimilarPhotoRef.current = null;
    setSearchError("");
    const controller = new AbortController();
    const handler = setTimeout(async () => {
      setIsSearching(true);
      try {
        const res = await searchText(query, activeMrl, 40, controller.signal);
        if (requestId !== searchRequestRef.current) return;
        setPhotos(res.results || []);
        setHasMoreMedia(false);
        setLatencyMs(res.latency_ms);
        setActiveSearch({ type: "text", label: query.trim() });
      } catch (err) {
        if (err.name === "AbortError" || requestId !== searchRequestRef.current) return;
        console.error("Search error:", err);
        setSearchError(`Search failed: ${err.message}`);
      } finally {
        if (requestId === searchRequestRef.current) setIsSearching(false);
      }
    }, 300);

    return () => { clearTimeout(handler); controller.abort(); };
  }, [query, activeMrl]);

  // Real-time MRL Dimension Switch
  const handleMrlSwitch = (newDim) => {
    setActiveMrl(newDim);
    if (lastQueryRef.current.trim()) return;
    if (lastImageFileRef.current) {
      handleImageSearch(lastImageFileRef.current, newDim);
    } else if (lastSimilarPhotoRef.current) {
      handleFindSimilar(lastSimilarPhotoRef.current, newDim);
    }
  };

  // Reverse Visual Image Search
  const handleImageSearch = async (file, mrlDim = activeMrl) => {
    if (!file) return;
    const requestId = ++searchRequestRef.current;
    if (searchPreviewObjectUrlRef.current) {
      URL.revokeObjectURL(searchPreviewObjectUrlRef.current);
      searchPreviewObjectUrlRef.current = null;
    }
    const previewUrl = URL.createObjectURL(file);
    searchPreviewObjectUrlRef.current = previewUrl;
    lastImageFileRef.current = file;
    lastSimilarPhotoRef.current = null;
    setQuery("");
    lastQueryRef.current = "";
    setActiveSearch({ type: "image", label: file.name || "Selected image", previewUrl });
    setSearchError("");
    setIsSearching(true);

    try {
      const res = await searchByImage(file, mrlDim, 40);
      if (requestId !== searchRequestRef.current) return;
      setPhotos(res.results || []);
      setHasMoreMedia(false);
      setLatencyMs(res.latency_ms);
      setActiveSearch({ type: "image", label: file.name || "Selected image", previewUrl });
    } catch (err) {
      if (requestId !== searchRequestRef.current) return;
      console.error("Visual search error:", err);
      setSearchError(`Image search failed: ${err.message}`);
    } finally {
      if (requestId === searchRequestRef.current) setIsSearching(false);
    }
  };

  // Step 1: Inspect folder instantly on local disk and get exact count & estimate
  const handleInspectFolder = async (folderPath, samplingRate = frameRate, isAdaptive = adaptiveSampling) => {
    const cleanPath = (folderPath || "").trim().replace(/^["']|["']$/g, "");
    if (!cleanPath) return;
    setFolderInput(cleanPath);
    setIsInspecting(true);
    setInspectionProgress({ phase: "starting", scanned: 0, checked: 0, total: null, current_file: "Preparing folder scan" });
    try {
      const data = await inspectFolder(cleanPath, samplingRate, setInspectionProgress, isAdaptive);
      setInspectedFolder(data);
      setFolderNotice("");
      setFolderModalOpen(true);
    } catch (err) {
      console.error("Folder inspection error:", err);
      setFolderNotice(`Folder inspection failed: ${err.message}`);
    } finally {
      setIsInspecting(false);
      setInspectionProgress(null);
    }
  };

  const saveSettings = async (changes) => {
    setSettingsNotice("");
    setIsSavingSettings(true);
    const previous = settings;
    setSettings((current) => ({ ...current, ...changes }));
    try {
      const updated = await updateSettings(changes);
      setSettings((current) => ({ ...current, ...(updated.settings || {}) }));
      if (updated.face_index) setFaceIndexState(updated.face_index);
    } catch (error) {
      setSettings(previous);
      setSettingsNotice(`Could not save settings: ${error.message}`);
    } finally {
      setIsSavingSettings(false);
    }
  };

  // Step 2: Native Windows Folder Dialog Trigger with AbortController & cancel
  const handlePickWindowsFolder = async () => {
    setIsPickingFolder(true);
    try {
      const res = await openFolderPicker();
      if (res?.status === "success" && res.folder) {
        setFolderInput(res.folder);
        // Instantly inspect folder & show file count and time estimation
        await handleInspectFolder(res.folder);
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        console.error("Native folder picker error:", err);
        setFolderNotice(`Folder picker failed: ${err.message}`);
      }
    } finally {
      setIsPickingFolder(false);
    }
  };

  // Step 3: Start real-time SSE Indexing for a folder path
  const startIndexingFolder = (folderPath, isResume = false, samplingRate = frameRate, isAdaptive = adaptiveSampling) => {
    const cleanPath = (folderPath || "").trim().replace(/^["']|["']$/g, "");
    if (!cleanPath) return;
    setFolderInput(cleanPath);
    setFolderNotice("");
    setFolderModalOpen(false);
    setInspectedFolder(null);
    setResumableSession(null);
    setNewMediaDetected(null);
    setIsNewMediaSnoozed(false);

    if (activeEventSourceRef.current) {
      activeEventSourceRef.current.close();
    }

    setIndexingState({
      active: true,
      folder: cleanPath,
      current: "Scanning files & starting GPU stream...",
      indexed: 0,
      total: 0,
      percent: 0,
      speed: 0,
      eta: 0,
      isPaused: false,
      done: false,
      indexedFrames: 0,
      failed: 0,
    });

    try {
      const eventSource = createIndexEventSource(folderPath, isResume, samplingRate, isAdaptive);
      activeEventSourceRef.current = eventSource;

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === "start") {
            setResumableSession(null);
            setIndexingState((prev) => ({
              ...prev,
              total: data.total,
              indexed: data.already_indexed || 0,
              indexedFrames: data.already_indexed_frames || 0,
              processedFiles: data.already_processed_files || 0,
              percent: data.total > 0 ? Math.round(((data.already_processed_files || data.already_indexed || 0) / data.total) * 100) : 0,
            }));
          } else if (data.event === "photos_added") {
            const added = data.photos || [];
            if (added.length) {
              setPhotos((prev) => {
                const known = new Set(prev.map((item) => item.id));
                return [...added.filter((item) => !known.has(item.id)), ...prev];
              });
            }
          } else if (data.event === "photo_added") {
            // Live Real-Time Ingestion onto the home page grid!
            if (data.photo) {
              setPhotos((prev) => {
                if (prev.some((p) => p.id === data.photo.id)) return prev;
                return [data.photo, ...prev];
              });
            }
          } else if (data.event === "video_frame_added") {
            if (data.video) {
              setPhotos((prev) => prev.some((item) => item.media_type === "video" && item.path === data.video.path) ? prev : [data.video, ...prev]);
            }
          } else if (data.event === "file_error") {
            setIndexingState((prev) => prev ? { ...prev, failed: (prev.failed || 0) + 1, lastError: `${data.filename}: ${data.message}` } : prev);
          } else if (data.event === "progress") {
            setIndexingState((prev) => ({
              ...prev,
              indexed: data.indexed,
              total: data.total,
              percent: data.percent,
              current: data.current_file,
              speed: data.speed_fps,
              eta: data.eta_seconds,
              indexedFrames: data.indexed_frames || 0,
              processedFiles: data.processed_files || 0,
              failed: data.failed || 0,
              isPaused: false,
            }));
          } else if (data.event === "paused") {
            setIndexingState((prev) => ({
              ...prev,
              isPaused: true,
              indexed: data.indexed,
              total: data.total,
              indexedFrames: data.indexed_frames || 0,
              failed: data.failed || 0,
              processedFiles: data.processed_files || prev.processedFiles || 0,
            }));
          } else if (data.event === "resumed") {
            setIndexingState((prev) => ({
              ...prev,
              isPaused: false,
              indexed: data.indexed,
              total: data.total,
            }));
          } else if (data.event === "complete") {
            setIndexingState((prev) => ({
              ...prev,
              active: false,
              done: true,
              isPaused: false,
              percent: 100,
              indexed: data.indexed,
              total: data.total,
              indexedFrames: data.indexed_frames || 0,
              failed: data.failed || 0,
              elapsed: data.elapsed_seconds,
            }));
            fetchSystemStatus().then(setSystem);
            sessionSeedRef.current = Math.floor(Math.random() * 1000000);
        fetchRecentPhotos(0, 100, { shuffle: true, seed: sessionSeedRef.current }).then((res) => {
              setPhotos(res.photos || []);
              setHasMoreMedia(Boolean(res.has_more));
            }).catch(() => {});
            loadNewlyAdded();
            eventSource.close();
          } else if (data.event === "cancelled") {
            setIndexingState(null);
            eventSource.close();
          }
        } catch (err) {
          console.error("SSE parse error:", err);
        }
      };

      eventSource.onerror = (err) => {
        console.error("SSE connection error:", err);
        eventSource.close();
        activeEventSourceRef.current = null;
        setIndexingState((prev) => prev ? {
          ...prev,
          active: false,
          isPaused: false,
          error: "The indexing connection was interrupted. Click Resume to continue or Stop to cancel.",
        } : null);
      };
    } catch (err) {
      console.error("Failed to start indexing:", err);
    }
  };

  // Pause / Resume Handlers
  const handlePauseIndexing = async () => {
    try {
      await pauseIndexing();
      setIndexingState((prev) => (prev ? { ...prev, isPaused: true } : null));
    } catch (err) {
      console.error("Error pausing indexing:", err);
    }
  };

  const handleResumeIndexing = async () => {
    const folderToResume = indexingState?.folder || resumableSession?.directory || folderInput;
    if (!folderToResume) return;

    if (system?.model_status === "loading" || system?.model_status === "not_started") {
      pendingAutoResumeFolderRef.current = {
        directory: folderToResume,
        frameRate: indexingState?.frameRate || frameRate,
        adaptiveSampling,
      };
      setIndexingState((prev) => (prev ? {
        ...prev,
        isPaused: true,
        error: null,
        current: "Waiting for Gemma model to finish loading before resuming…",
      } : null));
      return;
    }

    try {
      if (!indexingState?.active || indexingState?.error || !activeEventSourceRef.current) {
        startIndexingFolder(
          folderToResume,
          true,
          indexingState?.frameRate || frameRate,
          adaptiveSampling
        );
        return;
      }
      await resumeIndexing();
      setIndexingState((prev) => (prev ? { ...prev, isPaused: false, error: null } : null));
    } catch (err) {
      console.error("Error resuming indexing:", err);
      startIndexingFolder(
        folderToResume,
        true,
        indexingState?.frameRate || frameRate,
        adaptiveSampling
      );
    }
  };

  const handleCancelIndexing = async () => {
    pendingAutoResumeFolderRef.current = null;
    try {
      await cancelIndexing();
    } catch (err) {
      console.error("Error cancelling indexing:", err);
    } finally {
      if (activeEventSourceRef.current) {
        activeEventSourceRef.current.close();
        activeEventSourceRef.current = null;
      }
      setIndexingState(null);
      setResumableSession(null);
    }
  };

  // Clear All Indexing (Database + Saved Sessions)
  const handleClearAllIndexing = async () => {
    const confirmed = window.confirm("Clear all indexed image and video embeddings and forget the saved indexing session? Your original media files will not be deleted.");
    if (!confirmed) return;
    setIsClearingIndex(true);
    setClearNotice(null);
    try {
      await clearAllIndexing();
      if (activeEventSourceRef.current) {
        activeEventSourceRef.current.close();
        activeEventSourceRef.current = null;
      }
      setPhotos([]);
      setIndexingState(null);
      setResumableSession(null);
      setInspectedFolder(null);
      setQuery("");
      lastQueryRef.current = "";
      lastImageFileRef.current = null;
      lastSimilarPhotoRef.current = null;
      setActiveSearch(null);
      setSearchError("");
      setLatencyMs(null);
      const sys = await fetchSystemStatus();
      setSystem(sys);
      setClearNotice("All indexed photos and vector database records have been cleared!");
    } catch (err) {
      console.error("Error clearing index:", err);
      setClearNotice(`Could not clear the index: ${err.message}`);
    } finally {
      setIsClearingIndex(false);
    }
  };

  // Find Similar Photos action
  const handleFindSimilar = async (photo, mrlDim = activeMrl) => {
    if (isVideoMedia(photo)) return;
    if (!photo?.id) return;
    const requestId = ++searchRequestRef.current;
    const previewUrl = getPhotoUrl(photo);
    setActivePage("gallery");
    setIsSearching(true);
    setQuery("");
    lastQueryRef.current = "";
    lastImageFileRef.current = null;
    lastSimilarPhotoRef.current = photo;
    setActiveSearch({ type: "similar", label: photo.filename || "selected photo", previewUrl });
    setSearchError("");

    try {
      const res = await fetchSimilarPhotos(photo.id, mrlDim, 40);
      if (requestId !== searchRequestRef.current) return;
      setPhotos(res.results || []);
      setHasMoreMedia(false);
      setLatencyMs(res.latency_ms);
      setActiveSearch({ type: "similar", label: photo.filename || "selected photo", previewUrl });
      setSelectedPhoto(null);
    } catch (err) {
      if (requestId !== searchRequestRef.current) return;
      console.error("Similar photos error:", err);
      setSearchError(`Similar-photo search failed: ${err.message}`);
    } finally {
      if (requestId === searchRequestRef.current) setIsSearching(false);
    }
  };

  const handleFindSimilarFace = async (photo, faceIndex) => {
    if (!photo?.id) return;
    const mediaType = isVideoMedia(photo) ? "video" : "image";
    const kind = mediaType === "video" ? "videos" : "photos";
    if (!settings[mediaType === "video" ? "face_search_videos_enabled" : "face_search_photos_enabled"]) {
      throw new Error(`Turn on face matching for ${kind} in Settings first.`);
    }
    const requestId = ++searchRequestRef.current;
    const previewUrl = getFacePreviewUrl(photo.id, mediaType, faceIndex);
    setActivePage("gallery");
    setIsSearching(true);
    setQuery("");
    lastQueryRef.current = "";
    lastImageFileRef.current = null;
    lastSimilarPhotoRef.current = null;
    setActiveSearch({ type: "face", label: settings.show_filenames ? (photo.filename || "selected face") : "selected face", previewUrl });
    setSearchError("");
    try {
      const res = await searchSimilarFaces(photo.id, mediaType, faceIndex, 40);
      if (requestId !== searchRequestRef.current) return;
      setPhotos(res.results || []);
      setHasMoreMedia(false);
      setLatencyMs(null);
      setActiveSearch({ type: "face", label: settings.show_filenames ? (photo.filename || "selected face") : "selected face", previewUrl });
      setSelectedPhoto(null);
    } catch (error) {
      if (requestId === searchRequestRef.current) setSearchError(`Face search failed: ${error.message}`);
      throw error;
    } finally {
      if (requestId === searchRequestRef.current) setIsSearching(false);
    }
  };

  // Drag & drop handlers for visual search
  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragOver(true);
  };
  const handleDragLeave = (e) => {
    e.preventDefault();
    setIsDragOver(false);
  };
  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith("image/")) {
      setActivePage("gallery");
      handleImageSearch(file);
    }
  };

  const runImageAudit = async () => {
    setIsAuditingImages(true);
    try {
      setImageAudit(await auditImageProcessing(8));
    } catch (error) {
      setImageAudit({ error: error.message });
    } finally {
      setIsAuditingImages(false);
    }
  };

  const gpuName = system?.gpu_name || "Detecting hardware";
  const hardware = system;
  const totalIndexed = (system?.indexed_count || 0) + (system?.indexed_video_frames || 0);

  const visiblePhotos = useMemo(() => {
    return photos.filter((p) => p.accuracy_percent === undefined || p.accuracy_percent >= minAccuracy);
  }, [photos, minAccuracy]);

  const activeViewerItems = useMemo(() => {
    if (activePage === "albums" && albumData?.media) {
      return albumData.media.filter((item) => {
        if (albumMediaFilter === "photos") return !isVideoMedia(item);
        if (albumMediaFilter === "videos") return isVideoMedia(item);
        return true;
      });
    }
    if (activePage === "newly_added" && newlyAddedList) {
      return newlyAddedList.filter((item) => {
        if (newlyAddedFilter === "photos") return !isVideoMedia(item);
        if (newlyAddedFilter === "videos") return isVideoMedia(item);
        return true;
      });
    }
    return visiblePhotos;
  }, [activePage, albumData?.media, albumMediaFilter, newlyAddedList, newlyAddedFilter, visiblePhotos]);

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className="min-h-screen bg-transparent text-slate-100 flex flex-col font-sans"
    >
      {/* Server Disconnect / Reconnecting Floating Indicator */}
      {isServerDisconnected && (
        <div role="status" className="fixed top-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2.5 rounded-full bg-amber-500/20 text-amber-200 border border-amber-500/35 backdrop-blur-xl px-4 py-1.5 text-xs font-medium shadow-[0_8px_24px_rgba(245,158,11,0.25)]">
          <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-300" />
          <span>Reconnecting to Gemma server…</span>
          <button
            type="button"
            onClick={() => {
              fetchSystemStatus().then((st) => {
                setSystem(st);
                consecutiveFailuresRef.current = 0;
                setIsServerDisconnected(false);
              }).catch(() => {});
            }}
            className="ml-1 rounded-full bg-amber-500/30 hover:bg-amber-500/50 text-amber-100 px-2 py-0.5 text-[10px] font-bold transition cursor-pointer"
          >
            Retry
          </button>
        </div>
      )}
      {/* Drag & Drop Visual Overlay */}
      {isDragOver && (
        <div className="fixed inset-0 z-50 bg-[#0d0f12]/90 backdrop-blur-xl flex flex-col items-center justify-center p-6 pointer-events-none">
          <Camera className="w-16 h-16 text-blue-400 animate-bounce" />
          <h2 className="text-2xl font-bold mt-4 text-white">
            Drop Image to Run Visual Search on GPU
          </h2>
          <p className="text-sm text-slate-400 mt-1">
            EmbeddingGemma 2 will instantly find matching photos
          </p>
        </div>
      )}

      {/* App Restart Resumable Session Alert Banner */}
      {resumableSession && (!indexingState || (!indexingState.active && !indexingState.done)) && (
        <div className="bg-amber-500/15 backdrop-blur-xl px-4 sm:px-8 py-3 flex flex-wrap items-center justify-between gap-3 text-xs text-amber-200 shadow-lg">
          <div className="flex items-center gap-2.5 min-w-0">
            <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 animate-pulse" />
            <span className="truncate">
              <strong>Resume indexing from where you left off?</strong>{" "}
              <span className="font-mono text-white">{resumableSession.directory}</span>{" "}
              ({resumableSession.processed_files ?? resumableSession.indexed_count ?? 0} / {resumableSession.total_files} files completed, {resumableSession.percent}%)
            </span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={handleResumeIndexing}
              className="bg-amber-500/25 hover:bg-amber-500/35 text-amber-200 font-bold px-3.5 py-1.5 rounded-full flex items-center gap-1.5 transition shadow-[0_0_16px_rgba(245,158,11,0.2)] backdrop-blur-md cursor-pointer"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>Resume Indexing</span>
            </button>
            <button
              onClick={handleCancelIndexing}
              className="bg-black/45 hover:bg-white/10 text-slate-300 hover:text-white px-3 py-1.5 rounded-full transition backdrop-blur-md cursor-pointer"
            >
              Discard
            </button>
          </div>
        </div>
      )}

      {/* Detected New Media User Prompt Banner */}
      {newMediaDetected && !isNewMediaSnoozed && (!indexingState || (!indexingState.active && !indexingState.done)) && (
        <div className="bg-blue-500/15 backdrop-blur-xl px-4 sm:px-8 py-3 flex flex-wrap items-center justify-between gap-3 text-xs text-blue-200 shadow-lg">
          <div className="flex items-center gap-2.5 min-w-0">
            <Sparkles className="w-4 h-4 text-blue-400 shrink-0 animate-pulse" />
            <span className="truncate">
              <strong>New media files detected in watched folder!</strong>{" "}
              Found <span className="font-semibold text-white">{newMediaDetected.total_new}</span> new file{newMediaDetected.total_new === 1 ? "" : "s"}{" "}
              ({newMediaDetected.new_images > 0 ? `${newMediaDetected.new_images} photo${newMediaDetected.new_images === 1 ? "" : "s"}` : ""}{newMediaDetected.new_images > 0 && newMediaDetected.new_videos > 0 ? ", " : ""}{newMediaDetected.new_videos > 0 ? `${newMediaDetected.new_videos} video${newMediaDetected.new_videos === 1 ? "" : "s"}` : ""}) in{" "}
              <span className="font-mono text-white">{newMediaDetected.directory}</span>. Would you like to scan and index them now?
            </span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={handleStartNewMediaScan}
              className="bg-blue-500/30 hover:bg-blue-500/45 text-blue-100 font-bold px-3.5 py-1.5 rounded-full flex items-center gap-1.5 transition shadow-[0_0_16px_rgba(59,130,246,0.25)] backdrop-blur-md cursor-pointer"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>Start Scan</span>
            </button>
            <button
              onClick={handleRemindLaterNewMedia}
              className="bg-black/45 hover:bg-white/10 text-slate-300 hover:text-white px-3 py-1.5 rounded-full transition backdrop-blur-md cursor-pointer"
            >
              Remind Later
            </button>
          </div>
        </div>
      )}

      {/* Modern, Frosted Obsidian Acrylic Header */}
      <header className="sticky top-0 z-40 bg-[#12161c]/80 backdrop-blur-xl px-4 sm:px-8 py-3.5 shadow-[0_4px_30px_rgba(0,0,0,0.45)]">
        <div className="max-w-7xl mx-auto flex items-center justify-between gap-4">
          {/* Logo */}
          <div
            onClick={() => {
              setActivePage("gallery");
              clearActiveSearch();
            }}
            className="flex items-center gap-2.5 cursor-pointer select-none shrink-0"
          >
            <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-blue-500/35 to-emerald-500/35 backdrop-blur-md flex items-center justify-center shadow-[0_0_20px_rgba(59,130,246,0.25)]">
              <Sparkles className="w-5 h-5 text-blue-200" />
            </div>
            <div>
              <h1 className="text-base font-bold tracking-tight text-white flex items-center gap-1.5 leading-none">
                GemmaGallery
              </h1>
              <span className="text-[10px] text-slate-400 font-medium">
                Frosted Obsidian AI
              </span>
            </div>
          </div>

          {/* Central Frosted Acrylic Search Capsule */}
          <div className="flex-1 max-w-xl mx-2">
            <div className="relative flex items-center">
              {activeSearch?.previewUrl ? (
                <div className="absolute left-2.5 h-7 w-7 overflow-hidden rounded-full bg-slate-900 shadow-sm pointer-events-none">
                  <img
                    src={activeSearch.previewUrl}
                    alt="Query preview"
                    className="h-full w-full object-cover"
                  />
                </div>
              ) : (
                <Search className="absolute left-3.5 w-4 h-4 text-slate-400 pointer-events-none" />
              )}
              <input
                type="text"
                value={query}
                onChange={(e) => {
                  if (activePage !== "gallery") setActivePage("gallery");
                  setQuery(e.target.value);
                  if (e.target.value.trim()) {
                    setActiveSearch(null);
                    setSearchError("");
                    lastImageFileRef.current = null;
                    lastSimilarPhotoRef.current = null;
                  }
                }}
                placeholder="Search photos and videos (e.g., 'sunset', 'invoice')..."
                className={`w-full bg-black/50 backdrop-blur-md rounded-full py-2.5 ${activeSearch?.previewUrl ? "pl-11" : "pl-10"} pr-20 text-sm text-slate-100 placeholder:text-slate-400 focus:outline-none focus:bg-black/65 transition shadow-inner`}
              />

              {/* Clear button */}
              {(query || activeSearch) && (
                <button
                  onClick={clearActiveSearch}
                  className="absolute right-11 w-6 h-6 rounded-full bg-white/5 hover:bg-white/15 flex items-center justify-center text-slate-300 hover:text-white transition cursor-pointer"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}

              {/* Camera Icon for Image Search */}
              <input
                type="file"
                ref={cameraInputRef}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) {
                    setActivePage("gallery");
                    handleImageSearch(f);
                  }
                }}
                accept="image/*"
                className="hidden"
              />
              <button
                onClick={() => cameraInputRef.current?.click()}
                title="Search by image (Visual Reverse Search)"
                className="absolute right-2.5 w-7 h-7 rounded-full bg-blue-500/20 hover:bg-blue-500/35 text-blue-300 hover:text-white flex items-center justify-center transition cursor-pointer shadow-sm"
              >
                <Camera className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          {/* Right Actions: Inspect Folder (Explorer), Settings & GPU Badge */}
          <div className="flex items-center gap-2 shrink-0">
            {/* Primary Inspect Folder Button (Directly opens Windows Explorer like Camera button, then inspects!) */}
            <div className="flex items-center rounded-xl bg-blue-500/20 backdrop-blur-md shadow-[0_0_20px_rgba(59,130,246,0.18)] overflow-hidden">
              <button
                onClick={handlePickWindowsFolder}
                disabled={isInspecting || isPickingFolder}
                title={folderInput ? `Open Explorer to inspect folder (Current: ${folderInput})` : "Open Windows Explorer to pick and inspect a media folder"}
                className="hover:bg-blue-500/30 disabled:opacity-60 text-blue-200 hover:text-white text-xs font-semibold px-3.5 py-2 flex items-center gap-2 transition active:scale-95 cursor-pointer"
              >
                {isInspecting || isPickingFolder ? (
                  <Loader2 className="w-4 h-4 animate-spin text-blue-300" />
                ) : (
                  <FolderOpen className="w-4 h-4 text-blue-300" />
                )}
                <span>
                  {isPickingFolder
                    ? "Explorer Open…"
                    : isInspecting
                    ? "Inspecting…"
                    : "Inspect Folder"}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setFolderModalOpen(true)}
                disabled={isInspecting || isPickingFolder}
                title="Paste folder path or browse recent folders"
                className="px-2.5 py-2 text-blue-300 hover:bg-blue-500/30 hover:text-white transition cursor-pointer"
              >
                <Sliders className="w-3.5 h-3.5" />
              </button>
            </div>

            {/* Settings Page Toggle Button */}
            <button
              onClick={() => {
                setClearNotice(null);
                setActivePage((prev) => (prev === "settings" ? "gallery" : "settings"));
              }}
              title="Open Settings & Index Management"
              className={`text-xs font-semibold px-3.5 py-2 rounded-xl flex items-center gap-1.5 backdrop-blur-md transition cursor-pointer ${
                activePage === "settings"
                  ? "bg-blue-500/20 text-blue-200 shadow-[0_0_16px_rgba(59,130,246,0.2)]"
                  : "bg-black/50 hover:bg-white/10 text-slate-300 hover:text-white"
              }`}
            >
              <Settings className="w-4 h-4 text-blue-300" />
              <span className="hidden sm:inline">Settings</span>
            </button>

            {/* GPU Telemetry Badge */}
            <div title={system?.model_error || (system?.is_synthetic ? "Gemma is unavailable; semantic search and indexing are disabled" : system?.model_loaded ? "Real Gemma model loaded" : "EmbeddingGemma model is loading")} className="flex items-center gap-2 bg-black/50 backdrop-blur-md px-3 py-1.5 rounded-xl text-xs">
              <span className={`w-2 h-2 rounded-full ${system?.model_loaded && !system?.is_synthetic ? "bg-emerald-400 shadow-[0_0_8px_#34d399]" : system?.model_status === "loading" ? "bg-blue-400 animate-pulse" : "bg-amber-400"}`} />
              <Zap className="w-3.5 h-3.5 text-blue-300" />
              <span className="font-semibold text-slate-200 hidden md:inline">
                {system?.is_synthetic ? "Model unavailable" : system?.model_loaded ? "Gemma ready" : system?.model_status === "loading" || system?.model_status === "not_started" ? "Loading Gemma" : "Model unavailable"}
              </span>
              <span className="text-slate-400 font-mono text-[11px]">
                {gpuName} · {system?.dtype || "checking"}
              </span>
            </div>
          </div>
        </div>
      </header>

      {folderInput && (
        <div className="bg-[#0d0f12]/80 backdrop-blur-md px-4 py-1.5 sm:px-8">
          <div className="mx-auto max-w-7xl flex items-center justify-between gap-2 text-[10px] text-slate-400">
            <p className="truncate" title={folderInput}>
              <Folder className="mr-1.5 inline h-3 w-3 text-blue-400" />
              Watched folder: <span className="font-mono text-slate-300">{folderInput}</span>
            </p>
            {newMediaDetected && newMediaDetected.total_new > 0 ? (
              <button
                onClick={handleStartNewMediaScan}
                className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/25 hover:bg-blue-500/35 px-2.5 py-0.5 text-[10px] font-semibold text-blue-200 transition cursor-pointer"
              >
                <Sparkles className="h-3 w-3 text-blue-300 animate-pulse" />
                <span>{newMediaDetected.total_new} new file{newMediaDetected.total_new === 1 ? "" : "s"} detected · Start Scan</span>
              </button>
            ) : (
              <span className="hidden sm:inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                Folder monitored for new files
              </span>
            )}
          </div>
        </div>
      )}

      {["loading", "not_started"].includes(system?.model_status) && (
        <div className="bg-blue-500/10 backdrop-blur-md px-4 py-2 text-center text-xs text-blue-200">
          <Loader2 className="mr-2 inline h-3.5 w-3.5 animate-spin" />
          {system.model_stage || "Loading EmbeddingGemma"}. The gallery is available now; search and indexing will unlock when the model is ready.
        </div>
      )}
      {["error", "fallback"].includes(system?.model_status) && (
        <div role="alert" className="bg-red-500/15 backdrop-blur-md px-4 py-2 text-center text-xs text-red-200">
          EmbeddingGemma could not start: {system.model_error || "Check the model setup and restart the app."}
        </div>
      )}

      {(activeSearch || searchError || folderNotice) && (
        <div className="bg-[#12161c]/75 backdrop-blur-md px-4 py-2 sm:px-8">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2">
            {activeSearch && (
              <div className="inline-flex max-w-full items-center gap-2.5 rounded-full bg-blue-500/20 backdrop-blur-md pl-2 pr-3 py-1 text-xs text-blue-200 shadow-sm">
                {activeSearch.previewUrl ? (
                  <img
                    src={activeSearch.previewUrl}
                    alt={activeSearch.label || "Query image"}
                    className="h-8 w-8 shrink-0 rounded-lg object-cover shadow-md"
                  />
                ) : (
                  <Search size={13} className="ml-1 shrink-0 text-blue-300" />
                )}
                <span className="shrink-0 text-blue-300">
                  {activeSearch.type === "similar"
                    ? "Similar to"
                    : activeSearch.type === "image"
                    ? "Image search:"
                    : activeSearch.type === "face"
                    ? "Matching face:"
                    : "Search:"}
                </span>
                <span className="max-w-[min(55vw,32rem)] truncate font-semibold text-white" title={activeSearch.label}>
                  {activeSearch.type === "similar" && !settings.show_filenames ? "selected image" : activeSearch.label}
                </span>
                <button onClick={clearActiveSearch} aria-label="Clear search and show all media" title="Clear search" className="ml-1 rounded-full p-1 text-blue-200 transition hover:bg-blue-400/20 hover:text-white cursor-pointer">
                  <X size={13} />
                </button>
              </div>
            )}
            {searchError && (
              <div role="alert" className="flex max-w-full items-center gap-2 rounded-full bg-red-500/20 px-3 py-1 text-xs text-red-200">
                <AlertCircle size={14} className="shrink-0" />
                <span>{searchError}</span>
                <button onClick={() => setSearchError("")} aria-label="Dismiss search error" className="rounded-full p-1 hover:bg-red-500/25"><X size={13} /></button>
              </div>
            )}
            {folderNotice && (
              <div role="alert" className="flex max-w-full items-center gap-2 rounded-full bg-amber-500/20 px-3 py-1 text-xs text-amber-200">
                <AlertCircle size={14} className="shrink-0 text-amber-300" />
                <span>{folderNotice}</span>
                <button onClick={() => setFolderModalOpen(true)} className="shrink-0 rounded-full bg-amber-500/25 px-2.5 py-0.5 font-semibold text-amber-100 hover:bg-amber-500/35">Choose folder</button>
              </div>
            )}
          </div>
        </div>
      )}

      {isInspecting && inspectionProgress && (
        <div className="mx-auto w-full max-w-7xl px-4 pt-3 sm:px-8">
          <div className="rounded-2xl bg-blue-500/15 backdrop-blur-xl px-4 py-3 text-xs text-blue-100 shadow-lg">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="inline-flex items-center gap-2 font-semibold">
                <Loader2 size={14} className="animate-spin text-blue-300" />
                {inspectionProgress.phase === "checking" ? "Checking indexed files" : "Scanning folder contents"}
              </span>
              {inspectionProgress.phase === "checking" && inspectionProgress.total != null ? (
                <span className="font-mono">{inspectionProgress.checked || 0} / {inspectionProgress.total} files · {inspectionProgress.percent || 0}%</span>
              ) : (
                <span className="font-mono">{inspectionProgress.scanned || 0} items scanned</span>
              )}
            </div>
            {inspectionProgress.phase === "checking" && inspectionProgress.total > 0 && (
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-black/50">
                <div className="h-full bg-blue-400 transition-all" style={{ width: `${inspectionProgress.percent || 0}%` }} />
              </div>
            )}
            {inspectionProgress.current_file && <p className="mt-1.5 truncate text-[10px] text-slate-300" title={inspectionProgress.current_file}>{inspectionProgress.current_file}</p>}
            {inspectionProgress.warning && <p className="mt-1 text-[10px] text-amber-300">Some folders could not be read: {inspectionProgress.warning}</p>}
          </div>
        </div>
      )}

      {/* Frosted Acrylic Navigation Toolbar: Media Library and Stats */}
      <div className="bg-[#12161c]/70 backdrop-blur-md py-2.5 px-4 sm:px-8">
        <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-3 text-xs">
          {/* Primary Section Tabs */}
          <div className="flex items-center gap-1.5 bg-black/50 backdrop-blur-md p-1 rounded-xl">
            <button
              type="button"
              onClick={() => {
                setActivePage("gallery");
              }}
              className={`px-3.5 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition cursor-pointer ${
                activePage === "gallery"
                  ? "bg-blue-500/20 text-blue-200 shadow-[0_0_16px_rgba(59,130,246,0.2)]"
                  : "text-slate-400 hover:text-white hover:bg-white/5"
              }`}
            >
              <Sparkles className="w-3.5 h-3.5 text-blue-300" />
              <span>Media Library</span>
            </button>

            <button
              type="button"
              onClick={() => {
                setActivePage("albums");
                loadAlbumFolder();
              }}
              className={`px-3.5 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition cursor-pointer ${
                activePage === "albums"
                  ? "bg-blue-500/20 text-blue-200 shadow-[0_0_16px_rgba(59,130,246,0.2)]"
                  : "text-slate-400 hover:text-white hover:bg-white/5"
              }`}
            >
              <FolderTree className="w-3.5 h-3.5 text-blue-300" />
              <span>Albums</span>
              {albumData?.all_albums?.length > 0 && (
                <span className="ml-1 px-1.5 py-0.5 rounded-full bg-blue-500/30 text-blue-200 text-[10px] font-mono font-bold leading-none">
                  {albumData.all_albums.length}
                </span>
              )}
            </button>

            <button
              type="button"
              onClick={() => {
                setActivePage("newly_added");
                loadNewlyAdded();
              }}
              className={`px-3.5 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition cursor-pointer ${
                activePage === "newly_added"
                  ? "bg-blue-500/20 text-blue-200 shadow-[0_0_16px_rgba(59,130,246,0.2)]"
                  : "text-slate-400 hover:text-white hover:bg-white/5"
              }`}
            >
              <Clock className="w-3.5 h-3.5 text-blue-300" />
              <span>Newly Added</span>
              {newlyAddedList.length > 0 && (
                <span className="ml-1 px-1.5 py-0.5 rounded-full bg-blue-500/30 text-blue-200 text-[10px] font-mono font-bold leading-none">
                  {newlyAddedList.length}
                </span>
              )}
            </button>

            <button
              type="button"
              onClick={() => setActivePage("settings")}
              className={`px-3.5 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition cursor-pointer ${
                activePage === "settings"
                  ? "bg-blue-500/20 text-blue-200 shadow-[0_0_16px_rgba(59,130,246,0.2)]"
                  : "text-slate-400 hover:text-white hover:bg-white/5"
              }`}
            >
              <Settings className="w-3.5 h-3.5 text-blue-300" />
              <span>Settings</span>
            </button>
          </div>

          {/* Stats, Active MRL Badge & Search Latency */}
          <div className="flex items-center gap-3 text-slate-400">
            {/* Quick Shuffle Feed Button */}
            {activePage === "gallery" && !activeSearch && (
              <button
                type="button"
                onClick={handleReshuffle}
                disabled={isShuffling}
                title="Shuffle feed and show different random media from your library"
                className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/20 hover:bg-blue-500/35 px-3 py-1 text-[11px] font-semibold text-blue-200 backdrop-blur-md transition cursor-pointer disabled:opacity-50"
              >
                <Shuffle className={`w-3 h-3 text-blue-300 ${isShuffling ? "animate-spin" : ""}`} />
                <span>Shuffle Feed</span>
              </button>
            )}
            {latencyMs !== null && (
              <span className="rounded-full bg-emerald-500/20 px-2.5 py-1 text-emerald-300 font-mono font-semibold flex items-center gap-1 backdrop-blur-md">
                <Zap className="w-3.5 h-3.5" /> {latencyMs} ms on GPU
              </span>
            )}
            <button
              type="button"
              onClick={() => setActivePage("settings")}
              title="Change MRL embedding dimension in Settings"
              className="hidden sm:inline-flex items-center gap-1.5 rounded-full bg-black/50 backdrop-blur-md px-3 py-1 text-[11px] text-slate-300 hover:bg-white/10 hover:text-white transition cursor-pointer"
            >
              <Layers className="w-3 h-3 text-blue-300" />
              <span>{activeMrl}d MRL</span>
            </button>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-black/40 backdrop-blur-md px-3 py-1">
              <HardDrive className="w-3.5 h-3.5 text-blue-300" />
              <strong className="text-slate-100">{photos.length}</strong> media results
            </span>
          </div>
        </div>
      </div>

      {/* Real-Time Live Indexing Progress Banner with PAUSE & RESUME */}
      {indexingState && (
        <div className="max-w-7xl mx-auto w-full px-4 sm:px-8 pt-4">
          <div className={`rounded-2xl p-4 backdrop-blur-xl shadow-[0_12px_36px_rgba(0,0,0,0.5)] transition-colors ${
            indexingState.error
              ? "bg-red-500/15"
              : indexingState.isPaused
              ? "bg-amber-500/15"
              : "bg-[#141a22]/85"
          }`}>
            <div className="flex items-center justify-between gap-3 text-xs mb-2">
              <div className="flex items-center gap-2 min-w-0">
                {indexingState.done ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                ) : indexingState.error ? (
                  <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
                ) : indexingState.isPaused ? (
                  <Pause className="w-4 h-4 text-amber-300 shrink-0 animate-pulse" />
                ) : (
                  <Loader2 className="w-4 h-4 text-blue-300 animate-spin shrink-0" />
                )}
                <span className="font-semibold text-white truncate">
                  {indexingState.done
                    ? `Indexing complete for: ${indexingState.folder}`
                    : indexingState.error
                    ? `Indexing interrupted: ${indexingState.folder}`
                    : indexingState.isPaused
                    ? `PAUSED: ${indexingState.folder}`
                    : `Indexing folder: ${indexingState.folder}`}
                </span>
                {indexingState.isPaused && (
                  <span className="text-[10px] bg-amber-500/25 text-amber-200 px-2.5 py-0.5 rounded-full font-bold">
                    PAUSED
                  </span>
                )}
                {indexingState.error && (
                  <span className="text-[10px] bg-red-500/25 text-red-200 px-2.5 py-0.5 rounded-full font-bold">
                    INTERRUPTED
                  </span>
                )}
              </div>

              <div className="flex items-center gap-2.5 shrink-0">
                {indexingState.speed > 0 && !indexingState.done && !indexingState.isPaused && !indexingState.error && (
                  <span className="rounded-full bg-emerald-500/20 px-2.5 py-0.5 text-emerald-300 font-mono font-bold">
                    ⚡ {indexingState.speed} embeddings/sec
                  </span>
                )}

                {indexingState.eta > 0 && !indexingState.done && !indexingState.isPaused && !indexingState.error && (
                  <span className="text-slate-300 font-mono flex items-center gap-1">
                    <Clock className="w-3 h-3 text-blue-300" /> ~{Math.round(indexingState.eta)}s
                  </span>
                )}

                <span className="text-slate-200 font-mono font-bold">
                  {indexingState.processedFiles ?? indexingState.indexed} / {indexingState.total} files ({indexingState.percent}%)
                </span>

                {/* Pause, Resume & Stop Controls (ALWAYS available when not done) */}
                {!indexingState.done && (
                  <div className="flex items-center gap-1.5 ml-2">
                    {indexingState.isPaused || indexingState.error || !indexingState.active ? (
                      <button
                        onClick={handleResumeIndexing}
                        disabled={system?.model_status === "loading" || system?.model_status === "not_started"}
                        title="Resume indexing progress"
                        className="bg-emerald-500/25 hover:bg-emerald-500/35 disabled:opacity-50 disabled:cursor-wait text-emerald-200 font-bold px-3 py-1 rounded-full flex items-center gap-1.5 text-xs transition shadow-[0_0_16px_rgba(16,185,129,0.2)] backdrop-blur-md cursor-pointer"
                      >
                        {system?.model_status === "loading" || system?.model_status === "not_started" ? (
                          <>
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            <span>Model Loading…</span>
                          </>
                        ) : (
                          <>
                            <Play className="w-3.5 h-3.5 fill-current" />
                            <span>Resume</span>
                          </>
                        )}
                      </button>
                    ) : (
                      <button
                        onClick={handlePauseIndexing}
                        title="Pause indexing"
                        className="bg-amber-500/25 hover:bg-amber-500/35 text-amber-200 font-bold px-3 py-1 rounded-full flex items-center gap-1 text-xs transition backdrop-blur-md cursor-pointer"
                      >
                        <Pause className="w-3.5 h-3.5 fill-current" />
                        <span>Pause</span>
                      </button>
                    )}

                    <button
                      onClick={handleCancelIndexing}
                      title="Stop and cancel indexing session"
                      className="bg-red-500/25 hover:bg-red-500/40 text-red-200 hover:text-white px-3 py-1 rounded-full flex items-center gap-1 text-xs transition backdrop-blur-md cursor-pointer"
                    >
                      <StopCircle className="w-3.5 h-3.5" />
                      <span>Stop</span>
                    </button>
                  </div>
                )}

                {/* Dismiss button for completed or interrupted state */}
                {(indexingState.done || indexingState.error) && (
                  <button
                    onClick={() => setIndexingState(null)}
                    title="Dismiss"
                    className="text-slate-400 hover:text-white p-1 rounded-full transition hover:bg-white/10 cursor-pointer ml-1"
                  >
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>

            {/* Live Progress Bar */}
            <div className="w-full bg-black/50 rounded-full h-2 overflow-hidden mb-2">
              <div
                className={`h-full transition-all duration-150 ${
                  indexingState.isPaused
                    ? "bg-amber-400"
                    : "bg-gradient-to-r from-blue-500 to-emerald-400"
                }`}
                style={{ width: `${indexingState.percent}%` }}
              />
            </div>

            <div className="flex items-center justify-between text-[11px] text-slate-300 font-mono">
              <span className="truncate max-w-md">
                {indexingState.done
                  ? `✓ ${indexingState.indexed} media files processed · ${indexingState.indexedFrames || 0} video frames · ${indexingState.failed || 0} skipped`
                  : indexingState.error
                  ? "Indexing was interrupted. Click Resume to continue from checkpoint or Stop to cancel."
                  : indexingState.isPaused
                  ? "Indexing paused. Click Resume whenever you are ready."
                  : `Processing ${indexingState.current}${indexingState.indexedFrames ? ` · ${indexingState.indexedFrames} video frames indexed` : ""}${indexingState.failed ? ` · ${indexingState.failed} failed` : ""}`}
              </span>
              {!indexingState.done && !indexingState.isPaused && !indexingState.error && (
                <span className="text-blue-300 font-semibold animate-pulse">
                  Media is being embedded and added to your library
                </span>
              )}
            </div>
            {indexingState.error && <p role="alert" className="mt-2 rounded-xl bg-red-500/20 px-3 py-2 text-[11px] text-red-200">{indexingState.error}</p>}
            {indexingState.lastError && <p className="mt-2 truncate rounded-xl bg-amber-500/15 px-3 py-2 text-[11px] text-amber-200" title={indexingState.lastError}>Skipped item: {indexingState.lastError}</p>}
          </div>
        </div>
      )}

      {/* Main Content Area: Settings Page or Photo Gallery Grid */}
      <main className="flex-1 max-w-7xl mx-auto w-full p-4 sm:p-8">
        {activePage === "settings" ? (
          <div className="max-w-3xl mx-auto space-y-6">
            {/* Settings Header */}
            <div className="flex items-center justify-between bg-[#141a22]/85 backdrop-blur-xl rounded-2xl p-5 shadow-[0_8px_32px_rgba(0,0,0,0.45)]">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-blue-500/20 flex items-center justify-center text-blue-300 shadow-[0_0_16px_rgba(59,130,246,0.2)]">
                  <Settings className="w-5 h-5" />
                </div>
                <div>
                  <h2 className="text-lg font-bold text-white">Application Settings</h2>
                  <p className="text-xs text-slate-400">
                    Manage media indexing, search relevance, and embedding dimensions
                  </p>
                </div>
              </div>
              <button
                onClick={() => setActivePage("gallery")}
                className="bg-black/50 hover:bg-white/10 backdrop-blur-md text-slate-200 hover:text-white text-xs font-semibold px-4 py-2 rounded-xl transition cursor-pointer"
              >
                ← Back to Gallery
              </button>
            </div>

            {settingsNotice && <div role="alert" className="rounded-xl bg-red-500/20 backdrop-blur-md px-4 py-3 text-xs text-red-200">{settingsNotice}</div>}

            <section className="space-y-3 rounded-2xl bg-[#141a22]/85 backdrop-blur-xl p-5 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
              <div>
                <h3 className="text-sm font-bold text-white">Display</h3>
                <p className="mt-1 text-xs text-slate-400">Choose how much file information appears in the gallery and viewer.</p>
              </div>
              <PreferenceSwitch
                label="Show filenames"
                description="When off, media names and full file paths are hidden in cards and the viewer."
                checked={Boolean(settings.show_filenames)}
                disabled={isSavingSettings}
                onChange={(value) => saveSettings({ show_filenames: value })}
              />
            </section>

            <section className="space-y-3 rounded-2xl bg-[#141a22]/85 backdrop-blur-xl p-5 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-bold text-white">Similar face search</h3>
                  <p className="mt-1 text-xs leading-relaxed text-slate-400">Local face detection powers the optional similar-face search on media in your library.</p>
                </div>
              </div>
              {["photos", "videos"].map((kind) => {
                const isPhoto = kind === "photos";
                const settingKey = isPhoto ? "face_search_photos_enabled" : "face_search_videos_enabled";
                const progress = faceIndexState?.[kind];
                const loading = progress && ["queued", "loading_models", "downloading_models", "in_progress"].includes(progress.status);
                return (
                  <div key={kind} className="space-y-2">
                    <PreferenceSwitch
                      label={isPhoto ? "Find similar faces in photos" : "Find similar faces in sampled video frames"}
                      description={isPhoto ? "Detect faces in indexed images to find visually similar faces." : "Detect faces in sampled video frames to find visually similar faces."}
                      checked={Boolean(settings[settingKey])}
                      disabled={isSavingSettings}
                      onChange={(value) => saveSettings({ [settingKey]: value })}
                    />
                    {loading && (
                      <div className="rounded-xl bg-blue-500/15 backdrop-blur-md px-4 py-3">
                        <div className="flex items-center justify-between gap-3 text-[11px] text-blue-100">
                          <span className="flex items-center gap-2"><Loader2 size={13} className="animate-spin text-blue-300" />{progress.current || "Building face index"}</span>
                          {progress.total > 0 && <span className="shrink-0 font-mono">{progress.processed || 0}/{progress.total} · {progress.percent || 0}%</span>}
                        </div>
                        {progress.total > 0 && <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-black/50"><div className="h-full bg-blue-400 transition-all" style={{ width: `${progress.percent || 0}%` }} /></div>}
                        {progress.model_percent != null && <p className="mt-1.5 text-[10px] text-slate-300">Face model download: {progress.model_percent}%</p>}
                      </div>
                    )}
                    {progress?.status === "complete" && (
                      <p className={`px-1 text-[10px] ${progress.failed_count ? "text-amber-200" : "text-emerald-300"}`}>
                        {progress.failed_count
                          ? `Face index ready · ${progress.failed_count} item(s) skipped. ${progress.error || "Check that the affected media is available and try toggling this index off and on to retry."}`
                          : `Face index ready · ${progress.total || 0} media items checked.`}
                      </p>
                    )}
                    {progress?.status === "error" && <p role="alert" className="rounded-xl bg-red-500/20 px-3 py-2 text-[11px] text-red-200">Face index error: {progress.error}</p>}
                  </div>
                );
              })}
            </section>

            {clearNotice && (
              <div className={`${clearNotice.startsWith("Could not") ? "bg-red-500/25 text-red-200" : "bg-emerald-500/20 text-emerald-200"} backdrop-blur-md rounded-xl p-4 flex items-center justify-between text-xs font-medium`}>
                <div className="flex items-center gap-2.5">
                  {clearNotice.startsWith("Could not") ? <AlertCircle className="w-4 h-4 text-red-300 shrink-0" /> : <CheckCircle2 className="w-4 h-4 text-emerald-300 shrink-0" />}
                  <span>{clearNotice}</span>
                </div>
                <button
                  onClick={() => setClearNotice(null)}
                  className="text-emerald-200 hover:text-white p-1"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            {/* Clear Indexing & Database Management Card */}
            <div className="bg-[#141a22]/85 backdrop-blur-xl rounded-2xl p-6 shadow-[0_8px_32px_rgba(0,0,0,0.45)]">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="space-y-1.5">
                  <div className="flex items-center gap-2">
                    <Trash2 className="w-4 h-4 text-red-300" />
                    <h3 className="text-sm font-bold text-white">
                      Vector Index & Database Management
                    </h3>
                  </div>
                  <p className="text-xs text-slate-400 max-w-xl leading-relaxed">
                    Clear indexed photo and video-frame embeddings from the local vector stores and reset saved indexing checkpoints. Original media files are never modified.
                  </p>
                  <div className="flex flex-wrap items-center gap-3 pt-2 text-[11px] font-mono text-slate-300">
                    <span className="bg-black/50 backdrop-blur-md px-2.5 py-1 rounded-full">
                      Indexed media vectors: <strong className="text-blue-300">{totalIndexed}</strong>
                    </span>
                    <span className="bg-black/50 backdrop-blur-md px-2.5 py-1 rounded-full">
                      Vector Store: <strong className="text-emerald-300">backend/data/lancedb</strong>
                    </span>
                  </div>
                </div>

                <button
                  onClick={handleClearAllIndexing}
                  disabled={isClearingIndex}
                  className="bg-red-500/25 hover:bg-red-500/35 disabled:opacity-50 text-red-200 hover:text-white text-xs font-bold px-5 py-3 rounded-xl shadow-[0_0_20px_rgba(239,68,68,0.2)] backdrop-blur-md flex items-center justify-center gap-2 transition shrink-0 cursor-pointer"
                >
                  {isClearingIndex ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>Clearing Index...</span>
                    </>
                  ) : (
                    <>
                      <Trash2 className="w-4 h-4" />
                      <span>Clear All Indexing</span>
                    </>
                  )}
                </button>
              </div>
            </div>

            {/* Search Accuracy & MRL Configuration Card */}
            <div className="bg-[#141a22]/85 backdrop-blur-xl rounded-2xl p-6 space-y-5 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <Sparkles className="w-4 h-4 text-blue-300" />
                    <span>Search Relevance & Embedding Dimensions (MRL)</span>
                  </h3>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Uses Gemma embeddings and cosine similarity. Large libraries use indexed candidate retrieval with exact MRL reranking.
                  </p>
                </div>
                <span className="bg-emerald-500/20 text-emerald-300 text-[11px] font-semibold px-3 py-1 rounded-full backdrop-blur-md">
                  Mean Pooling + Query Expansion Active
                </span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
                <div>
                  <label className="block text-xs font-semibold text-slate-300 mb-2">
                    Active MRL Embedding Dimension (Default: 768d)
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    {MRL_OPTIONS.map((opt) => (
                      <button
                        key={opt.dim}
                        onClick={() => handleMrlSwitch(opt.dim)}
                        className={"px-3 py-2 rounded-xl text-xs font-semibold text-left backdrop-blur-md transition cursor-pointer " + (activeMrl === opt.dim
                          ? "bg-blue-500/20 text-blue-200 shadow-[0_0_16px_rgba(59,130,246,0.2)]"
                          : "bg-black/45 text-slate-400 hover:bg-white/10 hover:text-slate-200")}
                      >
                        <div className="font-bold">{opt.label}</div>
                        <div className="text-[10px] opacity-80">{opt.desc}</div>
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-slate-300 mb-2">
                    Minimum Relevance Filter ({minAccuracy}%)
                  </label>
                  <p className="text-[11px] text-slate-400 mb-3">
                    Hide lower-ranked results in text or visual search. Relevance percentages guide ranking; they are not confidence probabilities.
                  </p>
                  <div className="flex items-center gap-2">
                    {[0, 55, 70, 80].map((val) => (
                      <button
                        key={val}
                        onClick={() => setMinAccuracy(val)}
                        className={"px-3 py-2 rounded-xl text-xs font-semibold backdrop-blur-md transition cursor-pointer " + (minAccuracy === val
                          ? "bg-emerald-500/20 text-emerald-200 shadow-[0_0_16px_rgba(16,185,129,0.2)]"
                          : "bg-black/45 text-slate-400 hover:bg-white/10 hover:text-slate-200")}
                      >
                        {val === 0 ? "Show All (0%)" : String(val) + "%+"}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {/* GPU Hardware & Model Telemetry Card */}
            <div className="bg-[#141a22]/85 backdrop-blur-xl rounded-2xl p-6 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
              <h3 className="text-sm font-bold text-white flex items-center gap-2 mb-3">
                <Cpu className="w-4 h-4 text-emerald-400" />
                <span>GPU Hardware & EmbeddingGemma-2 Engine</span>
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                <div className="bg-[#1b222c]/70 backdrop-blur-md rounded-xl p-3">
                  <div className="text-slate-400 text-[11px]">GPU Device</div>
                  <div className="text-slate-200 font-semibold mt-0.5">
                    {hardware?.gpu_name || "Detecting hardware"}
                  </div>
                </div>
                <div className="bg-[#1b222c]/70 backdrop-blur-md rounded-xl p-3">
                  <div className="text-slate-400 text-[11px]">Precision & Pooling</div>
                  <div className="text-emerald-300 font-semibold mt-0.5">
                    {hardware?.dtype || "Unknown precision"} · FP32 pooling
                  </div>
                </div>
                <div className="bg-[#1b222c]/70 backdrop-blur-md rounded-xl p-3">
                  <div className="text-slate-400 text-[11px]">VRAM Allocated</div>
                  <div className="text-blue-300 font-mono font-semibold mt-0.5">
                    {hardware?.vram_allocated_mb != null ? `${hardware.vram_allocated_mb} MB` : "Not reported"}
                  </div>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-black/45 backdrop-blur-md p-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-xs font-semibold text-slate-200"><ShieldCheck size={15} className={system?.model_loaded && !system?.is_synthetic ? "text-emerald-400" : "text-amber-400"} />{system?.model_loaded && !system?.is_synthetic ? "Gemma model loaded" : "Gemma model not active"}</div>
                  <p className="mt-1 truncate text-[11px] text-slate-400">{system?.model_id || "Model status unavailable"}{system?.is_synthetic ? " · Synthetic fallback is active; semantic search quality is unavailable." : ""}</p>
                  {system?.model_stage && ["loading", "not_started"].includes(system?.model_status) && <p className="mt-1 flex items-center gap-1.5 text-[11px] text-blue-200"><Loader2 size={12} className="animate-spin" />{system.model_stage}</p>}
                  {system?.model_status === "error" && <p role="alert" className="mt-1 text-[11px] text-red-200">Model error: {system.model_error || "Check the model setup and restart the app."}</p>}
                  {system?.video_processing_available === false && <p className="mt-1 text-[11px] text-amber-200">Video indexing unavailable · run install.bat to add OpenCV</p>}
                </div>
                <button onClick={runImageAudit} disabled={isAuditingImages} className="inline-flex items-center gap-2 rounded-xl bg-blue-500/20 backdrop-blur-md px-3.5 py-2 text-xs font-semibold text-blue-200 transition hover:bg-blue-500/30 hover:text-white disabled:opacity-60 cursor-pointer">
                  {isAuditingImages ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
                  Audit sample images
                </button>
              </div>
              {imageAudit && <div className={`mt-3 rounded-xl backdrop-blur-md p-3 text-xs ${imageAudit.error || imageAudit.is_synthetic || imageAudit.sampled === 0 || imageAudit.passed !== imageAudit.sampled ? "bg-amber-500/20 text-amber-200" : "bg-emerald-500/20 text-emerald-200"}`}>
                {imageAudit.error ? imageAudit.error : imageAudit.sampled === 0 ? "No indexed images are available to audit yet." : `${imageAudit.passed}/${imageAudit.sampled} sample images passed embedding dimension, normalization, and stored-vector consistency checks${imageAudit.is_synthetic ? " · synthetic fallback, not Gemma" : " · Gemma model"}`}
                {imageAudit.items?.filter((item) => !item.ok).map((item) => <p key={item.path || item.filename} className="mt-1 text-[11px] text-amber-100">{item.filename}: {item.reason}</p>)}
              </div>}
            </div>
          </div>

        ) : activePage === "newly_added" ? (
          <div className="space-y-6">
            {/* Header Banner */}
            <div className="flex flex-wrap items-center justify-between gap-4 bg-[#141a22]/85 backdrop-blur-xl rounded-2xl p-5 shadow-[0_8px_32px_rgba(0,0,0,0.45)]">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-blue-500/20 flex items-center justify-center text-blue-300 shadow-[0_0_16px_rgba(59,130,246,0.2)]">
                  <Clock className="w-5 h-5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-lg font-bold text-white">✨ Newly Added Media</h2>
                    {newlyAddedList.length > 0 && (
                      <span className="rounded-full bg-blue-500/25 px-2.5 py-0.5 text-xs font-mono font-semibold text-blue-200">
                        {newlyAddedList.length} items
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-400">
                    Media files newly scanned and indexed in recent sessions.
                  </p>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {/* Media Type Filters */}
                {newlyAddedList.length > 0 && (
                  <div className="flex items-center gap-1 bg-black/50 p-1 rounded-xl text-xs mr-2">
                    <button
                      type="button"
                      onClick={() => setNewlyAddedFilter("all")}
                      className={`px-2.5 py-1 rounded-lg font-medium transition cursor-pointer ${
                        newlyAddedFilter === "all" ? "bg-blue-500/25 text-blue-200" : "text-slate-400 hover:text-white"
                      }`}
                    >
                      All ({newlyAddedList.length})
                    </button>
                    <button
                      type="button"
                      onClick={() => setNewlyAddedFilter("photos")}
                      className={`px-2.5 py-1 rounded-lg font-medium transition cursor-pointer ${
                        newlyAddedFilter === "photos" ? "bg-blue-500/25 text-blue-200" : "text-slate-400 hover:text-white"
                      }`}
                    >
                      Photos ({newlyAddedList.filter((m) => !isVideoMedia(m)).length})
                    </button>
                    <button
                      type="button"
                      onClick={() => setNewlyAddedFilter("videos")}
                      className={`px-2.5 py-1 rounded-lg font-medium transition cursor-pointer ${
                        newlyAddedFilter === "videos" ? "bg-blue-500/25 text-blue-200" : "text-slate-400 hover:text-white"
                      }`}
                    >
                      Videos ({newlyAddedList.filter((m) => isVideoMedia(m)).length})
                    </button>
                  </div>
                )}

                {newlyAddedList.length > 0 && (
                  <button
                    type="button"
                    onClick={handleClearNewlyAdded}
                    className="flex items-center gap-1.5 rounded-xl bg-red-500/15 hover:bg-red-500/25 px-3 py-1.5 text-xs font-semibold text-red-200 transition cursor-pointer"
                    title="Clear newly added tracking history"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>Clear List</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setActivePage("gallery")}
                  className="bg-black/50 hover:bg-white/10 backdrop-blur-md text-slate-200 hover:text-white text-xs font-semibold px-4 py-2 rounded-xl transition cursor-pointer"
                >
                  ← Back to Library
                </button>
              </div>
            </div>

            {/* Content: Empty State vs Grid */}
            {isLoadingNewlyAdded ? (
              <div className="py-20 flex flex-col items-center justify-center text-center">
                <Loader2 className="w-8 h-8 text-blue-300 animate-spin mb-3" />
                <p className="text-sm text-slate-300 font-medium">Loading newly added media...</p>
              </div>
            ) : newlyAddedList.length === 0 ? (
              <div className="py-24 flex flex-col items-center justify-center text-center bg-[#141a22]/40 rounded-2xl border border-white/5 p-8">
                <div className="w-16 h-16 rounded-2xl bg-[#141a22]/85 backdrop-blur-xl flex items-center justify-center text-blue-300 mb-4 shadow-[0_0_24px_rgba(59,130,246,0.2)]">
                  <Sparkles className="w-8 h-8 text-blue-300" />
                </div>
                <h3 className="text-lg font-bold text-white">No Newly Added Files</h3>
                <p className="text-xs text-slate-400 max-w-sm mt-1 mb-6">
                  Files scanned during indexing will automatically appear here so you can easily review and inspect them.
                </p>
                <button
                  onClick={handlePickWindowsFolder}
                  disabled={isInspecting || isPickingFolder}
                  className="bg-blue-500/20 hover:bg-blue-500/35 backdrop-blur-md disabled:opacity-60 text-blue-200 hover:text-white text-xs font-semibold px-5 py-2.5 rounded-xl shadow-[0_0_20px_rgba(59,130,246,0.2)] flex items-center gap-2 transition cursor-pointer"
                >
                  <FolderOpen className="w-4 h-4 text-blue-300" />
                  <span>{isPickingFolder ? "Explorer Open…" : "Inspect & Index Folder"}</span>
                </button>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
                {newlyAddedList
                  .filter((item) => {
                    if (newlyAddedFilter === "photos") return !isVideoMedia(item);
                    if (newlyAddedFilter === "videos") return isVideoMedia(item);
                    return true;
                  })
                  .map((photo) => (
                    <ViewportMediaCard
                      key={photo.id || photo.path}
                      photo={photo}
                      onSelect={setSelectedPhoto}
                      showFilenames={Boolean(settings.show_filenames)}
                      videoDuration={photo.duration || videoDurations[photo.id]}
                      onHoverMediaChange={handleHoverMediaChange}
                    />
                  ))}
              </div>
            )}
          </div>

        ) : activePage === "albums" ? (
          <div className="space-y-6">
            {/* Header Banner & Breadcrumbs Toolbar */}
            <div className="bg-[#141a22]/85 backdrop-blur-xl rounded-2xl p-5 shadow-[0_8px_32px_rgba(0,0,0,0.45)] space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-blue-500/20 flex items-center justify-center text-blue-300 shadow-[0_0_16px_rgba(59,130,246,0.2)]">
                    <FolderTree className="w-5 h-5" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-lg font-bold text-white">📁 Albums & Folder Explorer</h2>
                      {albumData?.all_albums?.length > 0 && (
                        <span className="rounded-full bg-blue-500/25 px-2.5 py-0.5 text-xs font-mono font-semibold text-blue-200">
                          {albumData.all_albums.length} folders
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-slate-400">
                      {albumViewMode === "all_albums"
                        ? "Browse all distinct media folders across your indexed storage."
                        : `Current folder: ${albumData?.current_path || "Root Explorer"}`}
                    </p>
                  </div>
                </div>

                {/* Right controls: View mode toggles & Open in explorer */}
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex items-center gap-1 bg-black/50 p-1 rounded-xl text-xs">
                    <button
                      type="button"
                      onClick={() => setAlbumViewMode("explorer")}
                      className={`px-3 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition cursor-pointer ${
                        albumViewMode === "explorer"
                          ? "bg-blue-500/25 text-blue-200 shadow-[0_0_12px_rgba(59,130,246,0.2)]"
                          : "text-slate-400 hover:text-white"
                      }`}
                    >
                      <FolderTree className="w-3.5 h-3.5" />
                      <span>Explorer View</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setAlbumViewMode("all_albums")}
                      className={`px-3 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition cursor-pointer ${
                        albumViewMode === "all_albums"
                          ? "bg-blue-500/25 text-blue-200 shadow-[0_0_12px_rgba(59,130,246,0.2)]"
                          : "text-slate-400 hover:text-white"
                      }`}
                    >
                      <Grid className="w-3.5 h-3.5" />
                      <span>All Albums ({albumData?.all_albums?.length || 0})</span>
                    </button>
                  </div>

                  {albumViewMode === "explorer" && albumData?.parent_path && (
                    <button
                      type="button"
                      onClick={() => loadAlbumFolder(albumData.parent_path)}
                      className="flex items-center gap-1.5 rounded-xl bg-black/50 hover:bg-white/10 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white transition cursor-pointer"
                      title="Go to parent directory"
                    >
                      <ArrowUp className="w-3.5 h-3.5 text-blue-300" />
                      <span>Up</span>
                    </button>
                  )}

                  {albumData?.current_path && (
                    <button
                      type="button"
                      onClick={() => handleOpenFolderInExplorer(albumData.current_path)}
                      className="flex items-center gap-1.5 rounded-xl bg-black/50 hover:bg-white/10 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white transition cursor-pointer"
                      title="Reveal in Windows File Explorer"
                    >
                      <ExternalLink className="w-3.5 h-3.5 text-blue-300" />
                      <span className="hidden sm:inline">Windows Explorer</span>
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={() => setActivePage("gallery")}
                    className="bg-black/50 hover:bg-white/10 backdrop-blur-md text-slate-200 hover:text-white text-xs font-semibold px-4 py-2 rounded-xl transition cursor-pointer"
                  >
                    ← Back to Library
                  </button>
                </div>
              </div>

              {/* Breadcrumbs Navigation Bar (Explorer Mode) */}
              {albumViewMode === "explorer" && albumData?.breadcrumbs && (
                <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-white/5 text-xs">
                  <span className="text-slate-500 font-medium mr-1 flex items-center gap-1">
                    <Folder className="w-3.5 h-3.5 text-blue-400" /> Location:
                  </span>
                  {albumData.breadcrumbs.map((crumb, idx) => {
                    const isLast = idx === albumData.breadcrumbs.length - 1;
                    return (
                      <React.Fragment key={crumb.path || idx}>
                        {idx > 0 && <ChevronRight className="w-3 h-3 text-slate-600 shrink-0" />}
                        <button
                          type="button"
                          onClick={() => loadAlbumFolder(crumb.path)}
                          disabled={isLast}
                          className={`px-2 py-0.5 rounded-md transition font-mono ${
                            isLast
                              ? "font-bold text-blue-200 bg-blue-500/20"
                              : "text-slate-400 hover:text-white hover:bg-white/5 cursor-pointer"
                          }`}
                        >
                          {crumb.name}
                        </button>
                      </React.Fragment>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Main Content Area */}
            {isLoadingAlbum && !albumData ? (
              <div className="py-20 flex flex-col items-center justify-center text-center">
                <Loader2 className="w-8 h-8 text-blue-300 animate-spin mb-3" />
                <p className="text-sm text-slate-300 font-medium">Loading folder structure and album files...</p>
              </div>
            ) : albumViewMode === "all_albums" ? (
              /* All Albums Grid View */
              <div className="space-y-4">
                {/* Search / Filter for Albums */}
                <div className="flex flex-wrap items-center justify-between gap-3 bg-[#141a22]/60 backdrop-blur-md p-3 rounded-xl border border-white/5">
                  <div className="flex items-center gap-2 flex-1 max-w-md">
                    <Search className="w-4 h-4 text-slate-400 shrink-0" />
                    <input
                      type="text"
                      placeholder="Search album folders by name..."
                      value={albumFilterText}
                      onChange={(e) => setAlbumFilterText(e.target.value)}
                      className="w-full bg-transparent text-xs text-white placeholder-slate-500 focus:outline-none"
                    />
                    {albumFilterText && (
                      <button onClick={() => setAlbumFilterText("")} className="text-slate-400 hover:text-white">
                        <X size={13} />
                      </button>
                    )}
                  </div>
                  <div className="text-xs text-slate-400 font-mono">
                    Showing {(albumData?.all_albums || []).filter((a) => !albumFilterText || a.name.toLowerCase().includes(albumFilterText.toLowerCase()) || a.path.toLowerCase().includes(albumFilterText.toLowerCase())).length} of {albumData?.all_albums?.length || 0} albums
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
                  {(albumData?.all_albums || [])
                    .filter((a) => !albumFilterText || a.name.toLowerCase().includes(albumFilterText.toLowerCase()) || a.path.toLowerCase().includes(albumFilterText.toLowerCase()))
                    .map((album) => (
                      <div
                        key={album.path}
                        onClick={() => {
                          setAlbumViewMode("explorer");
                          loadAlbumFolder(album.path);
                        }}
                        className="group relative flex flex-col bg-[#141a22]/85 rounded-2xl overflow-hidden hover:bg-[#1b222c]/95 shadow-[0_8px_28px_rgba(0,0,0,0.45)] hover:shadow-[0_14px_40px_rgba(0,0,0,0.7)] transition-all duration-200 cursor-pointer border border-white/5 hover:border-blue-500/30"
                      >
                        <div className="relative aspect-video w-full overflow-hidden bg-[#0d0f12]">
                          {album.cover_media ? (
                            <img
                              src={album.cover_media.media_type === "video"
                                ? getVideoFrameUrl(album.cover_media.path, album.cover_media.timestamp || 0)
                                : getPhotoUrl(album.cover_media, true)}
                              alt={album.name}
                              loading="lazy"
                              decoding="async"
                              className="h-full w-full object-cover transition-all duration-300 group-hover:scale-105"
                            />
                          ) : (
                            <div className="h-full w-full flex items-center justify-center text-blue-400/40 bg-gradient-to-br from-blue-950/20 to-black">
                              <Folder className="w-12 h-12" />
                            </div>
                          )}
                          <div className="absolute top-2.5 right-2.5">
                            <span className="rounded-full bg-black/60 backdrop-blur-md px-2.5 py-1 text-xs font-mono font-semibold text-white shadow-md">
                              {album.total_count} items
                            </span>
                          </div>
                        </div>

                        <div className="p-4 bg-[#12161c]/70 flex flex-col justify-between flex-1">
                          <div>
                            <div className="flex items-center gap-1.5">
                              <Folder className="w-4 h-4 text-blue-400 shrink-0" />
                              <h3 title={album.name} className="font-bold text-sm text-white truncate group-hover:text-blue-300 transition-colors">
                                {album.name}
                              </h3>
                            </div>
                            <p title={album.path} className="text-[11px] font-mono text-slate-400 truncate mt-1">
                              {album.path}
                            </p>
                          </div>

                          <div className="mt-3 pt-3 border-t border-white/5 flex items-center justify-between text-xs text-slate-400">
                            <span>
                              {album.photo_count > 0 && `${album.photo_count} photos`}
                              {album.photo_count > 0 && album.video_count > 0 && " · "}
                              {album.video_count > 0 && `${album.video_count} videos`}
                            </span>
                            <span className="text-blue-300 group-hover:translate-x-0.5 transition-transform flex items-center gap-1 font-semibold text-[11px]">
                              Open →
                            </span>
                          </div>
                        </div>
                      </div>
                    ))}
                </div>
              </div>
            ) : (
              /* Explorer View (Hierarchical Directory Browser) */
              <div className="space-y-6">
                {/* Subfolders Grid (if this directory contains subfolders) */}
                {albumData?.subfolders?.length > 0 && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
                        <Folder className="w-3.5 h-3.5 text-blue-400" />
                        Subfolders ({albumData.subfolders.length})
                      </h3>
                      {albumData.parent_path && (
                        <button
                          onClick={() => loadAlbumFolder(albumData.parent_path)}
                          className="text-xs text-blue-300 hover:text-white flex items-center gap-1"
                        >
                          <ArrowUp size={12} /> Up to parent
                        </button>
                      )}
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                      {albumData.subfolders.map((sub) => (
                        <div
                          key={sub.path}
                          onClick={() => loadAlbumFolder(sub.path)}
                          className="group flex items-center gap-3 bg-[#141a22]/85 hover:bg-[#1b222c]/95 p-3 rounded-xl border border-white/5 hover:border-blue-500/30 transition shadow-md cursor-pointer"
                        >
                          <div className="relative w-12 h-12 rounded-lg overflow-hidden bg-black/40 shrink-0 flex items-center justify-center">
                            {sub.cover_media ? (
                              <img
                                src={sub.cover_media.media_type === "video"
                                  ? getVideoFrameUrl(sub.cover_media.path, sub.cover_media.timestamp || 0)
                                  : getPhotoUrl(sub.cover_media, true)}
                                alt={sub.name}
                                className="h-full w-full object-cover group-hover:scale-110 transition duration-300"
                              />
                            ) : (
                              <Folder className="w-6 h-6 text-blue-400" />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <p title={sub.name} className="font-semibold text-xs text-white truncate group-hover:text-blue-300 transition-colors">
                              {sub.name}
                            </p>
                            <p className="text-[10px] text-slate-400 font-mono mt-0.5">
                              {sub.total_count} item{sub.total_count === 1 ? "" : "s"}
                              {sub.has_subfolders && " · subfolders"}
                            </p>
                          </div>
                          <ChevronRight className="w-4 h-4 text-slate-500 group-hover:text-blue-300 shrink-0 transition" />
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Media Files in this Folder */}
                <div className="space-y-4 pt-2">
                  <div className="flex flex-wrap items-center justify-between gap-3 bg-[#141a22]/60 backdrop-blur-md p-3.5 rounded-xl border border-white/5">
                    <div className="flex flex-wrap items-center gap-3">
                      <div className="text-xs font-semibold text-white flex items-center gap-1.5">
                        <HardDrive className="w-3.5 h-3.5 text-blue-300" />
                        <span>Media in this folder ({albumData?.media_total || 0})</span>
                      </div>

                      {/* Include subfolders toggle */}
                      <label className="flex items-center gap-1.5 text-xs text-slate-300 hover:text-white cursor-pointer select-none">
                        <input
                          type="checkbox"
                          checked={albumIncludeSubfolders}
                          onChange={(e) => {
                            setAlbumIncludeSubfolders(e.target.checked);
                            loadAlbumFolder(currentAlbumFolder, e.target.checked);
                          }}
                          className="rounded bg-black/40 border-white/10 text-blue-500 focus:ring-0 cursor-pointer"
                        />
                        <span>Include subfolder files</span>
                      </label>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      {/* Filter by media type */}
                      <div className="flex items-center gap-1 bg-black/50 p-1 rounded-xl">
                        <button
                          type="button"
                          onClick={() => setAlbumMediaFilter("all")}
                          className={`px-2.5 py-1 rounded-lg font-medium transition cursor-pointer ${
                            albumMediaFilter === "all" ? "bg-blue-500/25 text-blue-200" : "text-slate-400 hover:text-white"
                          }`}
                        >
                          All ({albumData?.media_total && albumData.media_total > (albumData.media?.length || 0) ? `${albumData.media.length} of ${albumData.media_total}` : (albumData?.media?.length || 0)})
                        </button>
                        <button
                          type="button"
                          onClick={() => setAlbumMediaFilter("photos")}
                          className={`px-2.5 py-1 rounded-lg font-medium transition cursor-pointer ${
                            albumMediaFilter === "photos" ? "bg-blue-500/25 text-blue-200" : "text-slate-400 hover:text-white"
                          }`}
                        >
                          Photos ({albumData?.media?.filter((m) => !isVideoMedia(m)).length || 0})
                        </button>
                        <button
                          type="button"
                          onClick={() => setAlbumMediaFilter("videos")}
                          className={`px-2.5 py-1 rounded-lg font-medium transition cursor-pointer ${
                            albumMediaFilter === "videos" ? "bg-blue-500/25 text-blue-200" : "text-slate-400 hover:text-white"
                          }`}
                        >
                          Videos ({albumData?.media?.filter((m) => isVideoMedia(m)).length || 0})
                        </button>
                      </div>

                      {/* Sort options */}
                      <select
                        value={`${albumSortBy}-${albumSortOrder}`}
                        onChange={(e) => {
                          const [sb, so] = e.target.value.split("-");
                          setAlbumSortBy(sb);
                          setAlbumSortOrder(so);
                          loadAlbumFolder(currentAlbumFolder, albumIncludeSubfolders, sb, so);
                        }}
                        className="bg-black/50 text-slate-300 rounded-xl px-2.5 py-1.5 border-none focus:outline-none cursor-pointer"
                      >
                        <option value="name-asc">Sort: Name (A → Z)</option>
                        <option value="name-desc">Sort: Name (Z → A)</option>
                        <option value="date-desc">Sort: Date (Newest)</option>
                        <option value="date-asc">Sort: Date (Oldest)</option>
                        <option value="size-desc">Sort: Size (Largest)</option>
                        <option value="type-asc">Sort: Type (Photos first)</option>
                      </select>
                    </div>
                  </div>

                  {/* Media Grid */}
                  {!albumData?.media || albumData.media.length === 0 ? (
                    <div className="py-16 flex flex-col items-center justify-center text-center bg-[#141a22]/30 rounded-2xl border border-white/5 p-8">
                      <FolderOpen className="w-10 h-10 text-slate-500 mb-2" />
                      <h4 className="text-sm font-semibold text-slate-200">No media files directly in this directory level</h4>
                      <p className="text-xs text-slate-400 mt-1 max-w-sm">
                        {albumData?.subfolders?.length > 0
                          ? "Check the subfolders above, or tick 'Include subfolder files' to see everything nested here."
                          : "This directory contains no indexed media files."}
                      </p>
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
                      {albumData.media
                        .filter((m) => {
                          if (albumMediaFilter === "photos") return !isVideoMedia(m);
                          if (albumMediaFilter === "videos") return isVideoMedia(m);
                          return true;
                        })
                        .map((photo) => (
                          <ViewportMediaCard
                            key={photo.id || photo.path}
                            photo={photo}
                            onSelect={setSelectedPhoto}
                            showFilenames={Boolean(settings.show_filenames)}
                            videoDuration={photo.duration || videoDurations[photo.id]}
                            onHoverMediaChange={handleHoverMediaChange}
                          />
                        ))}
                    </div>
                  )}

                  {/* Load More Button for large folders */}
                  {albumData?.media_has_more && (
                    <div className="flex justify-center py-6">
                      <button
                        onClick={loadMoreAlbumMedia}
                        disabled={isLoadingMoreAlbum}
                        className="rounded-xl bg-black/50 backdrop-blur-md px-5 py-2.5 text-xs font-semibold text-slate-200 hover:bg-white/10 hover:text-white disabled:opacity-60 cursor-pointer"
                      >
                        {isLoadingMoreAlbum ? (
                          <>
                            <Loader2 className="mr-2 inline h-4 w-4 animate-spin text-blue-300" />
                            Loading more files…
                          </>
                        ) : (
                          `Load more files (${albumData.media_total - albumData.media.length} remaining)`
                        )}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

        ) : (
          <>
            {isSearching && (
              <div className="py-20 flex flex-col items-center justify-center text-center">
                <Loader2 className="w-8 h-8 text-blue-300 animate-spin mb-3" />
                <p className="text-sm text-slate-300 font-medium">
                  Searching photos and sampled video frames with {activeMrl}d vectors...
                </p>
              </div>
            )}

            {!isSearching && photos.length === 0 ? (
              <div className="py-24 flex flex-col items-center justify-center text-center">
                <div className="w-16 h-16 rounded-2xl bg-[#141a22]/85 backdrop-blur-xl flex items-center justify-center text-blue-300 mb-4 shadow-[0_0_24px_rgba(59,130,246,0.2)]">
                  <FolderOpen className="w-8 h-8 text-blue-300" />
                </div>
                <h3 className="text-lg font-bold text-white">{activeSearch ? "No matching media found" : "No media indexed yet"}</h3>
                <p className="text-xs text-slate-400 max-w-sm mt-1 mb-6">
                  {activeSearch ? "Try another search or clear this filter to return to your library." : "Select a folder with photos or videos. Gemma can search indexed images and sampled moments inside your clips."}
                </p>
                <div className="flex flex-wrap items-center justify-center gap-3">
                  {activeSearch ? (
                    <button onClick={clearActiveSearch} className="bg-blue-500/20 hover:bg-blue-500/35 backdrop-blur-md text-blue-200 hover:text-white text-xs font-semibold px-5 py-2.5 rounded-xl shadow-[0_0_20px_rgba(59,130,246,0.2)] transition cursor-pointer">Clear search and show library</button>
                  ) : (
                    <button
                      onClick={handlePickWindowsFolder}
                      disabled={isInspecting || isPickingFolder}
                      className="bg-blue-500/20 hover:bg-blue-500/35 backdrop-blur-md disabled:opacity-60 text-blue-200 hover:text-white text-xs font-semibold px-5 py-2.5 rounded-xl shadow-[0_0_20px_rgba(59,130,246,0.2)] flex items-center gap-2 transition cursor-pointer"
                    >
                      <FolderOpen className="w-4 h-4 text-blue-300" />
                      <span>{isPickingFolder ? "Explorer Open…" : "Inspect & Index Folder"}</span>
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
                {visiblePhotos.map((photo) => (
                    <ViewportMediaCard
                      key={photo.id}
                      photo={photo}
                      onSelect={setSelectedPhoto}
                      showFilenames={Boolean(settings.show_filenames)}
                      videoDuration={videoDurations[photo.id]}
                      onHoverMediaChange={handleHoverMediaChange}
                    />
                  ))}
              </div>
            )}
            {!activeSearch && hasMoreMedia && (
              <div ref={loadMoreSentinelRef} className="flex justify-center py-8">
                <button onClick={loadMoreLibrary} disabled={isLoadingMore} className="rounded-xl bg-black/50 backdrop-blur-md px-5 py-2.5 text-xs font-semibold text-slate-200 hover:bg-white/10 hover:text-white disabled:opacity-60 cursor-pointer">
                  {isLoadingMore ? <><Loader2 className="mr-2 inline h-4 w-4 animate-spin text-blue-300" />Loading more…</> : "Load more media"}
                </button>
              </div>
            )}
          </>
        )}
      </main>

      {/* Select Folder & Inspection Dialog Modal */}
      {folderModalOpen && (
        <div
          onClick={() => {
            if (isPickingFolder) return;
            setFolderModalOpen(false);
            setInspectedFolder(null);
          }}
          className="fixed inset-0 z-50 bg-black/75 backdrop-blur-md flex items-center justify-center p-4"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="relative w-full max-w-xl bg-[#141a22]/95 backdrop-blur-xl rounded-2xl p-6 shadow-[0_28px_80px_rgba(0,0,0,0.85)] max-h-[92vh] overflow-y-auto"
          >
            {/* Header */}
            <div className="flex items-center justify-between pb-4">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-xl bg-blue-500/20 text-blue-300 flex items-center justify-center">
                  <FolderOpen className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-white">Select Folder to Index</h3>
                  <p className="text-xs text-slate-400">Instantly inspects photos and estimates GPU indexing time</p>
                </div>
              </div>
              <button
                disabled={isPickingFolder}
                onClick={() => {
                  setFolderModalOpen(false);
                  setInspectedFolder(null);
                }}
                className="w-8 h-8 rounded-full bg-black/45 hover:bg-white/10 flex items-center justify-center text-slate-400 hover:text-white transition cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {folderNotice && <div role="alert" className="mt-4 rounded-xl bg-amber-500/20 px-3 py-2 text-xs text-amber-200">{folderNotice}</div>}

            {/* Instant Inspection Card (When a folder is selected) */}
            {inspectedFolder ? (
              <div className="mt-5 space-y-4">
                <div className="bg-[#1b222c]/85 backdrop-blur-md rounded-xl p-5 shadow-lg">
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div className="min-w-0">
                      <span className={`text-[10px] uppercase font-bold tracking-wider ${inspectedFolder.unindexed_files === 0 ? "text-emerald-300" : "text-blue-300"}`}>
                        {inspectedFolder.unindexed_files === 0 ? "Folder Up To Date" : "Folder Ready For Indexing"}
                      </span>
                      <p className="text-xs font-mono font-semibold text-white truncate mt-0.5">
                        {inspectedFolder.directory}
                      </p>
                    </div>
                    <span className="bg-emerald-500/20 text-emerald-300 text-[11px] font-bold px-2.5 py-0.5 rounded-full shrink-0">
                      {system?.gpu_name || system?.device || "Local device"}
                    </span>
                  </div>

                    <div className="grid grid-cols-2 gap-4 py-3.5 text-xs">
                    <div>
                      <span className="text-slate-400 text-[11px]">Media files found:</span>
                      <p className="text-lg font-bold text-white mt-0.5">
                        {inspectedFolder.total_files} files
                      </p>
                      <p className="mt-1 flex items-center gap-2 text-[10px] text-slate-400"><span>{inspectedFolder.image_files ?? inspectedFolder.total_files} images</span><span>·</span><span>{inspectedFolder.video_files ?? 0} videos</span></p>
                      {inspectedFolder.already_indexed > 0 && (
                        <p className="text-[10px] text-emerald-300 font-medium mt-0.5">
                          ✓ {inspectedFolder.already_indexed} already in library
                        </p>
                      )}
                      {inspectedFolder.partially_indexed > 0 && (
                        <p className="mt-1 text-[10px] font-medium text-amber-200">
                          {inspectedFolder.partially_indexed} video(s) have missing frames and will resume
                        </p>
                      )}
                    </div>
                    <div>
                      <span className="text-slate-400 text-[11px]">Indexing estimate:</span>
                      <p className="text-lg font-bold text-emerald-300 font-mono mt-0.5">
                        {inspectedFolder.estimated_time_str}
                      </p>
                      <p className="text-[10px] text-slate-400 mt-0.5">
                        Actual ETA updates once indexing starts
                      </p>
                    </div>
                  </div>

                  {(inspectedFolder.video_files || 0) > 0 && (
                    <div className="mt-4 rounded-xl bg-black/45 backdrop-blur-md p-4">
                      <div className="flex items-center justify-between gap-3">
                        <label htmlFor="video-interval-sec" className="text-xs font-semibold text-slate-200">Video frame sampling interval</label>
                        <span className="rounded-full bg-blue-500/20 px-2.5 py-0.5 text-xs font-mono font-semibold text-blue-200">
                          {frameRate <= 0.05
                            ? "1 frame every 30s (Ultra-Fast ⚡⚡⚡)"
                            : frameRate <= 0.15
                            ? "1 frame every 10s (Fast ⚡⚡)"
                            : frameRate <= 0.25
                            ? "1 frame every 5s (Balanced ⚡)"
                            : frameRate <= 0.6
                            ? "1 frame every 2s (Standard)"
                            : frameRate <= 1.05
                            ? "1 frame every 1s (Dense)"
                            : `${frameRate.toFixed(1)} frames/sec`}
                        </span>
                      </div>

                      {/* Quick fixed time duration presets */}
                      <div className="mt-2.5 flex flex-wrap gap-1.5">
                        {[
                          { label: "Every 30s ⚡⚡⚡", sec: 30, rate: 1 / 30, desc: "Ultra-Fast (15×)" },
                          { label: "Every 10s ⚡⚡", sec: 10, rate: 0.1, desc: "Fast (5×)" },
                          { label: "Every 5s ⚡", sec: 5, rate: 0.2, desc: "Balanced" },
                          { label: "Every 2s", sec: 2, rate: 0.5, desc: "Standard" },
                          { label: "Every 1s", sec: 1, rate: 1.0, desc: "Dense" },
                        ].map((preset) => {
                          const currentSec = frameRate > 0 ? Math.round(1 / frameRate) : 30;
                          const isSelected = currentSec === preset.sec;
                          return (
                            <button
                              key={preset.sec}
                              type="button"
                              onClick={() => {
                                setFrameRate(preset.rate);
                                if (inspectedFolder?.directory) {
                                  handleInspectFolder(inspectedFolder.directory, preset.rate, adaptiveSampling);
                                }
                              }}
                              className={`rounded-lg px-2.5 py-1 text-xs font-medium transition cursor-pointer ${
                                isSelected
                                  ? "bg-blue-500/25 text-blue-200 shadow-sm font-semibold"
                                  : "bg-black/45 text-slate-300 hover:bg-white/10 hover:text-white"
                              }`}
                              title={preset.desc}
                            >
                              {preset.label}
                            </button>
                          );
                        })}
                      </div>

                      {/* Interval Slider (in seconds) */}
                      <div className="mt-3">
                        <div className="flex justify-between text-[11px] text-slate-400">
                          <span>Custom interval: {frameRate > 0 ? Math.round(1 / frameRate) : 30} seconds</span>
                          <span>{frameRate <= 0.05 ? "15× Speedup" : frameRate <= 0.1 ? "5× Speedup" : "Standard"}</span>
                        </div>
                        <input
                          id="video-interval-sec"
                          type="range"
                          min="1"
                          max="60"
                          step="1"
                          value={frameRate > 0 ? Math.min(60, Math.max(1, Math.round(1 / frameRate))) : 30}
                          onChange={(event) => setFrameRate(1.0 / Number(event.target.value))}
                          onPointerUp={(event) => handleInspectFolder(inspectedFolder.directory, 1.0 / Number(event.currentTarget.value), adaptiveSampling)}
                          onKeyUp={(event) => handleInspectFolder(inspectedFolder.directory, 1.0 / Number(event.currentTarget.value), adaptiveSampling)}
                          className="mt-1.5 w-full accent-blue-400 cursor-pointer"
                        />
                        <div className="mt-0.5 flex justify-between text-[10px] text-slate-500">
                          <span>1s · dense scene coverage</span>
                          <span>30s · ultra-fast</span>
                          <span>60s · sparse</span>
                        </div>
                      </div>

                      <div className="mt-3 flex items-center justify-between pt-3">
                        <div>
                          <span className="text-xs font-semibold text-slate-200">Smart Scene Detection</span>
                          <p className="text-[10px] text-slate-400">Skips redundant stationary frames while capturing all scene shifts (3× faster).</p>
                        </div>
                        <input
                          type="checkbox"
                          checked={adaptiveSampling}
                          onChange={(e) => {
                            setAdaptiveSampling(e.target.checked);
                            if (inspectedFolder?.directory) {
                              handleInspectFolder(inspectedFolder.directory, frameRate, e.target.checked);
                            }
                          }}
                          className="h-4 w-4 rounded bg-black/50 text-blue-500 accent-blue-400 cursor-pointer"
                        />
                      </div>
                      <p className="mt-2 text-[11px] leading-relaxed text-slate-400">Frames are embedded with Gemma so text and image searches can find moments inside your videos.</p>
                      {system?.video_processing_available === false && <p className="mt-2 rounded-lg bg-amber-500/20 p-2 text-[11px] text-amber-200">Install video indexing support by running install.bat, then restart GemmaGallery.</p>}
                    </div>
                  )}

                  <div className="mt-4 flex items-center gap-2.5">
                    <button
                      onClick={() => startIndexingFolder(inspectedFolder.directory, false, frameRate, adaptiveSampling)}
                      disabled={system?.model_status === "loading" || system?.model_status === "not_started"}
                      className="flex-1 bg-blue-500/25 hover:bg-blue-500/35 disabled:cursor-wait disabled:opacity-50 text-blue-100 hover:text-white font-bold py-2.5 px-4 rounded-xl flex items-center justify-center gap-2 shadow-[0_0_20px_rgba(59,130,246,0.2)] transition active:scale-98 cursor-pointer"
                    >
                      <Zap className="w-4 h-4 text-blue-300" />
                      <span>
                        {system?.model_status === "loading" || system?.model_status === "not_started"
                          ? "Waiting for Gemma model…"
                          : inspectedFolder.unindexed_files === 0
                          ? `All ${inspectedFolder.total_files} files indexed · Sync & Re-check`
                          : `Index ${inspectedFolder.unindexed_files} file(s) with remaining work`}
                      </span>
                    </button>
                    <button
                      onClick={handlePickWindowsFolder}
                      disabled={isPickingFolder || isInspecting}
                      className="bg-black/50 hover:bg-white/10 text-slate-200 font-semibold py-2.5 px-3.5 rounded-xl transition cursor-pointer text-xs flex items-center gap-1.5"
                      title="Pick another folder in Windows Explorer"
                    >
                      <FolderOpen className="w-3.5 h-3.5 text-blue-300" />
                      <span>Explorer</span>
                    </button>
                    <button
                      onClick={() => setInspectedFolder(null)}
                      className="bg-black/50 hover:bg-white/10 text-slate-300 font-semibold py-2.5 px-3 rounded-xl transition cursor-pointer text-xs"
                    >
                      Change
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              /* Folder Selection Options */
              <div className="mt-5 space-y-4">
                {/* 1. Dynamically available folders */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-xs text-slate-300">
                    <span className="font-semibold flex items-center gap-1.5 text-blue-300">
                      <Sparkles className="w-3.5 h-3.5 text-blue-300" />
                      Available Folders
                    </span>
                    <span className="text-[10px] text-slate-500">Recent and suggested locations</span>
                  </div>
                  {browseData.presets.length ? browseData.presets.map((preset) => (
                    <button
                      key={preset.path}
                      onClick={() => handleInspectFolder(preset.path)}
                      disabled={isInspecting}
                      className="w-full bg-[#1b222c]/70 hover:bg-blue-500/20 text-left p-3.5 rounded-xl flex items-center justify-between group transition cursor-pointer"
                    >
                      <div className="min-w-0">
                        <p className="text-xs font-semibold text-white">{preset.name}</p>
                        <p className="mt-1 truncate text-[11px] text-slate-400 font-mono" title={preset.path}>{preset.path}</p>
                        <p className="mt-0.5 text-[10px] text-blue-300">{preset.tag}</p>
                      </div>
                      <ChevronRight className="w-4 h-4 shrink-0 text-slate-400 group-hover:text-blue-300" />
                    </button>
                  )) : (
                    <p className="rounded-xl bg-black/45 p-3 text-[11px] text-slate-400">No recent folders yet. Choose a folder or browse a drive below.</p>
                  )}
                </div>

                {/* 2. Interactive Drive & Folder Browser */}
                <div className="bg-[#1b222c]/65 backdrop-blur-md rounded-xl p-3.5 space-y-3">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-semibold text-slate-300 flex items-center gap-1.5">
                      <HardDrive className="w-3.5 h-3.5 text-blue-300" />
                      Browse Drives & Folders
                    </span>
                    {browseData.current_path && (
                      <span className="text-[10px] text-slate-400 font-mono truncate max-w-[200px]">
                        {browseData.current_path}
                      </span>
                    )}
                  </div>

                  {/* Drive Quick Chips */}
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-slate-400">Drives:</span>
                    {browseData.drives.map((d) => (
                      <button
                        key={d}
                        onClick={() => loadDirectoryBrowse(d)}
                        className={`text-xs px-2.5 py-1 rounded-lg font-mono font-bold transition cursor-pointer ${
                          browseData.current_path.startsWith(d)
                            ? "bg-blue-500/25 text-blue-200 shadow-sm"
                            : "bg-black/45 hover:bg-white/10 text-slate-300"
                        }`}
                      >
                        {d}
                      </button>
                    ))}
                  </div>

                  {/* Subdirectories List (When browsing a drive/path) */}
                  {browseData.current_path && (
                    <div className="space-y-2 pt-1">
                      <div className="flex items-center justify-between text-[11px]">
                        <button
                          onClick={() => loadDirectoryBrowse(browseData.parent_path || "")}
                          className="text-blue-300 hover:text-blue-200 flex items-center gap-1 cursor-pointer"
                        >
                          <ArrowLeft className="w-3 h-3" /> Back
                        </button>
                        <button
                          onClick={() => handleInspectFolder(browseData.current_path)}
                          className="bg-blue-500/25 hover:bg-blue-500/35 text-blue-200 px-2.5 py-1 rounded-full font-semibold text-[11px] flex items-center gap-1 cursor-pointer"
                        >
                          <Check className="w-3 h-3" /> Select this folder
                        </button>
                      </div>

                      <div className="max-h-36 overflow-y-auto space-y-1 pr-1 font-mono text-xs">
                        {isBrowsingDir ? (
                          <div className="py-4 flex items-center justify-center text-slate-400 text-xs">
                            <Loader2 className="w-4 h-4 animate-spin mr-2" /> Loading folders...
                          </div>
                        ) : browseData.subdirectories.length === 0 ? (
                          <p className="text-[11px] text-slate-500 py-2">No subfolders found.</p>
                        ) : (
                          browseData.subdirectories.map((name) => {
                            const full = `${browseData.current_path.replace(/\\$/, '')}\\${name}`;
                            return (
                              <div
                                key={name}
                                onClick={() => loadDirectoryBrowse(full)}
                                className="flex items-center justify-between p-1.5 rounded-lg hover:bg-white/10 text-slate-300 hover:text-white cursor-pointer transition"
                              >
                                <span className="flex items-center gap-1.5 truncate">
                                  <Folder className="w-3.5 h-3.5 text-blue-300 shrink-0" />
                                  <span className="truncate">{name}</span>
                                </span>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleInspectFolder(full);
                                  }}
                                  title="Inspect and index this folder"
                                  className="text-[10px] text-blue-300 hover:text-white bg-blue-500/20 hover:bg-blue-500/40 px-2 py-0.5 rounded-full transition"
                                >
                                  Pick
                                </button>
                              </div>
                            );
                          })
                        )}
                      </div>
                    </div>
                  )}
                </div>

                {/* 3. Manual Path Input Bar + Direct Explorer Inspect Button (Camera-style) */}
                <div className="pt-1">
                  <label className="block text-[11px] font-semibold text-slate-400 mb-1.5">
                    Paste directory path or click Inspect to open Windows Explorer:
                  </label>
                  <div className="flex gap-2">
                    <div className="relative flex-1 flex items-center">
                      <input
                        type="text"
                        value={folderInput}
                        onChange={(e) => setFolderInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            if (folderInput.trim()) handleInspectFolder(folderInput.trim());
                            else handlePickWindowsFolder();
                          }
                        }}
                        placeholder="e.g. C:\\Users\\You\\Pictures"
                        className="w-full bg-black/50 backdrop-blur-md rounded-full py-2 pl-4 pr-11 text-xs text-white focus:outline-none focus:bg-black/65 font-mono shadow-inner"
                      />
                      <button
                        type="button"
                        onClick={handlePickWindowsFolder}
                        disabled={isInspecting || isPickingFolder}
                        title="Open Windows Explorer to pick & inspect a folder"
                        className="absolute right-2 w-7 h-7 rounded-full bg-blue-500/20 hover:bg-blue-500/35 text-blue-300 hover:text-white flex items-center justify-center transition cursor-pointer"
                      >
                        <FolderOpen className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    <button
                      onClick={() => {
                        if (folderInput.trim()) {
                          handleInspectFolder(folderInput.trim());
                        } else {
                          handlePickWindowsFolder();
                        }
                      }}
                      disabled={isInspecting || isPickingFolder}
                      title="Inspect typed path or open Windows Explorer to select a folder"
                      className="bg-blue-500/25 hover:bg-blue-500/35 disabled:opacity-50 text-blue-200 hover:text-white text-xs font-semibold px-4 py-2 rounded-full flex items-center gap-1.5 transition cursor-pointer shadow-[0_0_16px_rgba(59,130,246,0.2)]"
                    >
                      <FolderOpen className="w-3.5 h-3.5" />
                      <span>{isInspecting || isPickingFolder ? "Inspecting…" : "Inspect"}</span>
                    </button>
                  </div>
                </div>

                {/* 4. Native Windows Dialog Button (with Cancel protection) */}
                <div className="pt-2">
                  {isPickingFolder ? (
                    <div className="bg-blue-500/15 rounded-xl p-3 flex items-center justify-between text-xs">
                      <div className="flex items-center gap-2">
                        <Loader2 className="w-4 h-4 animate-spin text-blue-300" />
                        <span className="text-white font-medium">Windows Explorer is open. Select a folder to inspect or click Cancel.</span>
                      </div>
                    </div>
                  ) : (
                    <button
                      onClick={handlePickWindowsFolder}
                      disabled={isInspecting}
                      className="w-full bg-black/50 hover:bg-white/10 backdrop-blur-md text-slate-300 hover:text-white font-medium py-2.5 px-4 rounded-xl flex items-center justify-center gap-2 text-xs transition cursor-pointer"
                    >
                      <FolderOpen className="w-4 h-4 text-blue-300" />
                      <span>Inspect folder with Windows Explorer</span>
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Spacebar + Hover Quick Peek Popup Window with Stretchy Spring Animation (Stays open while cursor is inside!) */}
      {(isSpaceHeld || isPointerInPeek) && hoveredMedia && !selectedPhoto && (
        <div
          onClick={closeQuickPeek}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 backdrop-blur-md p-4"
          role="dialog"
          aria-label="Quick Peek Preview"
        >
          <div
            key={`${hoveredMedia.id}-${hoveredMedia.matched_timestamp ?? 0}`}
            onClick={(e) => e.stopPropagation()}
            onMouseEnter={() => {
              isPointerInPeekRef.current = true;
              setIsPointerInPeek(true);
            }}
            onMouseLeave={() => {
              isPointerInPeekRef.current = false;
              setIsPointerInPeek(false);
              if (!isSpaceHeldRef.current && !isCardHoveredRef.current) {
                hoveredMediaRef.current = null;
                setHoveredMedia(null);
              }
            }}
            className="animate-stretchy-peek pointer-events-auto relative w-full max-w-3xl overflow-hidden rounded-3xl bg-[#12161c]/95 backdrop-blur-xl shadow-[0_28px_80px_rgba(0,0,0,0.85)]"
          >
            <div className="relative flex aspect-video w-full items-center justify-center bg-[#0d0f12]">
              {isVideoMedia(hoveredMedia) ? (
                <video
                  src={`${getVideoUrl(hoveredMedia)}#t=${Math.max(0, hoveredMedia.matched_timestamp ?? hoveredMedia.timestamp ?? 0)}`}
                  autoPlay
                  playsInline
                  loop
                  controls
                  className="max-h-[70vh] w-full object-contain"
                />
              ) : (
                <img
                  src={getPhotoUrl(hoveredMedia)}
                  alt={hoveredMedia.filename || "Quick peek"}
                  onClick={() => {
                    const target = hoveredMedia;
                    closeQuickPeek();
                    setSelectedPhoto(target);
                  }}
                  className="max-h-[70vh] w-full object-contain cursor-pointer"
                />
              )}

              {/* Top Overlay Badges */}
              <div className="pointer-events-none absolute left-3.5 top-3.5 flex items-center gap-2">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/25 px-3 py-1 text-xs font-bold text-blue-100 shadow-lg backdrop-blur-md">
                  <Eye className="h-3.5 w-3.5 text-blue-300" />
                  <span>Quick Peek</span>
                </span>
                {isVideoMedia(hoveredMedia) && (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-black/55 px-3 py-1 font-mono text-xs font-semibold text-blue-200 backdrop-blur-md">
                    <VideoIcon className="h-3.5 w-3.5 text-blue-300" />
                    <span>Playing from {formatTime(hoveredMedia.matched_timestamp ?? hoveredMedia.timestamp ?? 0)}</span>
                    {(hoveredMedia.duration || videoDurations[hoveredMedia.id]) ? (
                      <span className="text-slate-400">
                        / {formatTime(hoveredMedia.duration || videoDurations[hoveredMedia.id])}
                      </span>
                    ) : null}
                  </span>
                )}
              </div>

              <div className="absolute right-3.5 top-3.5 flex items-center gap-2">
                {hoveredMedia.accuracy_percent !== undefined && (
                  <span className="pointer-events-none inline-flex items-center gap-1 rounded-full bg-emerald-500/25 backdrop-blur-md px-3 py-1 text-xs font-bold text-emerald-200 shadow-lg">
                    <Sparkles className="h-3.5 w-3.5" />
                    <span>{hoveredMedia.accuracy_percent}% relevance</span>
                  </span>
                )}
                <button
                  type="button"
                  onClick={closeQuickPeek}
                  title="Close Quick Peek (Esc)"
                  className="flex h-7 w-7 items-center justify-center rounded-full bg-black/55 text-slate-200 hover:bg-red-500/25 hover:text-red-200 backdrop-blur-md transition cursor-pointer"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between gap-4 bg-[#141a22]/90 backdrop-blur-xl px-5 py-3 text-xs">
              <div className="min-w-0">
                <p className="truncate font-semibold text-white">
                  {settings.show_filenames ? (hoveredMedia.filename || "Media Preview") : (isVideoMedia(hoveredMedia) ? "Matched Video Moment" : "Matched Photo")}
                </p>
                <p className="mt-0.5 text-[11px] text-slate-400">
                  {isVideoMedia(hoveredMedia)
                    ? `Matched at ${formatTime(hoveredMedia.matched_timestamp ?? hoveredMedia.timestamp ?? 0)}${(hoveredMedia.duration || videoDurations[hoveredMedia.id]) ? ` · Total ${formatTime(hoveredMedia.duration || videoDurations[hoveredMedia.id])}` : ""}`
                    : hoveredMedia.width && hoveredMedia.height
                    ? `${hoveredMedia.width} × ${hoveredMedia.height}`
                    : "High-resolution preview"}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  type="button"
                  onClick={() => {
                    const target = hoveredMedia;
                    closeQuickPeek();
                    setSelectedPhoto(target);
                  }}
                  className="rounded-xl bg-blue-500/25 hover:bg-blue-500/35 px-3.5 py-1.5 text-xs font-semibold text-blue-100 hover:text-white transition cursor-pointer shadow-sm backdrop-blur-md"
                >
                  Open Full Player
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {selectedPhoto && <Suspense fallback={null}>
      <MediaViewer
        media={selectedPhoto}
        items={activeViewerItems}
        onClose={() => setSelectedPhoto(null)}
        onSelect={setSelectedPhoto}
        onFindSimilar={handleFindSimilar}
        onFindSimilarFace={handleFindSimilarFace}
        faceSearchEnabled={{
          photos: Boolean(settings.face_search_photos_enabled),
          videos: Boolean(settings.face_search_videos_enabled),
        }}
        showFilenames={Boolean(settings.show_filenames)}
      />
      </Suspense>}
    </div>
  );
}
