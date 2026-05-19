"""Render a :class:`Persona` to a 1200×1600 PNG card.

The renderer's only responsibility is composing the Jinja2 context from
the Persona model. It does not call the LLM, does not load other runs,
and does not look at any global state — pass in the Persona, get back
a PNG path. That keeps determinism easy to reason about.
"""
from __future__ import annotations

from pathlib import Path

from src.render.core import (
    accent_palette,
    get_template,
    render_html_to_png,
    truncate,
)
from src.schemas.synthesis import Persona

PERSONA_VIEWPORT = {"width": 1200, "height": 1600}

# Category → (display title, glyph) for the footer coverage strip.
# Order matters: the strip always renders these in this order so the
# footer reads consistently across personas, with missing categories
# greyed out in place. Glyphs are pure Unicode — no icon fonts.
_COVERAGE_ICONS = [
    ("forums",         "Forums",         "\U0001F4AC"),  # 💬
    ("reviews",        "Reviews",        "⭐"),       # ⭐
    ("qa",             "Q&A",            "❓"),       # ❓
    ("blogs",          "Blogs",          "\U0001F4DD"),  # 📝
    ("news_comments",  "News",           "\U0001F4F0"),  # 📰
    ("social",         "Social",         "\U0001F4F1"),  # 📱
    ("video_comments", "Video",          "\U0001F3A5"),  # 🎥
]

_TIER_LABELS = {
    "single-perspective": "single perspective",
    "limited":            "limited",
    "balanced":           "balanced",
    "high":               "high",
}

# Persona card budgets — short, dense layouts read better than overflowing text.
_MAX_GOAL_CHARS = 100
_MAX_MOT_CHARS  = 100
_MAX_PAIN_CHARS = 100
_MAX_QUOTE_CHARS = 280


def _bucket(title: str, claim_list, *, max_chars: int,
            show_severity: bool = False) -> dict:
    # Key is 'rows' rather than 'items' to dodge Jinja2's attribute-vs-method
    # lookup: dict.items() shadows a dict key named 'items'.
    rows = []
    for c in claim_list.claims if claim_list else []:
        text, _ = truncate(c.claim, max_chars)
        entry = {"text": text}
        if show_severity and c.severity:
            entry["severity"] = c.severity
        rows.append(entry)
    return {
        "title": title,
        "unverified": bool(claim_list and claim_list.coverage == "unverified"),
        "rows": rows,
    }


def _chips(persona: Persona) -> list[dict]:
    out = []
    demo = persona.demographics or {}
    age = demo.get("age_range")
    if age:
        out.append({"label": "Age", "value": str(age)})
    occ = demo.get("occupation_examples") or []
    if occ:
        out.append({
            "label": "Occupation",
            "value": " · ".join(str(o) for o in occ[:3]),
        })
    out.append({"label": "Region", "value": persona.cluster_id and (
        # Fallback to extracting region from any RawPost we have — but the
        # cluster doesn't ride along here, so we display the persona's own
        # language instead. The header eyebrow already shows the region tag.
        persona.language.upper()
    ) or persona.language.upper()})
    if persona.cluster_size:
        out.append({"label": "Cluster size",
                    "value": f"{persona.cluster_size} posts"})
    return out


def _quotes(persona: Persona) -> list[dict]:
    out = []
    for q in (persona.representative_quotes or [])[:3]:
        text, _ = truncate(q.text_original, _MAX_QUOTE_CHARS)
        # 12-char doc id is enough for the eye; the full id rides on the URL.
        short_id = (q.doc_id or "").removeprefix("doc_")[:12] or q.doc_id
        out.append({
            "text": text,
            "translated": q.text_translated or "",
            "lang": (q.lang or "??").upper(),
            "source": q.source or "—",
            "short_id": short_id,
        })
    return out


def _coverage_context(persona: Persona) -> dict:
    cov = persona.data_source_coverage or {}
    present = set(cov.get("categories_present", []) or [])
    icons = [
        {
            "title": title + ("" if cat in present else " (no data)"),
            "glyph": glyph,
            "present": cat in present,
        }
        for cat, title, glyph in _COVERAGE_ICONS
    ]
    tier = cov.get("coverage_tier", "")
    label = _TIER_LABELS.get(tier, tier or "unknown")
    bias = cov.get("bias_warning") or (
        f"Coverage: {label} — {len(present)} source "
        f"categor{'y' if len(present) == 1 else 'ies'} represented."
    )
    return {
        "icons": icons,
        "tier_label": label,
        "label": label,
        "bias_text": bias,
    }


def _persona_context(persona: Persona) -> dict:
    region = (persona.demographics or {}).get("region") or persona.language.upper()
    return {
        "persona": {
            "id": persona.id,
            "run_id": persona.run_id,
            "name": persona.name,
            "one_liner": persona.one_liner,
            "language": persona.language,
            "region": region,
            "confidence": float(persona.confidence or 0.0),
        },
        "chips": _chips(persona),
        "buckets": [
            _bucket("Goals",       persona.goals,       max_chars=_MAX_GOAL_CHARS),
            _bucket("Motivations", persona.motivations, max_chars=_MAX_MOT_CHARS),
            _bucket("Pain Points", persona.pain_points, max_chars=_MAX_PAIN_CHARS,
                    show_severity=True),
        ],
        "quotes": _quotes(persona),
        "coverage": _coverage_context(persona),
        "accent": accent_palette(persona.id).as_template_dict(),
    }


def render_persona_card(persona: Persona, out_path: Path | str) -> Path:
    """Render ``persona`` to a PNG at ``out_path`` and return the path."""
    ctx = _persona_context(persona)
    tpl = get_template("persona_card.html")
    html = tpl.render(**ctx)
    return render_html_to_png(html, Path(out_path), viewport=PERSONA_VIEWPORT)
