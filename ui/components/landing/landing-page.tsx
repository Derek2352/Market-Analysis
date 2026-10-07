"use client";

/**
 * Landing page — Next.js port of the Claude Design prototype.
 *
 * Single client component on purpose: every section is presentational
 * and the launcher's local state (topic / region / source toggles) is
 * the only interactive part. Splitting further would buy nothing.
 *
 * Structure mirrors the prototype top-to-bottom:
 *   Nav · Hero · Marquee · Pipeline · Personas · Journey ·
 *   Sources · Eval · CLI + Recent · Privacy · CTA · Footer
 */

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { ThemeToggle } from "@/components/theme-toggle";
import {
  EVAL_RESULTS,
  JOURNEY,
  PERSONAS,
  RECENT_RUNS,
  REGIONS,
  SCRAPE_STREAM,
  SOURCES,
  SUGGESTED_TOPICS,
  type Emotion,
  type Friction,
} from "@/components/landing/data";

const DEFAULT_SOURCES = new Set(["lihkg", "reddit_old", "app_store_hk"]);

// The LLM providers the FastAPI backend + CLI accept. Default matches
// the launcher's default (deepseek) so the landing and /launch agree
// on the cheaper-by-default choice.
type Provider = "anthropic" | "deepseek";
const PROVIDERS: { id: Provider; label: string; hint: string }[] = [
  { id: "deepseek",  label: "DeepSeek", hint: "deepseek-chat · cheaper, ~$0.14/run" },
  { id: "anthropic", label: "Claude",   hint: "claude-sonnet-4 · higher quality, ~$0.60/run" },
];

export function LandingPage() {
  return (
    <div className="landing-root">
      <Nav />
      <Hero />
      <Marquee />
      <Pipeline />
      <Personas />
      <Journey />
      <Sources />
      <EvalSection />
      <RecentAndCli />
      <Privacy />
      <CTA />
      <LandingFooter />
    </div>
  );
}

// ── shared atoms ─────────────────────────────────────────────────────────

type PillKind = "default" | "warm" | "cool" | "good" | "bad";
function Pill({
  kind = "default",
  live = false,
  children,
}: {
  kind?: PillKind;
  live?: boolean;
  children: React.ReactNode;
}) {
  const k = kind === "default" ? "" : kind;
  return (
    <span className={`pill ${k} ${live ? "live" : ""}`}>
      <span className="dot" />
      {children}
    </span>
  );
}

