"""Render a :class:`JourneyMap` paired with its :class:`Persona` to a
2400×1400 PNG infographic.

The emotion curve is computed in Python rather than left to CSS or JS:
we pass the SVG path strings as template values so the template can stay
declarative and the geometry is testable.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from src.render.core import (
    get_template,
    render_html_to_png,
    truncate,
)
from src.schemas.synthesis import JourneyMap, JourneyStage, Persona

JOURNEY_VIEWPORT = {"width": 2400, "height": 1400}

# Canonical six-stage order. Per the spec the journey map renders these
# columns even if the model emitted them in a different order or missed
# one (missing stage renders as "no data").
_STAGES = (
    "Awareness",
    "Consideration",
    "Decision",
    "Onboarding",
    "Use",
    "Loyalty/Churn",
)

# Geometry of the SVG: row_label is 200px wide in the grid, so the SVG
# viewBox spans 2400 - 200 = 2200 units. Six equal stage columns.
_SVG_WIDTH = 2200
_COL_WIDTH = _SVG_WIDTH / len(_STAGES)
_STAGE_CENTERS = [
    round(_COL_WIDTH * (i + 0.5), 2) for i in range(len(_STAGES))
]
_SEPARATORS = [round(_COL_WIDTH * i, 2) for i in range(1, len(_STAGES))]

# Y-axis: viewBox height 280. Plot area between y=40 (high) and y=240 (low).
_Y_HIGH = 40
_Y_LOW = 240
_PLOT_H = _Y_LOW - _Y_HIGH

# Emotion mood mapping. The synthesizer emits an emotion label per stage
# plus an intensity 0..1; for the curve we want a *positivity* axis where
# higher = better. Negative emotions get inverted: high intensity of
# frustration plots LOW. Anything not in the lookup is treated as neutral.
_NEGATIVE_EMOTIONS = {
    "frustrated", "angry", "anxious", "annoyed", "confused", "stressed",
    "disappointed", "resigned", "regretful", "skeptical",
}
_POSITIVE_EMOTIONS = {
    "happy", "hopeful", "satisfied", "delighted", "excited",
    "curious", "loyal", "trusting",
}

# Marker color: red for negative, amber for mid, blue for positive/neutral.
_NEG_COLOR = "#c2474a"
_MID_COLOR = "#cdb255"
_POS_COLOR = "#5a8a99"

# Common emoji per emotion label — purely cosmetic. Any unrecognised label
# falls through to a neutral marker without an emoji prefix.
_EMOJIS = {
    "frustrated": "😤",
    "angry": "😠",
    "anxious": "😟",
    "annoyed": "😤",
    "confused": "😕",
    "stressed": "😫",
    "disappointed": "😞",
    "resigned": "😞",
    "skeptical": "🤨",
    "happy": "😄",
    "hopeful": "🙂",
    "satisfied": "😌",
    "delighted": "😄",
    "excited": "🤩",
    "curious": "😯",
    "loyal": "💙",
    "trusting": "🙂",
    "neutral": "😐",
    "uncertain": "😐",
    "cautious": "🤔",
}

# Cell budgets — keep dense.
_MAX_CELL_CHARS = 80
_MAX_ITEMS_PER_CELL = 3


# ---------------------------------------------------------------------------
# Footnote ledger — assigns stable numeric ids to URLs
# ---------------------------------------------------------------------------


@dataclass
class _CitationLedger:
    """Assign deterministic [n] numbers to citation strings."""

    by_key: "OrderedDict[str, int]"

    @classmethod
    def empty(cls) -> "_CitationLedger":
        return cls(by_key=OrderedDict())

    def assign(self, key: str) -> int:
        if not key:
            return 0
        if key not in self.by_key:
            self.by_key[key] = len(self.by_key) + 1
        return self.by_key[key]

    def as_footnotes(self) -> list[dict]:
        return [
            {"num": num, "text": key}
            for key, num in self.by_key.items()
        ]


# ---------------------------------------------------------------------------
# Per-row context builder
# ---------------------------------------------------------------------------


def _cell_items(
    claim_list,
    ledger: _CitationLedger,
    doc_to_label: dict[str, str],
) -> list[dict]:
    items: list[dict] = []
    for c in (claim_list.claims if claim_list else [])[:_MAX_ITEMS_PER_CELL]:
        text, _ = truncate(c.claim, _MAX_CELL_CHARS)
        # First doc_id with a known URL becomes the footnote anchor.
        cite_num = 0
        for doc_id in (c.evidence or []):
            url = doc_to_label.get(doc_id)
            if url:
                cite_num = ledger.assign(url)
                break
        items.append({"text": text, "cite": cite_num or None})
    return items


def _stage_emotions_intensity(stage: JourneyStage) -> tuple[float, str, str]:
    """Pick the dominant emotion for the stage and return (intensity, label, color).

    "Dominant" = highest intensity. If there are no emotion points we
    fall back to a neutral midpoint so the curve still passes through
    the stage instead of disappearing.
    """
    if not stage.emotions:
        return 0.5, "no data", _MID_COLOR
    dominant = max(stage.emotions, key=lambda e: e.intensity or 0.0)
    label = (dominant.label or "neutral").lower()
    intensity = max(0.0, min(1.0, float(dominant.intensity or 0.0)))
    # Invert negatives so the y axis stays "higher = better".
    if label in _NEGATIVE_EMOTIONS:
        plotted = 1.0 - intensity
        color = _NEG_COLOR
    elif label in _POSITIVE_EMOTIONS:
        plotted = intensity
        color = _POS_COLOR if intensity < 0.66 else _POS_COLOR
    else:
        plotted = max(0.3, min(0.7, intensity))  # squeeze toward middle
        color = _MID_COLOR
    return plotted, label, color


def _curve_paths(points: Sequence[dict]) -> tuple[str, str]:
    """Build SVG path strings for the curve line and the filled area.

    Uses a Catmull-Rom-to-Bézier conversion so the curve passes through
    every stage marker yet stays smooth between them. With 6 control
    points the math is short and inlined.
    """
    if not points:
        return "", ""
    n = len(points)
    pts = [(p["x"], p["y"]) for p in points]
    parts = [f"M {pts[0][0]:.2f} {pts[0][1]:.2f}"]
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
        # Standard Catmull-Rom -> cubic Bézier control points.
        c1x = p1[0] + (p2[0] - p0[0]) / 6
        c1y = p1[1] + (p2[1] - p0[1]) / 6
        c2x = p2[0] - (p3[0] - p1[0]) / 6
        c2y = p2[1] - (p3[1] - p1[1]) / 6
        parts.append(
            f"C {c1x:.2f} {c1y:.2f}, {c2x:.2f} {c2y:.2f}, "
            f"{p2[0]:.2f} {p2[1]:.2f}"
        )
    line_path = " ".join(parts)
    # Filled area: close down to the baseline at y=280.
    area_path = (
        line_path
        + f" L {pts[-1][0]:.2f} 280 L {pts[0][0]:.2f} 280 Z"
    )
    return line_path, area_path


def _emo_context(stage_lookup: dict[str, JourneyStage | None]) -> dict:
    points = []
    for i, name in enumerate(_STAGES):
        stage = stage_lookup.get(name)
        if stage is None:
            intensity, label, color = 0.5, "no data", _MID_COLOR
        else:
            intensity, label, color = _stage_emotions_intensity(stage)
        y = _Y_HIGH + (1.0 - intensity) * _PLOT_H
        x = _STAGE_CENTERS[i]
        emoji = _EMOJIS.get(label, "")
        # Label sits above the marker for high y (low intensity = low on chart =
        # high y value), below for low y. Keeps text away from the curve.
        label_text = f"{emoji} {label}".strip()
        label_y = y - 16 if y < 200 else y + 22
        points.append({
            "x": x,
            "y": round(y, 2),
            "label_y": round(label_y, 2),
            "label": label_text,
            "intensity": intensity,
            "color": color,
        })
    line_path, area_path = _curve_paths(points)
    return {
        "svg_width": _SVG_WIDTH,
        "separators": _SEPARATORS,
        "points": points,
        "line_path": line_path,
        "area_path": area_path,
    }


def _stage_coverage(stage: JourneyStage | None) -> str:
    if stage is None:
        return "none"
    return stage.coverage or "ok"


def _stage_lookup(journey: JourneyMap) -> dict[str, JourneyStage | None]:
    by_name: dict[str, JourneyStage | None] = {name: None for name in _STAGES}
    for s in journey.stages:
        # Normalise the stage name to match the canonical set.
        key = s.stage
        if key in by_name:
            by_name[key] = s
    return by_name


def _coverage_summary(journey: JourneyMap, persona: Persona) -> dict:
    cov = journey.data_source_coverage or persona.data_source_coverage or {}
    tier = cov.get("coverage_tier", "")
    from src.render.persona_card import _TIER_LABELS  # local import: same map
    label = _TIER_LABELS.get(tier, tier or "unknown")
    return {"tier_label": label, "label": label}


def _doc_to_label(persona: Persona) -> dict[str, str]:
    """Build doc_id → display URL from the persona's quotes.

    The persona quotes are the only place in the synthesis schema where
    a doc_id is paired with a human-readable URL, so the journey map
    cross-references through them. Stages whose evidence doesn't appear
    in any quote render without a citation footnote (rather than a
    broken [n]).
    """
    out: dict[str, str] = {}
    for q in persona.representative_quotes or []:
        if q.doc_id and q.url:
            out.setdefault(q.doc_id, q.url)
    return out


def _journey_context(journey: JourneyMap, persona: Persona, *, topic: str) -> dict:
    by_name = _stage_lookup(journey)
    ledger = _CitationLedger.empty()
    doc_to_label = _doc_to_label(persona)

    stages_ctx = []
    for name in _STAGES:
        st = by_name[name]
        stages_ctx.append({
            "label": name,
            "coverage": _stage_coverage(st),
            "touchpoints":   _cell_items(st.touchpoints if st else None,   ledger, doc_to_label),
            "user_actions":  _cell_items(st.user_actions if st else None,  ledger, doc_to_label),
            "frictions":     _cell_items(st.frictions if st else None,     ledger, doc_to_label),
            "opportunities": _cell_items(st.opportunities if st else None, ledger, doc_to_label),
        })

    region = persona.demographics.get("region") if persona.demographics else None
    return {
        "persona": {
            "id": persona.id,
            "run_id": persona.run_id,
            "name": persona.name,
            "region": region or persona.language.upper(),
        },
        "journey": {"id": journey.id},
        "topic": topic,
        "subtitle": (
            f"{len(persona.representative_quotes or [])} representative quotes · "
            f"{persona.cluster_size} posts in cluster"
        ),
        "stages": stages_ctx,
        "emo": _emo_context(by_name),
        "coverage": _coverage_summary(journey, persona),
        "footnotes": ledger.as_footnotes(),
    }


def render_journey_map(
    journey: JourneyMap,
    persona: Persona,
    out_path: Path | str,
    *,
    topic: str = "",
) -> Path:
    """Render a journey map paired with its persona to ``out_path``."""
    ctx = _journey_context(journey, persona, topic=topic or persona.name)
    tpl = get_template("journey_map.html")
    html = tpl.render(**ctx)
    return render_html_to_png(html, Path(out_path), viewport=JOURNEY_VIEWPORT)
