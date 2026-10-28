import React from "react";
import { Sliders, Sparkles, Zap, Target, Gem } from "lucide-react";

const MRL_OPTIONS = [
  { dim: 128, label: "128d", desc: "Ultra-Speed (~15ms)", icon: Zap, savings: "83% saved" },
  { dim: 256, label: "256d", desc: "Balanced (~22ms)", icon: Sparkles, savings: "67% saved", default: true },
  { dim: 512, label: "512d", desc: "High-Res (~38ms)", icon: Target, savings: "33% saved" },
  { dim: 768, label: "768d", desc: "Full Fidelity (~55ms)", icon: Gem, savings: "Canonical" },
];

const CATEGORY_CHIPS = [
  "All",
  "Exceptional (>90%)",
  "Landscapes & Nature",
  "People & Portraits",
  "Receipts & Documents",
  "Urban & Architecture",
  "Pets & Animals",
  "Food & Dining",
];

export default function FilterChips({
  activeMrl,
  onMrlChange,
  activeCategory,
  onCategorySelect,
}) {
  return (
    <div className="bg-yt-bg border-b border-yt-border py-2.5 px-4 sm:px-6">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row md:items-center justify-between gap-3">
        {/* MRL Dimension Selector */}
        <div className="flex items-center gap-2 overflow-x-auto pb-1 md:pb-0 scrollbar-none">
          <div className="flex items-center gap-1 text-xs text-yt-subtext font-semibold mr-1 shrink-0">
            <Sliders className="w-3.5 h-3.5 text-yt-blue" />
            <span>MRL Subspace:</span>
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            {MRL_OPTIONS.map((opt) => {
              const Icon = opt.icon;
              const isActive = activeMrl === opt.dim;
              return (
                <button
                  key={opt.dim}
                  onClick={() => onMrlChange(opt.dim)}
                  title={`${opt.desc} — ${opt.savings}`}
                  className={`flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-medium transition duration-150 ${
                    isActive
                      ? "bg-yt-red text-white shadow-sm shadow-yt-red/30"
                      : "bg-yt-surface text-yt-text hover:bg-yt-hover border border-yt-border"
                  }`}
                >
                  <Icon className="w-3 h-3" />
                  <span>{opt.label}</span>
                  <span className={`text-[10px] hidden sm:inline ${isActive ? "text-white/80" : "text-yt-subtext"}`}>
                    ({opt.savings})
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Quick Category Chips */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 md:pb-0">
          {CATEGORY_CHIPS.map((cat) => {
            const isSelected = activeCategory === cat;
            return (
              <button
                key={cat}
                onClick={() => onCategorySelect(cat)}
                className={`px-3 py-1 rounded-lg text-xs font-medium whitespace-nowrap transition duration-150 ${
                  isSelected
                    ? "bg-yt-text text-yt-bg"
                    : "bg-yt-surface text-yt-text hover:bg-yt-hover border border-yt-border/50"
                }`}
              >
                {cat}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
