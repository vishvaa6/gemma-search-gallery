import React from "react";
import { Cpu, Zap, HardDrive } from "lucide-react";

export default function GpuStatusBadge({ system }) {
  const isCuda = system?.device === "cuda";
  const gpuName = system?.gpu_name || (isCuda ? "NVIDIA RTX 3050" : "CPU");
  const allocated = system?.vram_allocated_mb ? (system.vram_allocated_mb / 1024).toFixed(1) : "0";
  const total = system?.vram_total_mb ? (system.vram_total_mb / 1024).toFixed(1) : "4.0";
  const indexed = system?.indexed_count ?? 0;

  return (
    <div className="flex items-center gap-2">
      {/* GPU Badge */}
      <div className="flex items-center gap-2 bg-yt-surface border border-yt-border px-3 py-1.5 rounded-full text-xs hover:border-yt-blue/50 transition">
        <span
          className={`w-2.5 h-2.5 rounded-full ${
            isCuda
              ? "bg-yt-green shadow-[0_0_8px_#2ba640] animate-pulse"
              : "bg-yt-blue"
          }`}
        />
        <Zap className="w-3.5 h-3.5 text-yt-blue" />
        <span className="font-semibold text-yt-text tracking-wide">
          {gpuName}
        </span>
        {isCuda && (
          <span className="text-yt-subtext border-l border-yt-border pl-2 font-mono">
            {allocated}GB / {total}GB
          </span>
        )}
      </div>

      {/* Indexed Photos Badge */}
      <div className="hidden sm:flex items-center gap-1.5 bg-yt-surface border border-yt-border px-3 py-1.5 rounded-full text-xs text-yt-subtext">
        <HardDrive className="w-3.5 h-3.5 text-yt-red" />
        <span>Indexed:</span>
        <span className="text-white font-mono font-semibold">{indexed}</span>
      </div>
    </div>
  );
}
