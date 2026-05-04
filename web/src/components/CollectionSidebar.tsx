"use client";

import { useEffect, useState, useCallback } from "react";
import { getCollections, type Collection } from "@/lib/api";

interface Props {
  selected: string | null;
  onSelect: (name: string) => void;
}

export default function CollectionSidebar({ selected, onSelect }: Props) {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getCollections();
      setCollections(data);
      if (data.length > 0 && !selected) {
        onSelect(data[0].name);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [onSelect, selected]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="flex-1 flex flex-col overflow-hidden px-3 py-3">
      <div className="flex items-center justify-between mb-3 px-1">
        <span className="text-xs font-medium text-gray-400 uppercase tracking-wider">Collections</span>
        <button
          onClick={load}
          className="p-1 rounded text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
          title="Refresh collections"
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>

      {loading && (
        <div className="space-y-2 px-1">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-9 rounded-lg bg-gray-800 animate-pulse" />
          ))}
        </div>
      )}

      {error && (
        <div className="px-1 text-xs text-red-400">{error}</div>
      )}

      {!loading && !error && collections.length === 0 && (
        <div className="px-1 text-xs text-gray-500">No collections found</div>
      )}

      <div className="flex-1 overflow-y-auto space-y-1">
        {collections.map((col) => {
          const isSelected = col.name === selected;
          return (
            <button
              key={col.name}
              onClick={() => onSelect(col.name)}
              className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-left transition-colors text-sm ${
                isSelected
                  ? "bg-violet-600/20 text-violet-300 border border-violet-600/30"
                  : "text-gray-400 hover:text-gray-200 hover:bg-gray-800 border border-transparent"
              }`}
            >
              <span className="truncate font-medium">{col.name}</span>
              <span className={`ml-2 text-xs flex-shrink-0 px-1.5 py-0.5 rounded-full ${
                isSelected ? "bg-violet-600/30 text-violet-300" : "bg-gray-700 text-gray-500"
              }`}>
                {col.chunk_count}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
