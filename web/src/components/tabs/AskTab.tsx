"use client";

import { useState } from "react";
import { ask, type AskResponse } from "@/lib/api";
import LoadingSpinner from "@/components/LoadingSpinner";

interface QA { question: string; answer: string; sources: string[] }

export default function AskTab({ collection }: { collection: string | null }) {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<QA[]>([]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || loading) return;
    const q = question.trim();
    setQuestion("");
    setLoading(true);
    setError(null);
    try {
      const res: AskResponse = await ask(collection ?? "all", q);
      setHistory((prev) => [...prev.slice(-4), { question: q, answer: res.answer, sources: res.sources }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-full p-6 gap-4 max-w-3xl mx-auto w-full">
      <div className="flex-1 overflow-y-auto space-y-4">
        {history.length === 0 && !loading && (
          <EmptyState label="Ask anything about your knowledge base" />
        )}
        {history.map((qa, i) => (
          <div key={i} className="space-y-2">
            <div className="flex justify-end">
              <div className="bg-violet-600/20 text-violet-200 rounded-2xl rounded-tr-sm px-4 py-2 text-sm max-w-xl border border-violet-600/20">
                {qa.question}
              </div>
            </div>
            <div className="bg-gray-900 rounded-2xl rounded-tl-sm px-4 py-3 text-sm text-gray-200 border border-gray-800 whitespace-pre-wrap">
              {qa.answer}
            </div>
            {qa.sources.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pl-1">
                {qa.sources.map((s) => (
                  <span key={s} className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full border border-gray-700">
                    {s}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="flex items-center gap-2 text-gray-400 text-sm pl-1">
            <LoadingSpinner size="sm" />
            <span>Thinking...</span>
          </div>
        )}
        {error && <div className="text-red-400 text-sm">{error}</div>}
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={collection ? `Ask about ${collection}…` : "Ask across all collections…"}
          className="flex-1 bg-gray-900 border border-gray-700 rounded-xl px-4 py-2.5 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-violet-500 transition-colors"
        />
        <button
          type="submit"
          disabled={!question.trim() || loading}
          className="bg-violet-600 hover:bg-violet-500 disabled:opacity-40 text-white px-4 py-2.5 rounded-xl text-sm font-medium transition-colors"
        >
          Send
        </button>
      </form>
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-gray-600">
      <svg className="w-10 h-10 mb-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <path d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <p className="text-sm">{label}</p>
    </div>
  );
}
