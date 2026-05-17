# Market Analytics Tool — Project Plan

A personal, locally-run tool that generates **Personas** and **User Journey Maps** for any product, brand, or category, grounded in publicly scrapeable online discussion. The user picks a target region; the pipeline adapts its sources and language handling.

> **Scope decisions (locked from clarifying round):**
> - **Deployment:** personal/local tool — single user, no auth, runs on one machine.
> - **Hard regions:** best-effort, public-only for CN (Weibo / RedNote / Bilibili public pages). No login-walled scraping, no residential proxies.
> - **Language:** hybrid — embed & cluster in native language with a multilingual model; synthesize final persona/journey output in the user's chosen language (English by default). Quotes stay original with translation alongside.
> - **Freshness:** cache per `(product, region)` with a TTL (default 21 days); manual "refresh" forces a re-scrape.

---

## 1. Architecture overview

### Data flow

```
 ┌────────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐   ┌──────────────┐   ┌─────────────┐
 │  user      │──▶│ query    │──▶│ scrape  │──▶│ clean &  │──▶│ embed &      │──▶│ LLM         │
 │  (product, │   │ planner  │   │ workers │   │ dedupe   │   │ cluster      │   │ synthesis   │
 │   region)  │   │          │   │ (per    │   │          │   │ (multilingual│   │ (Claude     │
 │            │   │          │   │  source)│   │          │   │  embeds +    │   │  Sonnet 4.6)│
 │            │   │          │   │         │   │          │   │  HDBSCAN)    │   │             │
 └────────────┘   └──────────┘   └─────────┘   └──────────┘   └──────────────┘   └──────┬──────┘
                                      │                                                  │
                                      ▼                                                  ▼
                              raw artifacts                                     persona + journey
                              on disk (jsonl)                                   JSON (grounded
                                      │                                          in quote IDs)
                                      └────────────────► Postgres ◀─────────────────┘
                                                         (+ pgvector)
                                                              │
                                                              ▼
                                                        FastAPI ──▶ Next.js UI
```

### Components

| Component | Responsibility |
|---|---|
| **Query planner** | Turns `(product, region)` into source-specific search terms, subreddits, hashtags, store IDs. Region-aware translations. |
| **Scrape workers** | One adapter per source (Reddit, YouTube, App Store, Play Store, Weibo, etc.). Returns normalized `Document` records to disk and DB. |
| **Cleaner** | Strip boilerplate, language-detect, dedupe near-identical posts (MinHash), drop PII heuristically. |
| **Embedder** | Multilingual sentence embeddings (BGE-M3) over post + comment chunks. |
| **Clusterer** | HDBSCAN over embeddings. Targets 5–9 viable clusters per run; merges tiny clusters, keeps a "noise" bucket. |
| **Synthesizer** | Claude Sonnet 4.6 reads representative quotes + cluster stats, returns Persona JSON and Journey JSON. **Every claim must cite a `doc_id`.** |
| **API** | FastAPI: `POST /runs`, `GET /runs/:id`, `GET /personas/:id`, `GET /journeys/:id`. |
| **UI** | Next.js + Tailwind. Two views: Persona cards, Journey Map (stages × dimensions). Click any claim → original quote + source URL. |

### Where Claude API fits

- **Synthesis only.** Persona generation, journey-map writing, cluster labeling, quote selection.
- **Not for scraping.** Scraping uses deterministic adapters — cheaper, faster, debuggable.
- **Not for embeddings.** Local multilingual model — free, batched, runs on CPU.
- One optional Claude-assisted step: **query expansion** at the start (turn "Notion" into `["Notion", "notion app", "second brain tool", …]` per locale).

---

## 2. Tech stack with tradeoffs

