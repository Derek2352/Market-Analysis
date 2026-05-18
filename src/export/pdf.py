"""PDF report generation for persona + journey map exports.

Produces a stakeholder-ready report with title page, persona summary,
pain point tables, journey visualizations, representative quotes with
doc_id citations, and quantitative grounding sections.

Uses fpdf2 for zero-dependency PDF generation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.enums import TableCellFillMode

from src.schemas.synthesis import (
    EmotionPoint,
    EvidenceClaim,
    JourneyMap,
    JourneyStage,
    Persona,
    RepresentativeQuote,
)

# ── Constants ──────────────────────────────────────────────────────────────

_PAGE_W = 210  # A4 width in mm
_PAGE_H = 297  # A4 height in mm
_MARGIN = 15
_BODY_W = _PAGE_W - 2 * _MARGIN

# Professional colour palette
_COLOR_DARK = (33, 37, 41)       # near-black for body text
_COLOR_HEADER = (25, 55, 109)    # dark navy for headers
_COLOR_ACCENT = (0, 123, 255)    # blue accent
_COLOR_LIGHT_BG = (245, 247, 250)  # light grey background
_COLOR_BORDER = (200, 210, 220)  # light border
_COLOR_SEVERITY_HIGH = (220, 53, 69)
_COLOR_SEVERITY_MEDIUM = (255, 193, 7)
_COLOR_SEVERITY_LOW = (40, 167, 69)
_COLOR_EMOTION_POSITIVE = (40, 167, 69)
_COLOR_EMOTION_NEGATIVE = (220, 53, 69)
_COLOR_EMOTION_NEUTRAL = (108, 117, 125)
_COLOR_QUOTE_BG = (240, 245, 255)
_COLOR_MUTED = (108, 117, 125)
_COLOR_WHITE = (255, 255, 255)

_EMOTION_COLORS: dict[str, tuple[int, int, int]] = {
    "satisfied": _COLOR_EMOTION_POSITIVE,
    "happy": _COLOR_EMOTION_POSITIVE,
    "excited": (0, 180, 100),
    "relieved": _COLOR_EMOTION_POSITIVE,
    "hopeful": _COLOR_EMOTION_POSITIVE,
    "curious": (0, 150, 200),
    "neutral": _COLOR_EMOTION_NEUTRAL,
    "confused": (255, 140, 0),
    "anxious": (255, 80, 80),
    "frustrated": _COLOR_EMOTION_NEGATIVE,
    "disappointed": _COLOR_EMOTION_NEGATIVE,
    "angry": (180, 20, 20),
}


# ── Main entry point ───────────────────────────────────────────────────────


def export_persona_report(
    persona: Persona,
    journey: JourneyMap | None,
    output_path: str | Path,
    topic: str = "",
    region: str = "",
) -> None:
    """Generate a professional PDF report for a persona + journey map.

    Args:
        persona: The synthesized Persona to export.
        journey: Optional JourneyMap for the same persona.
        output_path: File path for the output PDF.
        topic: Topic name for the report header.
        region: Region code for the report header.
    """
    pdf = _ReportPDF(topic=topic, region=region)
    pdf.add_page()

    # ── Title / cover section ──────────────────────────────────────────
    _draw_title_section(pdf, persona, topic, region)

    # ── Persona overview ───────────────────────────────────────────────
    _draw_persona_overview(pdf, persona)

    # ── Pain points table ──────────────────────────────────────────────
    _draw_pain_points_table(pdf, persona)

    # ── Representative quotes ──────────────────────────────────────────
    _draw_representative_quotes(pdf, persona)

    # ── Journey map ────────────────────────────────────────────────────
    if journey and journey.stages:
        _draw_journey_map(pdf, journey)

    # ── Quantitative grounding ─────────────────────────────────────────
    _draw_quantitative_grounding(pdf, persona, journey)

    pdf.output(str(output_path))


# ── PDF subclass ───────────────────────────────────────────────────────────


class _ReportPDF(FPDF):
    """Custom FPDF with headers, footers, and helper methods."""

    def __init__(self, topic: str = "", region: str = ""):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=_MARGIN + 5)
        self.topic = topic
        self.region = region
        # Use Noto Sans HK for Unicode support (CJK + Latin)
        font_path = Path("C:/Windows/Fonts/NotoSansHK-VF.ttf")
        if not font_path.exists():
            font_path = Path("C:/Windows/Fonts/msyh.ttc")
        if not font_path.exists():
            font_path = Path("C:/Windows/Fonts/simsun.ttc")
        if not font_path.exists():
            # Fallback: download DejaVu
            font_dir = Path(__file__).parent / "fonts"
            font_dir.mkdir(parents=True, exist_ok=True)
            _ensure_dejavu_font(font_dir)
            font_path = font_dir / "DejaVuSans.ttf"
        self.add_font("NotoSans", "", str(font_path), uni=True)
        self.add_font("NotoSans", "B", str(font_path), uni=True)
        self.add_font("NotoSans", "I", str(font_path), uni=True)
        self.add_font("NotoSans", "BI", str(font_path), uni=True)
        self.set_font("NotoSans", "", 10)

    def header(self) -> None:
        if self.page_no() == 1:
            return  # title page has its own header
        self.set_font("NotoSans", "I", 7)
        self.set_text_color(*_COLOR_MUTED)
        parts = [p for p in [self.topic, self.region] if p]
        header_text = "  |  ".join(parts) if parts else "Market Analysis Report"
        self.cell(0, 4, header_text, align="L")
        self.ln(3)
        # thin separator line
        self.set_draw_color(*_COLOR_BORDER)
        self.line(_MARGIN, self.get_y(), _PAGE_W - _MARGIN, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-_MARGIN)
        self.set_font("NotoSans", "I", 7)
        self.set_text_color(*_COLOR_MUTED)
        self.cell(0, 4, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_header(self, title: str) -> None:
        """Draw a styled section header."""
        self.ln(4)
        self.set_fill_color(*_COLOR_HEADER)
        self.set_text_color(*_COLOR_WHITE)
        self.set_font("NotoSans", "B", 12)
        self.cell(_BODY_W, 8, f"  {title}", fill=True, ln=True)
        self.set_text_color(*_COLOR_DARK)
        self.ln(3)

    def sub_header(self, title: str) -> None:
        """Draw a sub-section header."""
        self.set_font("NotoSans", "B", 10)
        self.set_text_color(*_COLOR_HEADER)
        self.cell(_BODY_W, 6, title, ln=True)
        self.set_text_color(*_COLOR_DARK)
        self.ln(1)

    def body_text(self, text: str, size: int = 9) -> None:
        """Render body text with word wrapping."""
        self.set_font("NotoSans", "", size)
        self.set_text_color(*_COLOR_DARK)
        self.multi_cell(_BODY_W, 5, text)

    def metric_pair(self, label1: str, value1: str, label2: str, value2: str) -> None:
        """Draw two metrics side by side in a 2-column layout."""
        col_w = _BODY_W / 2 - 2
        y_start = self.get_y()

        # Column 1
        self.set_xy(_MARGIN, y_start)
        self.set_font("NotoSans", "", 7)
        self.set_text_color(*_COLOR_MUTED)
        self.cell(col_w, 4, label1.upper())
        self.set_xy(_MARGIN, self.get_y() + 4)
        self.set_font("NotoSans", "B", 11)
        self.set_text_color(*_COLOR_DARK)
        self.cell(col_w, 6, str(value1))

        # Column 2
        col2_x = _MARGIN + col_w + 4
        self.set_xy(col2_x, y_start)
        self.set_font("NotoSans", "", 7)
        self.set_text_color(*_COLOR_MUTED)
        self.cell(col_w, 4, label2.upper())
        self.set_xy(col2_x, self.get_y() + 4)
        self.set_font("NotoSans", "B", 11)
        self.set_text_color(*_COLOR_DARK)
        self.cell(col_w, 6, str(value2))

        self.set_y(y_start + 12)

    def separator(self) -> None:
        """Draw a thin horizontal separator."""
        self.ln(2)
        self.set_draw_color(*_COLOR_BORDER)
        self.line(_MARGIN, self.get_y(), _PAGE_W - _MARGIN, self.get_y())
        self.ln(3)

    def check_page_break(self, needed_mm: float = 30) -> None:
        """Add a page break if there isn't enough room."""
        if self.get_y() > _PAGE_H - _MARGIN - needed_mm:
            self.add_page()


