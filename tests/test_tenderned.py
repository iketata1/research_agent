"""Tests du connecteur TenderNed (parsing RSS + filtrage + robustesse)."""

from datetime import datetime
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from research_agent.collectors.tenderned import TenderNedConnector
from research_agent.exceptions import ConnectorError
from research_agent.models import RawItem

FEED_URL = "https://www.tenderned.nl/rss"
SINCE = datetime(2026, 1, 1)


def _rss(items_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>TenderNed</title>'
        f"{items_xml}"
        "</channel></rss>"
    )


def _item_xml(title, link, description="", pub_dt=None, guid=None):
    pub = f"<pubDate>{format_datetime(pub_dt)}</pubDate>" if pub_dt else ""
    guid_xml = f"<guid>{guid}</guid>" if guid else ""
    return (
        "<item>"
        f"<title>{title}</title>"
        f"<link>{link}</link>"
        f"<description>{description}</description>"
        f"{pub}{guid_xml}"
        "</item>"
    )


def _mock_get(text):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.text = text
    return resp


def _connector(keywords=None, max_results=100):
    return TenderNedConnector(
        feed_url=FEED_URL, keywords=keywords or [], max_results=max_results
    )


# --- Parsing -----------------------------------------------------------------


def test_fetch_parses_entry_into_rawitem():
    xml = _rss(
        _item_xml(
            "Ventilatie renovatie woningen",
            "https://tenderned.nl/aankondigingen/1",
            description="Aanbesteding voor ventilatie en luchtkwaliteit",
            pub_dt=datetime(2026, 9, 1),
            guid="TN-001",
        )
    )
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        items = connector.fetch(SINCE)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, RawItem)
    assert item.source == "tenderned"
    assert item.title == "Ventilatie renovatie woningen"
    assert str(item.url) == "https://tenderned.nl/aankondigingen/1"
    assert item.language == "nl"
    assert item.metadata["guid"] == "TN-001"


# --- Filtrage par mots-cles --------------------------------------------------


def test_keyword_filter_keeps_matching():
    xml = _rss(
        _item_xml("Ventilatie project", "https://tn.nl/1", description="luchtkwaliteit")
        + _item_xml("Wegenbouw asfalt", "https://tn.nl/2", description="asfaltwerk")
    )
    connector = _connector(keywords=["ventilatie", "luchtkwaliteit"])
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        items = connector.fetch(SINCE)
    assert len(items) == 1
    assert str(items[0].url) == "https://tn.nl/1"


def test_no_keywords_keeps_all():
    xml = _rss(
        _item_xml("A", "https://tn.nl/1") + _item_xml("B", "https://tn.nl/2")
    )
    connector = _connector(keywords=[])
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        assert len(connector.fetch(SINCE)) == 2


# --- Filtrage par date -------------------------------------------------------


def test_date_filter_excludes_old_entries():
    xml = _rss(
        _item_xml("Recent", "https://tn.nl/new", pub_dt=datetime(2026, 6, 1))
        + _item_xml("Ancien", "https://tn.nl/old", pub_dt=datetime(2025, 1, 1))
    )
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        items = connector.fetch(SINCE)
    urls = [str(i.url) for i in items]
    assert "https://tn.nl/new" in urls
    assert "https://tn.nl/old" not in urls


def test_entry_without_date_is_kept():
    xml = _rss(_item_xml("Sans date", "https://tn.nl/x"))
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        items = connector.fetch(SINCE)
    assert len(items) == 1
    assert items[0].published_at is None


# --- Cas limites -------------------------------------------------------------


def test_entry_without_link_is_skipped():
    xml = _rss("<item><title>Sans lien</title></item>")
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        assert connector.fetch(SINCE) == []


def test_respects_max_results():
    xml = _rss("".join(_item_xml(f"T{i}", f"https://tn.nl/{i}") for i in range(5)))
    connector = _connector(max_results=2)
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        assert len(connector.fetch(SINCE)) == 2


# --- Robustesse reseau -------------------------------------------------------


def test_fetch_raises_connectorerror_on_http_error():
    connector = _connector()
    with patch.object(
        connector.client, "get", side_effect=httpx.ConnectError("boom")
    ):
        with pytest.raises(ConnectorError):
            connector.fetch(SINCE)


def test_collect_isolates_network_failure():
    connector = _connector()
    with patch.object(
        connector.client, "get", side_effect=httpx.ConnectError("boom")
    ):
        assert connector.collect(SINCE) == []
