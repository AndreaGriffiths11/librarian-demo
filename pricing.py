"""
Anthropic Claude Opus 4.6 list pricing as of May 2026.

Wire path: GitHub Copilot proxy (api.githubcopilot.com), authenticated with
the local Copilot CLI OAuth token. Token counts are real and measured.
Dollar amounts are computed at Anthropic's published API list prices (the
"what this would cost you if you were billing Anthropic directly" framing),
NOT what you actually pay on a Copilot subscription, which is flat-rate
premium-request based.

All rates are USD per 1 million tokens.
Source: https://www.anthropic.com/pricing#api
"""

MODEL = "claude-opus-4.6"

INPUT_PER_MTOK = 15.00
CACHED_INPUT_PER_MTOK = 1.50   # 90% off the input rate (Anthropic cache read)
OUTPUT_PER_MTOK = 75.00

# Anthropic prompt caching activates at >= 1024 tokens of identical prefix
# (same threshold as OpenAI auto-caching).
CACHE_MIN_TOKENS = 1024

COPILOT_PROXY_ENDPOINT = "https://api.githubcopilot.com"

# Back-compat name (some code still imports GITHUB_MODELS_ENDPOINT).
GITHUB_MODELS_ENDPOINT = COPILOT_PROXY_ENDPOINT


def cost_for(usage) -> float:
    """Compute USD cost from a chat-completions usage object."""
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    details = getattr(usage, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    uncached = max(0, prompt - cached)
    return (
        uncached * INPUT_PER_MTOK / 1_000_000
        + cached * CACHED_INPUT_PER_MTOK / 1_000_000
        + completion * OUTPUT_PER_MTOK / 1_000_000
    )


def cached_tokens(usage) -> int:
    details = getattr(usage, "prompt_tokens_details", None)
    return (getattr(details, "cached_tokens", 0) or 0) if details else 0