# ── Section drawing functions ──────────────────────────────────────────────


def _draw_title_section(
    pdf: _ReportPDF, persona: Persona, topic: str, region: str
) -> None:
    """Draw the report title / cover section."""
    # Coloured top bar
    pdf.set_fill_color(*_COLOR_HEADER)
    pdf.rect(0, 0, _PAGE_W, 50, "F")

    # Title text
    pdf.set_y(25)
    pdf.set_font("NotoSans", "B", 22)
    pdf.set_text_color(*_COLOR_WHITE)
    pdf.cell(0, 10, "Persona Analysis Report", align="C", ln=True)

    pdf.ln(4)
    pdf.set_font("NotoSans", "", 10)
    pdf.set_text_color(200, 215, 235)
    context = f"{topic}  |  {region}" if topic else ""
    if persona.generated_at:
        date_str = persona.generated_at.strftime("%B %d, %Y")
        context = f"{context}  |  {date_str}" if context else date_str
    pdf.cell(0, 5, context, align="C", ln=True)

    # Persona name block
    pdf.ln(16)
    pdf.set_font("NotoSans", "B", 18)
    pdf.set_text_color(*_COLOR_HEADER)
    pdf.cell(0, 10, persona.name, align="C", ln=True)

    pdf.ln(3)
    pdf.set_font("NotoSans", "I", 11)
    pdf.set_text_color(*_COLOR_MUTED)
    pdf.multi_cell(_BODY_W, 6, persona.one_liner, align="C")

    pdf.ln(2)
    pdf.separator()

    # Metadata row (2-column)
    lang = persona.language or "en"
    cluster_size = str(persona.cluster_size)
    confidence = f"{persona.confidence:.0%}" if persona.confidence else "N/A"
    model_info = f"{persona.provider}/{persona.model}" if persona.provider else "N/A"

    pdf.metric_pair("Language", lang.upper(), "Cluster Size", f"{cluster_size} posts")
    pdf.metric_pair("Confidence", confidence, "Model", model_info)

    pdf.separator()


