"""Region registry.

Each region is a first-class entry with its own ordered source list. Sources
are tagged with a `SourceCategory` (one of seven: forums, reviews, social,
video_comments, qa, blogs, news_comments) so downstream phases can filter and
weight evidence by what they're generating.

HK is a standalone region, NOT a subset of CN — different platforms, different
language profile (zh-HK / Cantonese / English), different legal regime.

`persona_value` and `journey_value` are 1-5 scores for how useful each source
typically is for persona synthesis vs. journey mapping. These are heuristic
defaults the analysis pipeline can override or refine empirically.
"""
from __future__ import annotations

from collections import defaultdict
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.enums import SignalType, SourceCategory


class TosRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AccessMethod(str, Enum):
    API = "api"                 # official, documented API (incl. public RSS)
    PUBLIC_JSON = "public_json" # undocumented JSON endpoints used by the source's own app
    HTML = "html"               # static HTML (httpx + parser)
    HTML_JS = "html_js"         # JS-rendered (Playwright)


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    category: SourceCategory
    priority: int                    # 1 = try first within its category
    access_method: AccessMethod
    tos_risk: TosRisk
    auth_required: bool
    signal_type: SignalType          # dominant signal type for this source
    persona_value: int = Field(ge=1, le=5)
    journey_value: int = Field(ge=1, le=5)
    notes: str = ""


class RegionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    region_id: str               # canonical short code: "HK", "US", "TW", "ID"
    display_name: str
    primary_languages: list[str] # BCP-47 codes
    sources: list[SourceConfig]  # flat; group via `by_category()` for display

    def by_category(self) -> dict[SourceCategory, list[SourceConfig]]:
        """Sources grouped by category, each group ordered by priority."""
        grouped: dict[SourceCategory, list[SourceConfig]] = defaultdict(list)
        for s in self.sources:
            grouped[s.category].append(s)
        for cat in grouped:
            grouped[cat].sort(key=lambda s: s.priority)
        return dict(grouped)

    def default_source_ids(self) -> list[str]:
        """All source ids, ordered by category then priority."""
        out: list[str] = []
        for cat in SourceCategory:
            for s in self.by_category().get(cat, []):
                out.append(s.source_id)
        return out


# ---------------------------------------------------------------------------
# Region definitions
# ---------------------------------------------------------------------------

