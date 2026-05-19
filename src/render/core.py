"""Render core — shared Playwright session, deterministic palette, CJK probe.

Used by persona_card.py and journey_map.py. Kept small on purpose; the
two renderers compose the page-specific Jinja context themselves and
hand HTML over to :func:`render_html_to_png` here.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from jinja2 import Environment, FileSystemLoader, select_autoescape

# ---------------------------------------------------------------------------
# Template environment
# ---------------------------------------------------------------------------

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def get_template(name: str):
    return _jinja_env.get_template(name)


# ---------------------------------------------------------------------------
# Deterministic accent palette
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Accent:
    from_: str
    via: str
    to: str

    def as_template_dict(self) -> dict:
        # Jinja2 can't access an attribute literally named ``from`` because it's
        # a reserved word; we expose the underscore-suffixed alias instead.
        return {"from_": self.from_, "via": self.via, "to": self.to}


# A muted, professional palette. Hue is chosen from ``persona_id`` so two
# personas in the same run feel distinct, but stays in a tasteful slice of
# HSL space (no neon, no full saturation). Lightness/saturation are fixed
# so the three stops always have similar visual weight.
_PALETTE_SAT = 0.32
_PALETTE_L_FROM = 0.32
_PALETTE_L_VIA = 0.46
_PALETTE_L_TO = 0.60


def accent_palette(persona_id: str) -> Accent:
    """Map ``persona_id`` to a stable three-stop muted gradient."""
    h_int = int(hashlib.sha256(persona_id.encode("utf-8")).hexdigest()[:8], 16)
    hue = (h_int % 360) / 360.0  # 0..1
    return Accent(
        from_=_hsl_hex(hue, _PALETTE_SAT, _PALETTE_L_FROM),
        via=_hsl_hex((hue + 0.03) % 1.0, _PALETTE_SAT, _PALETTE_L_VIA),
        to=_hsl_hex((hue + 0.06) % 1.0, _PALETTE_SAT, _PALETTE_L_TO),
    )


def _hsl_hex(h: float, s: float, l: float) -> str:
    """Convert HSL (all 0..1) to a 6-digit hex string."""
    if s == 0:
        v = int(round(l * 255))
        return f"#{v:02x}{v:02x}{v:02x}"
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    r = _hue_to_rgb(p, q, h + 1 / 3)
    g = _hue_to_rgb(p, q, h)
    b = _hue_to_rgb(p, q, h - 1 / 3)
    return (
        f"#{int(round(r * 255)):02x}"
        f"{int(round(g * 255)):02x}"
        f"{int(round(b * 255)):02x}"
    )


def _hue_to_rgb(p: float, q: float, t: float) -> float:
    if t < 0: t += 1
    if t > 1: t -= 1
    if t < 1 / 6: return p + (q - p) * 6 * t
    if t < 1 / 2: return q
    if t < 2 / 3: return p + (q - p) * (2 / 3 - t) * 6
    return p


# ---------------------------------------------------------------------------
# Playwright runner
# ---------------------------------------------------------------------------

_CHROME_ENV = "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"


def _chromium_launch_kwargs() -> dict:
    """Allow the container to point at an extracted Chromium binary."""
    kwargs: dict = {
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            # Deterministic rendering: turn off subpixel quirks that vary by host.
            "--font-render-hinting=none",
            "--disable-skia-runtime-opts",
        ],
    }
    env_path = os.environ.get(_CHROME_ENV)
    if env_path:
        kwargs["executable_path"] = env_path
    return kwargs


@contextmanager
def _playwright_browser() -> Iterator:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright is not installed. Run "
            "`pip install playwright && playwright install chromium`."
        ) from e
    with sync_playwright() as p:
        browser = p.chromium.launch(**_chromium_launch_kwargs())
        try:
            yield browser
        finally:
            browser.close()


def render_html_to_png(
    html: str,
    out_path: Path,
    *,
    viewport: dict[str, int],
) -> Path:
    """Render an HTML string to a PNG at ``out_path``.

    Writes the HTML to a temp file so Playwright can ``goto`` it via a
    ``file://`` URL. That avoids relying on ``set_content`` which has
    historically caused subtle race conditions with custom fonts.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="render_") as td:
        tmp_html = Path(td) / "page.html"
        tmp_html.write_text(html, encoding="utf-8")
        with _playwright_browser() as browser:
            ctx = browser.new_context(
                viewport=viewport,
                device_scale_factor=1,
                # Pin a fixed locale so date / number rendering doesn't drift
                # by host. Our templates don't render either, but defence
                # against future regressions is cheap.
                locale="en-US",
            )
            try:
                page = ctx.new_page()
                page.goto(f"file://{tmp_html}")
                page.wait_for_load_state("networkidle")
                page.screenshot(
                    path=str(out_path),
                    full_page=False,
                    omit_background=False,
                )
            finally:
                ctx.close()
    return out_path


# ---------------------------------------------------------------------------
# Public helpers used by both renderers
# ---------------------------------------------------------------------------


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate to ``max_chars`` with an ellipsis. Returns (text, was_truncated)."""
    text = text or ""
    if len(text) <= max_chars:
        return text, False
    # Don't break a CJK character: slicing by code point is safe.
    return text[: max_chars - 1].rstrip() + "…", True
