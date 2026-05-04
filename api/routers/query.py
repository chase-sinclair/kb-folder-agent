import asyncio
import json
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent import orchestrator
from agent.agent_loop import run_agent
from agent.rag import (
    answer_query,
    answer_query_all,
    compare_collections,
    draft_section,
    find_gaps,
    score_requirement,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request body models
# ---------------------------------------------------------------------------

class AskBody(BaseModel):
    collection: str
    question: str


class AgentBody(BaseModel):
    question: str


class ScoreBody(BaseModel):
    collection: str
    requirement: str


class GapsBody(BaseModel):
    collection: str
    topic: str


class DraftBody(BaseModel):
    collection: str
    requirement: str


class CompareBody(BaseModel):
    collection_a: str
    collection_b: str
    question: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/ask")
async def ask(body: AskBody):
    try:
        if body.collection == "all":
            result = await answer_query_all(body.question)
            sources_by_collection = result.get("sources_by_collection", {})
            flat_sources: list[str] = []
            for sources in sources_by_collection.values():
                for s in sources:
                    if s not in flat_sources:
                        flat_sources.append(s)
            return {"answer": result["answer"], "sources": flat_sources}
        else:
            result = await answer_query(body.collection, body.question)
            return {"answer": result.answer, "sources": result.sources}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/agent")
async def agent_stream(body: AgentBody):
    async def event_generator(question: str):
        queue: asyncio.Queue = asyncio.Queue()

        async def post_step(text: str) -> None:
            await queue.put({"type": "step", "text": text})

        async def run():
            try:
                answer = await run_agent(question, orchestrator, post_step, max_rounds=3)
                await queue.put({"type": "answer", "text": answer})
            except Exception as exc:
                await queue.put({"type": "error", "text": str(exc)})
            finally:
                await queue.put(None)

        asyncio.create_task(run())
        while True:
            item = await queue.get()
            if item is None:
                yield "data: [DONE]\n\n"
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(event_generator(body.question), media_type="text/event-stream")


@router.post("/score")
async def score(body: ScoreBody):
    try:
        result = await score_requirement(body.collection, body.requirement)
        raw = result.answer

        # Extract composite score
        score_match = re.search(r"COMPOSITE:\s*(\d+)/10", raw)
        score_val = int(score_match.group(1)) if score_match else 0

        # Summary is everything before STRENGTHS (or CRITERIA)
        first_section = re.search(r"\n(CRITERIA|STRENGTHS)\b", raw)
        summary = raw[:first_section.start()].strip() if first_section else raw.strip()

        # Extract STRENGTHS bullets
        strengths: list[str] = []
        strengths_match = re.search(r"STRENGTHS\s*\n(.*?)(?=\n[A-Z]+\s*\n|\Z)", raw, re.DOTALL)
        if strengths_match:
            for line in strengths_match.group(1).splitlines():
                line = line.strip()
                if line.startswith("•") or line.startswith("-"):
                    strengths.append(line.lstrip("•- ").strip())

        # Extract WEAKNESSES bullets
        weaknesses: list[str] = []
        weaknesses_match = re.search(r"WEAKNESSES\s*\n(.*?)(?=\n[A-Z]+\s*\n|\Z)", raw, re.DOTALL)
        if weaknesses_match:
            for line in weaknesses_match.group(1).splitlines():
                line = line.strip()
                if line.startswith("•") or line.startswith("-"):
                    weaknesses.append(line.lstrip("•- ").strip())

        return {
            "score": score_val,
            "summary": summary,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "raw": raw,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/gaps")
async def gaps(body: GapsBody):
    try:
        result = await find_gaps(body.collection, body.topic)
        raw = result.answer

        # Extract Hard Gaps bullets (lines after "**Hard Gaps**" until next section)
        hard_gaps: list[str] = []
        hard_match = re.search(r"\*\*Hard Gaps\*\*\s*\n(.*?)(?=\n\*\*|\Z)", raw, re.DOTALL)
        if hard_match:
            for line in hard_match.group(1).splitlines():
                line = line.strip()
                if line.startswith("•") or line.startswith("-"):
                    hard_gaps.append(line.lstrip("•- ").strip())

        # Extract Soft Gaps bullets
        soft_gaps: list[str] = []
        soft_match = re.search(r"\*\*Soft Gaps\*\*\s*\n(.*?)(?=\n\*\*|\Z)", raw, re.DOTALL)
        if soft_match:
            for line in soft_match.group(1).splitlines():
                line = line.strip()
                if line.startswith("•") or line.startswith("-"):
                    soft_gaps.append(line.lstrip("•- ").strip())

        # Recommendations: Priority line
        recommendations = ""
        priority_match = re.search(r"\*\*Priority\*\*\s*\n(.+)", raw)
        if priority_match:
            recommendations = priority_match.group(1).strip()

        return {
            "hard_gaps": hard_gaps,
            "soft_gaps": soft_gaps,
            "recommendations": recommendations,
            "raw": raw,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/draft")
async def draft(body: DraftBody):
    try:
        result = await draft_section(body.collection, body.requirement)
        raw = result.answer

        # Split off the Coverage line
        coverage = ""
        draft_text = raw
        lines = raw.splitlines()
        coverage_lines = [l for l in lines if l.strip().startswith("Coverage:")]
        if coverage_lines:
            coverage = coverage_lines[-1].strip()
            # Remove the coverage line from the draft
            non_coverage = [l for l in lines if not l.strip().startswith("Coverage:")]
            draft_text = "\n".join(non_coverage).strip()

        return {
            "draft": draft_text,
            "coverage": coverage,
            "sources": result.sources,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/compare")
async def compare(body: CompareBody):
    try:
        result = await compare_collections(
            body.collection_a,
            body.collection_b,
            body.collection_a,
            body.collection_b,
            body.question,
        )
        if "error" in result:
            return JSONResponse(status_code=400, content={"error": result["error"]})
        return {
            "comparison": result["answer"],
            "sources_a": result.get("sources_a", []),
            "sources_b": result.get("sources_b", []),
            "overlap_files": result.get("overlap_files", []),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