| Layer | Choice | Why | Alternatives | Risk |
|---|---|---|---|---|
| Scraping runtime | **Python 3.12 + Playwright** + `httpx` for API-first sources | Playwright handles JS-heavy pages (App Store, RedNote, Weibo). `httpx` for Reddit/YouTube APIs. Best ecosystem for parsing. | Node + Puppeteer; Crawlee | Playwright is heavier than `httpx`; only use it when an API doesn't exist. |
| Storage | **Postgres 16 + pgvector** in Docker | One store for documents, embeddings, clusters, runs. pgvector handles 100K-vector personal scale trivially. | SQLite + `sqlite-vec` (lighter), DuckDB (analytics-friendly) | Docker dependency. For a pure single-binary tool, SQLite is tempting — but pgvector ecosystem maturity wins. |
| Embeddings | **BAAI/bge-m3** via `sentence-transformers` | Multilingual (100+ languages), 1024-dim, strong on short social text, free, runs CPU-only on a laptop. | OpenAI `text-embedding-3-small` (cheap, English-leaning); Cohere multilingual (paid) | First-run model download (~2 GB). Slower than API embeddings without a GPU. |
| Clustering | **HDBSCAN** | Density-based; no need to pick `k`; produces "noise" naturally. Good for messy social data. | KMeans (need k), BERTopic (heavier wrapper) | Sensitive to `min_cluster_size`; tune per region. |
| LLM | **Claude Sonnet 4.6** (`claude-sonnet-4-6`) for synthesis | Strong reasoning over multilingual evidence; long context for many quotes per cluster; reliable JSON output. | Sonnet 4.6 with thinking for harder syntheses; Opus 4.7 for final report polish (rare). | Cost — bounded by quote-budget per cluster (§7). |
| Backend | **FastAPI** | Async-native (matches scraping), Pydantic schemas double as docs, trivial to run locally. | Litestar, Flask | None at this scale. |
| Frontend | **Next.js 15 + Tailwind + shadcn/ui** | Fast scaffolding, great component primitives, easy to host locally. | Vite + React; SvelteKit | Overkill for a personal tool, but the journey map needs rich layout — worth it. |
| Job orchestration | **`asyncio.TaskGroup` + Postgres `runs` table as state** | Personal scope — no need for Celery/RQ/Temporal. One worker process is enough. | Celery + Redis (if scope grows) | If scraping a region takes >30 min, a UI refresh mid-run is awkward. Use SSE for progress. |
| Caching/TTL | **Postgres row with `expires_at`** on `(product_slug, region)` | Trivial, no extra service. | Redis | None. |

**Prompt caching:** Claude API calls use **prompt caching** on the cluster-evidence block — the same evidence may feed both persona and journey synthesis prompts. Saves ~70% on the second call per cluster.

---

## 3. Data sources per region

**Priority order in every region:** official API → public RSS/JSON → HTML scrape (Playwright). App store listings and YouTube are global and always included.

| Region | Primary (API-first) | Secondary (HTML / public scrape) | Notes / risk |
|---|---|---|---|
| **US / UK** | Reddit API (OAuth, free tier), YouTube Data API v3 (comments), App Store RSS + Marketing API, Google Play (no API → Playwright on listing pages), Hacker News (Algolia API), Product Hunt API | Trustpilot listings, Quora public threads, Twitter/X public search (limited; treat as optional) | **Safe.** Reddit + YouTube + app stores cover most consumer products. |
| **EU (multilingual)** | Same APIs as US/UK with `lang` and country filters; Reddit `r/de`, `r/france`, `r/italy`; YouTube comments filtered by detected language | Local forum HTML (e.g., gutefrage.net, doctissimo.fr) — only public listing pages | **Mostly safe.** Respect robots.txt; per-domain rate limits. GDPR: never store usernames or PII. |
| **JP** | Reddit `r/japan`, YouTube JP, App Store JP RSS, Hatena Bookmark API | 5ch public read-only mirrors (e.g., open5ch), Yahoo Japan reviews (HTML), kakaku.com listings | 5ch scraping is socially tolerated but ToS-ambiguous → keep volume modest, identify as a bot UA. |
| **KR** | YouTube KR, App Store KR RSS, Reddit `r/korea` (English-language) | Naver Blog/Cafe **public** posts (HTML, JS-rendered → Playwright), DC Inside public boards | Naver is anti-bot; expect breakage. Login-walled cafes are off-limits. |
| **CN (best-effort, public-only)** | Bilibili open API (videos, public comments), Zhihu public question pages (HTML), Weibo public search (HTML, often partial) | RedNote (Xiaohongshu) public note pages (HTML, heavy anti-bot — flagged as fragile) | **Highest risk surface.** All three actively block bots. Plan assumes degraded coverage; CN runs will show a "coverage: partial" badge in the UI. No login. No proxies. |
| **SEA (ID/TH/VN/PH/MY)** | Reddit `r/indonesia` etc. (often English), YouTube regional, App Store regional RSS, Shopee/Tokopedia public listings (HTML — review text on product pages) | Kaskus (ID), Pantip (TH) public threads | Tokopedia/Shopee aggressive on bots → use Playwright, conservative rate. |
| **LATAM (BR/MX/AR)** | Reddit `r/brasil`, `r/mexico`; YouTube regional; App Store regional RSS; Mercado Libre public listings (reviews) | Reclame Aqui (BR) public complaint pages | Reclame Aqui = gold for pain points; respect robots.txt. |

