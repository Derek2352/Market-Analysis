/**
 * Illustrative landing-page data, drawn from the codebase's vocabulary.
 *
 * Not pulled live from the FastAPI backend on purpose — the landing is a
 * marketing surface, not a live view. The launcher at /launch is where
 * the actual app state lives.
 */

export type Source = {
  id: string;
  region: string;
  access: string;
  tos: "silent" | "allowed_with_conditions" | "prohibited";
  posts: string;
  lang: string;
  notes: string;
};

export const SOURCES: Source[] = [
  {
    id: "lihkg",
    region: "HK",
    access: "JSON",
    tos: "silent",
    posts: "428k",
    lang: "zh-HK",
    notes: "Cantonese-heavy forum. Canonical HK source for Phase 1.",
  },
  {
    id: "reddit_old",
    region: "global",
    access: "HTML",
    tos: "allowed_with_conditions",
    posts: "1.2M",
    lang: "en + 12",
    notes: "old.reddit.com web UI. No API key, no OAuth. Rate-limited at 1 req/s.",
  },
  {
    id: "app_store_hk",
    region: "HK",
    access: "iTunes RSS",
    tos: "silent",
    posts: "94k",
    lang: "zh-HK, en",
    notes: "App reviews — reference implementation for the SourceScraper protocol.",
  },
  {
    id: "openrice",
    region: "HK",
    access: "HTML + Playwright",
    tos: "prohibited",
    posts: "opt-in",
    lang: "zh-HK",
    notes: "Restaurant reviews. ToS prohibits automated access — opt-in only.",
  },
  {
    id: "google_play_hk",
    region: "HK",
    access: "google-play-scraper",
    tos: "prohibited",
    posts: "opt-in",
    lang: "zh-HK, en",
    notes: "Android app reviews via the anonymous internal API. ToS-flagged.",
  },
];

export type ScrapePost = {
  src: string;
  lang: string;
  body: string;
  ts: string;
};

export const SCRAPE_STREAM: ScrapePost[] = [
  { src: "lihkg",        lang: "zh", body: "登入次次都要等成分鐘，搞到我返工差啲遲到", ts: "08:42" },
  { src: "reddit_old",   lang: "en", body: "tap-to-go on the MTR Mobile is way more reliable than QR honestly", ts: "08:42" },
  { src: "app_store_hk", lang: "zh", body: "新版本之後 octopus 加值成日 timeout，要試三四次先得", ts: "08:41" },
  { src: "lihkg",        lang: "zh", body: "尋日地鐵壞，個 app 完全冇通知，要靠睇 ig story 先知", ts: "08:41" },
  { src: "reddit_old",   lang: "en", body: "anyone else getting 'session expired' every time they open the app at the gate?", ts: "08:40" },
  { src: "app_store_hk", lang: "en", body: "the route planner is great but it never accounts for typhoon-8 service changes", ts: "08:40" },
  { src: "lihkg",        lang: "zh", body: "想 set 自動增值，但個 flow 入到一半就 hang 機，client 同支付寶都係", ts: "08:39" },
  { src: "reddit_old",   lang: "en", body: "love that they finally added apple wallet support but loyalty points don't sync", ts: "08:39" },
  { src: "lihkg",        lang: "zh", body: "張 octopus 過期咗，app 完全唔提我，去到閘口先發現", ts: "08:38" },
  { src: "app_store_hk", lang: "zh", body: "用咗三年，依然係香港最穩陣嗰個交通 app，雖然 UI 老舊", ts: "08:38" },
];

export type Pain = { sev: "high" | "med" | "low"; text: string; cite: string };

export type LandingPersona = {
  id: string;
  name: string;
  one_liner: string;
  cluster_size: number;
  confidence: number;
  coverage: "balanced" | "limited" | "thin";
  sources: string[];
  pains: Pain[];
};

export const PERSONAS: LandingPersona[] = [
  {
    id: "p_commuter_01",
    name: "Margaret, the typhoon commuter",
    one_liner:
      "Tsuen Wan → Central daily. Treats the app as a service-disruption radar before her train, not a route planner.",
    cluster_size: 412,
    confidence: 0.82,
    coverage: "balanced",
    sources: ["lihkg", "reddit_old", "app_store_hk"],
    pains: [
      { sev: "high", text: "No push when a line goes amber — finds out from IG stories", cite: "lh-198342" },
      { sev: "med",  text: "Typhoon-8 service tables don't reflow her saved commute",    cite: "rd-77a14b" },
      { sev: "low",  text: "Octopus expiry shows at the gate, not in-app",                cite: "as-9c1d22" },
    ],
  },
  {
    id: "p_topup_02",
    name: "Wai-Lun, the auto-topup defector",
    one_liner:
      "Tried to wire AlipayHK auto-topup three times. Each attempt hung mid-flow. Now manually loads ¥50 weekly.",
    cluster_size: 268,
    confidence: 0.71,
    coverage: "limited",
    sources: ["lihkg", "app_store_hk"],
    pains: [
      { sev: "high", text: "Auto-topup setup hangs on payment-method binding",     cite: "lh-401b09" },
      { sev: "high", text: "No confirmation when a topup partially succeeds",       cite: "as-37e0c2" },
      { sev: "med",  text: "Receipts in Cantonese mix simplified and traditional",  cite: "lh-22f55a" },
    ],
  },
  {
    id: "p_visitor_03",
    name: "Priya, the 72-hour visitor",
    one_liner:
      "Lands at HKIA, needs the app working before clearing customs. Apple Wallet helps; loyalty does not.",
    cluster_size: 189,
    confidence: 0.66,
    coverage: "balanced",
    sources: ["reddit_old", "app_store_hk"],
    pains: [
      { sev: "med", text: "Loyalty points don't sync to Wallet pass",            cite: "rd-13ea88" },
      { sev: "med", text: "Language toggle resets between sessions",              cite: "as-4d2110" },
      { sev: "low", text: "Estimated wait time ignores platform crowding",        cite: "rd-902c14" },
    ],
  },
];

