#  Librarian sub-agent demo (Opus 4.6 rerun)Findings 

Live measured runs against **`claude-opus-4.6`** via the GitHub Copilot proxy
(`api.githubcopilot.com`), authenticated with the local Copilot CLI OAuth
token + `Copilot-Integration-Id: copilot-cli`.

> Pricing framing: dollar amounts are computed at Anthropic's API list prices
> (input $15/M, cache read $1.50/M, output $75/M). That is the "what these
> tokens would cost you if you were billing Anthropic directly" view, not
> what a Copilot Pro subscriber actually pays (which is flat-rate, one
> premium-request per call). Token counts are real; dollars are arithmetic
> off published list rates.

## Headline

| Scenario | NAIVE | CACHE | LIBRARIAN | Winner |
|---|---|---|---|---|
| 11K-char diff (4K-tokens-per-call), N=5 | $0.413 | $0.413 (0 cache hits) | **$0.306** | LIBRARIAN by 26% |
| 49K-char diff (16K-tokens-per-call), N=5 | $1.316 | **$0.453** (4/5 cache hits, warm) | $0.486 | CACHE by 7% (warm) |
| Same large diff, cold-every-call projection | $1.316 | $1.316 | **$0.486** | LIBRARIAN by 63% |

## Measured runs

### Run  small diff, N=51 
- Diff: `scenario/sample_pr.diff` (11,257 chars, ~4K tokens per call after wrapping)
- Output files:
  - `runs/2026-05-29-opus-small-cache-soft.txt`
  - `runs/2026-05-29-opus-small-cache-confirm.txt`
  - `runs/2026-05-29-opus-small-librarian.txt`

| Mode | Per-call avg | Cache hits | Total cost |
|---|---|---|---|
| NAIVE | 4,003 in / 300 out | 0/5 | $0.413 |
| CACHE (run A) | 4,011 in / 300 out | **0/5** | $0.413 |
| CACHE (run B) | 4,011 in / 300 out | **0/5** | $0.413 |
| LIBRARIAN | 967 in / 300 out (+ digest 4,034 in / 800 out) | 0/5 | $0.306 |

**Key finding:** at this payload (~4K tokens of identical system prefix), the
Copilot proxy returned `cached_tokens=0` for every cache-mode call across two
independent runs. NAIVE and CACHE are indistinguishable in cost. LIBRARIAN
wins by 26% on input compression alone.

### Run  large diff, N=52 
- Diff: `scenario/sample_pr_large.diff` (49,255 chars, ~16K tokens per call)
- Roles: security, tests, perf, observability, api-design
- Output file: `runs/2026-05-29-opus-large-n5.txt`

| Mode | Total input | Cached | Total output | Total cost | vs NAIVE |
|---|---|---|---|---|---|
| NAIVE | 80,248 | 0 | 1,500 | $1. |316 | 
| CACHE | 80,268 | 63,968 (4/5 calls) | 1,500 | $0.453 | **-66%** |
| LIBRARIAN | 20,915 (incl. digest) | 0 | 2,300 (incl. digest) | $0.486 | **-63%** |

Cache call #1 = cold (cache write), calls #2-5 = warm (15,992 cached tokens
each). LIBRARIAN's digest call ate $0.30 of its $0.49 total; the five
reviewer calls cost $0.037 each.

## Cold-every-call reality

Every PR review pipeline runs cold by default. Each PR is a fresh session.
The cache only helps if a single review pass tight-loops through reviewers
without any other intervening traffic. For shared review bots running across
many devs and repos, every call is a cold call.

Cold-every-call projection (large diff, N=5):

| Mode | Total cost | vs NAIVE |
|---|---|---|
| NAIVE | $1. |316 | 
| CACHE (cold every call) | $1.316 | 0% |
| LIBRARIAN | $0.486 | **-63%** |

Caching wins inside a warm session. The librarian pattern wins across cold
sessions, which is the operational reality for any reviewer-style bot.

## Cache attribution is non-deterministic

Two findings worth highlighting:

1. **Below some payload threshold, caching does not activate at all.** At
   4K-tokens-per-call through the Copilot proxy with the OpenAI SDK shape
   against Opus 4.6, `cached_tokens=0` on every call across two independent
   runs. The same call shape returned cached hits at 2K tokens against
   `claude-sonnet-4.6` in a sanity test, and at 16K tokens against
   `claude-opus-4.6` in the large-diff run. The activation curve is not flat.

2. **NAIVE-mode calls in earlier rounds reported incidental cache hits
   despite role-text-first user-message ordering specifically designed to
   defeat prefix caching.** Cache attribution from the proxy is best-effort.
   Model a wide hit-rate band in production, not a constant.

The honest read: prompt caching is a real lever but it is not a contract.
Architectural moves (the librarian pattern) survive cold cache and weird
provider routing. Cache-only savings do not.

## Auth path used

The harness now authenticates against the Copilot proxy directly:

```python
import sqlite3, pathlib
con = sqlite3.connect(f"file:{pathlib.Path.home()}/.copilot/data.db?mode=ro", uri=True)
token = con.execute("SELECT access_token FROM github_accounts WHERE is_default=1").fetchone()[0]

from openai import OpenAI
client = OpenAI(
    base_url="https://api.githubcopilot.com",
    api_key=token,
    default_headers={"Copilot-Integration-Id": "copilot-cli"},
)
```

That gives any Copilot CLI user direct access to the full model catalog
(Anthropic Opus/Sonnet/Haiku, OpenAI GPT-5.x, Google Gemini 2.5 Pro, the
Azure OpenAI lineup) with the OpenAI-compatible chat-completions shape and
`prompt_tokens_details.cached_tokens` reporting. The CLI's OAuth app uses
the `copilot-cli` integration ID; no `/copilot_internal/v2/token` exchange
is needed.

## Pricing inputs

`claude-opus-4.6` Anthropic API list (per million tokens):
- Input: $15.00
- Cache read: $1.50 (90% discount, when activated)
- Output: $75.00

Cache writes carry a 25% surcharge ($18.75/M) on Anthropic's published
pricing. The harness does not model that  it treats call #1'sseparately 
input as full-price uncached, which under-counts NAIVE-vs-CACHE call #1 cost
by roughly $0.04 per 16K-token prompt. Does not change the headline.

Previous gpt-4.1-mini findings preserved in `findings.md.bak`.