function ConfidenceRing({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const r = 14;
  const c = 2 * Math.PI * r;
  const offset = c - (c * pct) / 100;
  const color =
    pct >= 80 ? "var(--good)" : pct >= 65 ? "var(--warm)" : "var(--bad)";
  return (
    <div className="ring" title={`Confidence ${pct}%`}>
      <svg width="36" height="36">
        <circle cx="18" cy="18" r={r} fill="none" stroke="var(--line)" strokeWidth="3" />
        <circle
          cx="18"
          cy="18"
          r={r}
          fill="none"
          stroke={color}
          strokeWidth="3"
          strokeDasharray={c}
          strokeDashoffset={offset}
          strokeLinecap="round"
        />
      </svg>
      <div className="num">{pct}</div>
    </div>
  );
}

// ── Nav ──────────────────────────────────────────────────────────────────

function Nav() {
  return (
    <nav className="nav">
      <div className="page nav-row">
        <div className="brand">
          <div className="brand-mark">m/</div>
          <div>Market Analytics</div>
          <Pill kind="cool">v0.4 · phase 4</Pill>
        </div>
        <div className="nav-links">
          <a href="#pipeline">Pipeline</a>
          <a href="#personas">Personas</a>
          <a href="#journey">Journeys</a>
          <a href="#sources">Sources</a>
          <a href="#eval">Eval</a>
          <a href="#cli" className="mono">
            docs ↗
          </a>
        </div>
        <div className="nav-cta">
          <ThemeToggle />
          <a className="btn btn-sm" href="https://github.com/Derek2352/Market-Analysis" target="_blank" rel="noopener">
            GitHub ↗
          </a>
          <Link className="btn btn-sm btn-primary" href="/launch">
            Start a run
          </Link>
        </div>
      </div>
    </nav>
  );
}

// ── Hero ─────────────────────────────────────────────────────────────────

function Hero() {
  const [topic, setTopic] = useState("");
  const [region, setRegion] = useState("HK");
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(DEFAULT_SOURCES),
  );
  const [provider, setProvider] = useState<Provider>("deepseek");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (window.location.hash === "#launcher") inputRef.current?.focus();
  }, []);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Launch the actual /launch page with the selected topic preserved in
  // the URL query so the user lands on the launcher with their choices
  // already filled in (the launcher reads these on mount).
  const launchHref = useMemo(() => {
    const params = new URLSearchParams();
    if (topic.trim()) params.set("topic", topic.trim());
    params.set("region", region);
    if (selected.size > 0) params.set("sources", [...selected].join(","));
    params.set("provider", provider);
    const q = params.toString();
    return q ? `/launch?${q}` : "/launch";
  }, [topic, region, selected, provider]);

  return (
    <section className="hero page" id="launcher">
      <div className="hero-grid">
        <div>
          <div className="hero-meta">
            <Pill kind="warm" live>scraping live</Pill>
            <Pill kind="cool">HK · phase 1</Pill>
            <Pill>5 sources · 19 regions</Pill>
            <Pill>BGE-M3 · UMAP · HDBSCAN · Claude</Pill>
          </div>
          <h1 className="hero-h1">
            Personas grounded in{" "}
            <span className="em">what people actually say</span> online.
          </h1>
          <p className="hero-sub">
            Type a brand or product. The pipeline scrapes public discussion in
            the target region, embeds it multilingually with BGE-M3, clusters
            with UMAP + HDBSCAN, and asks Claude to synthesize personas and
            journey maps — where{" "}
            <em>every claim cites a real post</em>.
          </p>

          <div className="launcher">
            <input
              ref={inputRef}
              type="text"
              placeholder='Topic — e.g. "MTR Mobile" or "Octopus card"'
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
            />
            <Link
              className="btn btn-primary"
              href={launchHref}
              aria-disabled={!topic.trim() || selected.size === 0}
              onClick={(e) => {
                if (!topic.trim() || selected.size === 0) e.preventDefault();
              }}
              style={
                !topic.trim() || selected.size === 0
                  ? { opacity: 0.5, pointerEvents: "none" }
                  : undefined
              }
            >
              Start run →
            </Link>
          </div>

          <div className="launcher-row">
            <span className="label">try:</span>
            {SUGGESTED_TOPICS.map((t) => (
              <button key={t} className="chip" onClick={() => setTopic(t)}>
                {t}
              </button>
            ))}
          </div>

          <div className="launcher-row" style={{ marginTop: 14 }}>
            <span className="label">region</span>
            {REGIONS.map((r) => (
              <button
                key={r}
                className={`chip ${region === r ? "on" : ""}`}
                onClick={() => setRegion(r)}
              >
                {r}
              </button>
            ))}
            <span style={{ flexBasis: "100%" }} />
            <span className="label">sources</span>
            {SOURCES.map((s) => {
              const on = selected.has(s.id);
              const prohibited = s.tos === "prohibited";
              return (
                <button
                  key={s.id}
                  className={`chip ${on ? "on" : ""} ${prohibited && on ? "warn" : ""}`}
                  onClick={() => toggle(s.id)}
                  title={s.notes}
                >
                  {on ? "✓" : "○"} {s.id}
                  {prohibited && " ⚠"}
                </button>
              );
            })}
          </div>

          <div className="launcher-row" style={{ marginTop: 14 }}>
            <span className="label">llm</span>
            {PROVIDERS.map((p) => (
              <button
                key={p.id}
                className={`chip ${provider === p.id ? "on" : ""}`}
                onClick={() => setProvider(p.id)}
                title={p.hint}
              >
                {provider === p.id ? "✓" : "○"} {p.label}
              </button>
            ))}
          </div>

          <div
            className="launcher-row"
            style={{ marginTop: 14, color: "var(--ink-4)" }}
          >
            <span className="mono" style={{ fontSize: 11 }}>
              ⌘K to launch · ⌥ for opt-in sources · all data &lt; 90d
            </span>
          </div>
        </div>

        <HeroPreview
          topic={topic || "MTR Mobile"}
          region={region}
          selected={selected}
        />
      </div>
    </section>
  );
}

