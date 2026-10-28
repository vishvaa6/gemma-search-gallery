import React, { useRef } from "react";
import {
  Menu,
  Search,
  Camera,
  FolderOpen,
  X,
  Play,
  Sparkles,
} from "lucide-react";
import GpuStatusBadge from "./GpuStatusBadge";

export default function Navbar({
  query,
  onQueryChange,
  onImageSelected,
  onOpenIndexModal,
  onToggleSidebar,
  system,
  isSearching,
  onClearSearch,
}) {
  const fileInputRef = useRef(null);

  const handleFileChange = (e) => {
    const file = e.target.files?.[0];
    if (file) {
      onImageSelected(file);
    }
  };

  return (
    <header className="sticky top-0 z-40 bg-yt-bg/95 backdrop-blur-md border-b border-yt-border px-3 sm:px-6 py-2.5 flex items-center justify-between gap-3">
      {/* Left: Brand & Menu */}
      <div className="flex items-center gap-4 shrink-0">
        <button
          onClick={onToggleSidebar}
          className="w-10 h-10 rounded-full hover:bg-yt-hover flex items-center justify-center text-yt-text transition"
        >
          <Menu className="w-5 h-5" />
        </button>

        <div className="flex items-center gap-2 cursor-pointer select-none">
          {/* GemmaGallery Modern AI Lens Logo */}
          <div className="w-8 h-7 bg-gradient-to-tr from-blue-600 via-indigo-600 to-emerald-500 rounded-lg flex items-center justify-center shadow-md shadow-blue-500/25">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <span className="text-lg font-bold tracking-tight text-white flex items-center">
            Gemma<span className="text-blue-400">Gallery</span>
          </span>
          <span className="text-[10px] bg-blue-500/15 border border-blue-400/30 px-1.5 py-0.5 rounded text-blue-300 font-bold tracking-wider ml-0.5">
            AI
          </span>
        </div>
      </div>

      {/* Center: Search Bar with Camera & Index Buttons */}
      <div className="flex-1 max-w-2xl mx-2">
        <div className="flex items-center">
          <div className="relative flex-1 flex items-center">
            <input
              type="text"
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              placeholder="Search photos with natural language (e.g. 'sunset at the beach', 'car on road', 'invoice')..."
              className="w-full bg-yt-surface border border-yt-border rounded-l-full py-2.5 pl-4 pr-16 text-sm text-yt-text placeholder:text-yt-subtext focus:outline-none focus:border-yt-blue transition"
            />

            {/* Clear Button */}
            {query && (
              <button
                onClick={onClearSearch}
                className="absolute right-12 w-6 h-6 rounded-full hover:bg-yt-hover flex items-center justify-center text-yt-subtext hover:text-white transition"
              >
                <X className="w-4 h-4" />
              </button>
            )}

            {/* Camera / Visual Search Trigger */}
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileChange}
              accept="image/*"
              className="hidden"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              title="Search by image (Visual Reverse Search)"
              className="absolute right-3 w-7 h-7 rounded-full hover:bg-yt-hover flex items-center justify-center text-yt-subtext hover:text-yt-blue transition"
            >
              <Camera className="w-4 h-4" />
            </button>
          </div>

          {/* Search Magnifier Button (YouTube Style) */}
          <button
            title="Search"
            className="bg-yt-card hover:bg-yt-hover border border-l-0 border-yt-border px-6 py-2.5 rounded-r-full text-yt-subtext hover:text-white transition flex items-center justify-center"
          >
            {isSearching ? (
              <Sparkles className="w-4 h-4 text-yt-blue animate-spin" />
            ) : (
              <Search className="w-4 h-4" />
            )}
          </button>

          {/* Index Folder Button */}
          <button
            onClick={onOpenIndexModal}
            title="Index Local Directory"
            className="ml-2.5 bg-yt-surface hover:bg-yt-hover border border-yt-border p-2.5 rounded-full text-yt-subtext hover:text-yt-red transition shrink-0 hidden sm:flex items-center justify-center"
          >
            <FolderOpen className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Right: Live GPU Telemetry */}
      <div className="shrink-0 flex items-center gap-3">
        <GpuStatusBadge system={system} />
      </div>
    </header>
  );
}
