"""Wikipedia feed links must point at articles a viewer can actually check a claim against.

The fixtures below are trimmed from the live feeds (2026-09-08). Both feeds hand out a first
`/wiki/` link that is worthless as a citation, in different ways, which is what shipped a video
citing `Special:FeedItem/featured/20260903000000/en`.
"""

from __future__ import annotations

from aimz.providers.research.rss import (
    _split_wikipedia_onthisday,
    wiki_article_url,
)

ONTHISDAY_LI = (
    '<li><a href="/wiki/1800" title="1800">1800</a> – Gabriel Prosser postponed '
    '<b><a href="/wiki/Gabriel%27s_Rebellion" title="Gabriel\'s Rebellion">a planned slave '
    'revolt</a></b> in <a href="/wiki/Virginia" title="Virginia">Virginia</a>, but he was '
    "betrayed and hanged.</li>"
)

FEATURED_HTML = (
    '<p><a href="/wiki/File:AZ_vs_Ipswich,_1981.jpg" class="image"><img src="x.jpg"></a>'
    '<a href="/wiki/Arnold_M%C3%BChren">Arnold Mühren</a> played in the '
    '<b><a href="/wiki/1980%E2%80%9381_Ipswich_Town_F.C._season">1980–81 season</a></b> of '
    '<a href="/wiki/Ipswich_Town_F.C.">Ipswich Town</a>.</p>'
)


def test_onthisday_prefers_the_bolded_subject_over_the_year() -> None:
    """The first link in an 'On this day' item is always the year, which cites nothing."""
    url = wiki_article_url(ONTHISDAY_LI)
    assert url == "https://en.wikipedia.org/wiki/Gabriel%27s_Rebellion"
    assert "/wiki/1800" not in url


def test_featured_skips_the_image_file_page() -> None:
    """The first link in a featured blurb is the illustration's File: page."""
    url = wiki_article_url(FEATURED_HTML)
    assert url == "https://en.wikipedia.org/wiki/1980%E2%80%9381_Ipswich_Town_F.C._season"
    assert "File:" not in url


def test_falls_back_to_the_first_real_article_when_nothing_is_bolded() -> None:
    fragment = '<li><a href="/wiki/1912">1912</a> and <a href="/wiki/Titanic">Titanic</a> sank.</li>'
    assert wiki_article_url(fragment) == "https://en.wikipedia.org/wiki/Titanic"


def test_dates_decades_and_namespaces_are_never_citations() -> None:
    for slug in ("1800", "1980s", "August_30", "File:X.jpg", "Special:FeedItem", "Category:Ships"):
        fragment = f'<a href="/wiki/{slug}">x</a>'
        assert wiki_article_url(fragment, "FALLBACK") == "FALLBACK", slug


def test_fallback_is_used_when_there_is_no_wiki_link_at_all() -> None:
    assert wiki_article_url("<p>no links here</p>", "https://example.org/x") == "https://example.org/x"
    assert wiki_article_url("<p>no links here</p>") == ""


def test_split_onthisday_pairs_text_with_the_article_url() -> None:
    entry = {
        "content": [{"value": f"<ul>{ONTHISDAY_LI}</ul>"}],
        "link": "https://en.wikipedia.org/wiki/Special:FeedItem/onthisday/20260830000000/en",
    }
    leads = _split_wikipedia_onthisday(entry)
    assert len(leads) == 1
    text, url = leads[0]
    assert "Gabriel Prosser" in text
    assert url == "https://en.wikipedia.org/wiki/Gabriel%27s_Rebellion"
    assert "Special:FeedItem" not in url  # the entry permalink must never win


def test_short_list_items_are_dropped() -> None:
    entry = {"content": [{"value": "<ul><li>too short</li></ul>"}], "link": "x"}
    assert _split_wikipedia_onthisday(entry) == []