def _draw_persona_overview(pdf: _ReportPDF, persona: Persona) -> None:
    """Draw persona demographics, goals, motivations, and behaviors."""
    pdf.section_header("Persona Profile")

    # Demographics
    demo = persona.demographics
    if demo:
        pdf.sub_header("Demographics")
        lines: list[str] = []
        if demo.get("age_range"):
            lines.append(f"Age Range: {demo['age_range']}")
        if demo.get("occupation_examples"):
            lines.append(
                f"Occupations: {', '.join(demo['occupation_examples'])}"
            )
        if lines:
            pdf.body_text("  |  ".join(lines))
            pdf.ln(2)

    # Goals
    _draw_claim_list(pdf, "Goals", persona.goals.claims, persona.goals.coverage)

    # Motivations
    _draw_claim_list(pdf, "Motivations", persona.motivations.claims, persona.motivations.coverage)

    # Behaviors
    _draw_claim_list(pdf, "Behaviors", persona.behaviors.claims, persona.behaviors.coverage)

    # Preferred channels
    _draw_claim_list(
        pdf, "Preferred Channels", persona.preferred_channels.claims,
        persona.preferred_channels.coverage,
    )


def _draw_claim_list(
    pdf: _ReportPDF,
    title: str,
    claims: list[EvidenceClaim],
    coverage: str,
) -> None:
    """Draw a titled list of claims with evidence citations."""
    if not claims:
        return

    tag = f" [coverage: {coverage}]" if coverage != "ok" else ""
    pdf.sub_header(f"{title}{tag}")

    for i, c in enumerate(claims, 1):
        pdf.check_page_break(15)
        evidence_str = ", ".join(c.evidence[:3])
        if len(c.evidence) > 3:
            evidence_str += f" +{len(c.evidence) - 3} more"

        pdf.set_font("NotoSans", "", 9)
        pdf.set_text_color(*_COLOR_DARK)
        pdf.cell(5, 5, f"{i}.", align="R")
        pdf.multi_cell(_BODY_W - 5, 5, c.claim)

        # Evidence line
        pdf.set_x(_MARGIN + 5)
        pdf.set_font("NotoSans", "I", 7)
        pdf.set_text_color(*_COLOR_MUTED)
        pdf.cell(_BODY_W - 5, 4, f"Sources: {evidence_str}", ln=True)

        # Severity badge for pain points
        if c.severity:
            pdf.set_x(_MARGIN + 5)
            _draw_severity_badge(pdf, c.severity)

        pdf.ln(1)
    pdf.ln(2)


