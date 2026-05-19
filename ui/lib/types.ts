// Mirror of the FastAPI Pydantic models. Hand-maintained to keep the UI
// strongly typed without a code generator. Anything new on the backend
// needs a matching line here.

// GET /regions response shape — drives the launcher's region+sources UI.
export interface SourceInfo {
  source_id: string;
  category: string;
  priority: number;
  default_enabled: boolean;
  tos_scraping_stance:
    | "silent"
    | "allowed_with_conditions"
    | "prohibited"
    | "unknown";
  last_verified_working: string | null;
  notes: string;
}

export interface RegionInfo {
  region_id: string;
  display_name: string;
  primary_languages: string[];
  default_sources: SourceInfo[];
  opt_in_sources: SourceInfo[];
}

export type RunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type PipelineStage = "scrape" | "embed" | "cluster" | "synthesize";
export type Provider = "anthropic" | "deepseek";

// Region codes the API has actually wired. New regions added on the
// backend show up in GET /regions automatically — Region is a soft
// type that accepts those without a UI rebuild.
export type Region = string;

export interface RunRequest {
  topic: string;
  region: Region;
  sources: string[];
  since_days: number;
  provider: Provider;
  max_cost_usd?: number;
  force?: boolean;
  limit_per_source?: number;
}

export interface RunCreated {
  run_id: string;
  status: "queued";
  stream_url: string;
}

export interface RunCounts {
  posts: number;
  clusters: number;
  personas: number;
  journeys: number;
}

export interface StageProgress {
  stage: PipelineStage;
  pct: number;
  message: string;
}

export interface RunSummary {
  run_id: string;
  topic: string;
  region: string;
  sources: string[];
  status: RunStatus;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  counts: RunCounts;
}

export interface RunDetail extends RunSummary {
  progress: StageProgress | null;
  params: Record<string, unknown>;
}

export interface EvidenceClaim {
  claim: string;
  evidence: string[];
  severity?: "high" | "medium" | "low" | null;
  // Phase 6+ quantitative grounding — computed pre-LLM-call from the
  // evidence pack and backfilled after validation. Defaults to 0 / [].
  mentioned_by_n_users?: number;
  pct_of_cluster?: number;             // 0.0 – 1.0
  sentiment_scores?: Record<string, number>;
  contested_by?: string[];             // doc_ids that contradict this claim
}

export interface ClaimList {
  claims: EvidenceClaim[];
  coverage: "ok" | "unverified";
}

// data_source_coverage tier — populated server-side per persona/journey.
export type CoverageTier =
  | "single-perspective"
  | "limited"
  | "balanced"
  | "high";

export interface RepresentativeQuote {
  text_original: string;
  text_translated?: string | null;
  lang: string;
  source: string;
  url: string;
  doc_id: string;
}

export interface DataSourceCoverage {
  categories_present: string[];
  categories_missing: string[];
  sources_used: string[];
  doc_counts: Record<string, number>;
  bias_warning: string;
  // Phase 6 additions; older personas omit these.
  category_count?: number;
  coverage_tier?: CoverageTier;
}

export interface Persona {
  id: string;
  run_id: string;
  cluster_id: string;
  name: string;
  one_liner: string;
  language: string;
  demographics: Record<string, unknown> & { evidence?: string[] };
  goals: ClaimList;
  motivations: ClaimList;
  pain_points: ClaimList;
  preferred_channels: ClaimList;
  behaviors: ClaimList;
  representative_quotes: RepresentativeQuote[];
  data_source_coverage: DataSourceCoverage;
  confidence: number;
  cluster_size: number;
  generated_at: string | null;
  model: string;
  provider: string;
}

export interface EmotionPoint {
  label: string;
  intensity: number;
  evidence: string[];
}

export interface JourneyStage {
  stage:
    | "Awareness"
    | "Consideration"
    | "Decision"
    | "Onboarding"
    | "Use"
    | "Loyalty/Churn";
  touchpoints: ClaimList;
  user_actions: ClaimList;
  emotions: EmotionPoint[];
  frictions: ClaimList;
  opportunities: ClaimList;
  coverage: "ok" | "thin" | "none" | "unverified";
}

export interface JourneyMap {
  id: string;
  run_id: string;
  persona_id: string;
  language: string;
  data_source_coverage: DataSourceCoverage;
  stages: JourneyStage[];
  generated_at: string | null;
  model: string;
  provider: string;
}

export interface DocResponse {
  doc_id: string;
  post_id: string;
  source: string;
  url: string;
  title: string | null;
  body: string;
  language: string;
  posted_at: string | null;
}

export type SSEEvent =
  | { type: "queued"; data: { run_id: string; topic: string; region: string; sources: string[] } }
  | { type: "stage_start"; data: { stage: PipelineStage; message: string } }
  | { type: "progress"; data: { stage: PipelineStage; pct: number; message: string } }
  | { type: "stage_done"; data: { stage: PipelineStage; message: string } }
  | { type: "done"; data: { run_id: string; personas: number; journeys: number; cost_usd: number; counts: RunCounts } }
  | { type: "error"; data: { run_id: string; stage: PipelineStage | null; error: string } }
  | { type: "cancelled"; data: { run_id: string } };
