"use client";

import { useState } from "react";
import { score, getCollections, type ScoreResponse, type Collection } from "@/lib/api";
import LoadingSpinner from "@/components/LoadingSpinner";
import { useEffect } from "react";

export default function ScoreTab({ collection: initialCollection }: { collection: string | null }) {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collection, setCollection] = useState(initialCollection ?? "");
  const [requirement, setRequirement] = useState("");
  const [result, setResult] = useState<ScoreResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCollections().then(setCollections).catch(() => {});
  }, []);
  useEffect(() => { if (initialCollection) setCollection(initialCollection); }, [initialCollection]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!collection || !requirement.trim() || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await score(collection, requirement));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  const scoreColor = result
    ? result.score >= 7 ? "text-emerald-400" : result.score >= 4 ? "text-yellow-400" : "text-red-400"
    : "";
  const scoreBg = result
    ? result.score >= 7 ? "border-emerald-500/30 bg-emerald-900/20" : result.score >= 4 ? "border-yellow-500/30 bg-yellow-900/20" : "border-red-500/30 bg-red-900/20"
    : "";

  return (
    <div className="p-6 max-w-3xl mx-auto w-full space-y-5">
      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="flex gap-3">
          <select
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
            className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-violet-500 min-w-0 w-48"
          >
            <option value="">Select collection…</option>
            {collections.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </div>
        <textarea
          value={requirement}
          onChange={(e) => setRequirement(e.target.value)}
          placeholder="Paste the full RFP requirement text…"
          rows={4}
          className="w-full bg-gray-900 border border-gray-700 rounded-xl px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-violet-500 resize-none"
        />
        <button
          type="submit"
          disabled={!collection || !requirement.trim() || loading}
          className="bg-violet-600 hover:bg-violet-500 disabled:opacity-40 text-white px-5 py-2.5 rounded-xl text-sm font-medium transition-colors flex items-center gap-2"
        >
          {loading && <LoadingSpinner size="sm" />}
          Score Readiness
        </button>
      </form>

      {error && <div className="text-red-400 text-sm">{error}</div>}

      {!result && !loading && (
        <EmptyState label="Score your KB against an RFP requirement" />
      )}

      {result && (
        <div className="space-y-4">
          <div className={`border rounded-2xl p-5 flex items-center gap-5 ${scoreBg}`}>
            <div className={`text-5xl font-bold ${scoreColor}`}>{result.score}</div>
            <div>
              <div className="text-xs text-gray-500 uppercase tracking-wider mb-0.5">Score / 10</div>
              <p className="text-sm text-gray-300">{result.summary}</p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <p className="text-xs font-medium text-emerald-400 mb-2 uppercase tracking-wider">Strengths</p>
              <ul className="space-y-1.5">
                {result.strengths.map((s, i) => (
                  <li key={i} className="text-sm text-gray-300 flex gap-2">
                    <span className="text-emerald-500 flex-shrink-0">•</span>{s}
                  </li>
                ))}
              </ul>
            </div>
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <p className="text-xs font-medium text-red-400 mb-2 uppercase tracking-wider">Weaknesses</p>
              <ul className="space-y-1.5">
                {result.weaknesses.map((w, i) => (
                  <li key={i} className="text-sm text-gray-300 flex gap-2">
                    <span className="text-red-500 flex-shrink-0">•</span>{w}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-gray-600">
      <svg className="w-10 h-10 mb-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <path d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <p className="text-sm">{label}</p>
    </div>
  );
}
