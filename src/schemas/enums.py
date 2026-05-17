"""Shared enums used by both raw post schemas and the region registry."""
from __future__ import annotations

from enum import Enum


class SourceCategory(str, Enum):
    """The seven canonical source categories.

    Every scraped source falls into exactly one. Downstream pipeline stages
    can filter and weight evidence by category (e.g. for journey mapping,
    weight `reviews` higher; for personas, weight `forums` and `blogs` higher).
    """

    FORUMS = "forums"                  # discussion boards, BBS
    REVIEWS = "reviews"                # product / place / app review sites
    SOCIAL = "social"                  # Twitter/X, Threads, IG, TikTok, Xiaohongshu, Weibo, FB public
    VIDEO_COMMENTS = "video_comments"  # YouTube, Bilibili, Douyin
    QA = "qa"                          # Quora, Zhihu, Stack Exchange
    BLOGS = "blogs"                    # Medium, Substack, Naver blogs
    NEWS_COMMENTS = "news_comments"    # local news sites with comment sections


class SignalType(str, Enum):
    """Dominant intent / framing of content from a source.

    Declared at the source level (in `SourceConfig`) as the source's typical
    signal. Carried onto each `RawPost` as a default; later phases may
    re-classify per-post.
    """

    OPINION = "opinion"
    EXPERIENCE = "experience"
    COMPARISON = "comparison"
    COMPLAINT = "complaint"
    RECOMMENDATION = "recommendation"
