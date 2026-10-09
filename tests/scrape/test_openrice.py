"""Fixture-based tests for the OpenRice scraper.

The ``reviews_<id>_p1`` / ``search_*`` fixtures are full pages from the
older layout (with a schema.org review ItemList); ``*_anon`` fixtures are
current-layout pages (no review JSON-LD) trimmed to the review articles with
reviewers anonymised. The flow tests drive ``OpenriceScraper.search`` through a
fake browser that serves those fixtures — no network, no Chromium.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from src.scrape.base.http import ForbiddenError
from src.scrape.base.playwright import PlaywrightManager
from src.scrape.base.protocol import SourceError
from src.scrape.openrice import (
    _SERVER_CUT_WIDTH,
    Branch,
    OpenriceScraper,
    _looks_truncated,
    anonymise_page_html,
    doctor_check,
    parse_review_list_html,
    parse_review_page_html,
    parse_review_total,
    parse_search_results_html,
    pick_branches,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "html" / "openrice"
YUKI = "https://www.openrice.com/en/hongkong/r-yuki-house-ramen-wan-chai-japanese-ramen-r530978"
TG_PLACE = "https://www.openrice.com/en/hongkong/r-cafe-de-coral-kwun-tong-hong-kong-style-meatless-menu-r528467"


def _html(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def _search_page(rest_url: str, counts: tuple[int, ...] = ()) -> str:
    score = "".join(f'<div class="smile icon-wrapper"><div class="text">{c}</div></div>' for c in counts)
    return (
        '<div class="poi-list-cell"><div class="poi-name">Branch</div>'
        f'<div class="poi-score-row">{score}</div>'
        f'<a class="poi-list-cell-desktop-right-link-overlay" href="{rest_url}/"></a></div>'
    )


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def test_search_results_are_canonical_restaurant_urls_with_review_counts() -> None:
    branches = parse_search_results_html(_html("search_ramen"))
    assert len(branches) == 15
    assert all(b.url.startswith("https://www.openrice.com/en/hongkong/r-") for b in branches)
    assert not any(b.url.endswith(("/photos/all", "/")) for b in branches)
    yuki = next(b for b in branches if b.rest_id == "530978")
    assert yuki.url == YUKI
    assert yuki.face_count == 1056 + 30  # smile + cry on the card (OK reviews aren't shown)


def test_pick_branches_ranks_by_card_count_without_dropping_any() -> None:
    def b(i: str, n: int | None) -> Branch:
        return Branch(name=i, url=f"u{i}", rest_id=i, face_count=n)

    # A card showing 0 can still have OK-rated reviews: ranked last, not dropped.
    picked = pick_branches([b("a", 3), b("b", 0), b("c", None), b("d", 22), b("e", 3)], 4)
    assert [x.rest_id for x in picked] == ["d", "a", "e", "b"]


def test_review_total_comes_from_the_list_page_not_the_card() -> None:
    # Yuki House's card shows 1056 + 30; the list page says 1190 reviews.
    assert parse_review_total(_html("reviews_530978_p1")) == 1190
    assert parse_review_total(_html("reviews_864960_p1")) == 5
    assert parse_review_total("<html><body>no total</body></html>") is None


def test_rating_is_the_star_rating_not_the_reviewer_level() -> None:
    posts = parse_review_list_html(_html("reviews_530978_p1"), rest_url=YUKI, rest_id="530978")
    assert len(posts) == 15
    # The page's own structured data says what each review rated.
    expected = ["5", "4.5", "4", "4.5", "4.5", "4.5", "5", "5", "5", "5", "5", "5", "4.5", "4", "5"]
    assert [p.raw_metadata["rating_value"] for p in posts] == [float(x) for x in expected]
    # Half stars round half up for the int 1-5 rating metric.
    assert posts[1].engagement_metrics["rating"] == 5
    assert posts[2].engagement_metrics["rating"] == 4
    # "Level 3" is the reviewer's badge, recorded separately, never the rating.
    level3 = next(p for p in posts if p.raw_metadata["reviewer_level"] == 3)
    assert level3.engagement_metrics["rating"] == 5


def test_each_review_links_to_its_own_page() -> None:
    posts = parse_review_list_html(_html("reviews_530978_p1"), rest_url=YUKI, rest_id="530978")
    urls = [str(p.url) for p in posts]
    assert len(set(urls)) == 15
    assert all("/en/hongkong/review/" in u for u in urls)
    first = posts[0]
    assert first.id == "openrice_6359064"
    assert str(first.url).endswith("-e6359064")
    assert first.raw_metadata["rest_url"] == YUKI


def test_structured_data_timestamp_is_used_when_present() -> None:
    posts = parse_review_list_html(_html("reviews_530978_p1"), rest_url=YUKI, rest_id="530978")
    # datePublished 2026-03-24T18:50:10+08:00
    assert posts[0].posted_at == datetime(2026, 3, 24, 10, 50, 10, tzinfo=timezone.utc)
    assert posts[0].title == "🍜 Yuki House Ramen 幸屋 (灣仔) 👍😋✨"
    assert posts[0].body.endswith("Their menu balance")  # "…Read More" stripped
    assert posts[0].raw_metadata["extract_truncated"] is True


def test_current_layout_without_structured_data() -> None:
    posts = parse_review_list_html(_html("reviews_528467_p1_anon"), rest_url=TG_PLACE, rest_id="528467")
    assert len(posts) == 15
    # Author names come from the profile link; every review has its own author.
    assert len({p.author_hash for p in posts}) == 15
    assert not any(re.search(r"reviewer_\d", p.model_dump_json()) for p in posts)  # hashed only
    first = posts[0]
    assert first.raw_metadata["rating_value"] == 3.5
    assert first.engagement_metrics["rating"] == 4
    # Date-only values are pinned to noon HKT: same calendar day in UTC.
    assert first.posted_at == datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)
    # A review without a title link still gets its own URL (from the stars link).
    untitled = posts[1]
    assert untitled.title is None
    assert str(untitled.url) == "https://www.openrice.com/en/hongkong/review/--e6442858"
    assert untitled.engagement_metrics["rating"] == 4
    assert untitled.engagement_metrics["views"] == 39
    # In the live DOM, any extract as wide as the client-side cut may be cut.
    assert all(p.raw_metadata["extract_truncated"] for p in posts)
    # As the server sent them, extracts are whole reviews: only the one that
    # ends mid-sentence (on a hashtag) is checked against its review page.
    server = parse_review_list_html(
        _html("reviews_528467_p1_anon"), rest_url=TG_PLACE, rest_id="528467",
        cut_width=_SERVER_CUT_WIDTH,
    )
    assert [p.id for p in server if p.raw_metadata["extract_truncated"]] == ["openrice_5353507"]


def test_abbreviated_view_counts_are_parsed() -> None:
    # Popular reviews show "2K views" rather than an exact count.
    posts = parse_review_list_html(_html("reviews_864960_p1"), rest_url=YUKI, rest_id="864960")
    assert [p.engagement_metrics.get("views") for p in posts] == [764, 2000, 3000, 4000, 3000]


def test_fallback_id_depends_on_the_salt_not_the_raw_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a review link the id is a digest; it must not be computable
    from the raw username plus fields stored in the same post."""
    html = re.sub(r'href="[^"]*/review/[^"]*"', 'href="#"', _html("reviews_864960_p1"))
    first = parse_review_list_html(html, rest_url=YUKI, rest_id="864960")
    assert all(p.id.startswith("openrice_864960_") for p in first)
    monkeypatch.setenv("AUTHOR_HASH_SALT", "another-install")
    second = parse_review_list_html(html, rest_url=YUKI, rest_id="864960")
    assert [p.id for p in first] != [p.id for p in second]


