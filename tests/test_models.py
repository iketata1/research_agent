"""Tests du modele normalise RawItem."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from research_agent.models import RawItem, Theme, compute_stable_id


def test_auto_id_generated_from_url():
    item = RawItem(source="aedes", title="Titre", url="https://aedes.nl/a")
    assert item.id is not None
    assert len(item.id) == 16


def test_same_url_yields_same_id():
    a = RawItem(source="s1", title="T1", url="https://example.org/x")
    b = RawItem(source="s2", title="T2", url="https://example.org/x")
    # Meme URL -> meme id (deduplication inter-sources sur la ressource).
    assert a.id == b.id


def test_doi_takes_priority_over_url():
    with_doi = RawItem(
        source="openalex",
        title="Paper",
        url="https://example.org/paper",
        metadata={"doi": "10.1000/xyz"},
    )
    without_doi = RawItem(
        source="openalex",
        title="Paper",
        url="https://example.org/paper",
    )
    assert with_doi.id != without_doi.id
    assert with_doi.id == compute_stable_id(doi="10.1000/xyz")


def test_explicit_id_is_preserved():
    item = RawItem(
        id="custom-123", source="ted", title="T", url="https://ted.europa.eu/n"
    )
    assert item.id == "custom-123"


def test_language_normalized_to_lowercase():
    item = RawItem(source="s", title="T", url="https://e.org", language="EN")
    assert item.language == "en"


def test_invalid_url_rejected():
    with pytest.raises(ValidationError):
        RawItem(source="s", title="T", url="not-a-url")


def test_empty_source_rejected():
    with pytest.raises(ValidationError):
        RawItem(source="", title="T", url="https://e.org")


def test_relevance_out_of_range_rejected():
    with pytest.raises(ValidationError):
        RawItem(source="s", title="T", url="https://e.org", relevance=1.5)


def test_metadata_and_theme_defaults():
    item = RawItem(
        source="rechtspraak",
        title="Uitspraak",
        url="https://rechtspraak.nl/1",
        published_at=datetime(2026, 9, 7),
        metadata={"ecli": "ECLI:NL:XX:2026:1"},
    )
    assert item.theme is Theme.UNKNOWN
    assert item.metadata["ecli"] == "ECLI:NL:XX:2026:1"


def test_compute_stable_id_requires_a_key():
    with pytest.raises(ValueError):
        compute_stable_id()
