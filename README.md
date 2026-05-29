# librarian-demo

A small Python harness that measures the real dollar cost of three
multi-agent code-review architectures against the same pull request diff.

Headline numbers, 5 reviewer agents reading a 16K-token PR on Claude Opus 4.6
(Anthropic API list prices, May 29 2026):

| Mode | Input | Cached | Output | Total |
|---|---|---|---|---|
| Naive | 80,248 | 0 | 1,500 | **\$1.316** |
| Cache | 80,268 | 63,968 (4/5 calls) | 1,500 | **\$0.453** |
| Librarian | 20,915 | 0 | 2,300 | **\$0.486** |

Cache wins on warm series, librarian wins on cold-every-call (the operational
reality for review bots that run on PRs from many devs across many repos):

| Mode | Warm series (5 calls back-to-back) | Cold every call |
|---|---|---|
| Naive | \$1.32 | \$1.32 |
| Cache | \$0.45 | \$1.32 |
| Librarian | \$0.49 | \$0.49 |

Full writeup and methodology in [`runs/findings.md`](runs/findings.md).

Inspired by Cho et al., [*Long Live the Librarian!*](https://arxiv.org/abs/2605.27787)
(arXiv:2605.27787). The paper measures GPU energy savings on SWE-Bench
Verified. This harness measures wall-clock dollars on a single PR review pass.
Same architectural idea, different proxy.

## What it shows

Five specialist reviewer agents (security, tests, perf, observability,
api-design) examine the same diff. The harness runs the task in three modes
and prints real measured token counts plus cost numbers priced at Anthropic
list rates.

| mode | what it does |
|---|---|
| `naive` | Each reviewer makes its own call with the full diff in the user message. Role text comes first so the prefix differs per call and prompt caching cannot activate. |
| `cache` | Each reviewer puts the diff in the system message (identical across all 5 calls). The provider caches the system prefix on call 1; calls 2-5 read from cache at 90% off. |
| `librarian` | A first agent digests the diff into a compact JSON summary. Reviewers run against the digest instead of the raw diff. |

## What I learned

1. **Naive is comically wasteful** at any meaningful scale. $1.32 for one PR
   pass on five reviewers. Caching or librarian cuts roughly two-thirds off.
2. **Cache wins on warm series, librarian wins on cold-every-call.** Code
   review bots that run on PRs from many devs across many repos are the
   second case. The librarian's savings are baked into call shape, not
   runtime cache state.
3. **Cache attribution is non-deterministic.** Two separate runs at
   4K-tokens-per-call returned `cached_tokens=0` on every single call.
   The same call shape against the same model at 16K tokens hit the cache 4 of
   5 times. The activation curve is not flat. Model your hit rate as a band.

## Auth: use your GitHub Copilot subscription

The harness authenticates against the GitHub Copilot proxy
(`api.githubcopilot.com`) using your local Copilot CLI OAuth token. No
personal access token required. Sign in once with the
[GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/use-copilot-cli),
then the harness reads the token directly from `~/.copilot/data.db`.

The proxy exposes the full model catalog (Anthropic Opus / Sonnet / Haiku,
OpenAI GPT-5.x, Google Gemini 2.5 Pro, Azure OpenAI lineup) on the standard
OpenAI chat-completions shape with `prompt_tokens_details.cached_tokens`
reporting. Roughly 10 lines of auth glue:

```python
import sqlite3, pathlib
from openai import OpenAI

con = sqlite3.connect(
    f"file:{pathlib.Path.home()}/.copilot/data.db?mode=ro", uri=True
)
token = con.execute(
    "SELECT access_token FROM github_accounts WHERE is_default=1"
).fetchone()[0]

client = OpenAI(
    base_url="https://api.githubcopilot.com",
    api_key=token,
    default_headers={"Copilot-Integration-Id": "copilot-cli"},
)
```

> **About the dollar numbers.** Copilot subscriptions are flat-rate, billed
> per premium request. The cost figures in this repo are what these tokens
> would cost on Anthropic's published API list rates (input $15/M, cache read
> $1.50/M, output $75/M for Opus 4.6). Token counts are real; dollars are
> arithmetic. The architectural takeaways are the same either way.

## Setup

```bash
git clone https://github.com/AndreaGriffiths11/librarian-demo
cd librarian-demo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Sign in with the Copilot CLI once if you haven't already.
copilot --version  # confirms CLI is installed

python demo.py all --verbose --diff scenario/sample_pr_large.diff
```

If you do not have the Copilot CLI installed or you want to bypass it, set
`COPILOT_OAUTH_TOKEN` to any OAuth token that the
`Copilot-Integration-Id: copilot-cli` header accepts. The harness checks the
env var before falling back to the local SQLite store.

## CLI

```bash
python demo.py [naive|cache|librarian|all] [options]
```

| Flag | Default | Effect |
|---|---|---|
| `--diff PATH` | `scenario/sample_pr.diff` | Diff file to review |
| `--verbose` / `-v` | off | Print per-call token + cost breakdown |
| `--soft-cache` | off | Warn instead of hard-fail when cache mode reports a cache miss on calls 2+ |

## Scenarios

| File | Size | When to use |
|---|---|---|
| `scenario/sample_pr.diff` | 11K chars (~4K tokens per call) | Quick iteration. Caching does NOT activate at this size on Opus through the proxy. |
| `scenario/sample_pr_large.diff` | 49K chars (~16K tokens per call) | The headline benchmark. Caching activates here. |

Both diffs are hand-crafted to have real, reviewable smells in each file
(SQL injection, sync I/O in request paths, PII in logs, missing tests, etc.)
so the reviewer JSON output is not just hallucinated.

## Guardrails

- `cache` mode fails loud (exit code 2) when calls 2+ report zero cached
  tokens. That state means cache did not activate and the comparison is
  invalid. Use `--soft-cache` to keep going for diagnostic purposes.
- All model names are pinned. No `latest` aliases.
- Token counts and cost are reported directly from the API's usage payload
  (`prompt_tokens`, `prompt_tokens_details.cached_tokens`, `completion_tokens`),
  not estimated.

## Files

```
librarian-demo/
 demo.py              entry point + orchestrator + output
 librarian.py         digest builder
 agents.py            the 5 reviewer agents (system prompts per role)
 pricing.py           model name + token rates + endpoint
 scenario/
 sample_pr.diff           ~4K-token Flask PR with smells   
 sample_pr_large.diff     ~16K-token version with 11 files   
 runs/
 findings.md              full writeup of measured runs   
 2026-05-29-opus-*.txt    raw per-call breakdowns   
 requirements.txt
```

## Companion writeup

The Main Branch article that frames the experiment for a developer audience
is at <https://mainbranch.dev/articles/stop-paying-for-the-same-tokens-twice>
(published 2026-05-29).

## License

MIT
