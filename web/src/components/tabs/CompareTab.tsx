"use client";

import { useState, useEffect } from "react";
import { compare, getCollections, type CompareResponse, type Collection } from "@/lib/api";
import LoadingSpinner from "@/components/LoadingSpinner";

export default function CompareTab() {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collA, setCollA] = useState("");
  const [collB, setCollB] = useState("");
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { getCollections().then(setCollections).catch(() => {}); }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!collA || !collB || !question.trim() || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await compare(collA, collB, question));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  function parseMarkdownTable(text: string): { headers: string[]; rows: string[][] } | null {
    const lines = text.split("\n").filter((l) => l.trim().startsWith("|"));
    if (lines.length < 2) return null;
    const headers = lines[0].split("|").filter(Boolean).map((s) => s.trim());
    const rows = lines.slice(2).map((l) => l.split("|").filter(Boolean).map((s) => s.trim()));
    return { headers, rows };
  }

  function extractSection(text: string, heading: string): string {
    const re = new RegExp(`\\*\\*${heading}\\*\\*\\s*\\n([\\s\\S]*?)(?=\\n\\*\\*|$)`);
    const m = text.match(re);
    return m ? m[1].trim() : "";
  }

  const table = result ? parseMarkdownTable(result.comparison) : null;
  const complementary = result ? extractSection(result.comparison, "Complementary Strengths") : "";
  const divergences = result ? extractSection(result.comparison, "Divergences") : "";
  const bottomLine = result ? extractSection(result.comparison, "Bottom Line") : "";

  return (
    <div className="p-6 max-w-4xl mx-auto w-full space-y-5">
      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="flex gap-3 flex-wrap">
          <CollectionSelect label="Collection A" value={collA} options={collections} onChange={setCollA} exclude={collB} />
          <CollectionSelect label="Collection B" value={collB} options={collections} onChange={setCollB} exclude={collA} />
        </div>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="What do you want to compare between them?"
          className="w-full bg-gray-900 border border-gray-700 rounded-xl px-4 py-2.5 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-violet-500"
        />
        <button
          type="submit"
          disabled={!collA || !collB || !question.trim() || loading}
          className="bg-violet-600 hover:bg-violet-500 disabled:opacity-40 text-white px-5 py-2.5 rounded-xl text-sm font-medium transition-colors flex items-center gap-2"
        >
          {loading && <LoadingSpinner size="sm" />}
          Compare
        </button>
      </form>

      {error && <div className="text-red-400 text-sm">{error}</div>}
      {!result && !loading && <EmptyState />}

      {result && (
        <div className="space-y-4">
          {/* Comparison table */}
          {table && (
            <div className="overflow-x-auto rounded-xl border border-gray-800">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-900 border-b border-gray-800">
                    {table.headers.map((h, i) => (
                      <th key={i} className="px-4 py-2.5 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, i) => (
                    <tr key={i} className={i % 2 === 0 ? "bg-gray-950" : "bg-gray-900/50"}>
                      {row.map((cell, j) => (
                        <td key={j} className="px-4 py-3 text-gray-300 align-top">{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            {complementary && (
              <TextSection title="Complementary Strengths" text={complementary} accent="emerald" />
            )}
            {divergences && (
              <TextSection title="Divergences" text={divergences} accent="yellow" />
            )}
          </div>

          {bottomLine && (
            <div className="bg-violet-950/40 border border-violet-700/40 rounded-xl px-5 py-4">
              <p className="text-xs font-medium text-violet-400 mb-1.5 uppercase tracking-wider">Bottom Line</p>
              <p className="text-sm text-gray-200 leading-relaxed">{bottomLine}</p>
            </div>
          )}

          {result.overlap_files.length > 0 && (
            <div className="text-xs text-yellow-400 bg-yellow-900/20 border border-yellow-700/30 rounded-lg px-3 py-2">
              ⚠️ Overlapping files: {result.overlap_files.join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CollectionSelect({
  label, value, options, onChange, exclude,
}: {
  label: string;
  value: string;
  options: Collection[];
  onChange: (v: string) => void;
  exclude: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-gray-500">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-violet-500 w-44"
      >
        <option value="">Select…</option>
        {options.filter((c) => c.name !== exclude).map((c) => (
          <option key={c.name} value={c.name}>{c.name}</option>
        ))}
      </select>
    </div>
  );
}

function TextSection({ title, text, accent }: { title: string; text: string; accent: "emerald" | "yellow" }) {
  const headingClass = accent === "emerald" ? "text-emerald-400" : "text-yellow-400";
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <p className={`text-xs font-medium mb-2 uppercase tracking-wider ${headingClass}`}>{title}</p>
      <p className="text-sm text-gray-300 whitespace-pre-wrap">{text}</p>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-gray-600">
      <svg className="w-10 h-10 mb-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <path d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <p className="text-sm">Compare two collections side by side</p>
    </div>
  );
}
