const BASE = "http://localhost:8000";

export interface Collection {
  name: string;
  chunk_count: number;
  last_updated: string;
}

export interface AskResponse {
  answer: string;
  sources: string[];
}

export interface ScoreResponse {
  score: number;
  summary: string;
  strengths: string[];
  weaknesses: string[];
  raw: string;
}

export interface GapsResponse {
  hard_gaps: string[];
  soft_gaps: string[];
  recommendations: string;
  raw: string;
}

export interface DraftResponse {
  draft: string;
  coverage: string;
  sources: string[];
}

export interface CompareResponse {
  comparison: string;
  sources_a: string[];
  sources_b: string[];
  overlap_files: string[];
}

export async function getCollections(): Promise<Collection[]> {
  const res = await fetch(`${BASE}/collections`);
  if (!res.ok) throw new Error(`Failed to fetch collections: ${res.status}`);
  return res.json();
}

export async function ask(collection: string, question: string): Promise<AskResponse> {
  const res = await fetch(`${BASE}/query/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ collection, question }),
  });
  if (!res.ok) throw new Error(`Ask failed: ${res.status}`);
  return res.json();
}

export async function score(collection: string, requirement: string): Promise<ScoreResponse> {
  const res = await fetch(`${BASE}/query/score`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ collection, requirement }),
  });
  if (!res.ok) throw new Error(`Score failed: ${res.status}`);
  return res.json();
}

export async function gaps(collection: string, topic: string): Promise<GapsResponse> {
  const res = await fetch(`${BASE}/query/gaps`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ collection, topic }),
  });
  if (!res.ok) throw new Error(`Gaps failed: ${res.status}`);
  return res.json();
}

export async function draft(collection: string, requirement: string): Promise<DraftResponse> {
  const res = await fetch(`${BASE}/query/draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ collection, requirement }),
  });
  if (!res.ok) throw new Error(`Draft failed: ${res.status}`);
  return res.json();
}

export async function compare(a: string, b: string, question: string): Promise<CompareResponse> {
  const res = await fetch(`${BASE}/query/compare`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ collection_a: a, collection_b: b, question }),
  });
  if (!res.ok) throw new Error(`Compare failed: ${res.status}`);
  return res.json();
}