def test_related_review_block_does_not_leak_into_the_review() -> None:
    """Some reviews embed a "Related Review" (another review by the same
    author, with its own star row); its stars used to be added to the
    review's own, giving 4 + 4 = 8.0 on a 5-star scale."""
    posts = parse_review_list_html(
        _html("reviews_537014_p1_anon"), rest_url=TG_PLACE, rest_id="537014"
    )
    assert len(posts) == 15
    assert all(0 < p.raw_metadata["rating_value"] <= 5 for p in posts)
    by_id = {p.id: p for p in posts}
    a = by_id["openrice_6498552"]
    assert a.raw_metadata["rating_value"] == 4.0
    assert "煎蛋香茅雞扒魚餅飯" not in a.body  # the related review's text
    assert a.raw_metadata["sub_ratings"] == {
        "taste": 4, "decor": 4, "service": 4, "hygiene": 4, "value": 4,
    }


def test_review_page_gives_the_full_text_without_photos() -> None:
    text = parse_review_page_html(_html("review_e6442858_anon"))
    assert text.startswith("午市時間觀塘周圍都係人")
    assert text.endswith("係工廠區打工仔嘅午餐生存首選。")
    assert "\n" in text  # paragraph breaks kept


@pytest.mark.parametrize(
    ("extract", "cut_width", "truncated"),
    [
        ("出餐快、有位坐，係工廠區打工仔嘅午餐生存首選。", 150, False),
        ("可以提升，出餐又好睇 😏", 150, False),
        ("Hidden in Wan Chai's streets. Their menu balance", _SERVER_CUT_WIDTH, True),
        ("快餐店都有靚靚兒童餐 #車車餐盤", _SERVER_CUT_WIDTH, True),
        # Live DOM: the client-side cut (~85+ CJK chars) can land right after 。
        ("好味。" * 30, 150, True),
        # Server HTML: the same text is a whole review...
        ("好味。" * 30, _SERVER_CUT_WIDTH, False),
        # ...unless it is unusually long (safety net for a server-side cut).
        ("好味。" * 200, _SERVER_CUT_WIDTH, True),
        ("", 150, False),
    ],
)
def test_truncated_extract_detection(extract: str, cut_width: int, truncated: bool) -> None:
    assert _looks_truncated(extract, cut_width) is truncated


