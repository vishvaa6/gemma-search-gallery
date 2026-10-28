import React from "react";
import { Image as ImageIcon, Sparkles, Zap } from "lucide-react";
import { getPhotoUrl } from "../services/api";

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return "";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export default function PhotoCard({ photo, onSelect, showFilenames = false }) {
  const imageUrl = getPhotoUrl(photo, true);
  const accuracy = photo.accuracy_percent;
  const tier = photo.accuracy_tier;

  // Relevance scores are ranking guidance, not calibrated probabilities.
  let badgeColor = "bg-black/80 text-white";
  let tierLabel = "";

  if (accuracy !== undefined) {
    if (tier === "exceptional" || accuracy >= 90) {
      badgeColor = "bg-emerald-600/90 text-white font-bold border border-emerald-400/40 shadow-sm";
      tierLabel = "EXCEPTIONAL";
    } else if (tier === "high" || accuracy >= 80) {
      badgeColor = "bg-blue-600/90 text-white font-bold border border-blue-400/40";
      tierLabel = "HIGH";
    } else if (tier === "moderate" || accuracy >= 65) {
      badgeColor = "bg-amber-600/90 text-white font-semibold border border-amber-400/40";
      tierLabel = "MODERATE";
    } else {
      badgeColor = "bg-slate-700/90 text-slate-300 font-medium";
      tierLabel = "MATCH";
    }
  }

  const parentFolder = photo.path
    ? photo.path.split(/[\\/]/).slice(-2, -1)[0] || "Gallery"
    : "Gallery";

  return (
    <div
      onClick={() => onSelect(photo)}
      className="group flex flex-col cursor-pointer transition-all duration-200"
    >
      {/* Thumbnail Container (YouTube 16:9 Style) */}
      <div className="relative aspect-video w-full overflow-hidden rounded-xl bg-yt-surface border border-yt-border group-hover:border-yt-border/80 group-hover:shadow-lg transition-all">
        <img
          src={imageUrl}
          alt={showFilenames ? photo.filename : "Indexed media"}
          loading="lazy"
          decoding="async"
          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300 ease-out"
        />

        {/* Timestamp-Style Accuracy Percentage Badge (Bottom-Right) */}
        <div className="absolute bottom-2 right-2 flex items-center gap-1">
          {accuracy !== undefined ? (
            <span
              className={`px-2 py-0.5 rounded text-[11px] tracking-wide flex items-center gap-1 backdrop-blur-sm ${badgeColor}`}
            >
              <Sparkles className="w-2.5 h-2.5" />
              <span title="Ranking guidance from similarity; not a probability.">{accuracy}% relevance</span>
              <span className="text-[9px] opacity-80 hidden sm:inline">
                {tierLabel}
              </span>
            </span>
          ) : (
            <span className="bg-black/80 backdrop-blur-sm text-yt-text text-[11px] px-1.5 py-0.5 rounded font-mono">
              {photo.width && photo.height
                ? `${photo.width}x${photo.height}`
                : formatBytes(photo.size_bytes)}
            </span>
          )}
        </div>

      </div>

      {/* Metadata Row below Thumbnail */}
      <div className="flex gap-3 mt-3 px-0.5">
        {/* Category Avatar */}
        <div className="w-9 h-9 rounded-full bg-yt-surface border border-yt-border flex items-center justify-center shrink-0 mt-0.5">
          <ImageIcon className="w-4 h-4 text-yt-subtext" />
        </div>

        {/* Title & Details */}
        <div className="flex-1 min-w-0">
          {showFilenames && <h3
            title={photo.filename}
            className="text-sm font-semibold text-yt-text line-clamp-1 group-hover:text-yt-blue transition-colors"
          >
            {photo.filename}
          </h3>}

          <p className="text-xs text-yt-subtext mt-0.5 truncate">
            {showFilenames ? parentFolder : (photo.width && photo.height ? `${photo.width}×${photo.height}` : "Media")}
            {photo.size_bytes ? ` • ${formatBytes(photo.size_bytes)}` : ""}
          </p>

          {/* Metric Footer */}
          {photo.similarity !== undefined && (
            <div className="flex items-center gap-2 mt-1 text-[11px] text-yt-subtext">
              <span className="flex items-center gap-1 font-mono text-emerald-400">
                <Zap className="w-3 h-3" />
                Cosine: {photo.similarity}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