REGIONS: dict[str, RegionConfig] = {
    # -------------------------------------------------------------- HK ----
    "HK": RegionConfig(
        region_id="HK",
        display_name="Hong Kong",
        primary_languages=["zh-HK", "yue", "en"],
        sources=[
            # forums
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
                notes="Mobile-app JSON endpoints. Cantonese-heavy. Keep <1 req/2s.",
            ),
            SourceConfig(
                source_id="baby_kingdom",
                category=SourceCategory.FORUMS,
                priority=2,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.LOW,
                auth_required=False,
                signal_type=SignalType.EXPERIENCE,
                persona_value=4,
                journey_value=4,
                notes="Parenting forum. Strong purchase/service-journey content for family brands.",
            ),
            SourceConfig(
                source_id="discuss_hk",
                category=SourceCategory.FORUMS,
                priority=3,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.MEDIUM,
                auth_required=False,
                signal_type=SignalType.OPINION,
                persona_value=4,
                journey_value=3,
                notes="Discuss.com.hk — long-running general HK forum.",
            ),
            # reviews
            SourceConfig(
                source_id="google_maps_hk",
                category=SourceCategory.REVIEWS,
                priority=1,
                access_method=AccessMethod.API,
                tos_risk=TosRisk.LOW,
                auth_required=True,
                signal_type=SignalType.EXPERIENCE,
                persona_value=3,
                journey_value=5,
                notes="Places API; quota-billed. 5 reviews per place hard cap; query many places per brand.",
            ),
            SourceConfig(
                source_id="openrice",
                category=SourceCategory.REVIEWS,
                priority=2,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.MEDIUM,
                auth_required=False,
                signal_type=SignalType.EXPERIENCE,
                persona_value=3,
                journey_value=5,
                notes="Restaurant reviews. Strong signal for F&B brands.",
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
                notes="iTunes RSS customer reviews. No key. ~500 reviews per app per market.",
            ),
            SourceConfig(
                source_id="trustpilot",
                category=SourceCategory.REVIEWS,
                priority=4,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.LOW,
                auth_required=False,
                signal_type=SignalType.EXPERIENCE,
                persona_value=3,
                journey_value=4,
                notes="Public review pages; thinner HK coverage than UK/EU.",
            ),
            # social
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
                notes="Public profile/post pages. Schema unstable.",
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
                notes="Public profiles only. Aggressive anti-bot.",
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
                notes="HK-tagged notes on 小红书. Heavy anti-bot; treat as best-effort.",
            ),
            SourceConfig(
                source_id="facebook_hk_groups",
                category=SourceCategory.SOCIAL,
                priority=4,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.HIGH,
                auth_required=True,
                signal_type=SignalType.EXPERIENCE,
                persona_value=4,
                journey_value=3,
                notes="HK public groups. Login wall; ToS-restrictive — gated behind explicit opt-in.",
            ),
            # video_comments
            SourceConfig(
                source_id="youtube_hk",
                category=SourceCategory.VIDEO_COMMENTS,
                priority=1,
                access_method=AccessMethod.API,
                tos_risk=TosRisk.LOW,
                auth_required=True,
                signal_type=SignalType.OPINION,
                persona_value=3,
                journey_value=3,
                notes="YouTube Data API v3; filter regionCode=HK and relLanguage=zh-Hant/yue.",
            ),
            # qa
            SourceConfig(
                source_id="reddit_hongkong",
                category=SourceCategory.QA,
                priority=1,
                access_method=AccessMethod.API,
                tos_risk=TosRisk.LOW,
                auth_required=True,
                signal_type=SignalType.RECOMMENDATION,
                persona_value=4,
                journey_value=4,
                notes="r/HongKong + r/HKtechnology. English-dominant; expat-skewed.",
            ),
            SourceConfig(
                source_id="quora_hk",
                category=SourceCategory.QA,
                priority=2,
                access_method=AccessMethod.HTML_JS,
                tos_risk=TosRisk.MEDIUM,
                auth_required=False,
                signal_type=SignalType.COMPARISON,
                persona_value=3,
                journey_value=4,
                notes="HK-tagged questions/answers. Soft login wall after N pages.",
            ),
            # blogs
            SourceConfig(
                source_id="medium_hk",
                category=SourceCategory.BLOGS,
                priority=1,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.LOW,
                auth_required=False,
                signal_type=SignalType.COMPARISON,
                persona_value=5,
                journey_value=4,
                notes="Medium tag/topic pages for HK writers. Honor metered paywall.",
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
                notes="Long tail discovered via Google site: queries. Per-domain robots.txt.",
            ),
            # news_comments
            SourceConfig(
                source_id="hk01",
                category=SourceCategory.NEWS_COMMENTS,
                priority=1,
                access_method=AccessMethod.HTML,
                tos_risk=TosRisk.LOW,
                auth_required=False,
                signal_type=SignalType.OPINION,
                persona_value=3,
                journey_value=2,
                notes="HK01 article comments; low volume per article.",
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
                notes="Yahoo News HK comment threads.",
            ),
        ],
    ),
    # ------------------------------------------------------ US / UK / AU --
    "US": RegionConfig(
        region_id="US",
        display_name="United States",
        primary_languages=["en"],
        sources=[
            SourceConfig(source_id="reddit", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="Official OAuth API. Free tier sufficient at personal scale."),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5),
            SourceConfig(source_id="app_store_us", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=4,
                         notes="iTunes RSS customer reviews."),
            SourceConfig(source_id="youtube", category=SourceCategory.VIDEO_COMMENTS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=3, journey_value=3,
                         notes="YouTube Data API v3; quota 10k units/day."),
            SourceConfig(source_id="quora", category=SourceCategory.QA, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=3, journey_value=4,
                         notes="Anti-bot; soft login wall after N pages."),
        ],
    ),
    "UK": RegionConfig(
        region_id="UK",
        display_name="United Kingdom",
        primary_languages=["en"],
        sources=[
            SourceConfig(source_id="reddit", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="r/unitedkingdom, r/AskUK."),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         notes="Trustpilot is UK-headquartered; strong UK coverage."),
            SourceConfig(source_id="youtube", category=SourceCategory.VIDEO_COMMENTS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=3, journey_value=3,
                         notes="Filter regionCode=GB."),
            SourceConfig(source_id="quora", category=SourceCategory.QA, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=3, journey_value=4),
        ],
    ),
    "AU": RegionConfig(
        region_id="AU",
        display_name="Australia",
        primary_languages=["en"],
        sources=[
            SourceConfig(source_id="reddit", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="r/australia, r/AusFinance, r/melbourne, r/sydney."),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5),
            SourceConfig(source_id="youtube", category=SourceCategory.VIDEO_COMMENTS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=3, journey_value=3,
                         notes="Filter regionCode=AU."),
            SourceConfig(source_id="quora", category=SourceCategory.QA, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=3, journey_value=4),
        ],
    ),
    # -------------------------------------------------------------- CN ----
    # Mainland China only. HK and TW are separate regions.
    "CN": RegionConfig(
        region_id="CN",
        display_name="China (Mainland)",
        primary_languages=["zh-CN"],
        sources=[
            SourceConfig(source_id="xiaohongshu", category=SourceCategory.SOCIAL, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.RECOMMENDATION, persona_value=4, journey_value=4,
                         notes="小红书. Heavy anti-bot; public note pages only. Fragile."),
            SourceConfig(source_id="weibo", category=SourceCategory.SOCIAL, priority=2,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=3, journey_value=2,
                         notes="Public search. Often truncated without login."),
            SourceConfig(source_id="zhihu", category=SourceCategory.QA, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=4, journey_value=4,
                         notes="Public question pages. Login wall after N pages — accept partial."),
            SourceConfig(source_id="douyin_comments", category=SourceCategory.VIDEO_COMMENTS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=2, journey_value=2,
                         notes="Comments on public videos. Schema changes often."),
            SourceConfig(source_id="dianping", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         notes="Restaurant/local-business reviews. Aggressive anti-bot."),
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
                         notes="ptt.cc web mirror. Tolerant of low-volume scraping."),
            SourceConfig(source_id="dcard", category=SourceCategory.FORUMS, priority=2,
                         access_method=AccessMethod.PUBLIC_JSON, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=5, journey_value=4,
                         notes="Public web JSON endpoints. Some boards login-walled."),
            SourceConfig(source_id="mobile01", category=SourceCategory.FORUMS, priority=3,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=4, journey_value=4,
                         notes="Long-form tech/lifestyle forum."),
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
                         notes="5ch via open read-only mirrors. Identify as bot UA; low volume."),
            SourceConfig(source_id="yahoo_japan_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5,
                         notes="Shopping reviews."),
            SourceConfig(source_id="cosme", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=4, journey_value=5,
                         notes="@cosme. Critical for beauty/skincare brands."),
            SourceConfig(source_id="twitter_jp", category=SourceCategory.SOCIAL, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.HIGH, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=3, journey_value=2,
                         notes="X API; unauthed browsing blocked. Needs paid tier."),
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
                         notes="Public boards. Variable anti-bot per gallery."),
            SourceConfig(source_id="coupang_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5,
                         notes="Product reviews. Aggressive anti-bot."),
            SourceConfig(source_id="naver_blog", category=SourceCategory.BLOGS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.RECOMMENDATION, persona_value=5, journey_value=4,
                         notes="Public blog posts. Naver Cafés mostly login-walled — out of scope."),
        ],
    ),
    # ----------------------------------------------------- SEA (split) ----
    # Per-country because languages and platforms differ sharply.
    "ID": RegionConfig(
        region_id="ID",
        display_name="Indonesia",
        primary_languages=["id"],
        sources=[
            SourceConfig(source_id="kaskus", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=5, journey_value=3),
            SourceConfig(source_id="reddit_indonesia", category=SourceCategory.FORUMS, priority=2,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="r/indonesia. Often English-language."),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5),
            SourceConfig(source_id="lazada_reviews", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5),
        ],
    ),
    "TH": RegionConfig(
        region_id="TH",
        display_name="Thailand",
        primary_languages=["th"],
        sources=[
            SourceConfig(source_id="pantip", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=5, journey_value=4,
                         notes="Largest TH discussion forum."),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5),
            SourceConfig(source_id="lazada_reviews", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5),
        ],
    ),
    "VN": RegionConfig(
        region_id="VN",
        display_name="Vietnam",
        primary_languages=["vi"],
        sources=[
            SourceConfig(source_id="tinhte", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=4, journey_value=4,
                         notes="Tinhte.vn tech-and-lifestyle forum."),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5),
            SourceConfig(source_id="lazada_reviews", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5),
        ],
    ),
    "PH": RegionConfig(
        region_id="PH",
        display_name="Philippines",
        primary_languages=["en", "tl"],
        sources=[
            SourceConfig(source_id="reddit_philippines", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="r/Philippines, r/phinvest. English-dominant."),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5),
            SourceConfig(source_id="lazada_reviews", category=SourceCategory.REVIEWS, priority=2,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5),
        ],
    ),
    "MY": RegionConfig(
        region_id="MY",
        display_name="Malaysia",
        primary_languages=["ms", "en", "zh"],
        sources=[
            SourceConfig(source_id="lowyat", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="Lowyat.NET forum; tech & general."),
            SourceConfig(source_id="reddit_malaysia", category=SourceCategory.FORUMS, priority=2,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="r/malaysia."),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5),
        ],
    ),
    "SG": RegionConfig(
        region_id="SG",
        display_name="Singapore",
        primary_languages=["en", "zh", "ms", "ta"],
        sources=[
            SourceConfig(source_id="reddit_singapore", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="r/singapore, r/askSingapore."),
            SourceConfig(source_id="hardwarezone", category=SourceCategory.FORUMS, priority=2,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=3,
                         notes="HWZ EDMW. Some boards login-walled."),
            SourceConfig(source_id="shopee_reviews", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML_JS, tos_risk=TosRisk.HIGH, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=2, journey_value=5),
        ],
    ),
    # ------------------------------------------------------- EU (sample) --
    "DE": RegionConfig(
        region_id="DE",
        display_name="Germany",
        primary_languages=["de"],
        sources=[
            SourceConfig(source_id="reddit_de", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="r/de, r/Finanzen."),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5),
            SourceConfig(source_id="gutefrage", category=SourceCategory.QA, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=3, journey_value=4,
                         notes="gutefrage.net Q&A site."),
        ],
    ),
    "FR": RegionConfig(
        region_id="FR",
        display_name="France",
        primary_languages=["fr"],
        sources=[
            SourceConfig(source_id="reddit_fr", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="r/france, r/AskFrance."),
            SourceConfig(source_id="doctissimo", category=SourceCategory.FORUMS, priority=2,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=4, journey_value=4,
                         notes="Health/lifestyle forum; strong for wellness brands."),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5),
        ],
    ),
    "ES": RegionConfig(
        region_id="ES",
        display_name="Spain",
        primary_languages=["es"],
        sources=[
            SourceConfig(source_id="reddit_es", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="r/spain, r/askspain."),
            SourceConfig(source_id="forocoches", category=SourceCategory.FORUMS, priority=2,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.MEDIUM, auth_required=False,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=3,
                         notes="General forum; high noise, high signal."),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5),
        ],
    ),
    "IT": RegionConfig(
        region_id="IT",
        display_name="Italy",
        primary_languages=["it"],
        sources=[
            SourceConfig(source_id="reddit_it", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="r/italy, r/Italia."),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5),
        ],
    ),
    "NL": RegionConfig(
        region_id="NL",
        display_name="Netherlands",
        primary_languages=["nl"],
        sources=[
            SourceConfig(source_id="reddit_nl", category=SourceCategory.FORUMS, priority=1,
                         access_method=AccessMethod.API, tos_risk=TosRisk.LOW, auth_required=True,
                         signal_type=SignalType.OPINION, persona_value=4, journey_value=4,
                         notes="r/thenetherlands."),
            SourceConfig(source_id="tweakers", category=SourceCategory.FORUMS, priority=2,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.COMPARISON, persona_value=4, journey_value=4,
                         notes="Tweakers.net — tech/consumer reviews."),
            SourceConfig(source_id="trustpilot", category=SourceCategory.REVIEWS, priority=1,
                         access_method=AccessMethod.HTML, tos_risk=TosRisk.LOW, auth_required=False,
                         signal_type=SignalType.EXPERIENCE, persona_value=3, journey_value=5),
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
