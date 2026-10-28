import React, { useState, useEffect } from "react";
import { X, FolderOpen, Play, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";
import { createIndexEventSource } from "../services/api";

export default function IndexModal({ isOpen, onClose, onIndexingComplete }) {
  const [directoryPath, setDirectoryPath] = useState("");
  const [indexing, setIndexing] = useState(false);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);
  const [isDone, setIsDone] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setProgress(null);
      setError(null);
      setIsDone(false);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleStartIndex = (e) => {
    e.preventDefault();
    if (!directoryPath.trim()) return;

    setIndexing(true);
    setError(null);
    setIsDone(false);

    try {
      const eventSource = createIndexEventSource(directoryPath.trim());

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === "start") {
            setProgress({ indexed: 0, total: data.total, percent: 0, speed_fps: 0, current_file: "Starting scan..." });
          } else if (data.event === "progress") {
            setProgress(data);
          } else if (data.event === "complete") {
            setProgress((prev) => ({ ...prev, ...data, percent: 100 }));
            setIsDone(true);
            setIndexing(false);
            eventSource.close();
            onIndexingComplete();
          }
        } catch (err) {
          console.error("SSE parse error:", err);
        }
      };

      eventSource.onerror = (err) => {
        console.error("SSE error:", err);
        setError("Indexing failed or interrupted. Check that the folder path exists.");
        setIndexing(false);
        eventSource.close();
      };
    } catch (err) {
      setError(err.message);
      setIndexing(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="relative w-full max-w-lg bg-yt-surface border border-yt-border rounded-2xl p-6 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-yt-border">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-yt-red/20 text-yt-red flex items-center justify-center">
              <FolderOpen className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-base font-bold text-yt-text">Index Photo Directory</h3>
              <p className="text-xs text-yt-subtext">Crawl and embed images into LanceDB vector store</p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={indexing}
            className="w-8 h-8 rounded-full hover:bg-yt-hover flex items-center justify-center text-yt-subtext hover:text-white transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleStartIndex} className="mt-5 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-yt-subtext mb-1.5">
              Absolute Directory Path on Disk
            </label>
            <input
              type="text"
              value={directoryPath}
              onChange={(e) => setDirectoryPath(e.target.value)}
              disabled={indexing}
              placeholder="e.g. C:\Users\Username\Pictures or /home/user/photos"
              className="w-full bg-yt-card border border-yt-border rounded-xl px-4 py-2.5 text-sm text-yt-text focus:outline-none focus:border-yt-blue focus:ring-1 focus:ring-yt-blue transition font-mono"
            />
          </div>

          {error && (
            <div className="bg-red-950/50 border border-red-500/50 rounded-xl p-3 text-xs text-red-200 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Progress Stream View */}
          {progress && (
            <div className="bg-yt-card border border-yt-border rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-yt-text">
                  {isDone ? "Indexing Complete" : "Indexing in progress..."}
                </span>
                <span className="font-mono text-yt-blue font-bold">
                  {progress.indexed} / {progress.total} ({progress.percent ?? 0}%)
                </span>
              </div>

              {/* Progress Bar */}
              <div className="w-full bg-yt-hover rounded-full h-2 overflow-hidden">
                <div
                  className="bg-gradient-to-r from-yt-blue to-yt-red h-full transition-all duration-150"
                  style={{ width: `${progress.percent ?? 0}%` }}
                />
              </div>

              <div className="flex items-center justify-between text-[11px] text-yt-subtext">
                <span className="truncate max-w-[260px] font-mono">
                  {progress.current_file}
                </span>
                {progress.speed_fps !== undefined && !isDone && (
                  <span className="font-mono text-emerald-400 font-semibold shrink-0">
                    ⚡ {progress.speed_fps} photos/sec
                  </span>
                )}
                {isDone && (
                  <span className="font-mono text-emerald-400 flex items-center gap-1 font-semibold">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Finished in {progress.elapsed_seconds}s
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Action Buttons */}
          <div className="pt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={indexing}
              className="px-4 py-2 rounded-xl text-xs font-semibold text-yt-subtext hover:bg-yt-hover transition"
            >
              {isDone ? "Close" : "Cancel"}
            </button>
            <button
              type="submit"
              disabled={indexing || !directoryPath.trim()}
              className="bg-yt-red hover:bg-yt-redHover disabled:opacity-50 text-white px-5 py-2 rounded-xl text-xs font-semibold flex items-center gap-2 shadow-lg shadow-yt-red/20 transition"
            >
              {indexing ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Indexing on GPU...</span>
                </>
              ) : isDone ? (
                <>
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>Index Another</span>
                </>
              ) : (
                <>
                  <Play className="w-3.5 h-3.5" />
                  <span>Start Live Indexing</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
