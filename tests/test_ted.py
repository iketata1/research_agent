"""Tests du connecteur TED (parsing API, dates, robustesse)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from research_agent.collectors.ted import TEDConnector, _first_text
from research_agent.exceptions import ConnectorError
from research_agent.models import RawItem

SINCE = datetime(2026, 1, 1)


def _notice(**overrides):
    base = {
        "publication-number": "123456-2026",
        "notice-title": {"eng": "Ventilation works for social housing"},
        "publication-date": "2026-09-01+02:00",
        "links": "https://ted.europa.eu/notice/123456-2026",
        "buyer-name": {"eng": "Gemeente Amsterdam"},
        "notice-type": "cn-standard",
    }
    base.update(overrides)
    return base


def _mock_response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _connector(max_results=100):
    return TEDConnector(query="ventilation OR indoor air quality", max_results=max_results)


# --- Extraction de texte multilingue -----------------------------------------


def test_first_text_from_string():
    assert _first_text("hello") == "hello"


def test_first_text_prefers_english_in_dict():
    assert _first_text({"fra": "bonjour", "eng": "hello"}) == "hello"


def test_first_text_from_list():
    assert _first_text(["", "second"]) == "second"


def test_first_text_none():
    assert _first_text(None) is None


# --- Parsing -----------------------------------------------------------------


def test_fetch_parses_notice_into_rawitem():
    connector = _connector()
    payload = {"notices": [_notice()]}
    with patch.object(connector.client, "post", return_value=_mock_response(payload)):
        items = connector.fetch(SINCE)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, RawItem)
    assert item.source == "ted"
    assert item.title == "Ventilation works for social housing"
    assert str(item.url) == "https://ted.europa.eu/notice/123456-2026"
    assert item.published_at == datetime(2026, 9, 1)
    assert item.metadata["publication_number"] == "123456-2026"
    assert item.metadata["buyer"] == "Gemeente Amsterdam"


def test_url_derived_from_publication_number_when_no_link():
    connector = _connector()
    payload = {"notices": [_notice(links=None)]}
    with patch.object(connector.client, "post", return_value=_mock_response(payload)):
        item = connector.fetch(SINCE)[0]
    assert "TED:NOTICE:123456-2026" in str(item.url)


def test_notice_without_title_skipped():
    connector = _connector()
    payload = {"notices": [_notice(**{"notice-title": None})]}
    with patch.object(connector.client, "post", return_value=_mock_response(payload)):
        assert connector.fetch(SINCE) == []


def test_notice_without_title_and_number_skipped():
    connector = _connector()
    payload = {"notices": [_notice(**{"notice-title": None, "publication-number": None, "links": None})]}
    with patch.object(connector.client, "post", return_value=_mock_response(payload)):
        assert connector.fetch(SINCE) == []


def test_results_key_fallback():
    """L'API peut renvoyer 'results' au lieu de 'notices'."""
    connector = _connector()
    payload = {"results": [_notice()]}
    with patch.object(connector.client, "post", return_value=_mock_response(payload)):
        assert len(connector.fetch(SINCE)) == 1


def test_respects_max_results():
    connector = _connector(max_results=2)
    notices = [_notice(**{"publication-number": f"{i}-2026", "links": None}) for i in range(5)]
    payload = {"notices": notices}
    with patch.object(connector.client, "post", return_value=_mock_response(payload)):
        assert len(connector.fetch(SINCE)) == 2


# --- Expert query ------------------------------------------------------------


def test_expert_query_includes_date_and_keywords():
    connector = _connector()
    q = connector._build_expert_query(SINCE)
    assert "ventilation OR indoor air quality" in q
    assert "publication-date>=20260101" in q


# --- Robustesse --------------------------------------------------------------


def test_fetch_raises_on_http_error():
    connector = _connector()
    with patch.object(connector.client, "post", side_effect=httpx.ConnectError("x")):
        with pytest.raises(ConnectorError):
            connector.fetch(SINCE)


def test_collect_isolates_failure():
    connector = _connector()
    with patch.object(connector.client, "post", side_effect=httpx.ConnectError("x")):
        assert connector.collect(SINCE) == []


def test_fetch_raises_on_invalid_json():
    connector = _connector()
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.side_effect = ValueError("bad json")
    with patch.object(connector.client, "post", return_value=resp):
        with pytest.raises(ConnectorError):
            connector.fetch(SINCE)