def _draw_severity_badge(pdf: _ReportPDF, severity: str) -> None:
    """Draw a small coloured severity indicator."""
    color_map = {
        "high": _COLOR_SEVERITY_HIGH,
        "medium": _COLOR_SEVERITY_MEDIUM,
        "low": _COLOR_SEVERITY_LOW,
    }
    color = color_map.get(severity.lower(), _COLOR_MUTED)
    pdf.set_font("NotoSans", "B", 6)
    pdf.set_text_color(*color)
    pdf.cell(_BODY_W - 5, 4, f"Severity: {severity.upper()}", ln=True)
    pdf.set_text_color(*_COLOR_DARK)


def _draw_pain_points_table(pdf: _ReportPDF, persona: Persona) -> None:
    """Draw a formatted pain points table with quantitative columns."""
    claims = persona.pain_points.claims
    if not claims:
        return

    pdf.check_page_break(50)
    pdf.section_header("Pain Points Analysis")
    coverage = persona.pain_points.coverage
    if coverage != "ok":
        pdf.set_font("NotoSans", "I", 8)
        pdf.set_text_color(*_COLOR_MUTED)
        pdf.cell(_BODY_W, 5, f"Coverage: {coverage} — some claims may be incomplete", ln=True)
        pdf.set_text_color(*_COLOR_DARK)
        pdf.ln(2)

    # Table column widths
    col_w = [_BODY_W * 0.38, _BODY_W * 0.10, _BODY_W * 0.10, _BODY_W * 0.08, _BODY_W * 0.34]
    headers = ["Pain Point", "Users", "%", "Severity", "Sentiment"]

    # Header row
    pdf.set_fill_color(*_COLOR_HEADER)
    pdf.set_text_color(*_COLOR_WHITE)
    pdf.set_font("NotoSans", "B", 7)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, f" {h}", border=0, fill=True)
    pdf.ln()
    pdf.set_text_color(*_COLOR_DARK)

    # Data rows
    for idx, c in enumerate(claims):
        pdf.check_page_break(15)

        # Alternate row background
        if idx % 2 == 0:
            pdf.set_fill_color(*_COLOR_LIGHT_BG)
        else:
            pdf.set_fill_color(*_COLOR_WHITE)
        fill = True

        row_h = 7
        pdf.set_font("NotoSans", "", 7)

        # Truncate claim text to fit
        claim_text = c.claim[:80] + "..." if len(c.claim) > 80 else c.claim
        pdf.cell(col_w[0], row_h, f" {claim_text}", border=0, fill=fill)
        pdf.cell(col_w[1], row_h, str(c.mentioned_by_n_users), border=0, fill=fill, align="C")
        pdf.cell(col_w[2], row_h, f"{c.pct_of_cluster:.0f}%", border=0, fill=fill, align="C")

        # Severity with colour
        sev = (c.severity or "—").upper()
        sev_color = {
            "HIGH": _COLOR_SEVERITY_HIGH,
            "MEDIUM": _COLOR_SEVERITY_MEDIUM,
            "LOW": _COLOR_SEVERITY_LOW,
        }.get(sev, _COLOR_MUTED)
        pdf.set_text_color(*sev_color)
        pdf.set_font("NotoSans", "B", 7)
        pdf.cell(col_w[3], row_h, sev, border=0, fill=fill, align="C")
        pdf.set_text_color(*_COLOR_DARK)
        pdf.set_font("NotoSans", "", 7)

        # Sentiment counts
        sent_str = _format_sentiment(c.sentiment_scores)
        pdf.cell(col_w[4], row_h, f" {sent_str}", border=0, fill=fill)
        pdf.ln()

    pdf.ln(3)


