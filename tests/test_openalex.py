"""Tests du connecteur OpenAlex (parsing + robustesse reseau)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from research_agent.collectors.openalex import (
    OpenAlexConnector,
    reconstruct_abstract,
)
from research_agent.exceptions import ConnectorError
from research_agent.models import RawItem

SINCE = datetime(2026, 1, 1)


# --- Reconstruction d'abstract ----------------------------------------------


def test_reconstruct_abstract_orders_words():
    inverted = {"Indoor": [0], "air": [1], "quality": [2], "matters": [3]}
    assert reconstruct_abstract(inverted) == "Indoor air quality matters"


def test_reconstruct_abstract_handles_repeated_words():
    inverted = {"mould": [0, 2], "and": [1]}
    assert reconstruct_abstract(inverted) == "mould and mould"


def test_reconstruct_abstract_empty():
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""


# --- Helpers -----------------------------------------------------------------


def _work(**overrides):
    base = {
        "id": "https://openalex.org/W123",
        "title": "Improved mould-prediction model for concrete",
        "doi": "https://doi.org/10.1000/xyz",
        "publication_date": "2026-09-01",
        "language": "en",
        "type": "article",
        "cited_by_count": 5,
        "abstract_inverted_index": {"A": [0], "study": [1], "on": [2], "humidity": [3]},
    }
    base.update(overrides)
    return base


def _mock_response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


# --- Parsing -----------------------------------------------------------------


def test_fetch_parses_work_into_rawitem():
    connector = OpenAlexConnector(query="mould", max_results=10)
    payload = {"results": [_work()]}
    with patch.object(connector.client, "get", return_value=_mock_response(payload)):
        items = connector.fetch(SINCE)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, RawItem)
    assert item.source == "openalex"
    assert item.title == "Improved mould-prediction model for concrete"
    assert str(item.url) == "https://openalex.org/W123"
    assert item.raw_text == "A study on humidity"
    assert item.published_at == datetime(2026, 9, 1)


def test_fetch_extracts_doi_into_metadata():
    connector = OpenAlexConnector(query="mould")
    payload = {"results": [_work()]}
    with patch.object(connector.client, "get", return_value=_mock_response(payload)):
        item = connector.fetch(SINCE)[0]
    # DOI nettoye (sans prefixe URL) -> sert de cle stable pour l'id.
    assert item.metadata["doi"] == "10.1000/xyz"
    assert item.metadata["cited_by_count"] == 5


def test_fetch_skips_work_without_title():
    connector = OpenAlexConnector(query="mould")
    payload = {"results": [_work(title=None, display_name=None)]}
    with patch.object(connector.client, "get", return_value=_mock_response(payload)):
        assert connector.fetch(SINCE) == []


def test_fetch_respects_max_results():
    connector = OpenAlexConnector(query="mould", max_results=2)
    works = [_work(id=f"https://openalex.org/W{i}", doi=None) for i in range(5)]
    payload = {"results": works}
    with patch.object(connector.client, "get", return_value=_mock_response(payload)):
        items = connector.fetch(SINCE)
    assert len(items) == 2


def test_build_params_includes_date_filter():
    connector = OpenAlexConnector(query="mould OR humidity", mailto="a@b.org")
    params = connector._build_params(SINCE)
    assert params["search"] == "mould OR humidity"
    assert params["filter"] == "from_publication_date:2026-01-01"
    assert params["mailto"] == "a@b.org"


# --- Robustesse reseau -------------------------------------------------------


def test_fetch_raises_connectorerror_on_http_error():
    connector = OpenAlexConnector(query="mould")
    with patch.object(
        connector.client, "get", side_effect=httpx.ConnectError("boom")
    ):
        with pytest.raises(ConnectorError):
            connector.fetch(SINCE)


def test_collect_isolates_network_failure():
    """collect() ne doit jamais propager : renvoie [] en cas de panne reseau."""
    connector = OpenAlexConnector(query="mould")
    with patch.object(
        connector.client, "get", side_effect=httpx.ConnectError("boom")
    ):
        assert connector.collect(SINCE) == []


def test_fetch_raises_on_invalid_json():
    connector = OpenAlexConnector(query="mould")
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.side_effect = ValueError("not json")
    with patch.object(connector.client, "get", return_value=resp):
        with pytest.raises(ConnectorError):
            connector.fetch(SINCE)