**Never scraped:** anything behind a login, Facebook/Instagram private content, WeChat, LinkedIn, paywalled news, Discord, Slack.

**PII rule:** raw scraped text is stored only for the TTL window. Usernames and avatars are dropped at clean-time; only the post text, post URL, timestamp, and source label are persisted long-term. Quote attribution in the UI links to the public URL, not to a username we stored.

---

## 4. Output schemas (JSON)

All IDs are stable hashes so re-runs can diff. Every `claim` field carries an `evidence` array with `doc_id`s that resolve to a stored quote + URL.

### Persona

```json
{
  "id": "persona_<hash>",
  "run_id": "run_<hash>",
  "cluster_id": "cluster_<hash>",
  "name": "Pragmatic Power-User Priya",
  "one_liner": "Mid-career PM who switched from Evernote and now lives in Notion databases.",
  "language": "en",
  "demographics": {
    "age_range": "28-38",
    "occupation_examples": ["product manager", "consultant", "founder"],
    "region": "US",
    "evidence": ["doc_a1b2", "doc_c3d4"]
  },
  "goals": [
    { "claim": "Centralize meeting notes, specs, and OKRs in one searchable place.", "evidence": ["doc_..."] }
  ],
  "motivations": [
    { "claim": "Hates context-switching; rewards tools that absorb other tools.", "evidence": ["doc_..."] }
  ],
  "pain_points": [
    { "claim": "Mobile app is sluggish for quick capture.", "severity": "high", "evidence": ["doc_..."] }
  ],
  "preferred_channels": [
    { "channel": "Reddit r/Notion", "evidence": ["doc_..."] },
    { "channel": "YouTube tutorials (Thomas Frank, August Bradley)", "evidence": ["doc_..."] }
  ],
  "behaviors": [
    { "claim": "Builds custom databases before adopting templates.", "evidence": ["doc_..."] }
  ],
  "representative_quotes": [
    {
      "text_original": "I tried switching back to Evernote for a week and crawled back within days.",
      "text_translated": null,
      "lang": "en",
      "source": "reddit",
      "url": "https://reddit.com/r/Notion/...",
      "doc_id": "doc_a1b2"
    }
  ],
  "confidence": 0.78,
  "cluster_size": 142,
  "generated_at": "2026-05-17T12:00:00Z",
  "model": "claude-sonnet-4-6"
}
```

### Journey Map

Stages are fixed: **Awareness → Consideration → Decision → Onboarding → Use → Loyalty/Churn**. Per stage:

```json
{
  "id": "journey_<hash>",
  "run_id": "run_<hash>",
  "persona_id": "persona_<hash>",
  "language": "en",
  "stages": [
    {
      "stage": "Awareness",
      "touchpoints": [
        { "claim": "YouTube comparison videos (Notion vs Obsidian)", "evidence": ["doc_..."] }
      ],
      "user_actions": [
        { "claim": "Searches 'best note-taking app for PMs'", "evidence": ["doc_..."] }
      ],
      "emotions": [
        { "label": "curious", "intensity": 0.7, "evidence": ["doc_..."] },
        { "label": "overwhelmed", "intensity": 0.4, "evidence": ["doc_..."] }
      ],
      "frictions": [
        { "claim": "Too many influencer takes; hard to find unbiased comparison.", "evidence": ["doc_..."] }
      ],
      "opportunities": [
        { "claim": "First-party 'why switch' page with side-by-side teardown.", "evidence": ["doc_..."] }
      ]
    }
    /* Consideration, Decision, Onboarding, Use, Loyalty/Churn — same shape */
  ],
  "generated_at": "2026-05-17T12:00:00Z",
  "model": "claude-sonnet-4-6"
}
```