def _format_sentiment(scores: dict[str, int]) -> str:
    """Format sentiment dict into a compact string like 'neg:8 neu:2 pos:1'."""
    if not scores:
        return "—"
    parts = []
    for k in ("negative", "neutral", "positive"):
        v = scores.get(k, 0)
        if v:
            parts.append(f"{k[:3]}:{v}")
    return " ".join(parts) if parts else "—"


def _draw_representative_quotes(pdf: _ReportPDF, persona: Persona) -> None:
    """Draw representative quotes styled as callout blocks with doc_id references."""
    quotes = persona.representative_quotes
    if not quotes:
        return

    pdf.check_page_break(40)
    pdf.section_header("Representative Quotes")

    for i, q in enumerate(quotes, 1):
        pdf.check_page_break(25)

        # Quote background block
        y_start = pdf.get_y()
        pdf.set_fill_color(*_COLOR_QUOTE_BG)
        pdf.set_draw_color(*_COLOR_ACCENT)

        # Calculate height needed for the quote text
        pdf.set_font("NotoSans", "I", 9)
        # Estimate: each line ~5mm, _BODY_W - 8 for padding
        text_w = _BODY_W - 8
        lines = pdf.multi_cell(
            text_w, 5, f'"{q.text_original}"',
            dry_run=True, output="LINES",
        )
        quote_h = max(len(lines) * 5 + 12, 18)

        # Draw rounded-ish rect (using rect + smaller emphasis bar at left)
        pdf.set_fill_color(*_COLOR_QUOTE_BG)
        pdf.rect(_MARGIN, y_start, _BODY_W, quote_h, "F")
        # Left accent bar
        pdf.set_fill_color(*_COLOR_ACCENT)
        pdf.rect(_MARGIN, y_start, 3, quote_h, "F")
        # Bottom border
        pdf.set_draw_color(*_COLOR_BORDER)
        pdf.line(_MARGIN, y_start + quote_h, _MARGIN + _BODY_W, y_start + quote_h)

        # Quote text
        pdf.set_xy(_MARGIN + 6, y_start + 2)
        pdf.set_font("NotoSans", "I", 9)
        pdf.set_text_color(*_COLOR_DARK)
        pdf.multi_cell(text_w - 3, 5, f'"{q.text_original}"')

        # Citation line
        pdf.set_x(_MARGIN + 6)
        pdf.set_font("NotoSans", "", 6)
        pdf.set_text_color(*_COLOR_MUTED)

        citation_parts = [f"doc_id: {q.doc_id}"]
        if q.source:
            citation_parts.append(f"source: {q.source}")
        if q.lang and q.lang != "en":
            citation_parts.append(f"lang: {q.lang}")
        if q.url:
            url_short = q.url[:60] + "..." if len(q.url) > 60 else q.url
            citation_parts.append(url_short)

        pdf.cell(text_w - 3, 4, "  |  ".join(citation_parts), ln=True)

        pdf.set_y(y_start + quote_h + 3)

    pdf.ln(2)


def _draw_journey_map(pdf: _ReportPDF, journey: JourneyMap) -> None:
    """Draw the journey map with stages, emotions, and friction points."""
    pdf.check_page_break(50)
    pdf.section_header("Journey Map")

    if journey.data_source_coverage:
        cov = journey.data_source_coverage
        tier = cov.get("coverage_tier", "unknown")
        pdf.set_font("NotoSans", "I", 7)
        pdf.set_text_color(*_COLOR_MUTED)
        srcs = ", ".join(cov.get("sources_used", [])[:4])
        pdf.cell(
            _BODY_W, 4,
            f"Data coverage tier: {tier}  |  Sources: {srcs}",
            ln=True,
        )
        pdf.set_text_color(*_COLOR_DARK)
        pdf.ln(3)

    for stage in journey.stages:
        _draw_journey_stage(pdf, stage)


