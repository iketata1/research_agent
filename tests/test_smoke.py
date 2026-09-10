"""Test de fumee : verifie que le package s'importe et que le modele fonctionne."""

from research_agent import __version__
from research_agent.models import RawItem, Theme


def test_version():
    assert __version__ == "0.1.0"


def test_item_defaults():
    item = RawItem(source="openalex", title="Exemple", url="https://example.org")
    assert item.raw_text == ""
    assert item.theme is Theme.UNKNOWN
    assert item.relevance is None
    assert item.metadata == {}
    assert item.id  # genere automatiquement
