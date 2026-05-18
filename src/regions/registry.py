"""Region registry.

Each region is a first-class entry with its own ordered source list. Sources
are tagged with a `SourceCategory` (one of seven: forums, reviews, social,
video_comments, qa, blogs, news_comments) so downstream phases can filter and
weight evidence by what they're generating.

HK is a standalone region, NOT a subset of CN — different platforms, different
language profile (zh-HK / Cantonese / English), different legal regime.

Hard constraint: every source must be free and must NOT require a developer
API, API key, OAuth, app registration, or paid service. Sources that fail
this test are kept in the registry with `excluded_by_constraint=True` so we
can revisit later if the constraint is relaxed — but they are NOT wired into
any scraper and they do NOT contribute to `default_source_ids()`.

`persona_value` and `journey_value` are 1-5 heuristic scores for how useful
each source typically is for persona synthesis vs. journey mapping.

`tos_scraping_stance`, `robots_txt_allows`, `last_checked` capture the
legal/ethical posture per source. We honor `prohibited` stances by flagging
the source clearly and respecting 403 + robots.txt — we do not silently
exclude. `last_verified_working` is set by `mkt scrape-doctor` when a
parser successfully matches its stored fixture.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.enums import SignalType, SourceCategory, ToSStance


class TosRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AccessMethod(str, Enum):
    API = "api"                  # documented API (incl. iTunes RSS — no key but is an API)
    PUBLIC_JSON = "public_json"  # undocumented JSON endpoints used by the source's own app
    HTML = "html"                # static HTML (httpx + parser)
    HTML_JS = "html_js"          # JS-rendered (Playwright)


# Common review date: 2026-05-17 (initial registry audit under the no-API
# constraint). Most stances below are best-effort summaries of public ToS;
# they need a human review pass before they should be considered authoritative.
_AUDIT_DATE = date(2026, 5, 17)
# Phase 6 — five new HK scrapers landed on 2026-05-18. last_verified_working
# is set on each parser-test pass and updated by `mkt scrape-doctor` thereafter.
_PHASE6_DATE = date(2026, 5, 18)


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    category: SourceCategory
    priority: int                    # 1 = try first within its category (among non-excluded sources)
    access_method: AccessMethod
    tos_risk: TosRisk
    auth_required: bool
    signal_type: SignalType
    persona_value: int = Field(ge=1, le=5)
    journey_value: int = Field(ge=1, le=5)

    # Constraint compliance
    excluded_by_constraint: bool = False
    exclusion_reason: str = ""       # e.g. "requires Reddit OAuth", "requires GCP billing"

    # Opt-in gate for the default source list. ToS-prohibited sources MUST
    # set this to False so they never run without an explicit --sources flag.
    # The validator below hard-fails at import time if you forget.
    default_enabled: bool = True

    # Legal / ethical posture
    tos_scraping_stance: ToSStance = ToSStance.UNKNOWN
    robots_txt_allows: bool | None = None  # None = not yet verified
    last_checked: date | None = None

    # Operational health (set by scrape-doctor or by the parser-test pass).
    last_verified_working: date | None = None

    notes: str = ""

    @model_validator(mode="after")
    def _prohibited_must_be_opt_in(self) -> "SourceConfig":
        if (
            self.tos_scraping_stance == ToSStance.PROHIBITED
            and self.default_enabled
        ):
            raise ValueError(
                f"Source {self.source_id!r}: tos_scraping_stance=prohibited "
                f"but default_enabled=True. Set default_enabled=False so "
                f"this source only runs when explicitly listed on --sources."
            )
        return self


class RegionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    region_id: str               # canonical short code: "HK", "US", "TW", "ID"
    display_name: str
    primary_languages: list[str] # BCP-47 codes
    sources: list[SourceConfig]  # flat; group via `by_category()` for display

    def by_category(
        self,
        *,
        include_excluded: bool = False,
        include_opt_in: bool = False,
    ) -> dict[SourceCategory, list[SourceConfig]]:
        """Sources grouped by category, each group ordered by priority.

        By default, omits sources blocked by the no-API constraint and
        sources with ``default_enabled=False``. Pass the corresponding flag
        to include either set (e.g. for documentation / registry inspection).
        """
        grouped: dict[SourceCategory, list[SourceConfig]] = defaultdict(list)
        for s in self.sources:
            if s.excluded_by_constraint and not include_excluded:
                continue
            if not s.default_enabled and not include_opt_in:
                continue
            grouped[s.category].append(s)
        for cat in grouped:
            grouped[cat].sort(key=lambda s: s.priority)
        return dict(grouped)

    def default_source_ids(self) -> list[str]:
        """Source ids that run when ``--sources`` is omitted.

        Excludes constraint-blocked sources AND opt-in-only sources
        (``default_enabled=False`` — every prohibited source by validator).
        """
        out: list[str] = []
        for cat in SourceCategory:
            for s in self.by_category().get(cat, []):
                out.append(s.source_id)
        return out

    def excluded_sources(self) -> list[SourceConfig]:
        """Sources kept for documentation but disabled by the no-API constraint."""
        return [s for s in self.sources if s.excluded_by_constraint]

    def opt_in_sources(self) -> list[SourceConfig]:
        """Sources requiring explicit --sources to run (ToS-prohibited)."""
        return [
            s for s in self.sources
            if not s.default_enabled and not s.excluded_by_constraint
        ]

    def get_source(self, source_id: str) -> SourceConfig | None:
        for s in self.sources:
            if s.source_id == source_id:
                return s
        return None


# ---------------------------------------------------------------------------
# Region definitions
# ---------------------------------------------------------------------------

REGIONS: dict[str, RegionConfig] = {
    # -------------------------------------------------------------- HK ----
    # Phase 1 focus region. Re-ranked under the no-API constraint:
    # 1. LIHKG (public JSON)
    # 2. Discuss.com.hk, Baby Kingdom (HTML forums)
    # 3. Openrice (HTML reviews — Phase 2 source; ToS-prohibited, flagged)
    # 4. HK01, Yahoo News HK comments (HTML)
    # 5. Reddit r/HongKong via old.reddit.com HTML (replaces API entry)
    # 6. Quora HK, Medium HK, Google SERP for blog discovery
    "HK": RegionConfig(
        region_id="HK",
        display_name="Hong Kong",
        primary_languages=["zh-HK", "yue", "en"],
        sources=[
            # ---- forums --------------------------------------------------
            SourceConfig(
                source_id="lihkg",
                category=SourceCategory.FORUMS,
                priority=1,
                access_method=AccessMethod.PUBLIC_JSON,
                tos_risk=TosRisk.MEDIUM,
                auth_required=False,
                signal_type=SignalType.OPINION,
                persona_value=5,
                journey_value=3,
                tos_scraping_stance=ToSStance.SILENT,
                robots_txt_allows=None,
                last_checked=_AUDIT_DATE,
                notes="Mobile-app JSON endpoints. Cantonese-heavy. Keep <1 req/2s. Phase 1 source.",
            ),
            SourceConfig(
                source_id="discuss_hk",
                category=SourceCategory.FORUMS,
                priority=2,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.LOW,
                auth_required=False,
                signal_type=SignalType.OPINION,
                persona_value=4,
                journey_value=3,
                default_enabled=True,
                tos_scraping_stance=ToSStance.SILENT,
                robots_txt_allows=None,
                last_checked=_PHASE6_DATE,
                last_verified_working=_PHASE6_DATE,
                notes=(
                    "Discuss.com.hk — long-running general HK forum. "
                    "Top-level posts only (reply trees deferred). httpx + bs4. "
                    "Phase 6 source."
                ),
            ),
            SourceConfig(
                source_id="reddit_old",
                category=SourceCategory.FORUMS,
                priority=3,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.LOW,
                auth_required=False,
                signal_type=SignalType.RECOMMENDATION,
                persona_value=4,
                journey_value=4,
                default_enabled=True,
                tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS,
                last_checked=_AUDIT_DATE,
                notes=(
                    "old.reddit.com HTML scrape via the reddit_old scraper "
                    "(pass --subreddits HongKong,HKtechnology). Reclassified "
                    "from qa to forums in Phase 6 — Reddit's thread structure "
                    "is forum-shaped, not Q&A pairs."
                ),
            ),
            SourceConfig(
                source_id="baby_kingdom",
                category=SourceCategory.FORUMS,
                priority=3,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.LOW,
                auth_required=False,
                signal_type=SignalType.EXPERIENCE,
                persona_value=4,
                journey_value=4,
                tos_scraping_stance=ToSStance.SILENT,
                last_checked=_AUDIT_DATE,
                notes="Parenting forum. Strong purchase/service-journey content for family brands.",
            ),
            # ---- reviews -------------------------------------------------
            SourceConfig(
                source_id="openrice",
                category=SourceCategory.REVIEWS,
                priority=1,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.HIGH,
                auth_required=False,
                signal_type=SignalType.EXPERIENCE,
                persona_value=3,
                journey_value=5,
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                last_checked=_AUDIT_DATE,
                notes=(
                    "Restaurant reviews — gold for F&B brands. ToS prohibits "
                    "automated access; flagged. Phase 2 source as Playwright "
                    "infrastructure milestone."
                ),
            ),
            SourceConfig(
                source_id="trustpilot",
                category=SourceCategory.REVIEWS,
                priority=2,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.MEDIUM,
                auth_required=False,
                signal_type=SignalType.EXPERIENCE,
                persona_value=3,
                journey_value=4,
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                last_checked=_AUDIT_DATE,
                notes="Thin HK coverage. ToS forbids scraping.",
            ),
            SourceConfig(
                source_id="app_store_hk",
                category=SourceCategory.REVIEWS,
                priority=3,
                access_method=AccessMethod.API,
                tos_risk=TosRisk.LOW,
                auth_required=False,
                signal_type=SignalType.EXPERIENCE,
                persona_value=3,
                journey_value=4,
                tos_scraping_stance=ToSStance.SILENT,
                last_checked=_AUDIT_DATE,
                notes=(
                    "iTunes RSS — public, no key required (satisfies the no-API "
                    "constraint). Kept as reference scraper after Phase 1 pivot to LIHKG."
                ),
            ),
            SourceConfig(
                source_id="google_play_hk",
                category=SourceCategory.REVIEWS,
                priority=4,
                access_method=AccessMethod.PUBLIC_JSON,
                tos_risk=TosRisk.HIGH,
                auth_required=False,
                signal_type=SignalType.EXPERIENCE,
                persona_value=3,
                journey_value=4,
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                last_checked=_AUDIT_DATE,
                last_verified_working=_AUDIT_DATE,
                notes=(
                    "Google Play HK reviews via the `google-play-scraper` library, "
                    "which hits Google Play's anonymous internal API (no key, no "
                    "OAuth — satisfies the no-API constraint). Google Play Terms "
                    "of Service prohibit automated access; flagged. User assumes "
                    "ToS responsibility under their jurisdiction. Mirror of "
                    "app_store_hk for non-iOS coverage."
                ),
            ),
            # ---- news_comments -------------------------------------------
            SourceConfig(
                source_id="hk01",
                category=SourceCategory.NEWS_COMMENTS,
                priority=1,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.HIGH,
                auth_required=False,
                signal_type=SignalType.OPINION,
                persona_value=3,
                journey_value=2,
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                robots_txt_allows=None,
                last_checked=_PHASE6_DATE,
                last_verified_working=None,
                notes=(
                    "HK01 article comments — DEFERRED from Phase 6. Two issues "
                    "blocked the initial implementation: (1) the article fixture "
                    "captured an article with commentCount=0, so the comments "
                    "extractor has nothing to test against; (2) search results "
                    "are AJAX-paginated and the saved fixture contained zero "
                    "/<category>/<articleId>/... article links. Both blockers "
                    "can be resolved with a second Playwright capture pass "
                    "(article with commentCount>0 + a search page after AJAX "
                    "settles). ToS prohibits scraping; would be opt-in only. "
                    "Not registered in src/scrape/registry.py."
                ),
            ),
            SourceConfig(
                source_id="youtube_html",
                category=SourceCategory.VIDEO_COMMENTS,
                priority=1,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.HIGH,
                auth_required=False,
                signal_type=SignalType.OPINION,
                persona_value=3,
                journey_value=3,
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                robots_txt_allows=False,
                last_checked=_PHASE6_DATE,
                last_verified_working=_PHASE6_DATE,
                notes=(
                    "YouTube comments via Playwright. Top 10 videos × 50 "
                    "comments per topic. Language filter: keep zh-Hant / yue / en; "
                    "drop zh-Hans to exclude mainland CN signal. Robots.txt "
                    "disallows /search and /watch — flagged honestly. ToS "
                    "prohibits scraping; opt-in only. Phase 6 source."
                ),
            ),
            SourceConfig(
                source_id="yahoo_news_hk",
                category=SourceCategory.NEWS_COMMENTS,
                priority=2,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.LOW,
                auth_required=False,
                signal_type=SignalType.OPINION,
                persona_value=2,
                journey_value=2,
                tos_scraping_stance=ToSStance.SILENT,
                last_checked=_AUDIT_DATE,
                notes="Yahoo News HK comment threads.",
            ),
            # ---- qa ------------------------------------------------------
            SourceConfig(
                source_id="quora_hk",
                category=SourceCategory.QA,
                priority=1,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.HIGH,
                auth_required=False,
                signal_type=SignalType.COMPARISON,
                persona_value=3,
                journey_value=4,
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                robots_txt_allows=None,
                last_checked=_PHASE6_DATE,
                last_verified_working=_PHASE6_DATE,
                notes=(
                    "Quora questions tagged Hong Kong. Discovery via DuckDuckGo "
                    "SERP for site:quora.com 'Hong Kong' <topic>. 2s min delay. "
                    "ToS prohibits scraping; opt-in only. Phase 6 source."
                ),
            ),
            # ---- blogs ---------------------------------------------------
            SourceConfig(
                source_id="medium_hk",
                category=SourceCategory.BLOGS,
                priority=1,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.HIGH,
                auth_required=False,
                signal_type=SignalType.RECOMMENDATION,
                persona_value=5,
                journey_value=4,
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                robots_txt_allows=None,
                last_checked=_PHASE6_DATE,
                last_verified_working=_PHASE6_DATE,
                notes=(
                    "Medium HK writers via DuckDuckGo SERP discovery "
                    "(site:medium.com 'Hong Kong' <topic>). Pulls title, body, "
                    "claps_count, response_count, author_hash. ToS prohibits "
                    "scraping; opt-in only. Phase 6 source."
                ),
            ),
            SourceConfig(
                source_id="hk_lifestyle_blogs",
                category=SourceCategory.BLOGS,
                priority=2,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.MEDIUM,
                auth_required=False,
                signal_type=SignalType.RECOMMENDATION,
                persona_value=4,
                journey_value=4,
                tos_scraping_stance=ToSStance.SILENT,  # varies per blog; default conservative
                last_checked=_AUDIT_DATE,
                notes=(
                    "Long tail of HK lifestyle blogs discovered via Google SERP. "
                    "Per-domain robots.txt check before fetching."
                ),
            ),
            SourceConfig(
                source_id="google_serp",
                category=SourceCategory.BLOGS,  # used as a discovery tool, classified with blogs
                priority=3,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.HIGH,
                auth_required=False,
                signal_type=SignalType.OPINION,
                persona_value=1,  # SERP itself isn't evidence, just discovery
                journey_value=1,
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                last_checked=_AUDIT_DATE,
                notes=(
                    "Google search results — used ONLY to discover blog URLs, not as "
                    "evidence itself. Heavy rate limiting; ToS prohibits. Flagged."
                ),
            ),
            # ---- social --------------------------------------------------
            # All HK social sources are de-prioritized: most are fragile or
            # ToS-prohibited. Kept registered for opt-in.
            SourceConfig(
                source_id="threads_hk",
                category=SourceCategory.SOCIAL,
                priority=1,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.MEDIUM,
                auth_required=False,
                signal_type=SignalType.OPINION,
                persona_value=3,
                journey_value=2,
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                last_checked=_AUDIT_DATE,
                notes="Public profile/post pages. Schema unstable. ToS prohibits.",
            ),
            SourceConfig(
                source_id="instagram_public",
                category=SourceCategory.SOCIAL,
                priority=2,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.HIGH,
                auth_required=False,
                signal_type=SignalType.OPINION,
                persona_value=3,
                journey_value=2,
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                last_checked=_AUDIT_DATE,
                notes="Public profiles only. Aggressive anti-bot. ToS prohibits.",
            ),
            SourceConfig(
                source_id="xiaohongshu_hk",
                category=SourceCategory.SOCIAL,
                priority=3,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.HIGH,
                auth_required=False,
                signal_type=SignalType.RECOMMENDATION,
                persona_value=4,
                journey_value=4,
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                last_checked=_AUDIT_DATE,
                notes="HK-tagged notes. Heavy anti-bot. Best-effort; ToS prohibits.",
            ),
            # ---- excluded by no-API constraint --------------------------
            SourceConfig(
                source_id="google_maps_hk",
                category=SourceCategory.REVIEWS,
                priority=99, access_method=AccessMethod.API,
                tos_risk=TosRisk.LOW, auth_required=True,
                signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                excluded_by_constraint=True,
                exclusion_reason="Google Places API requires API key + billing account.",
                tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS,
                last_checked=_AUDIT_DATE,
                notes="Revisit if the no-API constraint relaxes.",
            ),
            SourceConfig(
                source_id="youtube_hk",
                category=SourceCategory.VIDEO_COMMENTS,
                priority=99, access_method=AccessMethod.API,
                tos_risk=TosRisk.LOW, auth_required=True,
                signal_type=SignalType.OPINION, persona_value=3, journey_value=3,
                excluded_by_constraint=True,
                exclusion_reason="YouTube Data API v3 requires API key.",
                tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS,
                last_checked=_AUDIT_DATE,
                notes="No public-HTML substitute for comments — video_comments coverage gap for HK.",
            ),
            SourceConfig(
                source_id="reddit_hongkong",
                category=SourceCategory.QA,
                priority=99, access_method=AccessMethod.API,
                tos_risk=TosRisk.LOW, auth_required=True,
                signal_type=SignalType.RECOMMENDATION, persona_value=4, journey_value=4,
                excluded_by_constraint=True,
                exclusion_reason="Reddit API requires OAuth registration.",
                tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS,
                last_checked=_AUDIT_DATE,
                notes="Replaced by reddit_old (old.reddit.com HTML scrape, no API).",
            ),
            SourceConfig(
                source_id="facebook_hk_groups",
                category=SourceCategory.SOCIAL,
                priority=99, access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.HIGH, auth_required=True,
                signal_type=SignalType.EXPERIENCE, persona_value=4, journey_value=3,
                excluded_by_constraint=True,
                exclusion_reason="Requires Facebook account login.",
                default_enabled=False,
                tos_scraping_stance=ToSStance.PROHIBITED,
                last_checked=_AUDIT_DATE,
            ),
        ],
    ),
    # ------------------------------------------------------ US / UK / AU --
    # All API-based Reddit/YouTube entries excluded. Re-ranking will be
    # finished in Phase 8; for now we mark exclusions so default_source_ids()
    # is correct.
    "US": RegionConfig(
        region_id="US",
        display_name="United States",
        primary_languages=["en"],
        sources=[
            SourceConfig(source_id="reddit", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True,
                         exclusion_reason="Reddit API requires OAuth registration.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE,
                         notes="HTML substitute to be added in Phase 8."),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE,
                         notes="ToS forbids scraping; flagged."),
            SourceConfig(source_id="app_store_us", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=4,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE,
                         notes="iTunes RSS — public, no key required."),
            SourceConfig(source_id="youtube", category=SourceCategory.VIDEO_COMMENTS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=3, journey_value=3,
                         excluded_by_constraint=True,
                         exclusion_reason="YouTube Data API v3 requires API key.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="quora", category=SourceCategory.QA, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=3, journey_value=4,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE,
                         notes="ToS prohibits."),
        ],
    ),
    "UK": RegionConfig(
        region_id="UK",
        display_name="United Kingdom",
        primary_languages=["en"],
        sources=[
            SourceConfig(source_id="reddit", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True, exclusion_reason="Reddit API requires OAuth.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE,
                         notes="UK-headquartered; strong UK coverage. ToS prohibits."),
            SourceConfig(source_id="youtube", category=SourceCategory.VIDEO_COMMENTS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=3, journey_value=3,
                         excluded_by_constraint=True, exclusion_reason="YouTube Data API requires key.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="quora", category=SourceCategory.QA, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=3, journey_value=4,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    "AU": RegionConfig(
        region_id="AU",
        display_name="Australia",
        primary_languages=["en"],
        sources=[
            SourceConfig(source_id="reddit", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True, exclusion_reason="Reddit API requires OAuth.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="youtube", category=SourceCategory.VIDEO_COMMENTS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=3, journey_value=3,
                         excluded_by_constraint=True, exclusion_reason="YouTube Data API requires key.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="quora", category=SourceCategory.QA, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=3, journey_value=4,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    # -------------------------------------------------------------- CN ----
    "CN": RegionConfig(
        region_id="CN",
        display_name="China (Mainland)",
        primary_languages=["zh-CN"],
        sources=[
            SourceConfig(source_id="xiaohongshu", category=SourceCategory.SOCIAL, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.RECOMMENDATION, persona_value=4, journey_value=4,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE,
                         notes="小红书. Heavy anti-bot; public note pages only; fragile."),
            SourceConfig(source_id="weibo", category=SourceCategory.SOCIAL, priority=2,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=3, journey_value=2,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE,
                         notes="Public search; often truncated without login."),
            SourceConfig(source_id="zhihu", category=SourceCategory.QA, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=4, journey_value=4,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE,
                         notes="Public question pages; login wall after N pages."),
            SourceConfig(source_id="dianping", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE,
                         notes="Restaurant/local-business reviews; aggressive anti-bot."),
            SourceConfig(source_id="douyin_comments", category=SourceCategory.VIDEO_COMMENTS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=2, journey_value=2,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE,
                         notes="Comments on public videos; schema changes often."),
        ],
    ),
    # -------------------------------------------------------------- TW ----
    "TW": RegionConfig(
        region_id="TW",
        display_name="Taiwan",
        primary_languages=["zh-TW"],
        sources=[
            SourceConfig(source_id="ptt", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=5, journey_value=3,
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE,
                         notes="ptt.cc web mirror. Tolerant of low-volume scraping."),
            SourceConfig(source_id="dcard", category=SourceCategory.FORUMS, priority=2,
                         access_method=AccessMethod.PUBLIC_JSON, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=5, journey_value=4,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE,
                         notes="Public web JSON endpoints."),
            SourceConfig(source_id="mobile01", category=SourceCategory.FORUMS, priority=3,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=4, journey_value=4,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE),
        ],
    ),
    # -------------------------------------------------------------- JP ----
    "JP": RegionConfig(
        region_id="JP",
        display_name="Japan",
        primary_languages=["ja"],
        sources=[
            SourceConfig(source_id="five_ch", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=3,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE,
                         notes="5ch via open read-only mirrors."),
            SourceConfig(source_id="yahoo_japan_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="cosme", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=4, journey_value=5,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE,
                         notes="@cosme — beauty/skincare gold."),
            SourceConfig(source_id="twitter_jp", category=SourceCategory.SOCIAL, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.HIGH, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=3, journey_value=2,
                         excluded_by_constraint=True,
                         exclusion_reason="X API requires paid tier; unauthed browsing blocked.",
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    # -------------------------------------------------------------- KR ----
    "KR": RegionConfig(
        region_id="KR",
        display_name="South Korea",
        primary_languages=["ko"],
        sources=[
            SourceConfig(source_id="dcinside", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=3,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="coupang_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="naver_blog", category=SourceCategory.BLOGS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.RECOMMENDATION, persona_value=5, journey_value=4,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE,
                         notes="Public blog posts only; Cafés are login-walled — out of scope."),
        ],
    ),
    # ----------------------------------------------------- SEA (split) ----
    "ID": RegionConfig(
        region_id="ID", display_name="Indonesia", primary_languages=["id"],
        sources=[
            SourceConfig(source_id="kaskus", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=5, journey_value=3,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="reddit_indonesia", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True, exclusion_reason="Reddit API requires OAuth.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="lazada_reviews", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    "TH": RegionConfig(
        region_id="TH", display_name="Thailand", primary_languages=["th"],
        sources=[
            SourceConfig(source_id="pantip", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=5, journey_value=4,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE,
                         notes="Largest TH discussion forum."),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="lazada_reviews", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    "VN": RegionConfig(
        region_id="VN", display_name="Vietnam", primary_languages=["vi"],
        sources=[
            SourceConfig(source_id="tinhte", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=4, journey_value=4,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="lazada_reviews", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    "PH": RegionConfig(
        region_id="PH", display_name="Philippines", primary_languages=["en", "tl"],
        sources=[
            SourceConfig(source_id="reddit_philippines", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True, exclusion_reason="Reddit API requires OAuth.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="lazada_reviews", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    "MY": RegionConfig(
        region_id="MY", display_name="Malaysia", primary_languages=["ms", "en", "zh"],
        sources=[
            SourceConfig(source_id="lowyat", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="reddit_malaysia", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True, exclusion_reason="Reddit API requires OAuth.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    "SG": RegionConfig(
        region_id="SG", display_name="Singapore", primary_languages=["en", "zh", "ms", "ta"],
        sources=[
            SourceConfig(source_id="hardwarezone", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=3,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE,
                         notes="HWZ EDMW. Some boards login-walled."),
            SourceConfig(source_id="reddit_singapore", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True, exclusion_reason="Reddit API requires OAuth.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    # ------------------------------------------------------- EU (sample) --
    "DE": RegionConfig(
        region_id="DE", display_name="Germany", primary_languages=["de"],
        sources=[
            SourceConfig(source_id="reddit_de", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True, exclusion_reason="Reddit API requires OAuth.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="gutefrage", category=SourceCategory.QA, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=3, journey_value=4,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE),
        ],
    ),
    "FR": RegionConfig(
        region_id="FR", display_name="France", primary_languages=["fr"],
        sources=[
            SourceConfig(source_id="reddit_fr", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True, exclusion_reason="Reddit API requires OAuth.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="doctissimo", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=4, journey_value=4,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE,
                         notes="Health/lifestyle forum; strong for wellness brands."),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    "ES": RegionConfig(
        region_id="ES", display_name="Spain", primary_languages=["es"],
        sources=[
            SourceConfig(source_id="reddit_es", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True, exclusion_reason="Reddit API requires OAuth.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="forocoches", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=3,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    "IT": RegionConfig(
        region_id="IT", display_name="Italy", primary_languages=["it"],
        sources=[
            SourceConfig(source_id="reddit_it", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True, exclusion_reason="Reddit API requires OAuth.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
    "NL": RegionConfig(
        region_id="NL", display_name="Netherlands", primary_languages=["nl"],
        sources=[
            SourceConfig(source_id="reddit_nl", category=SourceCategory.FORUMS, priority=99,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         excluded_by_constraint=True, exclusion_reason="Reddit API requires OAuth.",
                         tos_scraping_stance=ToSStance.ALLOWED_WITH_CONDITIONS, last_checked=_AUDIT_DATE),
            SourceConfig(source_id="tweakers", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=4, journey_value=4,
                         tos_scraping_stance=ToSStance.SILENT, last_checked=_AUDIT_DATE,
                         notes="Tweakers.net — tech/consumer reviews."),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         default_enabled=False, tos_scraping_stance=ToSStance.PROHIBITED, last_checked=_AUDIT_DATE),
        ],
    ),
}


def get_region(region_id: str) -> RegionConfig:
    """Look up a region by canonical code. Raises KeyError if unknown."""
    if region_id not in REGIONS:
        raise KeyError(
            f"Unknown region: {region_id!r}. "
            f"Available: {sorted(REGIONS)}"
        )
    return REGIONS[region_id]
