"""Offline tests for classify_exception. No network, no Qdrant, no DB writes.

Deliberately dependency-free (pytest is not in requirements.txt). Run:

    .\\.venv\\Scripts\\python.exe tests\\test_quarantine_classify.py

Context: on 2026-11-05 the OneDrive watcher quarantined 8 of 30 new ConsultingKB
files as CORRUPT_FILE with the message "ResponseHandlingException: " (empty
detail). That is qdrant_client's wrapper for a connection-level failure, it does
not subclass OSError, and its str() was empty -- so it slipped past every branch
and landed on the CORRUPT_FILE default. CORRUPT_FILE is not in RETRYABLE_ERRORS
and onedrive_watcher returns early on is_quarantined(), so those files were
skipped on every poll, permanently. These tests pin the classification so that
cannot regress.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests.exceptions as rexc  # noqa: E402
from qdrant_client.http.exceptions import (  # noqa: E402
    ResponseHandlingException,
    UnexpectedResponse,
)

from ingestion.quarantine import (  # noqa: E402
    ErrorType,
    RETRYABLE_ERRORS,
    classify_exception,
    should_retry,
)

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        _failures.append(label)


def _unexpected(status: int) -> UnexpectedResponse:
    return UnexpectedResponse(
        status_code=status,
        reason_phrase="",
        content=b"",
        headers=None,
    )


# ---------------------------------------------------------------------------
# The regression this file exists for.
# ---------------------------------------------------------------------------

def test_qdrant_connection_failure_is_transient() -> None:
    print("test_qdrant_connection_failure_is_transient")

    # The exact shape seen in production: empty detail, so the text scan at the
    # bottom of classify_exception finds no transient marker.
    empty = ResponseHandlingException(Exception())
    check(
        "ResponseHandlingException with an empty detail -> TRANSIENT_ERROR",
        classify_exception(empty) is ErrorType.TRANSIENT_ERROR,
        f"got {classify_exception(empty)}",
    )
    check(
        "...and it is retryable at retry_count 0",
        should_retry(classify_exception(empty), 0),
    )
    check(
        "...and still retryable at retry_count 2 (last of 3 attempts)",
        should_retry(classify_exception(empty), 2),
    )
    check(
        "...and exhausted at retry_count 3",
        not should_retry(classify_exception(empty), 3),
    )

    spoken = ResponseHandlingException(Exception("All connection attempts failed"))
    check(
        "ResponseHandlingException with a message -> TRANSIENT_ERROR",
        classify_exception(spoken) is ErrorType.TRANSIENT_ERROR,
        f"got {classify_exception(spoken)}",
    )

    # The structural facts that made the bug invisible. If either of these ever
    # changes upstream, the dedicated isinstance check becomes redundant rather
    # than wrong -- but the test should say so out loud.
    check(
        "ResponseHandlingException is not an OSError subclass",
        not issubclass(ResponseHandlingException, OSError),
    )
    check(
        "CORRUPT_FILE is not retryable (why the misclassification was permanent)",
        ErrorType.CORRUPT_FILE not in RETRYABLE_ERRORS,
    )
    check(
        "TRANSIENT_ERROR is retryable",
        ErrorType.TRANSIENT_ERROR in RETRYABLE_ERRORS,
    )


# ---------------------------------------------------------------------------
# Everything the earlier fix already got right must stay right.
# ---------------------------------------------------------------------------

def test_qdrant_http_statuses_unchanged() -> None:
    print("test_qdrant_http_statuses_unchanged")

    for status in (429, 500, 502, 503, 504):
        got = classify_exception(_unexpected(status))
        check(
            f"UnexpectedResponse({status}) -> TRANSIENT_ERROR",
            got is ErrorType.TRANSIENT_ERROR,
            f"got {got}",
        )

    for status in (400, 404):
        got = classify_exception(_unexpected(status))
        check(
            f"UnexpectedResponse({status}) -> CORRUPT_FILE (will not fix itself)",
            got is ErrorType.CORRUPT_FILE,
            f"got {got}",
        )


def test_requests_and_graph_errors_unchanged() -> None:
    print("test_requests_and_graph_errors_unchanged")

    for exc in (
        rexc.ConnectionError("Connection aborted"),
        rexc.Timeout("Read timed out"),
        rexc.ChunkedEncodingError("partial read"),
    ):
        got = classify_exception(exc)
        check(
            f"requests {type(exc).__name__} -> TRANSIENT_ERROR",
            got is ErrorType.TRANSIENT_ERROR,
            f"got {got}",
        )

    resp_503 = requests_response(503)
    http_503 = rexc.HTTPError("503 Server Error")
    http_503.response = resp_503
    check(
        "requests HTTPError 503 -> TRANSIENT_ERROR",
        classify_exception(http_503) is ErrorType.TRANSIENT_ERROR,
        f"got {classify_exception(http_503)}",
    )

    resp_401 = requests_response(401)
    http_401 = rexc.HTTPError("401 Unauthorized")
    http_401.response = resp_401
    check(
        "requests HTTPError 401 -> CORRUPT_FILE",
        classify_exception(http_401) is ErrorType.CORRUPT_FILE,
        f"got {classify_exception(http_401)}",
    )

    # Documented in CLAUDE.md: every requests exception subclasses OSError, so a
    # bare RequestException has to be settled as CORRUPT_FILE or the OSError
    # branch below would retry it forever.
    bare = rexc.RequestException("something else entirely")
    check(
        "bare requests RequestException -> CORRUPT_FILE (deliberate)",
        classify_exception(bare) is ErrorType.CORRUPT_FILE,
        f"got {classify_exception(bare)}",
    )


def test_genuinely_bad_files_still_corrupt() -> None:
    print("test_genuinely_bad_files_still_corrupt")

    decode = UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")
    check(
        "UnicodeDecodeError -> CORRUPT_FILE",
        classify_exception(decode) is ErrorType.CORRUPT_FILE,
        f"got {classify_exception(decode)}",
    )
    check(
        "ValueError -> CORRUPT_FILE",
        classify_exception(ValueError("could not parse document")) is ErrorType.CORRUPT_FILE,
    )
    check(
        "KeyError -> CORRUPT_FILE",
        classify_exception(KeyError("missing field")) is ErrorType.CORRUPT_FILE,
    )
    # OSError stays transient: a locked or momentarily unavailable file.
    check(
        "OSError -> TRANSIENT_ERROR",
        classify_exception(OSError("device not ready")) is ErrorType.TRANSIENT_ERROR,
    )
    check(
        "TimeoutError -> TRANSIENT_ERROR",
        classify_exception(TimeoutError()) is ErrorType.TRANSIENT_ERROR,
    )


def test_marker_scan_still_backstops() -> None:
    print("test_marker_scan_still_backstops")

    for message in (
        "503 Service Unavailable",
        "connection reset by peer",
        "the operation timed out",
        "429 Too Many Requests",
    ):
        got = classify_exception(Exception(message))
        check(
            f"text backstop: {message!r} -> TRANSIENT_ERROR",
            got is ErrorType.TRANSIENT_ERROR,
            f"got {got}",
        )
    check(
        "unrecognised message -> CORRUPT_FILE",
        classify_exception(Exception("malformed heading table")) is ErrorType.CORRUPT_FILE,
    )


def requests_response(status: int):
    """Minimal stand-in for a requests Response; only status_code is read."""

    class _Resp:
        status_code = status

    return _Resp()


if __name__ == "__main__":
    for test in (
        test_qdrant_connection_failure_is_transient,
        test_qdrant_http_statuses_unchanged,
        test_requests_and_graph_errors_unchanged,
        test_genuinely_bad_files_still_corrupt,
        test_marker_scan_still_backstops,
    ):
        test()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("all checks passed")