def _draw_journey_stage(pdf: _ReportPDF, stage: JourneyStage) -> None:
    """Draw one journey stage block."""
    pdf.check_page_break(40)
    pdf.sub_header(f"Stage: {stage.stage}")

    # Coverage flag
    if stage.coverage in ("thin", "none"):
        pdf.set_font("NotoSans", "I", 7)
        pdf.set_text_color(*_COLOR_MUTED)
        pdf.cell(
            _BODY_W, 4,
            f"Evidence coverage: {stage.coverage} — limited grounding",
            ln=True,
        )
        pdf.set_text_color(*_COLOR_DARK)

    # Emotions bar
    if stage.emotions:
        pdf.set_font("NotoSans", "", 7)
        pdf.set_text_color(*_COLOR_MUTED)
        pdf.cell(_BODY_W, 4, "EMOTIONS", ln=True)
        pdf.set_text_color(*_COLOR_DARK)

        for ep in stage.emotions:
            _draw_emotion_bar(pdf, ep)
        pdf.ln(2)

    # Frictions
    if stage.frictions.claims:
        pdf.set_font("NotoSans", "", 7)
        pdf.set_text_color(*_COLOR_MUTED)
        pdf.cell(_BODY_W, 4, "FRICTIONS", ln=True)
        pdf.set_text_color(*_COLOR_DARK)
        for fc in stage.frictions.claims:
            pdf.check_page_break(10)
            pdf.set_font("NotoSans", "", 8)
            pdf.set_text_color(*_COLOR_SEVERITY_HIGH)
            pdf.cell(4, 4, "⚠")
            pdf.set_text_color(*_COLOR_DARK)
            ev_str = ", ".join(fc.evidence[:2])
            pdf.cell(_BODY_W - 4, 4, f"{fc.claim}  [{ev_str}]", ln=True)
        pdf.ln(1)

    # Opportunities
    if stage.opportunities.claims:
        pdf.set_font("NotoSans", "", 7)
        pdf.set_text_color(*_COLOR_MUTED)
        pdf.cell(_BODY_W, 4, "OPPORTUNITIES", ln=True)
        pdf.set_text_color(*_COLOR_DARK)
        for oc in stage.opportunities.claims[:3]:
            pdf.check_page_break(10)
            pdf.set_font("NotoSans", "", 8)
            pdf.set_text_color(*_COLOR_EMOTION_POSITIVE)
            pdf.cell(4, 4, "→")
            pdf.set_text_color(*_COLOR_DARK)
            ev_str = ", ".join(oc.evidence[:2])
            pdf.cell(_BODY_W - 4, 4, f"{oc.claim}  [{ev_str}]", ln=True)
        pdf.ln(1)

    # Touchpoints
    if stage.touchpoints.claims:
        pdf.set_font("NotoSans", "", 7)
        pdf.set_text_color(*_COLOR_MUTED)
        pdf.cell(_BODY_W, 4, "TOUCHPOINTS", ln=True)
        pdf.set_text_color(*_COLOR_DARK)
        for tc in stage.touchpoints.claims[:3]:
            pdf.check_page_break(10)
            pdf.set_font("NotoSans", "", 8)
            ev_str = ", ".join(tc.evidence[:2])
            pdf.cell(_BODY_W, 4, f"  • {tc.claim}  [{ev_str}]", ln=True)

    pdf.ln(3)


def _draw_emotion_bar(pdf: _ReportPDF, ep: EmotionPoint) -> None:
    """Draw a mini emotion intensity bar."""
    bar_w = 60  # mm for the bar
    bar_h = 4
    label_w = 30

    color = _EMOTION_COLORS.get(ep.label.lower(), _COLOR_EMOTION_NEUTRAL)

    y = pdf.get_y()

    # Label
    pdf.set_font("NotoSans", "", 7)
    pdf.set_text_color(*_COLOR_DARK)
    pdf.cell(label_w, bar_h, ep.label.capitalize())

    # Background bar
    pdf.set_fill_color(*_COLOR_LIGHT_BG)
    pdf.set_draw_color(*_COLOR_BORDER)
    bar_x = pdf.get_x()
    pdf.rect(bar_x, y + 0.5, bar_w, bar_h - 1, "DF")

    # Filled portion
    fill_w = bar_w * ep.intensity
    pdf.set_fill_color(*color)
    pdf.rect(bar_x, y + 0.5, fill_w, bar_h - 1, "F")

    # Intensity number
    pdf.set_xy(bar_x + bar_w + 2, y)
    pdf.set_font("NotoSans", "", 7)
    pdf.set_text_color(*_COLOR_MUTED)
    pdf.cell(10, bar_h, f"{ep.intensity:.0%}", ln=True)


