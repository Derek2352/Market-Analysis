"""Query expansion — generate related search keywords from a topic.

Uses DeepSeek (cheapest available provider) to generate region-appropriate
search queries in the target language. Cached per (topic, region) to avoid
repeated API calls.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache

import structlog

_log = structlog.get_logger(__name__)

# Language hint per region for query generation.
_REGION_LANG_HINT: dict[str, str] = {
    "HK": "Traditional Chinese (Cantonese-influenced) or English",
    "TW": "Traditional Chinese (zh-TW)",
    "US": "English",
    "JP": "Japanese",
    "UK": "English",
    "AU": "English",
    "CN": "Simplified Chinese (zh-CN)",
    "KR": "Korean",
    "DE": "German",
    "FR": "French",
    "ES": "Spanish",
    "IT": "Italian",
    "NL": "Dutch",
}

# Domain-specific query templates per region for zero-cost expansion.
# Used as fallback when DeepSeek is unavailable.
_REGION_TEMPLATES: dict[str, list[str]] = {
    "HK": [
        "{topic} 評價", "{topic} 好唔好用", "{topic} review",
        "{topic} vs", "{topic} 比較", "{topic} 香港",
        "{topic} 香港", "{topic} hk", "{topic} 支付",
    ],
    "TW": [
        "{topic} 評價", "{topic} 推薦", "{topic} 心得",
        "{topic} 比較", "{topic} PTT", "{topic} 開箱",
    ],
    "US": [
        "{topic} review", "{topic} vs", "{topic} comparison",
        "{topic} worth it", "best {topic}", "{topic} alternative",
    ],
    "JP": [
        "{topic} レビュー", "{topic} 口コミ", "{topic} 評価",
        "{topic} おすすめ", "{topic} 比較", "{topic} 価格",
    ],
}


def expand_query(
    topic: str,
    region: str,
    *,
    n: int = 6,
    use_llm: bool = True,
) -> list[str]:
    """Generate *n* related search queries for *topic* in *region*.

    Uses DeepSeek for quality expansions, falling back to template-based
    generation when the API key is missing or the call fails.

    Returns a list starting with the original *topic*.
    """
    if use_llm and os.environ.get("DEEPSEEK_API_KEY"):
        try:
            return _llm_expand(topic, region, n)
        except Exception as e:
            _log.warning("query_expansion.llm_failed", error=str(e))

    return _template_expand(topic, region, n)


@lru_cache(maxsize=128)
def _llm_expand(topic: str, region: str, n: int) -> list[str]:
    """Use DeepSeek to generate region-appropriate queries."""
    import httpx

    lang_hint = _REGION_LANG_HINT.get(region, "the region's primary language")
    region_name = {
        "HK": "Hong Kong", "TW": "Taiwan", "US": "United States",
        "JP": "Japan", "UK": "United Kingdom",
    }.get(region, region)

    prompt = (
        f"Generate {n} related search keywords for finding consumer opinions "
        f"about '{topic}' in {region_name}. "
        f"Output in {lang_hint}. "
        f"Include different phrasings, synonyms, comparisons, and common "
        f"search patterns used by local consumers. "
        f"Return ONLY a JSON array of strings. No explanation."
    )

    api_key = os.environ["DEEPSEEK_API_KEY"]
    resp = httpx.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 200,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    # Extract JSON array from response
    queries = _extract_json_array(content)
    if not queries:
        raise ValueError("No valid JSON array in LLM response")

    # Always include original topic first
    result = [topic]
    seen = {topic.lower()}
    for q in queries[:n]:
        if q.lower() not in seen and q.strip():
            seen.add(q.lower())
            result.append(q.strip())

    _log.info("query_expansion.llm", topic=topic, region=region, count=len(result))
    return list(result)


def _template_expand(topic: str, region: str, n: int) -> list[str]:
    """Template-based query expansion — zero cost, always available.

    Generates region-appropriate modifier queries and, for compound terms
    (camelCase, kebab-case, underscores), splits into component variants.
    """
    templates = _REGION_TEMPLATES.get(region, _REGION_TEMPLATES["US"])
    result = [topic]
    seen = {topic.lower()}

    # Generate compound variants (e.g. AlipayHK → alipay hk, 支付寶 香港)
    compounds = _split_compound(topic)
    for comp in compounds:
        if comp.lower() not in seen:
            seen.add(comp.lower())
            result.append(comp)
            # Also generate modifier queries for each compound variant
            for tmpl in templates[:2]:  # Top 2 templates for compound variants
                q = tmpl.format(topic=comp).strip()
                if q.lower() not in seen:
                    seen.add(q.lower())
                    result.append(q)

    # Standard modifier queries on original topic
    for tmpl in templates:
        q = tmpl.format(topic=topic).strip()
        if q.lower() not in seen:
            seen.add(q.lower())
            result.append(q)
            if len(result) >= n:
                break

    return result[:n]


def _split_compound(topic: str) -> list[str]:
    """Split compound terms into space-separated variants.

    AlipayHK → alipay hk, Alipay HK
    iPhone16 → iphone 16
    """
    variants: list[str] = []

    # camelCase split
    words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)', topic)
    if len(words) > 1:
        variants.append(" ".join(words).lower())
        variants.append(" ".join(words))

    # Digit split: iPhone16 → iPhone 16
    digit_split = re.sub(r'(?<=\D)(?=\d)|(?<=\d)(?=\D)', ' ', topic)
    if digit_split != topic:
        variants.append(digit_split)

    # Underscore/hyphen split
    if '_' in topic or '-' in topic:
        variants.append(topic.replace('_', ' ').replace('-', ' '))

    return variants


def _extract_json_array(text: str) -> list[str]:
    """Extract a JSON array of strings from LLM output."""
    # Try direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x]
    except json.JSONDecodeError:
        pass

    # Try to find JSON array with regex
    match = re.search(r'\[([^\]]+)\]', text, re.DOTALL)
    if match:
        try:
            inner = match.group(0)
            parsed = json.loads(inner)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x]
        except json.JSONDecodeError:
            pass

    # Try line-by-line extraction
    lines = text.strip().split("\n")
    queries = []
    for line in lines:
        line = line.strip().strip('",').strip('"').strip("'")
        if line and not line.startswith("{") and not line.startswith("["):
            queries.append(line)
    return queries
