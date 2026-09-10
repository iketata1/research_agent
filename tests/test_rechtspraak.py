"""Tests du connecteur Rechtspraak (parsing Atom XML + filtrage + robustesse)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from research_agent.collectors.rechtspraak import RechtspraakConnector
from research_agent.exceptions import ConnectorError
from research_agent.models import RawItem

SINCE = datetime(2026, 1, 1)


def _atom(entries_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>Rechtspraak</title>"
        f"{entries_xml}"
        "</feed>"
    )


def _entry(ecli, title, summary="", updated="2026-09-01T00:00:00Z", link=None):
    link_xml = f'<link href="{link}"/>' if link else ""
    return (
        "<entry>"
        f"<id>{ecli}</id>"
        f"<title>{title}</title>"
        f"<summary>{summary}</summary>"
        f"<updated>{updated}</updated>"
        f"{link_xml}"
        "</entry>"
    )


def _mock_get(text):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.text = text
    return resp


def _connector(keywords=None, max_results=50):
    return RechtspraakConnector(keywords=keywords or [], max_results=max_results)


# --- Parsing -----------------------------------------------------------------


def test_fetch_parses_entry_into_rawitem():
    xml = _atom(
        _entry(
            "ECLI:NL:RBAMS:2026:1234",
            "Huurder vs verhuurder over schimmel in woning",
            summary="Geschil over vocht en schimmel",
            link="https://uitspraken.rechtspraak.nl/details?id=ECLI:NL:RBAMS:2026:1234",
        )
    )
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        items = connector.fetch(SINCE)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, RawItem)
    assert item.source == "rechtspraak"
    assert item.metadata["ecli"] == "ECLI:NL:RBAMS:2026:1234"
    assert item.language == "nl"
    assert item.published_at == datetime(2026, 9, 1)


def test_deeplink_derived_from_ecli_when_no_link():
    xml = _atom(_entry("ECLI:NL:RBAMS:2026:9", "Schimmel geschil"))
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        item = connector.fetch(SINCE)[0]
    assert "ECLI:NL:RBAMS:2026:9" in str(item.url)


# --- Filtrage mots-cles ------------------------------------------------------


def test_keyword_filter():
    xml = _atom(
        _entry("ECLI:NL:X:2026:1", "Geschil over schimmel en vocht")
        + _entry("ECLI:NL:X:2026:2", "Verkeersovertreding snelheid")
    )
    connector = _connector(keywords=["schimmel", "vocht"])
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        items = connector.fetch(SINCE)
    assert len(items) == 1
    assert items[0].metadata["ecli"] == "ECLI:NL:X:2026:1"


def test_no_keywords_keeps_all():
    xml = _atom(
        _entry("ECLI:NL:X:2026:1", "A") + _entry("ECLI:NL:X:2026:2", "B")
    )
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        assert len(connector.fetch(SINCE)) == 2


# --- Cas limites -------------------------------------------------------------


def test_entry_without_ecli_skipped():
    xml = _atom("<entry><title>Sans ECLI</title></entry>")
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        assert connector.fetch(SINCE) == []


def test_respects_max_results():
    xml = _atom(
        "".join(_entry(f"ECLI:NL:X:2026:{i}", f"Titre {i}") for i in range(5))
    )
    connector = _connector(max_results=2)
    with patch.object(connector.client, "get", return_value=_mock_get(xml)):
        assert len(connector.fetch(SINCE)) == 2


def test_build_params_includes_date():
    connector = _connector()
    params = connector._build_params(SINCE)
    assert params["date"] == "2026-01-01"


# --- Robustesse --------------------------------------------------------------


def test_fetch_raises_on_malformed_xml():
    connector = _connector()
    with patch.object(connector.client, "get", return_value=_mock_get("<not xml")):
        with pytest.raises(ConnectorError):
            connector.fetch(SINCE)


def test_fetch_raises_on_http_error():
    connector = _connector()
    with patch.object(connector.client, "get", side_effect=httpx.ConnectError("x")):
        with pytest.raises(ConnectorError):
            connector.fetch(SINCE)


def test_collect_isolates_failure():
    connector = _connector()
    with patch.object(connector.client, "get", side_effect=httpx.ConnectError("x")):
        assert connector.collect(SINCE) == []
