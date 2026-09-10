"""Tests du connecteur Google News (URL de flux, parsing, dates, robustesse)."""

from datetime import datetime
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from research_agent.collectors.googlenews import (
    GoogleNewsConnector,
    build_feed_url,
)
from research_agent.exceptions import ConnectorError
from research_agent.models import RawItem

SINCE = datetime(2026, 1, 1)


def _rss(items_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>Google News</title>'
        f"{items_xml}"
        "</channel></rss>"
    )


def _item_xml(title, link, description="", pub_dt=None, source=None):
    pub = f"<pubDate>{format_datetime(pub_dt)}</pubDate>" if pub_dt else ""
    src = f'<source url="https://x">{source}</source>' if source else ""
    return (
        "<item>"
        f"<title>{title}</title>"
        f"<link>{link}</link>"
        f"<description>{description}</description>"
        f"{pub}{src}"
        "</item>"
    )


def _mock_get(text):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.text = text
    return resp


def _connector(**kw):
    return GoogleNewsConnector(query="schimmel woning", **kw)


# --- Construction de l'URL de flux -------------------------------------------


def test_build_feed_url_encodes_query():
    url = build_feed_url("schimmel woning OR mould")
    assert "news.google.com/rss/search" in url
    assert "schimmel+woning" in url
    assert "mould" in url


def test_connector_uses_query_based_feed_url():
    connector = _connector()
    assert "news.google.com/rss/search" in connector.feed_url
    assert connector.query == "schimmel woning"


# --- Parsing -----------------------------------------------------------------


def test_fetch_parses_article_into_rawitem():
    xml = _rss(
        _item_xml(
            "Schimmel in huurwoningen neemt toe",
            "https://news.google.com/articles/abc",
            description="Bericht over vocht en schimmel",
            pub_dt=datetime(2026, 8, 1),
            source="NOS",
        )
    )
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        items = connector.fetch(SINCE)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, RawItem)
    assert item.source == "google_news"
    assert item.metadata["publisher"] == "NOS"


def test_title_suffix_publisher_is_stripped():
    xml = _rss(
        _item_xml(
            "Schimmelklachten stijgen - NOS",
            "https://news.google.com/articles/xyz",
            source="NOS",
        )
    )
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        item = connector.fetch(SINCE)[0]
    # Le suffixe " - NOS" est retire du titre, le media va dans les metadonnees.
    assert item.title == "Schimmelklachten stijgen"
    assert item.metadata["publisher"] == "NOS"


def test_article_without_source_still_parsed():
    xml = _rss(_item_xml("Titre sans source", "https://news.google.com/a"))
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        items = connector.fetch(SINCE)
    assert len(items) == 1
    assert "publisher" not in items[0].metadata


# --- Gestion des dates -------------------------------------------------------


def test_date_filter_excludes_old_articles():
    xml = _rss(
        _item_xml("Recent", "https://news.google.com/new", pub_dt=datetime(2026, 5, 1))
        + _item_xml("Ancien", "https://news.google.com/old", pub_dt=datetime(2024, 1, 1))
    )
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        urls = [str(i.url) for i in connector.fetch(SINCE)]
    assert "https://news.google.com/new" in urls
    assert "https://news.google.com/old" not in urls


def test_article_without_date_kept():
    xml = _rss(_item_xml("Sans date", "https://news.google.com/x"))
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        items = connector.fetch(SINCE)
    assert items[0].published_at is None


# --- Robustesse --------------------------------------------------------------


def test_respects_max_results():
    xml = _rss(
        "".join(_item_xml(f"T{i}", f"https://news.google.com/{i}") for i in range(6))
    )
    connector = _connector(max_results=3)
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        assert len(connector.fetch(SINCE)) == 3


def test_fetch_raises_on_http_error():
    connector = _connector()
    with patch.object(connector.client, "get", side_effect=httpx.ConnectError("x")):
        with pytest.raises(ConnectorError):
            connector.fetch(SINCE)


def test_collect_isolates_failure():
    connector = _connector()
    with patch.object(connector.client, "get", side_effect=httpx.ConnectError("x")):
        assert connector.collect(SINCE) == []
