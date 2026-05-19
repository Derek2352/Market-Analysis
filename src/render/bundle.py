"""Bundle command — render every persona + journey for a run, plus index.html.

``mkt render run <run_id>`` calls into here. The bundle directory is
self-contained: each persona has a card PNG, each journey has a map PNG,
and ``index.html`` displays the lot in a grid. Optional ``--zip``
produces ``{run_id}.zip`` so the bundle can be shared as one file.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from src.render.journey_map import render_journey_map
from src.render.persona_card import render_persona_card
from src.schemas.synthesis import JourneyMap, Persona


@dataclass
class RenderedRun:
    run_id: str
    out_dir: Path
    persona_pngs: list[Path]
    journey_pngs: list[Path]
    index_html: Path
    zip_path: Path | None = None


# ---------------------------------------------------------------------------
# JSON discovery
# ---------------------------------------------------------------------------


def _read_persona(path: Path) -> Persona | None:
    try:
        return Persona(**json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _read_journey(path: Path) -> JourneyMap | None:
    try:
        return JourneyMap(**json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def load_personas_for_run(personas_root: Path, run_id: str) -> list[Persona]:
    out: list[Persona] = []
    if not personas_root.exists():
        return out
    for f in sorted(personas_root.rglob("persona_*.json")):
        p = _read_persona(f)
        if p is not None and p.run_id == run_id:
            out.append(p)
    return out


def load_journeys_for_run(journeys_root: Path, run_id: str) -> dict[str, JourneyMap]:
    """Map ``persona_id -> JourneyMap`` for everything in this run."""
    out: dict[str, JourneyMap] = {}
    if not journeys_root.exists():
        return out
    for f in sorted(journeys_root.rglob("journey_*.json")):
        j = _read_journey(f)
        if j is not None and j.run_id == run_id:
            out[j.persona_id] = j
    return out


def load_topic_for_run(runs_root: Path, run_id: str) -> str:
    """Read the run.json (API-managed) to find the topic, else empty string."""
    run_file = runs_root / run_id / "run.json"
    if not run_file.exists():
        return ""
    try:
        payload = json.loads(run_file.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return (payload.get("summary") or {}).get("topic", "") or ""


# ---------------------------------------------------------------------------
# Bundle index
# ---------------------------------------------------------------------------


_INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Run {run_id} — render bundle</title>
<style>
  body {{
    margin: 0; padding: 32px 40px;
    background: #f6f7fa; color: #1c2533;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Noto Sans CJK TC", sans-serif;
  }}
  h1 {{ font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }}
  .meta {{ color: #5b6473; font-size: 13px; margin-bottom: 24px; }}
  .grid {{
    display: grid;
    grid-template-columns: 360px 1fr;
    gap: 24px;
    align-items: start;
  }}
  .row {{
    display: contents;
  }}
  .row .persona, .row .journey {{
    background: #fff; border-radius: 10px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.04);
    padding: 12px;
  }}
  .row img {{ display: block; width: 100%; height: auto; border-radius: 6px; }}
  .caption {{
    font-size: 12px; color: #5b6473; margin: 10px 4px 2px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }}
</style>
</head>
<body>
<h1>Run {run_id}</h1>
<p class="meta">{persona_count} persona{persona_s} · {journey_count} journey{journey_s}{topic_part}</p>

<div class="grid">
{rows}
</div>
</body>
</html>
"""


def _index_html(run_id: str, topic: str, pairs: list[dict]) -> str:
    rows = []
    for pair in pairs:
        if pair.get("journey_png"):
            journey_cell = (
                f'<img src="{pair["journey_png"]}" alt="journey map">'
            )
        else:
            journey_cell = '<em>no journey rendered</em>'
        rows.append(
            '  <div class="row">'
            f'<div class="persona"><img src="{pair["persona_png"]}" alt="persona card">'
            f'<div class="caption">{pair["persona_id"]}</div></div>'
            f'<div class="journey">{journey_cell}'
            f'<div class="caption">{pair.get("journey_id") or "—"}</div></div>'
            '</div>'
        )
    pc, jc = len(pairs), sum(1 for p in pairs if p.get("journey_png"))
    return _INDEX_TEMPLATE.format(
        run_id=run_id,
        persona_count=pc,
        persona_s="" if pc == 1 else "s",
        journey_count=jc,
        journey_s="" if jc == 1 else "s",
        topic_part=f" · topic: {topic}" if topic else "",
        rows="\n".join(rows),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_run(
    run_id: str,
    out_dir: Path | str,
    *,
    personas_root: Path,
    journeys_root: Path,
    runs_root: Path | None = None,
    topic: str = "",
    zip_bundle: bool = False,
) -> RenderedRun:
    """Render every persona + journey for ``run_id`` into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    personas = load_personas_for_run(personas_root, run_id)
    journeys = load_journeys_for_run(journeys_root, run_id)
    if not topic and runs_root is not None:
        topic = load_topic_for_run(runs_root, run_id)

    if not personas:
        raise FileNotFoundError(
            f"No personas found for run {run_id!r} under {personas_root}. "
            f"Run synthesize first or pass --personas-root."
        )

    persona_pngs: list[Path] = []
    journey_pngs: list[Path] = []
    pairs: list[dict] = []

    for persona in personas:
        p_png = out_dir / f"{persona.id}.png"
        render_persona_card(persona, p_png)
        persona_pngs.append(p_png)

        j = journeys.get(persona.id)
        if j is not None:
            j_png = out_dir / f"{j.id}.png"
            render_journey_map(j, persona, j_png, topic=topic)
            journey_pngs.append(j_png)
            pairs.append({
                "persona_id": persona.id,
                "persona_png": p_png.name,
                "journey_id": j.id,
                "journey_png": j_png.name,
            })
        else:
            pairs.append({
                "persona_id": persona.id,
                "persona_png": p_png.name,
                "journey_id": None,
                "journey_png": None,
            })

    index_html = out_dir / "index.html"
    index_html.write_text(
        _index_html(run_id, topic, pairs), encoding="utf-8",
    )

    zip_path: Path | None = None
    if zip_bundle:
        zip_path = out_dir.parent / f"{run_id}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in (*persona_pngs, *journey_pngs, index_html):
                zf.write(f, arcname=f"{run_id}/{f.name}")

    return RenderedRun(
        run_id=run_id,
        out_dir=out_dir,
        persona_pngs=persona_pngs,
        journey_pngs=journey_pngs,
        index_html=index_html,
        zip_path=zip_path,
    )