function HeroPreview({
  topic,
  region,
  selected,
}: {
  topic: string;
  region: string;
  selected: Set<string>;
}) {
  // Double the stream so the CSS marquee loop is seamless.
  const stream = useMemo(() => [...SCRAPE_STREAM, ...SCRAPE_STREAM], []);
  const filtered = stream.filter((s) => selected.has(s.src));

  // Static ingested count — illustrative, not live. The prototype
  // randomised this every render which caused hydration mismatches
  // in React; we pin it instead.
  const ingested = 1234;

  return (
    <div className="preview">
      <div className="preview-head">
        <div className="breadcrumb">
          mkt scrape&nbsp;&nbsp;<b>{topic}</b>&nbsp;&nbsp;·&nbsp;&nbsp;{region}
          &nbsp;&nbsp;·&nbsp;&nbsp;{selected.size} sources
        </div>
        <Pill live kind="warm">
          streaming
        </Pill>
      </div>
      <div className="stream">
        <div className="stream-track">
          {filtered.length === 0 ? (
            <div className="post" style={{ gridTemplateColumns: "1fr" }}>
              <div
                className="body"
                style={{ color: "var(--ink-4)", fontStyle: "italic" }}
              >
                Pick at least one source.
              </div>
            </div>
          ) : (
            filtered.map((p, i) => (
              <div className="post" key={i}>
                <div className="src">{p.src}</div>
                <div className="body">
                  <span className="lang">[{p.lang}]</span>
                  {p.body}
                </div>
                <div className="ts">{p.ts}</div>
              </div>
            ))
          )}
        </div>
      </div>
      <div className="preview-foot">
        <div>
          <b>{ingested.toLocaleString()}</b>&nbsp;posts ingested &nbsp;·&nbsp;{" "}
          <b>{filtered.length}</b>/sec
        </div>
        <div>dedup: sha256(author + salt)</div>
      </div>
    </div>
  );
}

// ── Marquee ──────────────────────────────────────────────────────────────

const MARQUEE_ITEMS = [
  "LIHKG · 428k posts · zh-HK",
  "reddit_old · 1.2M · 13 langs",
  "app_store_hk · 94k · iTunes RSS",
  "openrice · opt-in · ⚠ ToS prohibited",
  "google_play_hk · opt-in · ⚠ ToS prohibited",
  "BGE-M3 · 1024-d multilingual",
  "DuckDB + VSS · HNSW · cosine",
  "UMAP n=15 · HDBSCAN min_cluster=8",
  "Claude Sonnet 4 · DeepSeek-chat",
  "Eval gate · min_recovery 0.60",
  "Author hash: sha256(name + salt)",
  "1–3 req/sec/domain · robots.txt strict",
];

