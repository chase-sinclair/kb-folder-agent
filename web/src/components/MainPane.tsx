"use client";

import type { TabId } from "@/app/page";
import AskTab from "./tabs/AskTab";
import AgentTab from "./tabs/AgentTab";
import ScoreTab from "./tabs/ScoreTab";
import GapsTab from "./tabs/GapsTab";
import DraftTab from "./tabs/DraftTab";
import CompareTab from "./tabs/CompareTab";

const TABS: { id: TabId; label: string }[] = [
  { id: "ask",     label: "Ask" },
  { id: "agent",   label: "Agent" },
  { id: "score",   label: "Score" },
  { id: "gaps",    label: "Gaps" },
  { id: "draft",   label: "Draft" },
  { id: "compare", label: "Compare" },
];

interface Props {
  selectedCollection: string | null;
  activeTab: TabId;
  onTabChange: (t: TabId) => void;
}

export default function MainPane({ selectedCollection, activeTab, onTabChange }: Props) {
  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="border-b border-gray-800 px-4 pt-2 flex gap-1 bg-gray-950 flex-shrink-0 overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => onTabChange(t.id)}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors whitespace-nowrap ${
              activeTab === t.id
                ? "text-violet-300 border-b-2 border-violet-500 bg-gray-900/50"
                : "text-gray-500 hover:text-gray-300"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === "ask"     && <AskTab     collection={selectedCollection} />}
        {activeTab === "agent"   && <AgentTab />}
        {activeTab === "score"   && <ScoreTab   collection={selectedCollection} />}
        {activeTab === "gaps"    && <GapsTab    collection={selectedCollection} />}
        {activeTab === "draft"   && <DraftTab   collection={selectedCollection} />}
        {activeTab === "compare" && <CompareTab />}
      </div>
    </div>
  );
}