def _draw_quantitative_grounding(
    pdf: _ReportPDF, persona: Persona, journey: JourneyMap | None
) -> None:
    """Draw quantitative grounding: sentiment distribution, source coverage."""
    pdf.check_page_break(60)
    pdf.section_header("Quantitative Grounding")

    # ── Sentiment distribution (aggregated from all claims) ────────────
    _draw_sentiment_distribution(pdf, persona, journey)

    # ── Source coverage ────────────────────────────────────────────────
    _draw_source_coverage(pdf, persona, journey)

    # ── Key metrics footer ─────────────────────────────────────────────
    pdf.ln(4)
    pdf.separator()
    pdf.set_font("NotoSans", "I", 7)
    pdf.set_text_color(*_COLOR_MUTED)
    generated = ""
    if persona.generated_at:
        generated = persona.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    pdf.cell(
        _BODY_W, 4,
        f"Generated: {generated}  |  "
        f"Model: {persona.provider}/{persona.model}  |  "
        f"Run: {persona.run_id}",
        ln=True,
    )


def _draw_sentiment_distribution(
    pdf: _ReportPDF, persona: Persona, journey: JourneyMap | None
) -> None:
    """Aggregate and display sentiment distribution across all claims."""
    pdf.sub_header("Sentiment Distribution")

    # Collect all sentiment scores across persona claim lists
    all_sentiment: dict[str, int] = {}
    claim_lists = [
        persona.goals.claims,
        persona.motivations.claims,
        persona.pain_points.claims,
        persona.preferred_channels.claims,
        persona.behaviors.claims,
    ]
    for claims in claim_lists:
        for c in claims:
            for k, v in c.sentiment_scores.items():
                all_sentiment[k] = all_sentiment.get(k, 0) + v

    if journey:
        for s in journey.stages:
            for cl in (s.touchpoints.claims, s.user_actions.claims,
                       s.frictions.claims, s.opportunities.claims):
                for c in cl:
                    for k, v in c.sentiment_scores.items():
                        all_sentiment[k] = all_sentiment.get(k, 0) + v

    if not all_sentiment:
        pdf.body_text("No sentiment data available from claims.", size=8)
        pdf.ln(3)
        return

    total = sum(all_sentiment.values())
    if total == 0:
        pdf.body_text("No sentiment data available.", size=8)
        pdf.ln(3)
        return

    # Draw horizontal stacked bar
    bar_h = 8
    bar_w = _BODY_W * 0.7
    y = pdf.get_y()
    x_start = _MARGIN

    sentiment_order = [
        ("positive", _COLOR_EMOTION_POSITIVE),
        ("neutral", _COLOR_EMOTION_NEUTRAL),
        ("negative", _COLOR_EMOTION_NEGATIVE),
    ]

    # Background
    pdf.set_fill_color(*_COLOR_LIGHT_BG)
    pdf.rect(x_start, y, bar_w, bar_h, "F")

    # Segments
    cumulative_x = x_start
    for label, color in sentiment_order:
        count = all_sentiment.get(label, 0)
        if count == 0:
            continue
        seg_w = bar_w * (count / total)
        pdf.set_fill_color(*color)
        pdf.rect(cumulative_x, y, seg_w, bar_h, "F")
        # Label inside segment if wide enough
        if seg_w > 15:
            pdf.set_xy(cumulative_x + 1, y + 1)
            pdf.set_font("NotoSans", "B", 6)
            pdf.set_text_color(*_COLOR_WHITE)
            pct = count / total * 100
            pdf.cell(seg_w - 2, bar_h - 2, f"{label[:3]} {pct:.0f}%", align="C")
        cumulative_x += seg_w

    # Legend below
    pdf.set_y(y + bar_h + 2)
    for label, color in sentiment_order:
        count = all_sentiment.get(label, 0)
        if count == 0:
            continue
        pct = count / total * 100
        pdf.set_fill_color(*color)
        pdf.rect(pdf.get_x(), pdf.get_y() + 1, 4, 4, "F")
        pdf.set_x(pdf.get_x() + 5)
        pdf.set_font("NotoSans", "", 7)
        pdf.set_text_color(*_COLOR_DARK)
        pdf.cell(40, 5, f"{label}: {count} ({pct:.1f}%)")

    pdf.ln(7)


