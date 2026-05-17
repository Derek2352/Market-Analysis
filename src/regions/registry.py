"""Region registry.

Each region is a first-class entry with its own ordered source list. HK is a
standalone region, NOT a subset of CN — different platforms, different
language profile, different legal regime.

A `RegionConfig` only documents sources; whether a working scraper exists is
determined by what's registered in `src.scrape.registry`. Phase 1 ships HK +
LIHKG only; everything else is forward-looking metadata.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TosRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AccessMethod(str, Enum):
    API = "api"                 # official, documented API
    PUBLIC_JSON = "public_json" # undocumented JSON endpoints used by the source's own app
    HTML = "html"               # static HTML (httpx + parser)
    HTML_JS = "html_js"         # JS-rendered (Playwright)


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    priority: int                # 1 = try first
    access_method: AccessMethod
    tos_risk: TosRisk
    auth_required: bool
    notes: str = ""


class RegionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    region_id: str               # canonical short code: "HK", "US", "TW", "ID"
    display_name: str
    primary_languages: list[str] # BCP-47 codes
    sources: list[SourceConfig]  # any order; sort by `priority` to consume

    @property
    def default_source_ids(self) -> list[str]:
        return [s.source_id for s in sorted(self.sources, key=lambda s: s.priority)]


# ---------------------------------------------------------------------------
# Region definitions
# ---------------------------------------------------------------------------
# Notes on what's realistically scrapeable are kept brief; the `notes` field on
# each SourceConfig is the authoritative record for per-source caveats.

REGIONS: dict[str, RegionConfig] = {
    # -------------------------------------------------------------- HK ----
    "HK": RegionConfig(
        region_id="HK",
        display_name="Hong Kong",
        primary_languages=["zh-HK", "yue", "en"],
        sources=[
            SourceConfig(
                source_id="lihkg",
                priority=1,
                access_method=AccessMethod.PUBLIC_JSON,
                tos_risk=TosRisk.MEDIUM,
                auth_required=False,
                notes="Mobile-app JSON endpoints. Cantonese-heavy. Keep <1 req/2s.",
            ),
            SourceConfig(
                source_id="openrice",
                priority=2,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.MEDIUM,
                auth_required=False,
                notes="Restaurant reviews. Strong signal for F&B brands.",
            ),
            SourceConfig(
                source_id="hk01",
                priority=3,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.LOW,
                auth_required=False,
                notes="HK01 article comments. Honor robots.txt; low volume.",
            ),
            SourceConfig(
                source_id="google_maps",
                priority=4,
                access_method=AccessMethod.API,
                tos_risk=TosRisk.LOW,
                auth_required=True,
                notes="Places API for review text. Quota-billed; bound usage.",
            ),
            SourceConfig(
                source_id="threads",
                priority=5,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.MEDIUM,
                auth_required=False,
                notes="Public profile/post pages. Schema unstable; expect rework.",
            ),
            SourceConfig(
                source_id="instagram_public",
                priority=6,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.HIGH,
                auth_required=False,
                notes="Public profiles only. Aggressive anti-bot; treat as best-effort.",
            ),
        ],
    ),
    # ------------------------------------------------------ US / UK / AU --
    "US": RegionConfig(
        region_id="US",
        display_name="United States",
        primary_languages=["en"],
        sources=[
            SourceConfig(
                source_id="reddit",
                priority=1,
                access_method=AccessMethod.API,
                tos_risk=TosRisk.LOW,
                auth_required=True,
                notes="Official OAuth API. Free tier sufficient at personal scale.",
            ),
            SourceConfig(
                source_id="youtube",
                priority=2,
                access_method=AccessMethod.API,
                tos_risk=TosRisk.LOW,
                auth_required=True,
                notes="YouTube Data API v3. API key auth. Quota: 10k units/day.",
            ),
            SourceConfig(
                source_id="trustpilot",
                priority=3,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.LOW,
                auth_required=False,
                notes="Public review pages. Honor robots.txt.",
            ),
            SourceConfig(
                source_id="quora",
                priority=4,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.MEDIUM,
                auth_required=False,
                notes="Anti-bot; soft login wall after N pages. Use sparingly.",
            ),
        ],
    ),
    "UK": RegionConfig(
        region_id="UK",
        display_name="United Kingdom",
        primary_languages=["en"],
        sources=[
            SourceConfig(source_id="reddit", priority=1, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="Filter by uk-relevant subs (r/unitedkingdom, r/AskUK, etc.)."),
            SourceConfig(source_id="trustpilot", priority=2, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False,
                         notes="Trustpilot is UK-headquartered; strong UK coverage."),
            SourceConfig(source_id="youtube", priority=3, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="Filter by regionCode=GB."),
            SourceConfig(source_id="quora", priority=4, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.MEDIUM, auth_required=False),
        ],
    ),
    "AU": RegionConfig(
        region_id="AU",
        display_name="Australia",
        primary_languages=["en"],
        sources=[
            SourceConfig(source_id="reddit", priority=1, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="r/australia, r/AusFinance, r/melbourne, r/sydney."),
            SourceConfig(source_id="trustpilot", priority=2, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False),
            SourceConfig(source_id="youtube", priority=3, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="Filter by regionCode=AU."),
            SourceConfig(source_id="quora", priority=4, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.MEDIUM, auth_required=False),
        ],
    ),
    # -------------------------------------------------------------- CN ----
    # Mainland China only. HK and TW are separate regions above/below.
    "CN": RegionConfig(
        region_id="CN",
        display_name="China (Mainland)",
        primary_languages=["zh-CN"],
        sources=[
            SourceConfig(source_id="zhihu", priority=1, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.HIGH, auth_required=False,
                         notes="Public question pages. Login wall after N pages — accept partial."),
            SourceConfig(source_id="xiaohongshu", priority=2, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False,
                         notes="小红书. Heavy anti-bot; public note pages only. Fragile."),
            SourceConfig(source_id="weibo", priority=3, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.HIGH, auth_required=False,
                         notes="Public search. Often returns truncated results without login."),
            SourceConfig(source_id="douyin_comments", priority=4, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False,
                         notes="Comments on public videos. Schema changes often."),
            SourceConfig(source_id="dianping", priority=5, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False,
                         notes="Restaurant/local-business reviews. Aggressive anti-bot."),
        ],
    ),
    # -------------------------------------------------------------- TW ----
    "TW": RegionConfig(
        region_id="TW",
        display_name="Taiwan",
        primary_languages=["zh-TW"],
        sources=[
            SourceConfig(source_id="ptt", priority=1, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False,
                         notes="ptt.cc web mirror. Tolerant of low-volume scraping."),
            SourceConfig(source_id="dcard", priority=2, access_method=AccessMethod.PUBLIC_JSON,
                         tos_risk=TosRisk.MEDIUM, auth_required=False,
                         notes="Public web JSON endpoints. Some boards login-walled."),
            SourceConfig(source_id="mobile01", priority=3, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.MEDIUM, auth_required=False,
                         notes="Long-form tech/lifestyle forum. Honor robots.txt."),
        ],
    ),
    # -------------------------------------------------------------- JP ----
    "JP": RegionConfig(
        region_id="JP",
        display_name="Japan",
        primary_languages=["ja"],
        sources=[
            SourceConfig(source_id="five_ch", priority=1, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.MEDIUM, auth_required=False,
                         notes="5ch via open read-only mirrors. Identify as bot UA; low volume."),
            SourceConfig(source_id="yahoo_japan_reviews", priority=2, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.MEDIUM, auth_required=False,
                         notes="Shopping reviews. HTML structure stable."),
            SourceConfig(source_id="cosme", priority=3, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.MEDIUM, auth_required=False,
                         notes="@cosme. Critical for beauty/skincare brands."),
            SourceConfig(source_id="twitter_jp", priority=4, access_method=AccessMethod.API,
                         tos_risk=TosRisk.HIGH, auth_required=True,
                         notes="X API. Unauthed browsing blocked since 2023; needs paid tier."),
        ],
    ),
    # -------------------------------------------------------------- KR ----
    "KR": RegionConfig(
        region_id="KR",
        display_name="South Korea",
        primary_languages=["ko"],
        sources=[
            SourceConfig(source_id="naver_blog", priority=1, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False,
                         notes="Public blog posts. Naver Cafés mostly login-walled — out of scope."),
            SourceConfig(source_id="dcinside", priority=2, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.MEDIUM, auth_required=False,
                         notes="Public boards. Variable anti-bot per gallery."),
            SourceConfig(source_id="coupang_reviews", priority=3, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False,
                         notes="Product reviews. Aggressive anti-bot; expect breakage."),
        ],
    ),
    # ----------------------------------------------------- SEA (split) ----
    # SEA is modeled per-country because languages and platforms differ sharply.
    "ID": RegionConfig(
        region_id="ID",
        display_name="Indonesia",
        primary_languages=["id"],
        sources=[
            SourceConfig(source_id="shopee_reviews", priority=1, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False,
                         notes="Product reviews; conservative rate."),
            SourceConfig(source_id="lazada_reviews", priority=2, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False),
            SourceConfig(source_id="kaskus", priority=3, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False,
                         notes="Long-running general forum."),
            SourceConfig(source_id="reddit", priority=4, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="r/indonesia. Often English-language."),
        ],
    ),
    "TH": RegionConfig(
        region_id="TH",
        display_name="Thailand",
        primary_languages=["th"],
        sources=[
            SourceConfig(source_id="pantip", priority=1, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False,
                         notes="Largest TH discussion forum."),
            SourceConfig(source_id="shopee_reviews", priority=2, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False),
            SourceConfig(source_id="lazada_reviews", priority=3, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False),
        ],
    ),
    "VN": RegionConfig(
        region_id="VN",
        display_name="Vietnam",
        primary_languages=["vi"],
        sources=[
            SourceConfig(source_id="tinhte", priority=1, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False,
                         notes="Tinhte.vn tech-and-lifestyle forum."),
            SourceConfig(source_id="shopee_reviews", priority=2, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False),
            SourceConfig(source_id="lazada_reviews", priority=3, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False),
        ],
    ),
    "PH": RegionConfig(
        region_id="PH",
        display_name="Philippines",
        primary_languages=["en", "tl"],
        sources=[
            SourceConfig(source_id="reddit", priority=1, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="r/Philippines, r/phinvest. English-dominant."),
            SourceConfig(source_id="shopee_reviews", priority=2, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False),
            SourceConfig(source_id="lazada_reviews", priority=3, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False),
        ],
    ),
    "MY": RegionConfig(
        region_id="MY",
        display_name="Malaysia",
        primary_languages=["ms", "en", "zh"],
        sources=[
            SourceConfig(source_id="lowyat", priority=1, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False,
                         notes="Lowyat.NET forum; tech & general."),
            SourceConfig(source_id="reddit", priority=2, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="r/malaysia."),
            SourceConfig(source_id="shopee_reviews", priority=3, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False),
        ],
    ),
    "SG": RegionConfig(
        region_id="SG",
        display_name="Singapore",
        primary_languages=["en", "zh", "ms", "ta"],
        sources=[
            SourceConfig(source_id="reddit", priority=1, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="r/singapore, r/askSingapore."),
            SourceConfig(source_id="hardwarezone", priority=2, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.MEDIUM, auth_required=False,
                         notes="HWZ EDMW. Some boards login-walled."),
            SourceConfig(source_id="shopee_reviews", priority=3, access_method=AccessMethod.HTML_JS,
                         tos_risk=TosRisk.HIGH, auth_required=False),
        ],
    ),
    # ------------------------------------------------------- EU (sample) --
    # Sample of EU countries — extend as needed; pattern is Trustpilot + Reddit + 1 local forum.
    "DE": RegionConfig(
        region_id="DE",
        display_name="Germany",
        primary_languages=["de"],
        sources=[
            SourceConfig(source_id="reddit", priority=1, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="r/de, r/Finanzen, r/Fragreddit."),
            SourceConfig(source_id="trustpilot", priority=2, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False),
            SourceConfig(source_id="gutefrage", priority=3, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.MEDIUM, auth_required=False,
                         notes="gutefrage.net Q&A site."),
        ],
    ),
    "FR": RegionConfig(
        region_id="FR",
        display_name="France",
        primary_languages=["fr"],
        sources=[
            SourceConfig(source_id="reddit", priority=1, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="r/france, r/AskFrance."),
            SourceConfig(source_id="trustpilot", priority=2, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False),
            SourceConfig(source_id="doctissimo", priority=3, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.MEDIUM, auth_required=False,
                         notes="Health/lifestyle forum; valuable for wellness brands."),
        ],
    ),
    "ES": RegionConfig(
        region_id="ES",
        display_name="Spain",
        primary_languages=["es"],
        sources=[
            SourceConfig(source_id="reddit", priority=1, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="r/spain, r/askspain."),
            SourceConfig(source_id="trustpilot", priority=2, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False),
            SourceConfig(source_id="forocoches", priority=3, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.MEDIUM, auth_required=False,
                         notes="General forum; high noise, high signal."),
        ],
    ),
    "IT": RegionConfig(
        region_id="IT",
        display_name="Italy",
        primary_languages=["it"],
        sources=[
            SourceConfig(source_id="reddit", priority=1, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="r/italy, r/Italia."),
            SourceConfig(source_id="trustpilot", priority=2, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False),
        ],
    ),
    "NL": RegionConfig(
        region_id="NL",
        display_name="Netherlands",
        primary_languages=["nl"],
        sources=[
            SourceConfig(source_id="reddit", priority=1, access_method=AccessMethod.API,
                         tos_risk=TosRisk.LOW, auth_required=True,
                         notes="r/thenetherlands."),
            SourceConfig(source_id="trustpilot", priority=2, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False),
            SourceConfig(source_id="tweakers", priority=3, access_method=AccessMethod.HTML,
                         tos_risk=TosRisk.LOW, auth_required=False,
                         notes="Tweakers.net — tech/consumer reviews."),
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