def test_doctor_check_passes_on_every_fixture() -> None:
    for path in sorted(FIXTURES.glob("*.html")):
        meta_path = path.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        ok, detail = doctor_check(path.stem, path.read_text(encoding="utf-8"), meta)
        assert ok, f"{path.name}: {detail}"


# ---------------------------------------------------------------------------
# Flow — OpenriceScraper.search through a fake browser
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages
        self.url = ""

    def content(self) -> str:
        if self.url in self._pages:
            return self._pages[self.url]
        if "/review/" in self.url and "*review*" in self._pages:
            return self._pages["*review*"]
        return "<html><body></body></html>"

    def wait_for_selector(self, *a, **k) -> None:
        pass

    def wait_for_timeout(self, *a, **k) -> None:
        pass


class _FakeBrowser:
    """Serves fixture pages; document_html plays the server's HTML."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.page = _FakePage(pages)
        self.visited: list[str] = []

    @contextmanager
    def get_page(self, url: str):
        yield self.page

    def navigate(self, page, url: str, **kw) -> None:
        self.visited.append(url)
        page.url = url

    def ensure_ok(self, page, url: str) -> None:
        pass

    def document_html(self, page) -> str:
        return page.content()

    def close(self) -> None:
        pass


class _Fixtures:
    def __init__(self, existing: set[str] = frozenset()) -> None:
        self.existing = existing
        self.saved: dict[str, str] = {}

    def list_fixtures(self) -> list[str]:
        return sorted(self.existing)

    def save(self, name, html, metadata=None) -> None:
        self.saved[name] = html


class _AllowAll:
    def allowed(self, url, user_agent="*"):
        return True

    def denial_reason(self, url):
        return ""

    def close(self):
        pass


def _scraper(
    pages: dict[str, str], fixtures: _Fixtures | None = None, **kw
) -> tuple[OpenriceScraper, _FakeBrowser, _Fixtures]:
    browser, fixtures = _FakeBrowser(pages), fixtures or _Fixtures()
    scraper = OpenriceScraper(robots_cache=_AllowAll(), playwright=browser, fixtures=fixtures, **kw)
    return scraper, browser, fixtures


SEARCH = "https://www.openrice.com/en/hongkong/restaurants?what=ramen"


def test_older_reviews_are_skipped_not_a_stop_signal() -> None:
    """The list leads with featured reviews (here one from 2025-11-28) before
    the newest-first entries; the old scraper stopped at it and kept 3."""
    yuki_p1 = _html("reviews_530978_p1")
    scraper, browser, fixtures = _scraper(
        {SEARCH: _search_page(YUKI), f"{YUKI}/reviews?page=1": yuki_p1, f"{YUKI}/reviews?page=2": yuki_p1},
        fetch_full_text=False,
    )
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    posts = list(scraper.search("ramen", since=since, limit=100))

    assert len(posts) == 14
    assert "openrice_6254576" not in {p.id for p in posts}  # the 2025-11-28 review
    assert all(p.posted_at >= since for p in posts)
    # Page 2 repeated page 1 (no new reviews): pagination stopped there.
    assert browser.visited == [SEARCH, f"{YUKI}/reviews?page=1", f"{YUKI}/reviews?page=2"]
    assert list(fixtures.saved) == ["search_ramen", "reviews_530978_p1"]  # first of each kind only


def test_pagination_follows_the_page_total_not_the_card_count() -> None:
    """The card's smile + cry count leaves out OK reviews (a branch showing
    10 had 51); stopping on it dropped every later page."""
    p1 = _html("reviews_528467_p1_anon").replace(
        "<body>", '<body><div class="poi-score-detail-description">20 Reviews</div>', 1
    )
    scraper, browser, _ = _scraper(
        {
            SEARCH: _search_page(TG_PLACE, counts=(12, 1)),
            f"{TG_PLACE}/reviews?page=1": p1,
            f"{TG_PLACE}/reviews?page=2": _html("reviews_537014_p1_anon"),
        },
        fetch_full_text=False,
    )
    posts = list(scraper.search("ramen", since=datetime(2020, 1, 1, tzinfo=timezone.utc), limit=100))
    assert len(posts) == 30
    # 15 + 15 seen >= the page's own total of 20: no request for page 3.
    assert browser.visited == [SEARCH, f"{TG_PLACE}/reviews?page=1", f"{TG_PLACE}/reviews?page=2"]


def test_full_text_is_fetched_only_for_cut_off_extracts() -> None:
    scraper, browser, _ = _scraper({
        SEARCH: _search_page(TG_PLACE, counts=(15,)),
        f"{TG_PLACE}/reviews?page=1": _html("reviews_528467_p1_anon"),
        "*review*": _html("review_e6442858_anon"),
    })
    posts = list(scraper.search("ramen", since=datetime(2020, 1, 1, tzinfo=timezone.utc), limit=100))

    assert len(posts) == 15
    # From the server's HTML only the extract ending mid-sentence is checked
    # against its review page; then page 2 (empty here) ends the branch.
    assert browser.visited == [
        SEARCH,
        f"{TG_PLACE}/reviews?page=1",
        "https://www.openrice.com/en/hongkong/review/%E5%BF%AB%E9%A4%90%E5%BA%97%E9%83%BD%E6%9C%89"
        "%E9%9D%9A%E9%9D%9A%E5%85%92%E7%AB%A5%E9%A4%90-e5353507",
        f"{TG_PLACE}/reviews?page=2",
    ]
    completed = next(p for p in posts if p.id == "openrice_5353507")
    assert completed.raw_metadata["full_text"] is True
    assert completed.raw_metadata["extract_truncated"] is False


def test_live_dom_fallback_checks_every_extract_wide_enough_to_be_cut() -> None:
    """Without the server's HTML the scraper reads the live DOM, whose
    extracts the page's own script may have shortened."""
    scraper, browser, _ = _scraper({
        SEARCH: _search_page(TG_PLACE, counts=(15,)),
        f"{TG_PLACE}/reviews?page=1": _html("reviews_528467_p1_anon"),
        "*review*": _html("review_e6442858_anon"),
    })
    browser.document_html = lambda page: None
    list(scraper.search("ramen", since=datetime(2020, 1, 1, tzinfo=timezone.utc), limit=100))
    assert len([u for u in browser.visited if "/review/" in u]) == 15


