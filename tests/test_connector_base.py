"""Tests de l'interface commune BaseConnector et du MockConnector."""

from datetime import datetime

import pytest

from research_agent.collectors.base import BaseConnector
from research_agent.collectors.mock import MockConnector
from research_agent.models import RawItem

SINCE = datetime(2026, 9, 1)


def test_base_connector_is_abstract():
    # On ne doit pas pouvoir instancier l'interface directement.
    with pytest.raises(TypeError):
        BaseConnector()  # type: ignore[abstract]


def test_mock_fetch_returns_rawitems():
    connector = MockConnector()
    items = connector.fetch(SINCE)
    assert len(items) == 1
    assert isinstance(items[0], RawItem)
    assert items[0].source == "mock"


def test_mock_fetch_returns_provided_items():
    provided = [
        RawItem(source="mock", title="A", url="https://e.org/a"),
        RawItem(source="mock", title="B", url="https://e.org/b"),
    ]
    connector = MockConnector(items=provided)
    assert connector.fetch(SINCE) == provided


def test_collect_returns_items_on_success():
    connector = MockConnector()
    items = connector.collect(SINCE)
    assert len(items) == 1


def test_collect_isolates_failure():
    """Une panne dans fetch() ne doit pas se propager : collect() renvoie []."""
    connector = MockConnector(fail=True)
    # fetch() leve bien...
    with pytest.raises(Exception):
        connector.fetch(SINCE)
    # ... mais collect() isole la panne.
    assert connector.collect(SINCE) == []


def test_collect_returns_homogeneous_rawitems():
    connector = MockConnector()
    items = connector.collect(SINCE)
    assert all(isinstance(i, RawItem) for i in items)


def test_http_client_is_lazily_created_and_closed():
    connector = MockConnector()
    assert connector._client is None
    client = connector.client
    assert client is not None
    # Meme instance reutilisee.
    assert connector.client is client
    connector.close()
    assert connector._client is None


def test_context_manager_closes_client():
    with MockConnector() as connector:
        _ = connector.client
        assert connector._client is not None
    assert connector._client is None


def test_connector_has_named_logger():
    connector = MockConnector()
    assert connector.logger.name == "research_agent.collectors.mock"