export type JourneyStage = { name: string; cov: "ok" | "thin" };
export type Friction = { sev: "high" | "med" | "low"; text: string };
export type Emotion = { l: string; i: number };

export const JOURNEY = {
  stages: [
    { name: "Awareness",       cov: "ok"   as const },
    { name: "Consideration",   cov: "ok"   as const },
    { name: "Decision",        cov: "thin" as const },
    { name: "Onboarding",      cov: "ok"   as const },
    { name: "Use",             cov: "ok"   as const },
    { name: "Loyalty / Churn", cov: "ok"   as const },
  ],
  rows: {
    touchpoints: [
      "Service alerts on IG",
      "Friend recommendations, app store ratings",
      "Octopus card on-boarding leaflet",
      "First topup, language picker",
      "Daily gate tap, route planner",
      "App update prompt, push permission",
    ],
    actions: [
      "Searches '港鐵 app' on LIHKG to see if it's safe",
      "Compares route planner vs Citymapper",
      "Decides between Octopus and credit-card mode",
      "Links AlipayHK or sets up auto-topup",
      "Taps in, checks remaining balance, plans transfer",
      "Reviews loyalty rewards, decides to keep app",
    ],
    emotions: [
      { l: "skeptical",  i: 0.45 },
      { l: "curious",    i: 0.62 },
      { l: "anxious",    i: 0.78 },
      { l: "frustrated", i: 0.71 },
      { l: "neutral",    i: 0.40 },
      { l: "ambivalent", i: 0.55 },
    ] as Emotion[],
    frictions: [
      { sev: "med",  text: "Service-alert credibility is low without push" },
      { sev: "low",  text: "App store reviews are 60% pre-redesign" },
      { sev: "high", text: "Mode choice unclear — no explainer" },
      { sev: "high", text: "Auto-topup binding silently fails" },
      { sev: "med",  text: "Session-expired errors at the gate" },
      { sev: "med",  text: "Loyalty UI buried 3 taps deep" },
    ] as Friction[],
    opportunities: [
      "First-class IG / WhatsApp service-status share-cards",
      "In-store TVCs vs comparison page",
      "Onboarding mode-picker with cost worked example",
      "Inline diagnostics on failed payment-method binding",
      "Background session refresh on Wi-Fi",
      "Wallet-native loyalty rendering",
    ],
  },
};

export type EvalResult = { name: string; recovery: number; coverage: number; posts: number };

export const EVAL_RESULTS: EvalResult[] = [
  { name: "whatsapp_hk",   recovery: 0.83, coverage: 3.4, posts: 12 },
  { name: "mtr_mobile_hk", recovery: 0.91, coverage: 3.8, posts: 12 },
  { name: "tabelog_jp",    recovery: 0.67, coverage: 2.6, posts: 12 },
  { name: "iphone_us",     recovery: 0.78, coverage: 3.2, posts: 12 },
  { name: "dcard_tw",      recovery: 0.74, coverage: 2.9, posts: 12 },
];

export type RecentRun = {
  topic: string;
  region: string;
  sources: number;
  status: "done" | "running" | "failed";
  personas: number;
  when: string;
  id: string;
  stage?: string;
};

export const RECENT_RUNS: RecentRun[] = [
  { topic: "MTR Mobile",   region: "HK", sources: 3, status: "done",    personas: 4, when: "2m ago",  id: "run_8af2e1" },
  { topic: "Octopus card", region: "HK", sources: 2, status: "done",    personas: 3, when: "14m ago", id: "run_6c19a0" },
  { topic: "OpenRice",     region: "HK", sources: 4, status: "running", personas: 0, when: "now",     id: "run_30bb52", stage: "cluster" },
  { topic: "WhatsApp",     region: "HK", sources: 2, status: "done",    personas: 5, when: "1h ago",  id: "run_e114c7" },
  { topic: "HSBC HK app",  region: "HK", sources: 3, status: "done",    personas: 4, when: "2h ago",  id: "run_a2f009" },
  { topic: "Tabelog",      region: "JP", sources: 2, status: "failed",  personas: 0, when: "3h ago",  id: "run_b54110" },
];

export const SUGGESTED_TOPICS = [
  "MTR Mobile",
  "Octopus card",
  "WhatsApp HK",
  "OpenRice",
  "HSBC HK app",
];

export const REGIONS = ["HK", "JP", "TW", "US", "SG"];
