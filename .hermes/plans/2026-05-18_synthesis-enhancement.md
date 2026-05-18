# Synthesis Pipeline Enhancement Plan

> **Goal:** Add 5 capabilities to the persona/journey synthesis pipeline

## Architecture Summary
All features build on the existing `src/pipeline/synthesize.py` (1461 lines) and `src/schemas/synthesis.py` (133 lines). The pipeline: build evidence pack → call LLM → validate → build Persona/Journey objects.

---

## Feature 1: Quantitative Grounding
**What:** Each pain point/claim gets `mentioned_by_n_users`, `pct_of_cluster`, `sentiment_score_distribution`.

**Approach:** Compute these stats from cluster data BEFORE the LLM call. Add to `_EvidencePack` and include in evidence block text. Add fields to `EvidenceClaim` schema.

**Files to change:**
- `src/schemas/synthesis.py`: Add `mentioned_by_n_users: int`, `pct_of_cluster: float`, `sentiment_scores: dict[str, int]` to `EvidenceClaim`
- `src/pipeline/synthesize.py`: 
  - Add `_compute_quantitative_grounding(cluster)` that counts doc_id mentions per claim topic
  - Include grounding stats in evidence block text
  - After LLM returns, backfill grounding from evidence pack

**Tasks:**
1. Add fields to EvidenceClaim schema
2. Add `_compute_quantitative_grounding` to compute stats from cluster + post_texts
3. Include stats in evidence pack prompt block
4. Backfill grounding onto claims after LLM returns
5. Add unit test

---

## Feature 2: Persona Validation Pass
**What:** Second LLM call that attacks generated persona — "find evidence contradicting this pain point" — flags weak claims.

**Approach:** After persona is built, call LLM with the persona JSON + evidence pack, ask it to find contradictions. Set `coverage="contested"` on claims with counter-evidence.

**Files to change:**
- `src/pipeline/synthesize.py`: Add `_adversarial_validation(parsed, pack, client, model)` function
- `src/schemas/synthesis.py`: Add `contested_by: list[str]` field to EvidenceClaim (doc_ids that contradict)

**Tasks:**
1. Add `contested_by` field to EvidenceClaim
2. Add `_ADVERSARIAL_VALIDATION_PROMPT` constant
3. Add `_adversarial_validation()` function that calls LLM
4. Integrate into synthesize_run after persona generation
5. Add unit test

---

## Feature 3: Temporal Analysis
**What:** Same topic, two time windows → "how did sentiment shift after X date". Quote-level evidence.

**Approach:** New CLI subcommand or `--time-window` flag on synthesize. Splits posts by date, runs synthesis on both windows, then calls LLM to produce comparison.

**Files to change:**
- `src/pipeline/synthesize.py`: Add `synthesize_temporal()` function
- `src/cli.py`: Add `synthesize-temporal` command
- `src/schemas/synthesis.py`: Add `TemporalComparison` schema

**Tasks:**
1. Add `TemporalComparison` schema
2. Add `synthesize_temporal()` that filters clusters by time window
3. Add CLI command with `--before` and `--after` flags
4. Add unit test

---

## Feature 4: Comparative Analysis
**What:** Run two topics in one go ("HSBC vs Standard Chartered") → side-by-side diff.

**Approach:** New CLI subcommand. Runs synthesis on both topics, then calls LLM to produce comparison.

**Files to change:**
- `src/pipeline/synthesize.py`: Add `synthesize_comparative()` function
- `src/cli.py`: Add `synthesize-compare` command
- `src/schemas/synthesis.py`: Add `ComparativeReport` schema

**Tasks:**
1. Add `ComparativeReport` schema
2. Add `synthesize_comparative()` 
3. Add CLI command
4. Add unit test

---

## Feature 5: PDF Export
**What:** PDF report generation with embedded quote citations.

**Approach:** New `src/export/` module using `fpdf2` or `weasyprint`. Renders personas + journeys as structured PDF with quote callouts.

**Files to change:**
- `src/export/pdf.py`: New file, PDF generation
- `src/cli.py`: Add `export` command (or `synthesize --export-pdf`)

**Tasks:**
1. Add `fpdf2` to pyproject.toml dependencies
2. Create `src/export/__init__.py` and `src/export/pdf.py`
3. Implement `export_persona_report(persona, journey, output_path)`
4. Add CLI integration
5. Add unit test

---

## Implementation Order
1. Quantitative Grounding (foundation — other features use it)
2. Persona Validation Pass (uses grounding data)
3. Temporal Analysis (uses both above)
4. Comparative Analysis (uses all above)
5. PDF Export (renders all above)