function Marquee() {
  const row = [...MARQUEE_ITEMS, ...MARQUEE_ITEMS];
  return (
    <div className="marquee">
      <div className="marquee-row">
        {row.map((s, i) => (
          <span className="item" key={i}>
            <span style={{ color: "var(--ink-5)" }}>◆</span>
            <span>{s}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Pipeline ─────────────────────────────────────────────────────────────

const STAGES = [
  {
    n: "01",
    name: "Scrape",
    desc: "Source-pluggable framework. Polite httpx + Playwright, robots-aware, fixture-tested.",
    cmd: 'mkt scrape --topic "MTR Mobile" --region HK --sources lihkg,reddit_old --since 90d',
    out: [
      ["lihkg", "412 posts", "ok"],
      ["reddit_old", "807 posts", "ok"],
      ["dedup", "→ 1142 unique", "ok"],
    ] as const,
  },
  {
    n: "02",
    name: "Embed",
    desc: "BAAI/bge-m3 multilingual via sentence-transformers. Stored in DuckDB with the VSS extension.",
    cmd: 'mkt embed --topic "MTR Mobile" --region HK',
    out: [
      ["model", "bge-m3 · 1024-d", "v"],
      ["index", "hnsw · ef=200", "v"],
      ["throughput", "240 docs/sec", "ok"],
    ] as const,
  },
  {
    n: "03",
    name: "Cluster",
    desc: "UMAP → HDBSCAN. c-TF-IDF keyword extraction per cluster for interpretable labels.",
    cmd: 'mkt cluster --topic "MTR Mobile" --region HK && mkt diag',
    out: [
      ["umap", "n_neighbors=15", "v"],
      ["hdbscan", "min_cluster=8", "v"],
      ["clusters", "12 (noise 6%)", "ok"],
    ] as const,
  },
  {
    n: "04",
    name: "Synthesize",
    desc: "Claude generates persona + journey JSON per cluster. Every claim must cite a doc_id from the evidence pack.",
    cmd: 'mkt synthesize --topic "MTR Mobile" --provider anthropic',
    out: [
      ["personas", "4 generated", "ok"],
      ["journeys", "4 stages × 6 cols", "ok"],
      ["uncited", "0 claims", "ok"],
    ] as const,
  },
];

function Pipeline() {
  return (
    <section className="section" id="pipeline">
      <div className="page">
        <div className="section-head">
          <div>
            <div className="eyebrow">pipeline</div>
            <h2 className="section-h">
              Public posts <span className="em">in</span>, evidence-cited
              personas <span className="em">out</span>.
            </h2>
          </div>
          <p className="section-lede">
            Four stages, each its own CLI command. Every output is reproducible
            from a frozen <span className="mono">_run.json</span> sidecar.
          </p>
        </div>

        <div className="progression">
          <span>00:00:00</span>
          <div className="bar">
            <div className="fill" />
          </div>
          <span className="mono">~9 min · 1142 docs · 4 personas</span>
        </div>

        <div className="pipeline">
          {STAGES.map((s) => (
            <div className="stage" key={s.n}>
              <div className="stage-n">{s.n} ─────────</div>
              <div className="stage-name">{s.name}</div>
              <div className="stage-desc">{s.desc}</div>
              <div className="stage-cmd">
                <span className="prompt">$</span>
                {s.cmd}
              </div>
              <div className="stage-out">
                {s.out.map(([k, v, kind], i) => (
                  <div key={i}>
                    <span style={{ color: "var(--ink-4)" }}>
                      {k.padEnd(11, " ")}
                    </span>
                    <span className={kind === "ok" ? "ok" : "v"}>{v}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── Personas ─────────────────────────────────────────────────────────────

function Personas() {
  return (
    <section className="section" id="personas">
      <div className="page">
        <div className="section-head">
          <div>
            <div className="eyebrow">
              personas · run_8af2e1 · mtr mobile · hk
            </div>
            <h2 className="section-h">
              Three of four personas{" "}
              <span className="em">from a single run.</span>
            </h2>
          </div>
          <p className="section-lede">
            Each card is one HDBSCAN cluster. Confidence drops with unverified
            buckets. Pain points are sorted by severity; click any to see the
            cited post.
          </p>
        </div>

        <div className="personas">
          {PERSONAS.map((p) => (
            <article className="p-card" key={p.id}>
              <div className="p-head">
                <div>
                  <div className="p-tag">cluster_{p.id.slice(2)}</div>
                  <div className="p-name">{p.name}</div>
                </div>
                <ConfidenceRing value={p.confidence} />
              </div>
              <p className="p-line">{p.one_liner}</p>

              <div>
                <div className="label" style={{ marginBottom: 8 }}>
                  Top pain points ({p.pains.length})
                </div>
                <div className="p-pains">
                  {p.pains.map((pn, i) => (
                    <div className={`p-pain ${pn.sev}`} key={i}>
                      <div className="sev" />
                      <div>
                        {pn.text}
                        <span className="cite"> · {pn.cite}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="p-foot">
                <Pill kind={p.coverage === "balanced" ? "good" : "warm"}>
                  {p.coverage}
                </Pill>
                <Pill>{p.cluster_size} posts</Pill>
                <Pill>{p.sources.length} src</Pill>
                <span className="grow" />
                <Link className="view" href="/launch">
                  View →
                </Link>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── Journey ──────────────────────────────────────────────────────────────

function intensityBar(v: number): string {
  const blocks = "▁▂▃▄▅▆▇█";
  const idx = Math.min(blocks.length - 1, Math.floor(v * blocks.length));
  return blocks[idx].repeat(5);
}

function Journey() {
  const j = JOURNEY;
  return (
    <section className="section" id="journey">
      <div className="page">
        <div className="section-head">
          <div>
            <div className="eyebrow">journey map · persona: margaret</div>
            <h2 className="section-h">
              Six stages, five lenses,{" "}
              <span className="em">one post per claim.</span>
            </h2>
          </div>
          <p className="section-lede">
            Stages auto-mark <span className="mono">thin</span> when fewer
            than three posts cover them. Cells without evidence stay empty —
            no LLM confabulation.
          </p>
        </div>

        <div className="journey">
          <div className="j-head">
            <div></div>
            {j.stages.map((s) => (
              <div className="j-stage" key={s.name}>
                {s.name}
                <span className="label">
                  {s.cov === "thin" ? "thin · 2 posts" : s.cov}
                </span>
              </div>
            ))}
          </div>

          <JRow label="Touchpoints" cells={j.rows.touchpoints}
                stages={j.stages} render={(v) => v as React.ReactNode} />
          <JRow label="User actions" cells={j.rows.actions}
                stages={j.stages} render={(v) => v as React.ReactNode} />
          <JRow
            label="Emotions"
            cells={j.rows.emotions}
            stages={j.stages}
            render={(e) => {
              const em = e as Emotion;
              return (
                <div className="emo">
                  <span style={{ textTransform: "capitalize" }}>{em.l}</span>
                  <span className="bar">{intensityBar(em.i)}</span>
                </div>
              );
            }}
          />
          <JRow
            label="Frictions"
            cells={j.rows.frictions}
            stages={j.stages}
            render={(f) => {
              const fr = f as Friction;
              return (
                <div className={`item ${fr.sev}`}>
                  <span className="sev" />
                  <span>{fr.text}</span>
                </div>
              );
            }}
          />
          <JRow
            label="Opportunities"
            cells={j.rows.opportunities}
            stages={j.stages}
            render={(v) => (
              <div style={{ color: "var(--good)" }}>↑ {v as string}</div>
            )}
            last
          />
        </div>
      </div>
    </section>
  );
}

function JRow<T>({
  label,
  cells,
  stages,
  render,
  last,
}: {
  label: string;
  cells: T[];
  stages: typeof JOURNEY.stages;
  render: (cell: T) => React.ReactNode;
  last?: boolean;
}) {
  return (
    <div className="j-row" style={last ? { borderBottom: 0 } : undefined}>
      <div className="j-row-label">{label}</div>
      {cells.map((c, i) => (
        <div
          className={`j-cell ${stages[i].cov === "thin" ? "muted" : ""}`}
          key={i}
        >
          {render(c)}
        </div>
      ))}
    </div>
  );
}

// ── Sources matrix ───────────────────────────────────────────────────────

function Sources() {
  return (
    <section className="section" id="sources">
      <div className="page">
        <div className="section-head">
          <div>
            <div className="eyebrow">data sources · 19 regions · 5 wired</div>
            <h2 className="section-h">
              Five free, public sources.{" "}
              <span className="em">Two are ToS-flagged.</span>
            </h2>
          </div>
          <p className="section-lede">
            No API keys, no OAuth, no developer registration. Sources whose
            Terms of Service prohibit automated access are tagged{" "}
            <span className="mono">prohibited</span> — never silently included.
          </p>
        </div>

        <div className="matrix">
          <div className="m-head">
            <div>Source</div>
            <div>Region</div>
            <div>Access</div>
            <div>ToS stance</div>
            <div>Notes</div>
          </div>
          {SOURCES.map((s) => (
            <div className="m-row" key={s.id}>
              <div>
                <div className="src-id">{s.id}</div>
                <div className="src-meta">
                  {s.posts} · {s.lang}
                </div>
              </div>
              <div className="mono">{s.region}</div>
              <div className="mono" style={{ fontSize: 12 }}>
                {s.access}
              </div>
              <div>
                {s.tos === "prohibited" ? (
                  <Pill kind="warm">⚠ prohibited</Pill>
                ) : s.tos === "allowed_with_conditions" ? (
                  <Pill kind="good">conditional</Pill>
                ) : (
                  <Pill>silent</Pill>
                )}
              </div>
              <div className="src-notes">{s.notes}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── Eval ─────────────────────────────────────────────────────────────────

function EvalSection() {
  return (
    <section className="section" id="eval">
      <div className="page">
        <div className="section-head">
          <div>
            <div className="eyebrow">eval · five frozen fixtures · ci-gated</div>
            <h2 className="section-h">
              We measure ourselves{" "}
              <span className="em">against the posts we missed.</span>
            </h2>
          </div>
          <p className="section-lede">
            Hand-curated expected pain points per fixture.{" "}
            <span className="mono">recovery_rate</span> is the fraction
            recovered; <span className="mono">--min-recovery 0.60</span> gates
            CI.
          </p>
        </div>

        <div className="eval-grid">
          <div className="eval-card">
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: 12,
              }}
            >
              <div className="label">recovery_rate per fixture</div>
              <div
                className="mono"
                style={{ fontSize: 11, color: "var(--ink-3)" }}
              >
                mean:{" "}
                <span style={{ color: "var(--good)" }}>0.79</span> · gate:
                0.60
              </div>
            </div>
            {EVAL_RESULTS.map((r) => (
              <div className="eval-row" key={r.name}>
                <div className="fname">{r.name}</div>
                <div className="meter">
                  <div
                    className="fill"
                    style={{ transform: `scaleX(${r.recovery})` }}
                  />
                </div>
                <div className="v">{r.recovery.toFixed(2)}</div>
              </div>
            ))}
            <div
              style={{
                display: "flex",
                gap: 8,
                marginTop: 14,
                alignItems: "center",
              }}
            >
              <code
                className="mono"
                style={{
                  fontSize: 11.5,
                  background: "var(--bg-sub)",
                  padding: "5px 9px",
                  border: "1px solid var(--line)",
                  borderRadius: 4,
                  color: "var(--ink-2)",
                }}
              >
                $ mkt eval --provider mock --min-recovery 0.60 --json
              </code>
            </div>
          </div>

          <div>
            <div className="stat-grid">
              <Stat
                v="1142"
                u="docs"
                l="ingested"
                d="MTR Mobile · 90d · HK"
              />
              <Stat
                v="0.79"
                l="mean recovery"
                d="across 5 eval fixtures"
              />
              <Stat
                v="100"
                u="%"
                l="claims cited"
                d="zero uncited assertions"
              />
              <Stat
                v="$0.14"
                l="median run cost"
                d="DeepSeek · ~9 min"
              />
            </div>
            <div
              style={{
                marginTop: 14,
                fontSize: 12,
                color: "var(--ink-3)",
                lineHeight: 1.5,
              }}
            >
              All scrapers ship with HTML fixtures.{" "}
              <span className="mono">mkt scrape-doctor</span> runs every
              parser against its frozen snapshot to catch source-side drift
              before a run starts.
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function Stat({
  v,
  u,
  l,
  d,
}: {
  v: string;
  u?: string;
  l: string;
  d: string;
}) {
  return (
    <div className="stat">
      <div className="v">
        {v}
        {u && <span className="unit">{u}</span>}
      </div>
      <div className="l">{l}</div>
      <div className="d">{d}</div>
    </div>
  );
}

// ── Recent runs + CLI ────────────────────────────────────────────────────

function RecentAndCli() {
  return (
    <section className="section" id="cli">
      <div className="page">
        <div className="section-head">
          <div>
            <div className="eyebrow">a real run, end to end</div>
            <h2 className="section-h">
              It&apos;s a CLI first.{" "}
              <span className="em">The UI just makes it easier to share.</span>
            </h2>
          </div>
          <p className="section-lede">
            One Anthropic key (or none, if you&apos;re on DeepSeek). Everything
            else is local — DuckDB for vectors, SQLite for dedup, JSON for
            posts.
          </p>
        </div>

        <div className="cli-grid">
          <div className="cli">
            <div className="cli-head">
              <div className="traffic">
                <span />
                <span />
                <span />
              </div>
              <span>~/market-analysis</span>
              <span style={{ marginLeft: "auto" }}>zsh</span>
            </div>
            <div className="cli-body">
              <div>
                <span className="prompt">$</span>
                <span className="cmd">
                  mkt analyze --topic &quot;MTR Mobile&quot; --region HK
                  --sources lihkg,reddit_old --since 90d
                </span>
              </div>
              <div className="out">
                <div>
                  <span className="ok">✓</span> scrape{" "}
                  <span className="em">lihkg</span> 412 ·{" "}
                  <span className="em">reddit_old</span> 807 · dedup → 1142
                </div>
                <div>
                  <span className="ok">✓</span> embed bge-m3 · 1142 docs · 4.8s
                </div>
                <div>
                  <span className="ok">✓</span> cluster umap → hdbscan · 12
                  clusters · noise 6%
                </div>
                <div>
                  <span className="ok">✓</span> diag c-tf-idf labels written
                </div>
                <div>
                  <span className="ok">✓</span> synthesize claude-sonnet-4 · 4
                  personas, 4 journeys
                </div>
                <div>
                  <span className="warn">⚠</span> tabelog skipped (region
                  mismatch)
                </div>
                <div>
                  write → data/mtr_mobile_HK_persona.json (24kB)
                </div>
                <div>
                  write → data/mtr_mobile_HK_journey.json (41kB)
                </div>
                <div>
                  <span className="ok">✓</span> done 9m 12s · $0.14 · run_id{" "}
                  <span className="em">run_8af2e1</span>
                </div>
              </div>
              <div style={{ marginTop: 10 }}>
                <span className="prompt">$</span>
                <span className="cmd">
                  mkt export --run run_8af2e1 --csv
                </span>
              </div>
            </div>
          </div>

          <div className="card">
            <div
              style={{
                padding: "14px 16px",
                borderBottom: "1px solid var(--line)",
                display: "flex",
                justifyContent: "space-between",
              }}
            >
              <div style={{ fontWeight: 500 }}>Recent runs</div>
              <span
                className="mono"
                style={{ fontSize: 11, color: "var(--ink-4)" }}
              >
                GET /runs · polling 3s
              </span>
            </div>
            <div>
              {RECENT_RUNS.map((r) => (
                <div key={r.id} className="recent-row">
                  <RunIcon status={r.status} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 500 }}>{r.topic}</div>
                    <div
                      className="mono"
                      style={{ fontSize: 11, color: "var(--ink-4)" }}
                    >
                      {r.region} · {r.sources} src
                      {r.personas > 0 && ` · ${r.personas} personas`}
                      {r.stage && ` · stage: ${r.stage}`}
                    </div>
                  </div>
                  <div
                    className="mono"
                    style={{ fontSize: 11, color: "var(--ink-4)" }}
                  >
                    {r.when}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function RunIcon({ status }: { status: "done" | "running" | "failed" }) {
  if (status === "done")
    return <span style={{ color: "var(--good)" }}>●</span>;
  if (status === "failed")
    return <span style={{ color: "var(--bad)" }}>●</span>;
  return <span className="recent-spinner" />;
}

// ── Privacy ──────────────────────────────────────────────────────────────

const PRIVACY_POINTS: [string, string][] = [
  [
    "Author names hashed",
    "sha256(name + per-install salt). Raw names never persisted — not in records, not in logs.",
  ],
  [
    "robots.txt strict",
    "Hard-fail on 403. Honest User-Agent header. 1–3 req/sec/domain.",
  ],
  [
    "No API registration",
    "Free, public HTTP endpoints only. The Anthropic key is the single paid-service exception.",
  ],
  [
    "ToS stance per source",
    "Sources whose ToS prohibits scraping must be passed explicitly on --sources. Never silently included.",
  ],
  [
    "Reproducible runs",
    "Every output is regenerable from a frozen _run.json sidecar plus the same fixture pack.",
  ],
];

function Privacy() {
  return (
    <section className="section">
      <div className="page privacy-grid">
        <div>
          <div className="eyebrow">privacy &amp; constraints</div>
          <h2 className="section-h" style={{ marginTop: 8 }}>
            Polite by <span className="em">construction.</span>
          </h2>
          <p className="section-lede" style={{ marginTop: 16, maxWidth: 540 }}>
            We don&apos;t scrape harder than we&apos;d want to be scraped. The
            rules below are baked into the framework — not policy, code.
          </p>
        </div>

        <ul className="privacy-list">
          {PRIVACY_POINTS.map(([t, d], i) => (
            <li key={i}>
              <div className="ord">{String(i + 1).padStart(2, "0")}</div>
              <div>
                <div className="title">{t}</div>
                <div className="body">{d}</div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

// ── CTA ──────────────────────────────────────────────────────────────────

function CTA() {
  return (
    <section className="section" style={{ padding: "80px 0" }}>
      <div className="page" style={{ textAlign: "center" }}>
        <div className="eyebrow" style={{ marginBottom: 14 }}>
          get started
        </div>
        <h2 className="cta-h">
          <span className="em">One topic.</span> Nine minutes. Four personas.
        </h2>
        <p className="cta-lede">
          Install with <span className="mono">make install</span>, set your
          author hash salt, run.
        </p>
        <div
          style={{
            display: "flex",
            gap: 10,
            justifyContent: "center",
            flexWrap: "wrap",
          }}
        >
          <Link
            className="btn btn-primary"
            href="/launch"
            style={{ height: 40, padding: "0 18px" }}
          >
            Start a run →
          </Link>
          <a
            className="btn"
            href="https://github.com/Derek2352/Market-Analysis/blob/main/PROJECT_PLAN.md"
            target="_blank"
            rel="noopener"
            style={{ height: 40, padding: "0 18px" }}
          >
            Read PROJECT_PLAN.md ↗
          </a>
        </div>
      </div>
    </section>
  );
}

// ── Footer ───────────────────────────────────────────────────────────────

function LandingFooter() {
  return (
    <footer className="l-footer">
      <div className="page footer-row">
        <div>
          <div className="brand" style={{ marginBottom: 10 }}>
            <div className="brand-mark">m/</div>
            <div>Market Analytics</div>
          </div>
          <div style={{ color: "var(--ink-4)" }}>
            Personas + journeys from public discussion. HK-first, region-aware.
          </div>
          <div
            className="mono"
            style={{ fontSize: 11, color: "var(--ink-5)", marginTop: 8 }}
          >
            MarketAnalyticsBot/0.1 (research)
          </div>
        </div>
        <div className="footer-links">
          <a href="#pipeline">pipeline</a>
          <a href="#sources">sources</a>
          <a href="#eval">eval</a>
          <a
            href="https://github.com/Derek2352/Market-Analysis/blob/main/PROJECT_PLAN.md"
            target="_blank"
            rel="noopener"
          >
            PROJECT_PLAN.md ↗
          </a>
          <a
            href="https://github.com/Derek2352/Market-Analysis"
            target="_blank"
            rel="noopener"
          >
            github ↗
          </a>
        </div>
      </div>
    </footer>
  );
}
