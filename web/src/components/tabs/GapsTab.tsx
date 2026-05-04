"use client";

import { useState, useEffect } from "react";
import { gaps, getCollections, type GapsResponse, type Collection } from "@/lib/api";
import LoadingSpinner from "@/components/LoadingSpinner";

export default function GapsTab({ collection: initialCollection }: { collection: string | null }) {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collection, setCollection] = useState(initialCollection ?? "");
  const [topic, setTopic] = useState("");
  const [result, setResult] = useState<GapsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { getCollections().then(setCollections).catch(() => {}); }, []);
  useEffect(() => { if (initialCollection) setCollection(initialCollection); }, [initialCollection]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!collection || !topic.trim() || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await gaps(collection, topic));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="p-6 max-w-3xl mx-auto w-full space-y-5">
      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="flex gap-3">
          <select
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
            className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-violet-500 w-48"
          >
            <option value="">Select collection…</option>
            {collections.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
          <input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="Topic (e.g. cloud security, past performance)"
            className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-violet-500"
          />
        </div>
        <button
          type="submit"
          disabled={!collection || !topic.trim() || loading}
          className="bg-violet-600 hover:bg-violet-500 disabled:opacity-40 text-white px-5 py-2.5 rounded-xl text-sm font-medium transition-colors flex items-center gap-2"
        >
          {loading && <LoadingSpinner size="sm" />}
          Analyze Gaps
        </button>
      </form>

      {error && <div className="text-red-400 text-sm">{error}</div>}

      {!result && !loading && <EmptyState />}

      {result && (
        <div className="space-y-4">
          <GapSection title="Hard Gaps" items={result.hard_gaps} color="red" />
          <GapSection title="Soft Gaps" items={result.soft_gaps} color="yellow" />
          {result.recommendations && (
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <p className="text-xs font-medium text-violet-400 mb-1 uppercase tracking-wider">Priority Recommendation</p>
              <p className="text-sm text-gray-300">{result.recommendations}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function GapSection({ title, items, color }: { title: string; items: string[]; color: "red" | "yellow" }) {
  const badgeClass = color === "red"
    ? "bg-red-900/40 text-red-300 border border-red-700/40"
    : "bg-yellow-900/40 text-yellow-300 border border-yellow-700/40";
  const headingClass = color === "red" ? "text-red-400" : "text-yellow-400";

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <p className={`text-xs font-medium mb-3 uppercase tracking-wider ${headingClass}`}>{title}</p>
      {items.length === 0 ? (
        <p className="text-xs text-gray-500">None identified</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {items.map((item, i) => (
            <span key={i} className={`text-xs px-2.5 py-1 rounded-full ${badgeClass}`}>{item}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-gray-600">
      <svg className="w-10 h-10 mb-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <p className="text-sm">Identify what's missing in your knowledge base</p>
    </div>
  );
}