def test_bot_check_that_does_not_clear_stops_the_source() -> None:
    scraper, _, _ = _scraper({SEARCH: "<html><body>BytePlus Security Check in Progress...</body></html>"})
    with pytest.raises(SourceError, match="security check"):
        list(scraper.search("ramen", since=datetime(2020, 1, 1, tzinfo=timezone.utc), limit=10))


# ---------------------------------------------------------------------------
# Saved pages — opt-in, anonymised, never overwriting
# ---------------------------------------------------------------------------


def test_live_pages_are_not_saved_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MKT_CAPTURE_FIXTURES", raising=False)
    scraper = OpenriceScraper(robots_cache=_AllowAll(), playwright=_FakeBrowser({}))
    assert scraper._fixtures is None


def test_saved_pages_are_anonymised_and_never_overwrite() -> None:
    page = _html("reviews_528467_p1_anon").replace(">reviewer_1<", ">Chan Tai Man<", 1)
    assert "Chan Tai Man" in page
    fixtures = _Fixtures(existing={"search_ramen"})
    scraper, _, _ = _scraper(
        {SEARCH: _search_page(TG_PLACE), f"{TG_PLACE}/reviews?page=1": page},
        fixtures=fixtures, fetch_full_text=False,
    )
    list(scraper.search("ramen", since=datetime(2020, 1, 1, tzinfo=timezone.utc), limit=100))
    assert list(fixtures.saved) == ["reviews_528467_p1"]  # search_ramen already exists
    assert "Chan Tai Man" not in fixtures.saved["reviews_528467_p1"]