def _draw_source_coverage(
    pdf: _ReportPDF, persona: Persona, journey: JourneyMap | None
) -> None:
    """Draw source coverage stats."""
    # Prefer journey coverage, fall back to persona
    cov: dict[str, Any] | None = None
    if journey and journey.data_source_coverage:
        cov = journey.data_source_coverage
    elif persona.data_source_coverage:
        cov = persona.data_source_coverage

    if not cov:
        return

    pdf.sub_header("Data Source Coverage")

    # 2-column metrics
    tier = cov.get("coverage_tier", "unknown")
    cat_count = cov.get("category_count", 0)
    pdf.metric_pair("Coverage Tier", tier.upper(), "Categories", str(cat_count))

    # Source breakdown table
    doc_counts = cov.get("doc_counts", {})
    if doc_counts:
        pdf.set_font("NotoSans", "B", 8)
        pdf.set_text_color(*_COLOR_HEADER)
        pdf.cell(_BODY_W, 5, "Posts by Source", ln=True)
        pdf.set_text_color(*_COLOR_DARK)

        total_docs = sum(doc_counts.values()) or 1
        for src, count in sorted(doc_counts.items(), key=lambda x: -x[1]):
            pdf.set_font("NotoSans", "", 8)
            pct = count / total_docs * 100
            pdf.cell(50, 5, f"  {src}")
            # Mini bar
            bar_w = _BODY_W * 0.4
            bar_fill = bar_w * (count / total_docs)
            bar_y = pdf.get_y()
            pdf.set_fill_color(*_COLOR_LIGHT_BG)
            pdf.rect(pdf.get_x(), bar_y, bar_w, 4, "F")
            pdf.set_fill_color(*_COLOR_ACCENT)
            pdf.rect(pdf.get_x(), bar_y, bar_fill, 4, "F")
            pdf.set_x(pdf.get_x() + bar_w + 3)
            pdf.cell(30, 5, f"{count} ({pct:.0f}%)", ln=True)

    # Bias warning
    warning = cov.get("bias_warning", "")
    if warning and warning != "balanced coverage across categories":
        pdf.ln(2)
        pdf.set_font("NotoSans", "I", 8)
        pdf.set_text_color(*_COLOR_MUTED)
        pdf.multi_cell(_BODY_W, 4, f"Note: {warning}")

    categories_present = cov.get("categories_present", [])
    categories_missing = cov.get("categories_missing", [])
    if categories_present or categories_missing:
        pdf.ln(2)
        pdf.set_font("NotoSans", "", 7)
        pdf.set_text_color(*_COLOR_EMOTION_POSITIVE)
        present_str = ", ".join(categories_present) if categories_present else "none"
        pdf.cell(_BODY_W, 4, f"Present: {present_str}", ln=True)
        if categories_missing:
            pdf.set_text_color(*_COLOR_MUTED)
            missing_str = ", ".join(categories_missing[:6])
            if len(categories_missing) > 6:
                missing_str += f" +{len(categories_missing) - 6} more"
            pdf.cell(_BODY_W, 4, f"Missing: {missing_str}", ln=True)
        pdf.set_text_color(*_COLOR_DARK)

    pdf.ln(3)


# ---------------------------------------------------------------------------
# Font helper
# ---------------------------------------------------------------------------

from urllib.request import urlretrieve


def _ensure_dejavu_font(font_dir: Path) -> None:
    """Download DejaVu Sans TTF files if not already present."""
    base_url = "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/"
    fonts = {
        "DejaVuSans.ttf": base_url + "DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf": base_url + "DejaVuSans-Bold.ttf",
        "DejaVuSans-Oblique.ttf": base_url + "DejaVuSans-Oblique.ttf",
        "DejaVuSans-BoldOblique.ttf": base_url + "DejaVuSans-BoldOblique.ttf",
    }
    for name, url in fonts.items():
        dest = font_dir / name
        if not dest.exists():
            try:
                urlretrieve(url, str(dest))
            except Exception:
                pass
