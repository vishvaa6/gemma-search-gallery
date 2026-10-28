import React, { useState, useEffect } from "react";
import { X, Sparkles, Zap, HardDrive, Search, ExternalLink, ChevronRight } from "lucide-react";
import { getPhotoUrl, fetchSimilarPhotos } from "../services/api";

function formatBytes(bytes) {
  if (!bytes) return "";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export default function TheaterModal({ photo, onClose, onSelectPhoto, onFindSimilar, activeMrl }) {
  const [similarPhotos, setSimilarPhotos] = useState([]);
  const [loadingSimilar, setLoadingSimilar] = useState(false);

  useEffect(() => {
    if (!photo?.id) return;
    setLoadingSimilar(true);
    fetchSimilarPhotos(photo.id, activeMrl, 8)
      .then((res) => {
        setSimilarPhotos(res.results || []);
      })
      .catch((err) => {
        console.error("Failed to fetch similar photos:", err);
      })
      .finally(() => {
        setLoadingSimilar(false);
      });
  }, [photo?.id, activeMrl]);

  // Handle ESC key
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  if (!photo) return null;

  const currentUrl = getPhotoUrl(photo);
  const accuracy = photo.accuracy_percent;

  return (
    <div className="fixed inset-0 z-50 bg-black/90 backdrop-blur-md flex items-center justify-center p-2 sm:p-4 overflow-y-auto">
      <div className="relative w-full max-w-7xl bg-yt-bg border border-yt-border rounded-2xl overflow-hidden shadow-2xl flex flex-col lg:flex-row max-h-[92vh]">
        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute top-3 right-3 z-20 w-9 h-9 rounded-full bg-black/60 hover:bg-black/90 text-white flex items-center justify-center border border-yt-border transition"
        >
          <X className="w-5 h-5" />
        </button>

        {/* Main Left Media Player Area */}
        <div className="flex-1 flex flex-col bg-black overflow-y-auto min-h-[400px]">
          {/* Main Photo View */}
          <div className="relative flex-1 flex items-center justify-center p-4 bg-black/80">
            <img
              src={currentUrl}
              alt={photo.filename}
              className="max-h-[62vh] max-w-full object-contain rounded-lg shadow-2xl"
            />
          </div>

          {/* Details & Metadata Footer */}
          <div className="p-5 bg-yt-surface border-t border-yt-border">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-bold text-yt-text flex items-center gap-2">
                  <span>{photo.filename}</span>
                </h2>
                <p className="text-xs text-yt-subtext mt-1 break-all font-mono">
                  {photo.path}
                </p>
              </div>

              {/* Accuracy Badge */}
              {accuracy !== undefined && (
                <div className="flex items-center gap-2">
                  <div className="bg-emerald-600/90 border border-emerald-400 px-3 py-1.5 rounded-xl text-white flex items-center gap-2 shadow-lg shadow-emerald-500/10">
                    <Sparkles className="w-4 h-4 text-emerald-200" />
                    <div>
                      <div className="text-[10px] uppercase font-bold tracking-wider text-emerald-100">
                        Relevance score
                      </div>
                      <div className="text-base font-extrabold leading-none">
                        {accuracy}%
                      </div>
                      <div className="mt-1 max-w-40 text-[9px] leading-tight text-emerald-100/80">
                        Ranking guidance, not a probability.
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Metrics Chips & Actions */}
            <div className="mt-4 pt-4 border-t border-yt-border flex flex-wrap items-center justify-between gap-3 text-xs text-yt-subtext">
              <div className="flex flex-wrap items-center gap-2">
                {photo.similarity !== undefined && (
                  <span className="bg-yt-card px-2.5 py-1 rounded-md border border-yt-border font-mono text-emerald-400">
                    Cosine Sim: {photo.similarity}
                  </span>
                )}
                {photo.raw_distance !== undefined && (
                  <span className="bg-yt-card px-2.5 py-1 rounded-md border border-yt-border font-mono">
                    Distance: {photo.raw_distance}
                  </span>
                )}
                {photo.width && photo.height && (
                  <span className="bg-yt-card px-2.5 py-1 rounded-md border border-yt-border">
                    {photo.width} × {photo.height} px
                  </span>
                )}
                {photo.size_bytes && (
                  <span className="bg-yt-card px-2.5 py-1 rounded-md border border-yt-border">
                    {formatBytes(photo.size_bytes)}
                  </span>
                )}
              </div>

              <button
                onClick={() => {
                  onFindSimilar(photo);
                  onClose();
                }}
                className="bg-yt-red hover:bg-yt-redHover text-white px-4 py-2 rounded-xl text-xs font-semibold flex items-center gap-2 shadow-lg shadow-yt-red/20 transition"
              >
                <Search className="w-3.5 h-3.5" />
                <span>Find Similar Across Gallery</span>
              </button>
            </div>
          </div>
        </div>

        {/* Right Sidebar: YouTube-Style "Related Photos" Recommendations */}
        <div className="w-full lg:w-96 bg-yt-surface border-t lg:border-t-0 lg:border-l border-yt-border flex flex-col max-h-[92vh] overflow-hidden">
          <div className="p-4 border-b border-yt-border flex items-center justify-between">
            <h3 className="text-sm font-bold text-yt-text flex items-center gap-2">
              <span>Related Photos</span>
              <span className="text-[11px] bg-yt-card border border-yt-border px-2 py-0.5 rounded-full text-yt-subtext">
                {activeMrl}d MRL
              </span>
            </h3>
            {loadingSimilar && (
              <span className="text-xs text-yt-blue animate-pulse">Scanning...</span>
            )}
          </div>

          {/* Related Photos List */}
          <div className="flex-1 overflow-y-auto p-3 space-y-2.5">
            {similarPhotos.length === 0 ? (
              <div className="text-center py-12 text-yt-subtext text-xs">
                {loadingSimilar ? "Finding visually and semantically related photos..." : "No related photos found."}
              </div>
            ) : (
              similarPhotos.map((item) => (
                <div
                  key={item.id}
                  onClick={() => onSelectPhoto(item)}
                  className="flex gap-2.5 p-1.5 rounded-xl hover:bg-yt-hover cursor-pointer group transition"
                >
                  {/* Small Thumbnail */}
                  <div className="relative w-28 aspect-video rounded-lg overflow-hidden bg-black shrink-0 border border-yt-border">
                    <img
                      src={getPhotoUrl(item)}
                      alt={item.filename}
                      className="w-full h-full object-cover group-hover:scale-105 transition"
                    />
                    {item.accuracy_percent && (
                      <span className="absolute bottom-1 right-1 bg-black/80 text-emerald-400 text-[10px] font-bold px-1 rounded">
                        {item.accuracy_percent}%
                      </span>
                    )}
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0 flex flex-col justify-center">
                    <p className="text-xs font-semibold text-yt-text truncate group-hover:text-yt-blue">
                      {item.filename}
                    </p>
                    <p className="text-[11px] text-yt-subtext mt-0.5 truncate">
                      {item.path.split(/[\\/]/).slice(-2, -1)[0]}
                    </p>
                    <div className="flex items-center gap-1 mt-1 text-[10px] text-emerald-400 font-mono">
                      <span>Relevance: {item.accuracy_percent}%</span>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
