"use client";

import { useState, useRef } from "react";
import LoadingSpinner from "@/components/LoadingSpinner";

interface Step { type: "step" | "answer" | "error"; text: string }

const BASE = "http://localhost:8000";

export default function AgentTab() {
  const [question, setQuestion] = useState("");
  const [steps, setSteps] = useState<Step[]>([]);
  const [answer, setAnswer] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || running) return;
    const q = question.trim();
    setSteps([]);
    setAnswer(null);
    setError(null);
    setRunning(true);

    abortRef.current = new AbortController();
    try {
      const res = await fetch(`${BASE}/query/agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
        signal: abortRef.current.signal,
      });
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const reader = res.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (raw === "[DONE]") break;
          try {
            const event = JSON.parse(raw) as Step;
            if (event.type === "answer") {
              setAnswer(event.text);
            } else {
              setSteps((prev) => [...prev, event]);
            }
          } catch {}
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError(err instanceof Error ? err.message : "Agent failed");
      }
    } finally {
      setRunning(false);
    }
  }

  function getStepIcon(text: string) {
    if (text.startsWith("🔍")) return "🔍";
    if (text.startsWith("⚙️")) return "⚙️";
    if (text.startsWith("📄")) return "📄";
    return "💭";
  }

  return (
    <div className="flex flex-col h-full p-6 gap-4 max-w-3xl mx-auto w-full">
      <div className="flex-1 overflow-y-auto space-y-3">
        {steps.length === 0 && !answer && !running && (
          <EmptyState />
        )}

        {/* Reasoning chain */}
        {steps.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Reasoning Chain</p>
            {steps.map((step, i) => (
              <div
                key={i}
                className="flex gap-2.5 items-start animate-in fade-in slide-in-from-bottom-1 duration-200"
              >
                <span className="text-base flex-shrink-0 mt-0.5">{getStepIcon(step.text)}</span>
                <div className="bg-gray-900 border border-gray-800 rounded-xl px-3 py-2 text-xs text-gray-300 font-mono whitespace-pre-wrap flex-1">
                  {step.text.replace(/^[🔍⚙️📄💭]\s*/, "")}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Thinking indicator */}
        {running && (
          <div className="flex items-center gap-2 text-gray-400 text-sm">
            <LoadingSpinner size="sm" />
            <span className="animate-pulse">Agent thinking…</span>
          </div>
        )}

        {/* Final answer */}
        {answer && (
          <div className="mt-4 space-y-2">
            <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Final Answer</p>
            <div className="bg-violet-950/40 border border-violet-700/40 rounded-xl px-4 py-3 text-sm text-gray-200 whitespace-pre-wrap">
              {answer}
            </div>
          </div>
        )}

        {error && <div className="text-red-400 text-sm">{error}</div>}
      </div>

      <form onSubmit={handleRun} className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask a complex multi-step question…"
          className="flex-1 bg-gray-900 border border-gray-700 rounded-xl px-4 py-2.5 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-violet-500 transition-colors"
        />
        {running ? (
          <button
            type="button"
            onClick={() => abortRef.current?.abort()}
            className="bg-gray-700 hover:bg-gray-600 text-gray-200 px-4 py-2.5 rounded-xl text-sm font-medium transition-colors"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={!question.trim()}
            className="bg-violet-600 hover:bg-violet-500 disabled:opacity-40 text-white px-4 py-2.5 rounded-xl text-sm font-medium transition-colors"
          >
            Run
          </button>
        )}
      </form>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-gray-600">
      <svg className="w-10 h-10 mb-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <p className="text-sm">Run multi-step reasoning across your knowledge base</p>
    </div>
  );
}