_PERSONAL_PAGE = """<html><head><title>Review of X by Chan Tai Man | OpenRice</title>
<script type="application/ld+json">{"@type": "Review", "author": {"@type": "Person",
 "name": "Chan Tai Man", "url": "https://www.openrice.com/en/user/1b4e28ba-2fa1-11d2-883f-0016d3cca427/",
 "image": "https://static.example/u.jpg"}}</script></head><body>
<article class="review-post-desktop"><section class="review-post-main">
<div class="review-post-header"><a class="user-icon" href="/en/user/1b4e28ba-2fa1-11d2-883f-0016d3cca427/">
<img alt="Chan Tai Man" src="https://static.example/userphoto/u.jpg"></a>
<div class="review-post-writer-info"><div class="info-top"
 data-ga-event='{"label":"UserId:1b4e28ba-2fa1-11d2-883f-0016d3cca427"}'><a>Chan Tai Man</a></div></div></div>
<div class="review-post-body"><div class="review-post-extract">I, Chan Tai Man, liked it.</div></div>
</section></article></body></html>"""


def test_anonymise_replaces_reviewer_identity_but_keeps_review_text() -> None:
    out = anonymise_page_html(_PERSONAL_PAGE)
    assert out is not None
    outside = re.sub(r'<div class="review-post-extract">.*?</div>', "", out, flags=re.S)
    assert "Chan Tai Man" not in outside  # header, avatar, title, JSON-LD
    assert "1b4e28ba" not in out and "userphoto" not in out
    assert "I, Chan Tai Man, liked it." in out  # the reviewer's own words stay
    assert '"name": "reviewer_1"' in out


