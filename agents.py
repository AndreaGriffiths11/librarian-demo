"""
The three specialist reviewer agents: security, tests, performance.

Each agent is a single chat completion that returns a structured JSON review.
The same trio runs in all three modes (naive / cache / librarian). What
differs is the SHAPE of the context they receive and where the diff lives:

  naive      diff lives in the USER message, role-specific text comes FIRST
             (so the prefix differs per call -> caching cannot kick in).
  cache      diff lives in the SYSTEM message (identical across all 3 calls,
             so OpenAI auto-caches the system prefix on calls 2 and 3).
             Role-specific text is in the USER message.
  librarian  reviewer sees the compact digest only (in USER message). No
             caching (digest is below the 1024-token minimum).
"""
from __future__ import annotations

from typing import Any

from openai import OpenAI

from pricing import MODEL


REVIEWER_ROLES = {
    "security": (
        "You are the SECURITY reviewer. Focus only on auth, injection, "
        "secrets, sensitive-data exposure, CSRF, access control. Ignore "
        "perf and test concerns - other reviewers handle those."
    ),
    "tests": (
        "You are the TEST COVERAGE reviewer. Focus only on missing tests, "
        "skipped tests, weak assertions, untested code paths. Ignore "
        "security and perf concerns - other reviewers handle those."
    ),
    "perf": (
        "You are the PERFORMANCE reviewer. Focus only on N+1 queries, "
        "synchronous I/O in request paths, unbounded loads, missing "
        "pagination, inefficient filtering. Ignore security and test "
        "concerns - other reviewers handle those."
    ),
    "observability": (
        "You are the OBSERVABILITY reviewer. Focus only on logging hygiene "
        "(PII in logs, log levels, structured context), error handling "
        "(silent except, lost exceptions), and missing metrics or audit "
        "hooks on important paths. Ignore other concerns - other reviewers "
        "handle those."
    ),
    "api-design": (
        "You are the API DESIGN reviewer. Focus only on endpoint naming, "
        "HTTP status codes, request/response shape, idempotency, backward "
        "compatibility, and consistency with the rest of the API surface. "
        "Ignore other concerns - other reviewers handle those."
    ),
}


RESPONSE_FORMAT_INSTRUCTION = (
    "Respond with a strict JSON object: "
    '{"findings": [{"severity": "low|medium|high", '
    '"file": str, "line_hint": str, "issue": str}], "summary": str}. '
    "Keep findings concrete and short. Maximum 5 findings."
)


# --- naive mode -------------------------------------------------------------

def review_naive(client: OpenAI, role: str, raw_diff: str) -> Any:
    """Naive: role text + diff embedded in the user message. No caching.

    The role text comes FIRST in the user message, so the prompt prefix
    differs from call to call - OpenAI's auto-caching cannot kick in.
    """
    return client.chat.completions.create(
        model=MODEL,
        max_tokens=300,
        messages=[
            {
                "role": "system",
                "content": "You are a specialist code reviewer. " + RESPONSE_FORMAT_INSTRUCTION,
            },
            {
                "role": "user",
                "content": (
                    REVIEWER_ROLES[role] + "\n\n"
                    "Here is the pull request diff under review:\n\n"
                    "```diff\n" + raw_diff + "\n```\n\n"
                    "Produce your JSON review now."
                ),
            },
        ],
    )


# --- cache mode -------------------------------------------------------------

def review_cache(client: OpenAI, role: str, raw_diff: str) -> Any:
    """Cache: shared diff in system message (identical across all 3 calls).

    OpenAI/GitHub Models auto-caches identical prompt prefixes >= 1024 tokens.
    The system message contains everything that's identical across reviewers;
    the user message holds only the per-role variation.
    """
    return client.chat.completions.create(
        model=MODEL,
        max_tokens=300,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a specialist code reviewer examining a pull "
                    "request. " + RESPONSE_FORMAT_INSTRUCTION + "\n\n"
                    "Here is the pull request diff under review:\n\n"
                    "```diff\n" + raw_diff + "\n```"
                ),
            },
            {
                "role": "user",
                "content": REVIEWER_ROLES[role] + "\n\nProduce your JSON review now.",
            },
        ],
    )


# --- librarian mode ---------------------------------------------------------

def review_with_digest(client: OpenAI, role: str, digest: str) -> Any:
    """Librarian: reviewer sees the compact digest only, not the raw diff."""
    return client.chat.completions.create(
        model=MODEL,
        max_tokens=300,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a specialist code reviewer. The librarian "
                    "sub-agent has pre-analyzed the pull request and "
                    "produced the structured digest provided by the user. "
                    "Trust it as your view of the change. "
                    + RESPONSE_FORMAT_INSTRUCTION
                ),
            },
            {
                "role": "user",
                "content": (
                    REVIEWER_ROLES[role] + "\n\n"
                    "Librarian digest:\n\n" + digest + "\n\n"
                    "Produce your JSON review now."
                ),
            },
        ],
    )
