"use client";

import { useState } from "react";
import CollectionSidebar from "@/components/CollectionSidebar";
import MainPane from "@/components/MainPane";

export type TabId = "ask" | "agent" | "score" | "gaps" | "draft" | "compare";

export default function Home() {
  const [selectedCollection, setSelectedCollection] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("ask");

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="hidden md:flex w-70 flex-col bg-gray-900 border-r border-gray-800 flex-shrink-0">
        {/* Logo bar */}
        <div className="px-5 py-4 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-violet-500" />
            <span className="text-sm font-semibold tracking-wide text-gray-100">KB Agent</span>
          </div>
        </div>
        <CollectionSidebar
          selected={selectedCollection}
          onSelect={setSelectedCollection}
        />
      </aside>

      {/* Mobile collection dropdown */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-10 bg-gray-900 border-b border-gray-800 px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-violet-500 flex-shrink-0" />
          <span className="text-sm font-semibold text-gray-100 mr-auto">KB Agent</span>
          <CollectionDropdown
            selected={selectedCollection}
            onSelect={setSelectedCollection}
          />
        </div>
      </div>

      {/* Main */}
      <main className="flex-1 flex flex-col overflow-hidden md:pt-0 pt-12">
        <MainPane
          selectedCollection={selectedCollection}
          activeTab={activeTab}
          onTabChange={setActiveTab}
        />
      </main>
    </div>
  );
}

function CollectionDropdown({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (c: string) => void;
}) {
  // Minimal mobile fallback — full sidebar appears on md+
  return (
    <button
      className="text-xs text-violet-400 hover:text-violet-300"
      onClick={() => {}}
    >
      {selected ?? "Select collection"}
    </button>
  );
}
