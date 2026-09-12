"""
app/ai/lexical.py

Deterministic lexical-similarity fallback for Stage 2 when no embedding
provider is configured (no OPENAI_API_KEY, no sentence-transformers).

This REPLACES the old behavior where every post got one of exactly four
scores {0.3, 0.5, 0.7, 0.9} — which is why the UI showed "70" for
everything.

How it works:
  1. Tokenize post text + project profile text (lowercase, stopwords removed)
  2. Compute a Jaccard-style overlap weighted by IDF-ish rarity
  3. Add phrase-position credit: how much of the profile appears in the post
  4. Normalize to 0..1

Scores are continuous and differ per post — cheap, deterministic, and a
reasonable floor under the embedding providers.
"""
import math
import re
from collections import Counter

# Small English stopword list (kept inline — no external deps)
_STOPWORDS = frozenset("""
a an the and or but if while with without to from by of in on for at as is are was
were be been being this that these those it its i you he she they we me my your his
her their our them us what which who whom how when where why not no nor so than too
very can will just should now do does did doing done have has had having about into
over under again further then once here there all any both each few more most other
some such only own same s t don ll re ve d m o
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [
        tok for tok in _TOKEN_RE.findall(text.lower())
        if tok not in _STOPWORDS
    ]


def lexical_similarity(post_text: str, profile_text: str) -> float:
    """Weighted token overlap between a post and a project profile.

    Returns 0..1. Continuous — two different posts against the same
    profile produce different scores.
    """
    if not post_text or not profile_text:
        return 0.0

    post_tokens = tokenize(post_text)
    profile_tokens = tokenize(profile_text)
    if not post_tokens or not profile_tokens:
        return 0.0

    profile_counts = Counter(profile_tokens)
    post_counts = Counter(post_tokens)

    # Weight each profile token by inverse frequency in the post's own text —
    # rarer (more specific) matches matter more.
    total_post = len(post_tokens)
    score = 0.0
    matched = 0
    for tok, cnt in profile_counts.items():
        hits = post_counts.get(tok, 0)
        if hits <= 0:
            continue
        matched += 1
        idf_weight = 1.0 + math.log(1.0 + (total_post / (hits + 1)))
        # Saturating credit per token: first hit counts most
        score += (1.0 - math.exp(-0.7 * hits)) * idf_weight

    # Normalize: perfect coverage of every profile token ≈ 1.0
    denom = sum(1.0 + math.log(1.0 + (total_post / 2.0)) for _ in profile_counts)
    if denom <= 0:
        return 0.0

    coverage = score / denom

    # Bonus: bigram overlap (catches multi-word concepts like "notion alternative")
    post_bigrams = set(zip(post_tokens, post_tokens[1:]))
    profile_bigrams = set(zip(profile_tokens, profile_tokens[1:]))
    bigram_overlap = len(post_bigrams & profile_bigrams)
    bigram_bonus = min(0.25, 0.08 * bigram_overlap)

    # Bonus for matched keyword density relative to post length (long rambling
    # posts that mention the topic once are weaker signals)
    density = matched / max(len(post_tokens), 1)
    density_bonus = min(0.15, density * 0.6)

    return max(0.0, min(1.0, coverage * 0.75 + bigram_bonus + density_bonus))