def test_anonymise_refuses_a_page_it_cannot_clean() -> None:
    leaky = _PERSONAL_PAGE.replace("</body>", '<input value="Chan Tai Man"></body>')
    assert anonymise_page_html(leaky) is None


def test_committed_review_fixtures_carry_no_reviewer_names() -> None:
    for path in sorted(FIXTURES.glob("review*.html")):
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
        names = [e.get_text(strip=True) for e in soup.select(".review-post-writer-info .info-top a, "
                                                             ".review-post-writer-info .info-top span")]
        assert names and all(re.fullmatch(r"reviewer_\d+", n) for n in names), path.name


# ---------------------------------------------------------------------------
# PlaywrightManager.navigate — the rules apply to every navigation
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status


class _GotoPage:
    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.status, self.error, self.calls = status, error, []

    def goto(self, url, **kw):
        self.calls.append(url)
        if self.error:
            raise self.error
        return _Resp(self.status)


class _DenyPath(_AllowAll):
    def allowed(self, url, user_agent="*"):
        return "/private" not in url

    def denial_reason(self, url):
        return f"robots.txt disallows {url} — skipping"


def test_navigate_checks_robots_for_each_url_before_loading_it() -> None:
    pw = PlaywrightManager(robots_cache=_DenyPath(), rate=0)
    page = _GotoPage()
    pw.navigate(page, "https://x.test/ok")
    with pytest.raises(ForbiddenError, match="robots.txt disallows"):
        pw.navigate(page, "https://x.test/private/1")
    assert page.calls == ["https://x.test/ok"]


def test_navigate_hard_fails_on_403() -> None:
    pw = PlaywrightManager(robots_cache=_AllowAll(), rate=0)
    with pytest.raises(ForbiddenError, match="403"):
        pw.navigate(_GotoPage(status=403), "https://x.test/a")


def test_navigate_explains_an_untrusted_proxy_certificate() -> None:
    pw = PlaywrightManager(robots_cache=_AllowAll(), rate=0)
    page = _GotoPage(error=RuntimeError("Page.goto: net::ERR_CERT_AUTHORITY_INVALID at https://x.test/"))
    with pytest.raises(SourceError, match="trust store"):
        pw.navigate(page, "https://x.test/")


class _NavResponse:
    """A main-frame document response as the page's "response" event sees it."""

    class _Frame:
        parent_frame = None

    class _Request:
        frame = None

        def is_navigation_request(self) -> bool:
            return True

    def __init__(self, status: int, body: str = "") -> None:
        self.status, self._body = status, body
        self.request = self._Request()
        self.request.frame = self._Frame()

    def text(self) -> str:
        return self._body


def test_empty_body_403_still_hard_fails() -> None:
    """Chromium aborts an error status with an empty body as
    ERR_HTTP_RESPONSE_CODE_FAILURE; the recorded status still says 403."""
    pw = PlaywrightManager(robots_cache=_AllowAll(), rate=0)

    class _Page:
        def goto(self, url, **kw):
            pw._record_navigation(self, _NavResponse(403))
            raise RuntimeError("Page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE")

    with pytest.raises(ForbiddenError, match="403"):
        pw.navigate(_Page(), "https://x.test/review/1")


def test_a_403_on_the_reload_after_the_interstitial_is_caught() -> None:
    pw = PlaywrightManager(robots_cache=_AllowAll(), rate=0)

    class _Page:
        def goto(self, url, **kw):
            response = _NavResponse(200, "<html>checking…</html>")
            pw._record_navigation(self, response)
            return response

    page = _Page()
    pw.navigate(page, "https://x.test/list")  # the interstitial: 200
    pw._record_navigation(page, _NavResponse(403, "Access Denied"))  # its reload
    with pytest.raises(ForbiddenError, match="403"):
        pw.ensure_ok(page, "https://x.test/list")
    assert pw.document_html(page) == "Access Denied"
