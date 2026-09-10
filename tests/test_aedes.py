"""Tests du connecteur Aedes (parsing RSS + filtrage + robustesse)."""

from datetime import datetime
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from research_agent.collectors.aedes import AedesConnector
from research_agent.exceptions import ConnectorError
from research_agent.models import RawItem

FEED_URL = "https://www.aedes.nl/rss"
SINCE = datetime(2026, 1, 1)


def _rss(items_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>Aedes</title>'
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
    return AedesConnector(
        feed_url=FEED_URL, keywords=keywords or [], max_results=max_results
    )


def test_fetch_parses_entry_into_rawitem():
    xml = _rss(
        _item_xml(
            "Ventilatie in sociale huurwoningen",
            "https://aedes.nl/artikel/1",
            description="Aandacht voor luchtkwaliteit en duurzaamheid",
            pub_dt=datetime(2026, 8, 15),
            guid="AEDES-1",
        )
    )
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        items = connector.fetch(SINCE)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, RawItem)
    assert item.source == "aedes"
    assert item.title == "Ventilatie in sociale huurwoningen"
    assert str(item.url) == "https://aedes.nl/artikel/1"
    assert item.language == "nl"
    assert item.metadata["guid"] == "AEDES-1"


def test_keyword_filter():
    xml = _rss(
        _item_xml("Ventilatie project", "https://aedes.nl/1", description="luchtkwaliteit")
        + _item_xml("Financieel jaarverslag", "https://aedes.nl/2", description="cijfers")
    )
    connector = _connector(keywords=["ventilatie", "luchtkwaliteit"])
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        items = connector.fetch(SINCE)
    assert len(items) == 1
    assert str(items[0].url) == "https://aedes.nl/1"


def test_date_filter_excludes_old():
    xml = _rss(
        _item_xml("Recent", "https://aedes.nl/new", pub_dt=datetime(2026, 6, 1))
        + _item_xml("Ancien", "https://aedes.nl/old", pub_dt=datetime(2024, 1, 1))
    )
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        urls = [str(i.url) for i in connector.fetch(SINCE)]
    assert "https://aedes.nl/new" in urls
    assert "https://aedes.nl/old" not in urls


def test_entry_without_link_skipped():
    xml = _rss("<item><title>Sans lien</title></item>")
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        assert connector.fetch(SINCE) == []


def test_respects_max_results():
    xml = _rss("".join(_item_xml(f"T{i}", f"https://aedes.nl/{i}") for i in range(5)))
    connector = _connector(max_results=3)
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        assert len(connector.fetch(SINCE)) == 3


def test_fetch_raises_on_http_error():
    connector = _connector()
    with patch.object(connector.client, "get", side_effect=httpx.ConnectError("boom")):
        with pytest.raises(ConnectorError):
            connector.fetch(SINCE)


def test_collect_isolates_failure():
    connector = _connector()
    with patch.object(connector.client, "get", side_effect=httpx.ConnectError("boom")):
        assert connector.collect(SINCE) == []
