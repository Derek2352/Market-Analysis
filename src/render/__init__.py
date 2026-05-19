"""Phase 8 — render Persona + JourneyMap JSON as shareable offline PNGs.

  - persona_card.render_persona_card(persona, out_path)
  - journey_map.render_journey_map(journey, persona, out_path)
  - bundle.render_run(run_id, out_dir, zip=False)

Templates live in src/render/templates/. Rendering uses Playwright's
sync API against a Chromium binary; on a laptop, ``playwright install
chromium`` provides it. For containers / CI where that download isn't
allowed, set ``PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`` to an extracted
binary.
"""

from src.render.core import (
    accent_palette,
    render_html_to_png,
)
from src.render.persona_card import render_persona_card
from src.render.journey_map import render_journey_map
from src.render.bundle import render_run

__all__ = [
    "accent_palette",
    "render_html_to_png",
    "render_persona_card",
    "render_journey_map",
    "render_run",
]
