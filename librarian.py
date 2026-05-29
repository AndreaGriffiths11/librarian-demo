"""
Librarian sub-agent: pre-digests the raw PR diff into a compact structured
summary that downstream reviewers consume instead of the raw diff.

The digest gives reviewers a small, distilled view of the change. That saves
both input tokens (smaller context per reviewer) and output tokens (reviewers
don't re-parse the diff).

Honest tradeoff: reviewers only see what the librarian summarizes. Quality of
digestion -> quality of review.
"""
from __future__ import annotations

from typing import Any

from openai import OpenAI

from pricing import MODEL


DIGEST_SYSTEM = (
    "You are a senior code reviewer producing a structured digest of a pull "
    "request diff. Your output will be the ONLY context downstream specialist "
    "reviewers have. Be thorough but compact.\n\n"
    "Return a JSON object with these fields:\n"
    "  files: list of {path, summary} for each touched file\n"
    "  surface: dict of {security: [], tests: [], performance: []} "
    "noting concrete observations in each area, with file:line references "
    "where possible\n"
    "  risk_notes: short list of anything subtle a reviewer should know "
    "(architectural shifts, removed safeguards, etc.)\n\n"
    "Stay under 600 tokens. Be specific, not generic. Quote the actual "
    "identifiers and snippets that matter."
)


def build_digest(client: OpenAI, raw_diff: str) -> tuple[str, Any]:
    """Run the librarian's digestion pass. Returns (digest_text, usage)."""
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=800,
        messages=[
            {"role": "system", "content": DIGEST_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Here is the pull request diff:\n\n"
                    "```diff\n" + raw_diff + "\n```\n\n"
                    "Produce the structured JSON digest now."
                ),
            },
        ],
    )
    digest = response.choices[0].message.content or ""
    return digest, response.usage
