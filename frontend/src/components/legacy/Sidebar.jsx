import React from "react";
import {
  Home,
  Flame,
  FolderOpen,
  History,
  Cpu,
  HelpCircle,
  Sliders,
} from "lucide-react";

export default function Sidebar({
  isExpanded,
  activeTab,
  onTabSelect,
  onOpenIndexModal,
}) {
  const navItems = [
    { id: "all", label: "Home Feed", icon: Home },
    { id: "top", label: "Top Matches (>90%)", icon: Flame },
    { id: "index", label: "Index Library", icon: FolderOpen, action: onOpenIndexModal },
    { id: "recent", label: "Recent Index", icon: History },
  ];

  if (!isExpanded) {
    return (
      <aside className="w-18 shrink-0 bg-yt-bg border-r border-yt-border py-3 flex flex-col items-center gap-6 hidden md:flex">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => {
                if (item.action) item.action();
                else onTabSelect(item.id);
              }}
              className={`flex flex-col items-center gap-1.5 p-2 rounded-xl text-center w-16 transition ${
                isActive ? "text-white bg-yt-hover" : "text-yt-subtext hover:text-white hover:bg-yt-surface"
              }`}
            >
              <Icon className="w-5 h-5" />
              <span className="text-[10px] leading-tight truncate">{item.label.split(" ")[0]}</span>
            </button>
          );
        })}
      </aside>
    );
  }

  return (
    <aside className="w-56 shrink-0 bg-yt-bg border-r border-yt-border py-4 px-3 flex flex-col justify-between hidden md:flex">
      <div className="space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => {
                if (item.action) item.action();
                else onTabSelect(item.id);
              }}
              className={`flex items-center gap-4 w-full px-3 py-2.5 rounded-xl text-xs font-medium transition ${
                isActive
                  ? "bg-yt-hover text-white font-semibold"
                  : "text-yt-text hover:bg-yt-surface text-yt-subtext hover:text-white"
              }`}
            >
              <Icon className="w-4 h-4 text-yt-subtext" />
              <span>{item.label}</span>
            </button>
          );
        })}
      </div>

      {/* Footer Info */}
      <div className="pt-4 border-t border-yt-border/50 text-[11px] text-yt-subtext px-3 space-y-1">
        <p className="font-semibold text-white">EmbeddingGemma 2</p>
        <p>CUDA 12.4 • MRL Subspace</p>
        <p className="text-[10px] text-yt-subtext/60">Apache 2.0 License</p>
      </div>
    </aside>
  );
}