### Document (internal, what gets cited)

```json
{
  "doc_id": "doc_a1b2",
  "source": "reddit",
  "url": "https://reddit.com/...",
  "lang": "en",
  "text": "I tried switching back to Evernote...",
  "scraped_at": "2026-05-17T11:30:00Z"
}
```

---

## 5. Analysis pipeline detail

### 5a. Raw → clusters

1. **Chunk.** Split each post/comment to ≤ 512 tokens (keep parent thread context as metadata).
2. **Language detect** (`fasttext-langdetect`); tag each chunk.
3. **Dedupe.** MinHash + Jaccard ≥ 0.85 → drop near-duplicates (cross-post spam).
4. **Embed.** BGE-M3 in batches of 64. Store vectors in pgvector.
5. **Cluster.** HDBSCAN with `min_cluster_size = max(15, N/40)`, `min_samples=5`, cosine distance. Target 5–9 viable clusters; if >9, increase `min_cluster_size`; if <3, fall back to KMeans `k=5` over the largest connected component.
6. **Label clusters.** Pull top 20 chunks by closeness-to-centroid; send to Claude with a tight prompt → returns a short label + 5 representative quote IDs. This is a *cheap* call (no full synthesis yet).

### 5b. Clusters → personas

For each viable cluster:

- **Evidence pack** (prompt-cached):
  - Cluster label, size, top 30 quotes (by centroid proximity), language distribution, source distribution.
- **Synthesis prompt** asks Claude to:
  - Return the Persona JSON schema exactly.
  - For *every* field, cite ≥1 `doc_id` from the evidence pack. **No citation → field must be omitted.**
  - Pick 3–5 representative quotes verbatim from the pack.
  - Output `language` in the user's chosen output language; quotes stay in original language with a translation field.
