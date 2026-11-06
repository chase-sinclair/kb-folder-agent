"""Offline tests for the LLM reranker. No network, no API spend, no Qdrant.

Deliberately dependency-free (pytest is not in requirements.txt). Run:

    .\\.venv\\Scripts\\python.exe tests\\test_reranker.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")

from agent import orchestrator, reranker  # noqa: E402

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        _failures.append(label)


def _hits(n: int) -> list[dict]:
    return [
        {"file_path": f"/kb/Folder/file{i}.md", "content": f"content {i}", "score": 1.0 - i / 100}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# _parse_order - the length invariant is the whole point. A short return would
# shrink a result list below find_gaps()' 5-chunk gate.
# ---------------------------------------------------------------------------

def test_parse_order() -> None:
    print("_parse_order")
    n = 6

    check("empty string -> None", reranker._parse_order("", n) is None)
    check("whitespace -> None", reranker._parse_order("   \n ", n) is None)
    check(
        "prose with no digits -> None",
        reranker._parse_order("I cannot order these candidates.", n) is None,
    )

    order = reranker._parse_order("3,1,2,4,5,6", n)
    check("clean permutation parses", order == [2, 0, 1, 3, 4, 5], f"got {order}")

    order = reranker._parse_order("[3], [1]", n)
    check("bracketed partial -> full permutation", order is not None and len(order) == n, f"got {order}")
    check("bracketed partial keeps stated prefix", order[:2] == [2, 0], f"got {order}")
    check("bracketed partial appends remainder in original order", order[2:] == [1, 3, 4, 5], f"got {order}")

    order = reranker._parse_order("2, 2, 2, 1", n)
    check("duplicates deduped to a full permutation", order is not None and len(order) == n, f"got {order}")
    check("duplicates keep first occurrence", order[:2] == [1, 0], f"got {order}")
    check("permutation has no repeats", sorted(order) == list(range(n)))

    order = reranker._parse_order("99, 0, 1000, 4", n)
    check("out-of-range dropped, in-range kept", order is not None and order[0] == 3, f"got {order}")
    check("out-of-range still yields a full permutation", len(order) == n, f"got {order}")

    order = reranker._parse_order("5, 3, 1,", n)
    check("mid-list truncation repaired", order is not None and len(order) == n, f"got {order}")
    check("truncation repair is a true permutation", sorted(order) == list(range(n)))

    check("all out of range -> None", reranker._parse_order("77, 88", n) is None)
    check("n == 0 -> None", reranker._parse_order("1,2,3", 0) is None)
    check("n == 1 parses", reranker._parse_order("1", 1) == [0])


# ---------------------------------------------------------------------------
# rerank() must never raise and never shorten below min(top_k, len(hits)).
# ---------------------------------------------------------------------------

class _Boom:
    class messages:
        @staticmethod
        async def create(**kwargs):
            raise RuntimeError("simulated API failure")


class _Hang:
    class messages:
        @staticmethod
        async def create(**kwargs):
            await asyncio.sleep(30)


def _canned(text: str):
    class Block:
        type = "text"

    Block.text = text

    class Response:
        content = [Block()]

    class Client:
        class messages:
            @staticmethod
            async def create(**kwargs):
                return Response()

    return Client


def test_rerank_never_raises() -> None:
    print("rerank() failure behaviour")
    hits = _hits(10)
    original = reranker._get_client
    try:
        reranker._get_client = lambda: _Boom()
        check("API error falls back to retrieval order", asyncio.run(reranker.rerank("q", hits, 5)) == hits[:5])

        os.environ["RERANK_TIMEOUT_SECONDS"] = "0.05"
        reranker._get_client = lambda: _Hang()
        check("timeout falls back to retrieval order", asyncio.run(reranker.rerank("q", hits, 5)) == hits[:5])
        os.environ.pop("RERANK_TIMEOUT_SECONDS", None)

        reranker._get_client = _canned("I'd rather not.")
        check("unparseable response falls back", asyncio.run(reranker.rerank("q", hits, 5)) == hits[:5])

        reranker._get_client = _canned("Sure! Here you go: 4, 2")
        out = asyncio.run(reranker.rerank("q", hits, 5))
        check("chatty partial response still returns top_k", len(out) == 5, f"got {len(out)}")
        check("chatty partial response honours the stated order", out[0] is hits[3] and out[1] is hits[1])
        check("no duplicates leak through", len({id(hit) for hit in out}) == 5)

        # The find_gaps() gate: a 9-chunk pool at top_k=20 must come back as 9,
        # never fewer, or `if len(results) < 5` turns a pass into a refusal.
        pool = _hits(9)
        check("gaps-style call returns the whole pool", len(asyncio.run(reranker.rerank("q", pool, 20))) == 9)

        reranker._get_client = lambda: _Boom()
        check("single hit short-circuits with no API call", asyncio.run(reranker.rerank("q", hits[:1], 5)) == hits[:1])
        check("empty hits short-circuits", asyncio.run(reranker.rerank("q", [], 5)) == [])
        check("top_k <= 0 returns empty", asyncio.run(reranker.rerank("q", hits, 0)) == [])
    finally:
        reranker._get_client = original


# ---------------------------------------------------------------------------
# The off-path must match pre-reranker behaviour. That is what makes the
# rerank-OFF arm a trustworthy A/B baseline.
# ---------------------------------------------------------------------------

def test_off_path_identical() -> None:
    print("orchestrator off-path")
    calls: list[dict] = []
    rerank_calls: list[tuple] = []

    async def fake_query_collection(**kwargs):
        calls.append(kwargs)
        return _hits(5)

    async def fake_embed_query(query):
        return [0.0] * 1536

    async def fake_rerank(query, hits, top_k):
        rerank_calls.append((query, len(hits), top_k))
        return hits[:top_k]

    saved = (orchestrator.query_collection, orchestrator.embed_query, reranker.rerank)
    prior = os.environ.get("RERANK_ENABLED")
    try:
        orchestrator.query_collection = fake_query_collection
        orchestrator.embed_query = fake_embed_query
        reranker.rerank = fake_rerank

        os.environ.pop("RERANK_ENABLED", None)
        calls.clear()
        rerank_calls.clear()
        asyncio.run(orchestrator.search("resumes", "who?", top_k=5))
        check("off: exactly one query_collection call", len(calls) == 1)
        check("off: fetches exactly top_k", calls[0]["top_k"] == 5, f"got {calls[0]['top_k']}")
        check("off: still passes query_text (hybrid intact)", calls[0]["query_text"] == "who?")
        check("off: reranker never called", rerank_calls == [])

        os.environ["RERANK_ENABLED"] = "true"
        calls.clear()
        rerank_calls.clear()
        out = asyncio.run(orchestrator.search("resumes", "who?", top_k=5))
        check("on: over-fetches the candidate pool", calls[0]["top_k"] == 15, f"got {calls[0]['top_k']}")
        check("on: reranker called once", len(rerank_calls) == 1, f"got {rerank_calls}")
        check("on: reranker asked for top_k", rerank_calls[0][2] == 5)
        check("on: result truncated to top_k", len(out) == 5, f"got {len(out)}")

        calls.clear()
        asyncio.run(orchestrator.search("resumes", "who?", top_k=20))
        check(
            "on: candidate pool capped at RERANK_MAX_CANDIDATES",
            calls[0]["top_k"] == orchestrator.RERANK_MAX_CANDIDATES,
            f"got {calls[0]['top_k']}",
        )

        calls.clear()
        rerank_calls.clear()
        asyncio.run(orchestrator.search("resumes", "who?", top_k=5, rerank=False))
        check("explicit rerank=False wins over the env", calls[0]["top_k"] == 5 and rerank_calls == [])

        async def fake_collections():
            return [{"name": "a"}, {"name": "b"}]

        saved_collections = orchestrator.get_available_collections
        try:
            orchestrator.get_available_collections = fake_collections

            calls.clear()
            rerank_calls.clear()
            asyncio.run(orchestrator.infer_collection("who?"))
            check("infer_collection never reranks", rerank_calls == [])
            check(
                "infer_collection still fetches top_k=1",
                all(call["top_k"] == 1 for call in calls),
                f"got {[call['top_k'] for call in calls]}",
            )
            check("infer_collection sends no query_text", all("query_text" not in call for call in calls))

            calls.clear()
            rerank_calls.clear()
            per_collection = asyncio.run(orchestrator.search_all_collections("who?"))
            check("fan-out reranks per collection, not over a merged pool", len(rerank_calls) == 2, f"got {rerank_calls}")
            check("fan-out keeps its per-collection budget", all(len(hits) == 3 for hits in per_collection.values()))
            check("fan-out over-fetches per collection", all(call["top_k"] == 9 for call in calls), f"got {[c['top_k'] for c in calls]}")

            os.environ.pop("RERANK_ENABLED", None)
            calls.clear()
            rerank_calls.clear()
            per_collection = asyncio.run(orchestrator.search_all_collections("who?"))
            check("fan-out off: no rerank calls", rerank_calls == [])
            check("fan-out off: fetches exactly the budget", all(call["top_k"] == 3 for call in calls))
            check("fan-out off: returns the collections it found", sorted(per_collection) == ["a", "b"])
        finally:
            orchestrator.get_available_collections = saved_collections
    finally:
        orchestrator.query_collection, orchestrator.embed_query, reranker.rerank = saved
        if prior is None:
            os.environ.pop("RERANK_ENABLED", None)
        else:
            os.environ["RERANK_ENABLED"] = prior


# ---------------------------------------------------------------------------
# The eval runner must restore RERANK_ENABLED, or a Slack-triggered run leaves
# the live bot reranking for the rest of its uptime.
# ---------------------------------------------------------------------------

def test_runner_restores_env() -> None:
    print("eval runner env restore")
    from evals import runner
    from evals.schema import EvalRunConfig

    saved_load = runner.load_test_cases
    prior = os.environ.get("RERANK_ENABLED")
    try:
        runner.load_test_cases = lambda path: []

        os.environ["RERANK_ENABLED"] = "false"
        summary = asyncio.run(runner.run_evaluations(EvalRunConfig(cases_path="unused", rerank=True)))
        check("forced on is recorded in the summary", summary.rerank_enabled is True)
        check("model recorded when on", summary.rerank_model == reranker.DEFAULT_MODEL, f"got {summary.rerank_model}")
        check(
            "prior env value restored after a forced-on run",
            os.environ.get("RERANK_ENABLED") == "false",
            f"got {os.environ.get('RERANK_ENABLED')!r}",
        )

        os.environ["RERANK_ENABLED"] = "true"
        summary = asyncio.run(runner.run_evaluations(EvalRunConfig(cases_path="unused", rerank=False)))
        check("forced off is recorded in the summary", summary.rerank_enabled is False)
        check("no model recorded when off", summary.rerank_model is None)
        check(
            "prior env value restored after a forced-off run",
            os.environ.get("RERANK_ENABLED") == "true",
            f"got {os.environ.get('RERANK_ENABLED')!r}",
        )

        os.environ.pop("RERANK_ENABLED", None)
        asyncio.run(runner.run_evaluations(EvalRunConfig(cases_path="unused", rerank=True)))
        check(
            "an absent env var is removed again, not left set",
            "RERANK_ENABLED" not in os.environ,
            f"got {os.environ.get('RERANK_ENABLED')!r}",
        )

        os.environ["RERANK_ENABLED"] = "true"
        summary = asyncio.run(runner.run_evaluations(EvalRunConfig(cases_path="unused")))
        check("rerank=None honours the environment", summary.rerank_enabled is True)
        check("rerank=None leaves the env untouched", os.environ.get("RERANK_ENABLED") == "true")
    finally:
        runner.load_test_cases = saved_load
        if prior is None:
            os.environ.pop("RERANK_ENABLED", None)
        else:
            os.environ["RERANK_ENABLED"] = prior


def test_metric_and_report() -> None:
    print("MRR metric + report rendering")
    from evals.config import OVERALL_SCORE_WEIGHTS
    from evals.metrics import check_expected_source_rank, compute_overall_score
    from evals.report import build_markdown_report
    from evals.schema import DeterministicScores, EvalRunSummary

    mrr, top1 = check_expected_source_rank(["a.md", "b.md", "c.md"], ["b.md"])
    check("MRR of rank 2 is 0.5", mrr == 0.5, f"got {mrr}")
    check("top1 is 0.0 when the hit is at rank 2", top1 == 0.0)

    mrr, top1 = check_expected_source_rank(["b.md", "a.md"], ["b.md"])
    check("MRR of rank 1 is 1.0", mrr == 1.0)
    check("top1 is 1.0 when the hit is at rank 1", top1 == 1.0)

    check("MRR is 0.0 when absent", check_expected_source_rank(["x.md"], ["b.md"])[0] == 0.0)
    check("MRR is 0.0 on empty retrieval", check_expected_source_rank([], ["b.md"]) == (0.0, 0.0))
    check("no expectations -> (None, None)", check_expected_source_rank(["a.md"], []) == (None, None))
    check("matching is case-insensitive", check_expected_source_rank(["A.MD"], ["a.md"])[0] == 1.0)

    check(
        "MRR carries no weight in the overall score",
        "expected_source_mrr" not in OVERALL_SCORE_WEIGHTS
        and "expected_source_top1" not in OVERALL_SCORE_WEIGHTS,
    )

    baseline = compute_overall_score(
        DeterministicScores(expected_source_hit_rate=1.0, expected_fact_coverage=0.5)
    )
    with_mrr = compute_overall_score(
        DeterministicScores(
            expected_source_hit_rate=1.0,
            expected_fact_coverage=0.5,
            expected_source_mrr=0.0,
            expected_source_top1=0.0,
        )
    )
    check("a terrible MRR cannot move overall_score", baseline == with_mrr, f"{baseline} vs {with_mrr}")

    def summary(judge: bool) -> EvalRunSummary:
        return EvalRunSummary(
            run_timestamp="2026-11-04 23:00 UTC",
            judge_enabled=judge,
            rerank_enabled=True,
            rerank_model="claude-haiku-5-5",
            total_cases=1,
            passed=1,
            warnings=0,
            failed=0,
            overall_score=0.99,
            retrieval_quality=1.0,
            expected_source_mrr=0.75,
            expected_source_top1=0.5,
            expected_fact_coverage=1.0,
            format_compliance=1.0,
            citation_presence=1.0,
            hallucination_risk="Low",
            scores_by_task_type={"question_answering": 0.99},
            case_results=[],
            recommendations=[],
            output_markdown="x.md",
            output_json="x.json",
        )

    for judge in (False, True):
        lines = build_markdown_report(summary(judge)).splitlines()
        block = [
            line
            for line in lines[lines.index("## Overall Scores") + 1 : lines.index("## Scores by Task Type")]
            if line.strip()
        ]
        check(
            f"judge={judge}: every Overall Scores line is a bullet",
            all(line.startswith("- ") for line in block),
            f"got {block}",
        )
        check(
            f"judge={judge}: rerank line present",
            any(line.startswith("- Rerank: on (claude-haiku-5-5)") for line in block),
        )
        check(
            f"judge={judge}: MRR line present",
            any(line.startswith("- Expected Source MRR: 75%") for line in block),
        )
        check(
            f"judge={judge}: citation line survived the judge splice",
            any(line.startswith("- Citation Presence:") for line in block),
        )
        if judge:
            check(
                "judge=True: all three judge lines land inside Overall Scores",
                sum(1 for line in block if line.startswith("- Judge ")) == 3,
                f"got {block}",
            )


if __name__ == "__main__":
    for test in (
        test_parse_order,
        test_rerank_never_raises,
        test_off_path_identical,
        test_runner_restores_env,
        test_metric_and_report,
    ):
        test()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("all checks passed")
