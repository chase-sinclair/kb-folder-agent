"""Pins /kb ticket parsing and the modal-gated Notion creation flow.

Offline: no network, no Slack, no Notion, no pytest. Run with
    .\\.venv\\Scripts\\python.exe tests\\test_ticket_parsing.py

No real Notion task is created. The Notion HTTP layer is replaced by a fake that
records what would have been sent, so the payload assertions are exact.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# slack.bot builds an AsyncApp at import; give it throwaway credentials.
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_SIGNING_SECRET", "test-secret")
os.environ.setdefault("OPENAI_API_KEY", "test-not-used")
os.environ.setdefault("NOTION_API_KEY", "test-not-used")
os.environ.setdefault("NOTION_DATABASE_ID", "test-db")

FAILURES: list[str] = []
CHECKS = 0


def check(label, cond):
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        FAILURES.append(label)


import integrations.notion as N  # noqa: E402
from slack import bot as B  # noqa: E402

# The exact command from the real failed Slack test, with curly quotes.
CURLY = (
    "“Confirm which indirect rate schedule governs the "
    "HQ-2024-SOC-0041 cost volume” high 2026-11-20"
)
STRAIGHT = (
    '"Confirm which indirect rate schedule governs the '
    'HQ-2024-SOC-0041 cost volume" high 2026-11-20'
)
EXPECTED_NAME = (
    "Confirm which indirect rate schedule governs the HQ-2024-SOC-0041 cost volume"
)


# -------------------------------------------------------------- 1 & 2 parsing

def test_parsing():
    print("test_quote_styles_and_the_real_failure")
    for label, text in (("curly", CURLY), ("straight", STRAIGHT)):
        name, prio, due, err = B.parse_ticket_args(text)
        check(f"{label}: no error", err is None)
        check(f"{label}: task name exact", name == EXPECTED_NAME)
        check(f"{label}: priority High", prio == "High")
        check(f"{label}: due date 2026-11-20", due == "2026-11-20")
        check(f"{label}: no quote chars left in name", not any(q in (name or "") for q in "\"'“”"))
        check(f"{label}: args not appended to name", "high" not in (name or "").lower())

    print("test_backward_compatible_forms")
    cases = [
        ('"Simple task"', "Simple task", "Medium", None),
        ("'Single quoted' low", "Single quoted", "Low", None),
        ('"Dated" 2026-01-05', "Dated", "Medium", "2026-01-05"),
        ('"Reordered" 2026-01-05 high', "Reordered", "High", "2026-01-05"),
        ("Unquoted plain task", "Unquoted plain task", "Medium", None),
        ("Unquoted with args high 2026-03-03", "Unquoted with args", "High", "2026-03-03"),
        ("‘Curly single’ medium", "Curly single", "Medium", None),
    ]
    for text, exp_name, exp_prio, exp_due in cases:
        name, prio, due, err = B.parse_ticket_args(text)
        check(f"{text[:34]!r} -> {exp_name!r}/{exp_prio}/{exp_due}",
              err is None and name == exp_name and prio == exp_prio and due == exp_due)


# ------------------------------------------------------------------ 3 validation

def test_validation():
    print("test_invalid_input_errors_instead_of_creating")
    bad = [
        ("", "empty"),
        ("   ", "whitespace only"),
        ('"Unterminated quote high', "missing closing quote"),
        ('"Name" bogus-token', "unrecognised trailing token"),
        ('""', "empty quoted name"),
        ('"" high', "empty quoted name with args"),
    ]
    for text, label in bad:
        name, _p, _d, err = B.parse_ticket_args(text)
        check(f"{label}: rejected with a message", err is not None and name is None)
    name, _p, _d, err = B.parse_ticket_args('"Unterminated quote high')
    check("missing-quote error names the problem", err and "closing" in err.lower())
    name, _p, _d, err = B.parse_ticket_args('"Name" bogus-token')
    check("unknown-token error echoes the token", err and "bogus-token" in err)


# ----------------------------------------------------- 4 & 5 modal gates creation

def test_modal_does_not_create():
    print("test_opening_and_cancelling_create_nothing")
    created: list = []

    async def fake_create(*a, **kw):
        created.append((a, kw))
        return "https://notion.so/should-not-happen"

    orig_create, orig_users = B.create_ticket, B.list_assignable_users
    B.create_ticket = fake_create

    async def fake_users():
        return [{"id": "11111111-1111-1111-1111-111111111111", "name": "Chase Sinclair"}]

    B.list_assignable_users = fake_users

    opened: list = []

    class FakeClient:
        async def views_open(self, trigger_id=None, view=None):
            opened.append({"trigger_id": trigger_id, "view": view})

    asyncio.run(B._open_ticket_modal(FakeClient(), "trig-1", CURLY, "C123", "U123"))
    check("modal was opened", len(opened) == 1)
    check("opening the modal created NO Notion page", created == [])

    view = opened[0]["view"]
    check("callback_id is the ticket modal", view["callback_id"] == B.TICKET_CALLBACK_ID)
    check("submit button says Create Ticket", view["submit"]["text"] == "Create Ticket")
    check("close button says Cancel", view["close"]["text"] == "Cancel")
    meta = json.loads(view["private_metadata"])
    check("originating channel preserved in private_metadata", meta["channel_id"] == "C123")

    by_id = {b["block_id"]: b for b in view["blocks"] if "block_id" in b}
    check("task name prefilled from the command",
          by_id["task_name"]["element"]["initial_value"] == EXPECTED_NAME)
    check("priority prefilled High",
          by_id["priority"]["element"]["initial_option"]["value"] == "High")
    check("due date prefilled 2026-11-20",
          by_id["due_date"]["element"]["initial_date"] == "2026-11-20")
    check("assignee select offered when Notion users are listable", "assignee" in by_id)
    check("assignee is optional", by_id["assignee"].get("optional") is True)
    check("notes field present and optional",
          "notes" in by_id and by_id["notes"].get("optional") is True)

    # Cancelling = the view submission handler is never invoked. Nothing else runs,
    # so no page can be created.
    check("cancelling creates NO Notion page", created == [])

    # No listable users -> free-text requested owner, clearly not an assignment.
    async def no_users():
        return []

    B.list_assignable_users = no_users
    opened.clear()
    asyncio.run(B._open_ticket_modal(FakeClient(), "trig-2", CURLY, "C123", "U123"))
    by_id2 = {b["block_id"]: b for b in opened[0]["view"]["blocks"] if "block_id" in b}
    check("no Notion users -> requested_owner field instead", "requested_owner" in by_id2)
    check("no Notion users -> no assignee select", "assignee" not in by_id2)
    check("fallback label says it is not a Notion assignment",
          "not a Notion assignment" in by_id2["requested_owner"]["label"]["text"])

    B.create_ticket, B.list_assignable_users = orig_create, orig_users


# --------------------------------------------------- 6,7,8 submission -> payload

def test_payload_building():
    print("test_notion_payload_from_submission")

    def view_for(**over):
        vals = {
            "task_name": {"value": {"value": EXPECTED_NAME}},
            "priority": {"value": {"selected_option": {"value": "High"}}},
            "due_date": {"value": {"selected_date": "2026-11-20"}},
        }
        vals.update(over)
        return {"state": {"values": vals}, "private_metadata": "{}"}

    bare = B.extract_ticket_submission(view_for())
    check("minimal submission parses", bare["task_name"] == EXPECTED_NAME and bare["priority"] == "High")
    check("no assignee submitted -> None", bare["assignee_id"] is None)
    check("no notes submitted -> None", bare["notes"] is None)

    p = N.build_page_payload("db1", **{k: bare[k] for k in ("task_name", "priority", "due_date")})
    check("payload title correct",
          p["properties"]["Task name"]["title"][0]["text"]["content"] == EXPECTED_NAME)
    check("payload priority select correct", p["properties"]["Priority"]["select"]["name"] == "High")
    check("payload due date correct", p["properties"]["Due date"]["date"]["start"] == "2026-11-20")
    check("no Assignee property when unassigned", "Assignee" not in p["properties"])
    check("no children when no notes", "children" not in p)

    uid = "353d872b-594c-81af-81bd-0002099bca41"
    withall = B.extract_ticket_submission(view_for(
        assignee={"value": {"selected_option": {"value": uid, "text": {"text": "Chase Sinclair"}}}},
        notes={"value": {"value": "42% overhead vs 30% on a different base.\n\nFinance must confirm."}},
    ))
    check("assignee id extracted", withall["assignee_id"] == uid)
    check("assignee display name extracted", withall["assignee_name"] == "Chase Sinclair")
    check("notes extracted", withall["notes"].startswith("42% overhead"))

    p2 = N.build_page_payload(
        "db1", withall["task_name"], withall["priority"], withall["due_date"],
        assignee_id=withall["assignee_id"], notes=withall["notes"],
    )
    people = p2["properties"]["Assignee"]["people"]
    check("Assignee people property set with the Notion user id",
          people == [{"object": "user", "id": uid}])
    kids = p2["children"]
    check("notes written under an 'Additional Notes' heading",
          kids[0]["type"] == "heading_2"
          and kids[0]["heading_2"]["rich_text"][0]["text"]["content"] == "Additional Notes")
    body = " ".join(
        b["paragraph"]["rich_text"][0]["text"]["content"]
        for b in kids if b["type"] == "paragraph"
    )
    check("both note paragraphs present in the page body",
          "42% overhead" in body and "Finance must confirm" in body)

    p3 = N.build_page_payload("db1", "T", "Low", None, requested_owner="Dana")
    owner_text = " ".join(
        b["paragraph"]["rich_text"][0]["text"]["content"]
        for b in p3["children"] if b["type"] == "paragraph"
    )
    check("free-text owner is labelled as not assigned in Notion",
          "not assigned in Notion" in owner_text)
    check("free-text owner does NOT set the Assignee property",
          "Assignee" not in p3["properties"])

    long_note = "x" * 4500
    p4 = N.build_page_payload("db1", "T", "Low", None, notes=long_note)
    sizes = [len(b["paragraph"]["rich_text"][0]["text"]["content"])
             for b in p4["children"] if b["type"] == "paragraph"]
    check(f"a 4500-char note is split under Notion's 2000 cap (chunks={sizes})",
          sizes and all(n <= 2000 for n in sizes) and sum(sizes) == 4500)


# -------------------------------------------------- 9 & 10 success / failure paths

def test_http_paths():
    print("test_success_and_failure_handling")

    class FakeResp:
        def __init__(self, status, payload):
            self.status, self._p = status, payload
            self.ok = 200 <= status < 300

        async def json(self):
            return self._p

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        calls: list = []

        def __init__(self, *a, **kw):
            pass

        def post(self, url, headers=None, json=None):
            FakeSession.calls.append({"url": url, "json": json})
            return FakeResp(*FakeSession.next_response)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeAiohttp:
        ClientSession = FakeSession

        class ClientTimeout:
            def __init__(self, *a, **kw):
                pass

    orig = N.aiohttp
    N.aiohttp = FakeAiohttp

    FakeSession.calls.clear()
    FakeSession.next_response = (200, {"url": "https://www.notion.so/real-page-abc"})
    url = asyncio.run(N.create_ticket(EXPECTED_NAME, "High", "2026-11-20"))
    check("success returns the real Notion URL", url == "https://www.notion.so/real-page-abc")
    check("exactly one POST issued", len(FakeSession.calls) == 1)

    fields = {"task_name": EXPECTED_NAME, "priority": "High", "due_date": "2026-11-20",
              "assignee_id": None, "assignee_name": None, "requested_owner": None, "notes": None}
    fallback, blocks = asyncio.run(B.create_ticket_from_submission(fields))
    check("confirmation names the task", EXPECTED_NAME in fallback)
    check("confirmation reports High priority", "Priority: High" in fallback)
    check("confirmation includes the due date", "2026-11-20" in fallback)
    check("confirmation links to the real URL",
          any("real-page-abc" in json_dumps(b) for b in blocks))

    FakeSession.calls.clear()
    FakeSession.next_response = (401, {"message": "API token is invalid."})
    raised = None
    try:
        asyncio.run(N.create_ticket("x"))
    except RuntimeError as exc:
        raised = str(exc)
    check("HTTP error raises rather than returning success", raised is not None)
    check("error message carries the status and reason",
          raised and "401" in raised and "invalid" in raised.lower())
    check("failed create issued exactly one POST (no retry, no duplicates)",
          len(FakeSession.calls) == 1)

    N.aiohttp = orig


def json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


# -------------------------------------------------------------- regression guard

def test_other_commands_still_parse():
    print("test_existing_commands_unaffected")
    cases = [
        ('ask pastperformance "What DHS work have we done?"',
         ("ask", "pastperformance", "What DHS work have we done?")),
        ('ask pastperformance “What DHS work have we done?”',
         ("ask", "pastperformance", "What DHS work have we done?")),
        ('score technicalvolume "The contractor shall operate a SOC."',
         ("score", "technicalvolume", "The contractor shall operate a SOC.")),
        ("list", ("list", "", "")),
        ("diff CostVolume PricingStrategy_Notes.md",
         ("diff", "CostVolume", "PricingStrategy_Notes.md")),
    ]
    for text, expected in cases:
        got = B._parse_command(text)
        check(f"{text[:44]!r} parses unchanged", got == expected)
    check("curly quotes normalised for every command",
          B._normalize_quotes("“a” ‘b’") == "\"a\" 'b'")


if __name__ == "__main__":
    test_parsing()
    test_validation()
    test_modal_does_not_create()
    test_payload_building()
    test_http_paths()
    test_other_commands_still_parse()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} of {CHECKS} checks FAILED:")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print(f"all {CHECKS} checks passed")
