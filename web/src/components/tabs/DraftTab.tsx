"use client";

import { useState, useEffect } from "react";
import { draft, getCollections, type DraftResponse, type Collection } from "@/lib/api";
import LoadingSpinner from "@/components/LoadingSpinner";

export default function DraftTab({ collection: initialCollection }: { collection: string | null }) {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collection, setCollection] = useState(initialCollection ?? "");
  const [requirement, setRequirement] = useState("");
  const [result, setResult] = useState<DraftResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => { getCollections().then(setCollections).catch(() => {}); }, []);
  useEffect(() => { if (initialCollection) setCollection(initialCollection); }, [initialCollection]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!collection || !requirement.trim() || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await draft(collection, requirement));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleCopy() {
    if (!result) return;
    await navigator.clipboard.writeText(result.draft);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function renderDraft(text: string) {
    const parts = text.split(/(\[EVIDENCE MISSING:[^\]]*\])/g);
    return parts.map((part, i) =>
      part.startsWith("[EVIDENCE MISSING:") ? (
        <mark key={i} className="bg-red-900/50 text-red-300 rounded px-0.5 not-italic">{part}</mark>
      ) : (
        <span key={i}>{part}</span>
      )
    );
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
        </div>
        <textarea
          value={requirement}
          onChange={(e) => setRequirement(e.target.value)}
          placeholder="Paste the RFP requirement to draft against…"
          rows={4}
          className="w-full bg-gray-900 border border-gray-700 rounded-xl px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-violet-500 resize-none"
        />
        <button
          type="submit"
          disabled={!collection || !requirement.trim() || loading}
          className="bg-violet-600 hover:bg-violet-500 disabled:opacity-40 text-white px-5 py-2.5 rounded-xl text-sm font-medium transition-colors flex items-center gap-2"
        >
          {loading && <LoadingSpinner size="sm" />}
          Generate Draft
        </button>
      </form>

      {error && <div className="text-red-400 text-sm">{error}</div>}
      {!result && !loading && <EmptyState />}

      {result && (
        <div className="space-y-3">
          {/* Document card */}
          <div className="bg-white/5 border border-gray-700 rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-700 bg-gray-900/60">
              <span className="text-xs text-gray-400 font-medium">Proposal Narrative Draft</span>
              <button
                onClick={handleCopy}
                className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1.5 transition-colors"
              >
                {copied ? (
                  <><svg className="w-3.5 h-3.5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" /></svg><span className="text-emerald-400">Copied</span></>
                ) : (
                  <><svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" /></svg>Copy</>
                )}
              </button>
            </div>
            <div className="px-6 py-5 text-sm text-gray-200 leading-relaxed whitespace-pre-wrap font-serif">
              {renderDraft(result.draft)}
            </div>
            {result.coverage && (
              <div className="px-4 py-2.5 border-t border-gray-700 bg-gray-900/60">
                <p className="text-xs text-gray-400">{result.coverage}</p>
              </div>
            )}
          </div>
          {result.sources.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {result.sources.map((s) => (
                <span key={s} className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full border border-gray-700">{s}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-gray-600">
      <svg className="w-10 h-10 mb-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <p className="text-sm">Draft a compliant proposal narrative from KB content</p>
    </div>
  );
}
