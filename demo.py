"""
Librarian sub-agent demo - entry point.

Compares three architectures for running multiple specialist code reviewers
against the same pull request diff, using Anthropic Claude Opus 4.6 via
the GitHub Copilot proxy (api.githubcopilot.com) and Anthropic's prompt
caching.

  naive      Each reviewer makes its own call with the full diff in the user
             message. Role text comes first so prompts differ per call ->
             no caching.

  cache      Each reviewer makes its own call with the diff in the system
             message (identical across calls). The provider auto-caches the
             system prefix on calls 2+.

  librarian  A librarian sub-agent first digests the diff into a compact
             summary. Reviewers run against the digest instead of the raw
             diff. Saves both input (smaller context) and output (no
             re-parsing).

Usage:
    python demo.py [naive|cache|librarian|all] [--verbose]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI

from pricing import MODEL, COPILOT_PROXY_ENDPOINT, cost_for, cached_tokens
from librarian import build_digest
from agents import review_naive, review_cache, review_with_digest


COPILOT_DB_PATH = Path.home() / ".copilot" / "data.db"


def _copilot_oauth_token() -> str | None:
    """Read the local Copilot CLI OAuth token from its SQLite store.

    The Copilot CLI signs in via its own OAuth app and stores the resulting
    bearer token in ~/.copilot/data.db. That token works directly against
    api.githubcopilot.com when paired with the Copilot-Integration-Id header.
    No /copilot_internal/v2/token exchange is needed for this app.
    """
    import sqlite3
    if not COPILOT_DB_PATH.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{COPILOT_DB_PATH}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT access_token FROM github_accounts WHERE is_default=1 LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row and row[0] else None


DIFF_PATH = Path(__file__).parent / "scenario" / "sample_pr.diff"
ROLES = ["security", "tests", "perf", "observability", "api-design"]


# --- ANSI -------------------------------------------------------------------

_USE_COLOR = sys.stdout.isatty()
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s
def bold(s): return _c("1", s)
def dim(s):  return _c("2", s)
def red(s):  return _c("31", s)
def grn(s):  return _c("32", s)
def cyn(s):  return _c("36", s)


# --- data -------------------------------------------------------------------

@dataclass
class CallRecord:
    label: str
    prompt_tokens: int       # total input (including cached)
    cached_tokens: int       # subset that hit the cache
    completion_tokens: int
    wall_seconds: float
    cost: float


@dataclass
class ModeResult:
    name: str
    calls: list[CallRecord] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return sum(c.cost for c in self.calls)

    @property
    def total_prompt(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def total_cached(self) -> int:
        return sum(c.cached_tokens for c in self.calls)

    @property
    def total_completion(self) -> int:
        return sum(c.completion_tokens for c in self.calls)


def record(label: str, usage, elapsed: float) -> CallRecord:
    return CallRecord(
        label=label,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        cached_tokens=cached_tokens(usage),
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        wall_seconds=elapsed,
        cost=cost_for(usage),
    )


# --- runners ----------------------------------------------------------------

def run_naive(client: OpenAI, raw_diff: str) -> ModeResult:
    result = ModeResult(name="naive")
    for role in ROLES:
        t0 = time.perf_counter()
        resp = review_naive(client, role, raw_diff)
        result.calls.append(record(role, resp.usage, time.perf_counter() - t0))
    return result


def run_cache(client: OpenAI, raw_diff: str, soft_guard: bool = False) -> ModeResult:
    """Sequential - the 1st call primes cache, 2nd and 3rd MUST read it."""
    result = ModeResult(name="cache")
    for i, role in enumerate(ROLES):
        t0 = time.perf_counter()
        resp = review_cache(client, role, raw_diff)
        rec = record(role, resp.usage, time.perf_counter() - t0)
        result.calls.append(rec)
        # Fail-loud guardrail: calls 2+ must see cache reads.
        if i > 0 and rec.cached_tokens == 0:
            msg = (
                f"\nCACHE MISS DETECTED on cache/{role} (call #{i+1}). "
                + ("Continuing anyway (--soft-cache).\n" if soft_guard else "Demo invalid - the cached prefix is not being hit.\n")
                + "Possible causes:\n"
                + f"  - System message is below the 1024-token cache minimum "
                + f"(this call's prompt: {rec.prompt_tokens} tokens)\n"
                + "  - System message differs across calls (something varying "
                + "got into it - timestamps, UUIDs, etc.)\n"
                + "  - Cache eviction (>5-10 min between calls)\n"
                + "  - Provider routing - load balancer hit a worker without "
                + "the warmed prefix"
            )
            print(red(bold(msg)))
            if not soft_guard:
                sys.exit(2)
    return result


def run_librarian(client: OpenAI, raw_diff: str) -> ModeResult:
    """Digest pass first, then reviewers against the digest."""
    result = ModeResult(name="librarian")

    t0 = time.perf_counter()
    digest, usage = build_digest(client, raw_diff)
    result.calls.append(record("digest", usage, time.perf_counter() - t0))

    for role in ROLES:
        t0 = time.perf_counter()
        resp = review_with_digest(client, role, digest)
        result.calls.append(record(role, resp.usage, time.perf_counter() - t0))
    return result


# --- output -----------------------------------------------------------------

def print_summary(results: list[ModeResult]) -> None:
    """Tight vertical-friendly summary. This is the film-ready cut."""
    print()
    print(bold("  Librarian sub-agent demo  ") + dim(f"({MODEL})"))
    print(dim("  " + "-" * 50))

    baseline = next((r for r in results if r.name == "naive"), None)
    for r in results:
        label = r.name.upper().ljust(11)
        cost = f"${r.total_cost:.4f}"
        line = f"  {bold(label)} {cost}"
        if baseline and r.name != "naive" and baseline.total_cost > 0:
            pct = (1 - r.total_cost / baseline.total_cost) * 100
            tag = grn(f"-{pct:.0f}% vs naive") if pct >= 0 else red(f"+{-pct:.0f}% vs naive")
            line += f"   ( {tag} )"
        print(line)
    print()


def print_verbose(results: list[ModeResult]) -> None:
    print()
    print(bold("Per-call breakdown"))
    print(dim("─" * 78))
    hdr = f"  {'mode/call':<22} {'input':>7} {'cached':>7} {'output':>7} {'wall':>6}   cost"
    print(dim(hdr))
    print(dim("  " + "─" * 76))
    for r in results:
        for c in r.calls:
            cached_label = str(c.cached_tokens) if c.cached_tokens else "-"
            row = (
                f"  {(r.name + '/' + c.label):<22} "
                f"{c.prompt_tokens:>7} {cached_label:>7} "
                f"{c.completion_tokens:>7} "
                f"{c.wall_seconds:>5.1f}s   ${c.cost:.4f}"
            )
            print(row)
        tot = (
            f"  {(r.name.upper() + ' TOTAL'):<22} "
            f"{r.total_prompt:>7} {r.total_cached:>7} "
            f"{r.total_completion:>7} "
            f"{'':>6}   {bold(f'${r.total_cost:.4f}')}"
        )
        print(tot)
        print(dim("  " + "─" * 76))
    print()


# --- main -------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Librarian sub-agent demo")
    ap.add_argument("mode", nargs="?", default="all",
                    choices=["naive", "cache", "librarian", "all"])
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print per-call token/cost breakdown")
    ap.add_argument("--diff", default=str(DIFF_PATH),
                    help="path to diff file (default: scenario/sample_pr.diff)")
    ap.add_argument("--soft-cache", action="store_true",
                    help="warn on cache miss instead of hard-failing (diagnostic only)")
    args = ap.parse_args()

    token = os.environ.get("COPILOT_OAUTH_TOKEN") or _copilot_oauth_token()
    if not token:
        print(red("No Copilot OAuth token found."))
        print(dim("Sign in with `copilot` once so ~/.copilot/data.db has a default account,"))
        print(dim("or export COPILOT_OAUTH_TOKEN=<gho_...> manually."))
        return 1

    raw_diff = Path(args.diff).read_text()
    client = OpenAI(
        base_url=COPILOT_PROXY_ENDPOINT,
        api_key=token,
        default_headers={"Copilot-Integration-Id": "copilot-cli"},
    )

    runners = {
        "naive": run_naive,
        "cache": lambda c, d: run_cache(c, d, soft_guard=args.soft_cache),
        "librarian": run_librarian,
    }
    selected = ["naive", "cache", "librarian"] if args.mode == "all" else [args.mode]

    print(dim(f"Running mode(s): {', '.join(selected)} on {MODEL} ({Path(args.diff).name}, {len(raw_diff):,} chars)"))
    results: list[ModeResult] = []
    for mode_name in selected:
        print(cyn(f"\n▸ {mode_name} ..."))
        results.append(runners[mode_name](client, raw_diff))

    if args.verbose:
        print_verbose(results)
    print_summary(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