- **Dedup personas across clusters.** After all personas exist, embed their `one_liner` + top pain points; if cosine ≥ 0.9, merge (keep the larger cluster's persona; union the evidence).

### 5c. Same clusters → journey maps

- **Reuse the same evidence pack** (cache hit on the prompt prefix → cheap).
- **Different system prompt** that asks Claude to map quotes onto the six fixed stages.
- Same grounding rule: every claim cites `doc_id`s.
- If a stage has < 2 supporting quotes, mark `"coverage": "thin"` rather than fabricating content.

---

## 6. Build phases

Each phase ends in something runnable end-to-end at its slice.

| # | Phase | Deliverable | Runnable artifact |
|---|---|---|---|
| **1** | **Reddit-only, US-only, raw to disk** | One scraper adapter (Reddit), CLI command `mkt scrape --product "Notion" --region US --source reddit`. Writes `data/runs/<run_id>/reddit.jsonl`. No DB, no embeddings, no LLM. | `mkt scrape …` produces a jsonl you can `jq` through. |
| **2** | **Postgres + cleaning + embeddings** | Docker-compose Postgres+pgvector. Migrations. Load Phase-1 jsonl → clean → embed → store. `mkt index --run <id>`. | `mkt index …` populates `documents` and `embeddings` tables. |
| **3** | **Clustering + cluster labeling** | HDBSCAN over embeddings; cheap Claude call labels each cluster. `mkt cluster --run <id>`. CLI prints cluster table. | CLI shows `cluster_id | label | size | top_quote`. |
| **4** | **Persona + journey synthesis (one region)** | Full Claude synthesis with grounding. `mkt synthesize --run <id>` writes `personas.json` and `journeys.json` to disk + DB. | Inspect JSON; verify every claim has `evidence`. |
| **5** | **FastAPI + minimal Next.js UI** | `POST /runs`, `GET /runs/:id`, `GET /personas/:id`, `GET /journeys/:id`. UI: form (product + region), persona cards with click-through to quote, journey grid. | `make dev` → localhost:3000 → generate a persona end-to-end through the UI. |
| **6** | **Multi-source + multi-region (EN-speaking)** | Add YouTube, App Store, Play Store adapters. Add UK, CA, AU regions. Source-mix shown in UI. | UI dropdown for region; persona shows evidence from ≥2 sources. |
| **7** | **Non-English regions + hybrid language pipeline** | Add JP/KR/SEA/LATAM adapters. Multilingual clustering. Output language selector (default EN). Quote translation in UI hover. | Generate a JP persona, view output in EN with original JP quotes. |
| **8** | **CN best-effort + caching + polish** | Bilibili / Weibo / Zhihu adapters with "partial coverage" badge. TTL cache + manual refresh. Eval set (§7) wired into a `make eval` target. | Persona for CN region returns; eval CLI prints grounding score. |

If anything breaks early, Phase 1 alone is still a useful Reddit scraper.

---

## 7. Risk surface

### Legal / ToS

| Class | Examples | Policy |
|---|---|---|
| **Safe (API, generous terms)** | Reddit OAuth, YouTube Data API, App Store RSS, HN Algolia, Product Hunt | Use freely within rate limits; store responsibly. |
| **Public HTML, generally tolerated** | Trustpilot, Reclame Aqui, Pantip, Kaskus, kakaku.com | Honor robots.txt, identify a custom UA, conservative concurrency (≤ 2 rps per domain), cache aggressively. |
| **Anti-bot, ToS-ambiguous** | Naver, RedNote, Weibo, Zhihu, Shopee, Tokopedia, 5ch mirrors | Best-effort only. No login, no proxies. Fail soft; mark coverage as partial in the UI. Don't republish raw content — only quote excerpts with source links. |
| **Off-limits** | Anything login-walled (Facebook, IG, WeChat, LinkedIn, Discord, private subreddits), paywalled news, X/Twitter beyond unauthed public search | Never scrape. |

**PII storage rules**
- Drop usernames, avatars, profile URLs at clean-time. They're never written to the long-term store.
- Long-term we keep: post text, post URL, timestamp, source, detected language.
- Raw scrape artifacts (jsonl) on disk include usernames temporarily for debugging; auto-purged after TTL.
- The UI cites by URL, never by username.

### Cost

| Operation | Tokens (est) | Model | Cost/call | Notes |
|---|---|---|---|---|
| Cluster labeling (per cluster, ~7 clusters) | ~2 K in / 200 out | Sonnet 4.6 | ~$0.01 | 7 calls per run ≈ $0.07 |
| Persona synthesis (per persona, ~7) | ~6 K in / 1.5 K out | Sonnet 4.6 | ~$0.04 | $0.28 per run |
| Journey synthesis (per persona, ~7) | ~6 K in / 2 K out, **prompt-cached prefix from persona call** | Sonnet 4.6 | ~$0.015 | $0.10 per run with cache hits |
| Query expansion (optional, once) | ~500 in / 300 out | Sonnet 4.6 | <$0.005 | $0.005 |
| **Per full run (1 product × 1 region)** | | | **≈ $0.45** | Bounded by quote-budget (max 30 quotes/cluster, max 9 clusters). |

**Bounding mechanism:** hard caps in code on `clusters_per_run`, `quotes_per_cluster`, and `max_personas_per_run`. CLI prints estimated cost before kicking off a synthesis run; UI shows it on the "Generate" button.

### Quality — anti-hallucination

1. **Mandatory grounding.** Synthesis prompt requires every claim to cite ≥1 `doc_id` from the evidence pack. Output validator rejects + retries (once) any persona/journey where any claim lacks `evidence`.
2. **Verbatim quote check.** `representative_quotes[].text_original` must exist as a substring of some doc in the evidence pack. Validator drops fabricated quotes.
3. **Source-link integrity.** Every quote in the UI deep-links to the public URL; broken/missing URL → quote is hidden.
4. **Eval set.** A small fixture of 5 known products with hand-curated "ground truth" pain points. `make eval` runs the full pipeline and reports: % of ground-truth pain points the personas surfaced + average grounding coverage. Run before any prompt change.
5. **Partial-coverage badge.** If a region returns <50 documents or <3 viable clusters, the UI surfaces a warning instead of pretending the output is high-confidence.

---

## 8. Repo structure

```
market-analysis/
├── PROJECT_PLAN.md                  # this file
├── README.md
├── pyproject.toml                   # uv / hatchling
├── docker-compose.yml               # postgres + pgvector
├── Makefile                         # dev, eval, fmt, test
├── .env.example                     # ANTHROPIC_API_KEY, REDDIT_*, YOUTUBE_API_KEY, DB_URL
│
├── backend/
│   ├── mkt/                         # python package
│   │   ├── __init__.py
│   │   ├── cli.py                   # `mkt scrape|index|cluster|synthesize|run`
│   │   ├── config.py                # pydantic settings
│   │   ├── db/
│   │   │   ├── models.py            # sqlalchemy: Run, Document, Embedding, Cluster, Persona, Journey
│   │   │   ├── migrations/          # alembic
│   │   │   └── session.py
│   │   ├── scrape/
│   │   │   ├── base.py              # SourceAdapter protocol, normalized Document
│   │   │   ├── reddit.py
│   │   │   ├── youtube.py
│   │   │   ├── app_store.py
│   │   │   ├── play_store.py
│   │   │   ├── trustpilot.py
│   │   │   ├── bilibili.py
│   │   │   ├── weibo.py
│   │   │   ├── zhihu.py
│   │   │   ├── naver.py
│   │   │   └── registry.py          # region → [adapter, ...]
│   │   ├── clean/
│   │   │   ├── dedupe.py            # minhash
│   │   │   ├── lang.py              # fasttext
│   │   │   └── pii.py               # username/email strip
│   │   ├── embed/
│   │   │   ├── model.py             # BGE-M3 loader, batch embed
│   │   │   └── store.py             # pgvector r/w
│   │   ├── cluster/
│   │   │   ├── hdbscan_run.py
│   │   │   └── label.py             # cheap claude label call
│   │   ├── synthesize/
│   │   │   ├── prompts/
│   │   │   │   ├── persona.md
│   │   │   │   ├── journey.md
│   │   │   │   └── query_expand.md
│   │   │   ├── persona.py
│   │   │   ├── journey.py
│   │   │   ├── grounding.py         # validator: every claim → doc_id
│   │   │   └── client.py            # anthropic sdk wrapper with prompt caching
│   │   ├── api/
│   │   │   ├── app.py               # fastapi
│   │   │   ├── routes/
│   │   │   │   ├── runs.py
│   │   │   │   ├── personas.py
│   │   │   │   └── journeys.py
│   │   │   └── sse.py               # progress stream
│   │   └── eval/
│   │       ├── fixtures/            # known products + ground truth
│   │       └── score.py
│   └── tests/
│       ├── scrape/                  # vcr-recorded fixtures per adapter
│       ├── clean/
│       ├── cluster/
│       └── synthesize/
│
├── frontend/
│   ├── package.json
│   ├── app/
│   │   ├── page.tsx                 # form: product + region + output language
│   │   ├── runs/[id]/page.tsx       # live progress (SSE)
│   │   ├── personas/[id]/page.tsx   # persona card
│   │   └── journeys/[id]/page.tsx   # 6-stage grid
│   ├── components/
│   │   ├── PersonaCard.tsx
│   │   ├── JourneyGrid.tsx
│   │   ├── QuoteHover.tsx           # original + translation
│   │   └── CoverageBadge.tsx
│   └── lib/api.ts
│
└── data/
    ├── runs/<run_id>/*.jsonl        # raw scrape artifacts (TTL-purged)
    └── models/                      # BGE-M3 download cache
```

---

## Open items for review

Please flag any of the below you'd like changed before Phase 1:

1. **TTL value.** Default 21 days. Shorter (7) keeps data fresh; longer (60) cuts cost.
2. **Output language default.** Currently English regardless of region. Should it default to the region's primary language instead?
3. **Eval fixtures.** I'll seed `eval/fixtures/` with 5 products in Phase 4 — any specific products you want included (e.g., something you know well so you can sanity-check the personas)?
4. **Cluster count target (5–9).** Comfortable, or do you want more/fewer personas per run?
5. **Storage of raw scrape artifacts on disk** vs. **DB-only.** On-disk is easier to debug; DB-only is tidier. Current plan: disk for the TTL window, then purge.
6. **CN coverage badge wording.** OK to ship a visible "partial coverage" warning when CN sources return < N docs?

Once you've edited or approved, I'll start Phase 1: Reddit-only scraper for US, writing raw jsonl to disk.
