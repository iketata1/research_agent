"""Tests de la classification thematique par le LLM."""

import json
from unittest.mock import MagicMock

import pytest

from research_agent.exceptions import LLMError
from research_agent.filtering.classifier import (
    Classification,
    build_batch_prompt,
    classify_items,
    _parse,
)
from research_agent.models import RawItem, Theme


def _item(title="Titre", raw_text="", url="https://example.org/a"):
    return RawItem(source="test", title=title, url=url, raw_text=raw_text)


def _fake_client(response_text):
    client = MagicMock()
    if isinstance(response_text, list):
        client.generate.side_effect = response_text
    else:
        client.generate.return_value = response_text
    return client


def _json(*entries):
    return json.dumps({"classifications": list(entries)})


# --- Schema Pydantic ---------------------------------------------------------


def test_classification_accepts_valid_category():
    c = Classification(index=0, category="research", confidence=0.9)
    assert c.category is Theme.RESEARCH


def test_classification_invalid_label_becomes_unknown():
    c = Classification(index=0, category="sports", confidence=0.5)
    assert c.category is Theme.UNKNOWN


def test_classification_confidence_clamped():
    with pytest.raises(Exception):
        Classification(index=0, category="legal", confidence=1.5)


# --- Parsing -----------------------------------------------------------------


def test_parse_maps_by_index():
    raw = _json(
        {"index": 0, "category": "opportunity", "confidence": 0.8},
        {"index": 1, "category": "legal", "confidence": 0.7},
    )
    parsed = _parse(raw)
    assert parsed[0].category is Theme.OPPORTUNITY
    assert parsed[1].category is Theme.LEGAL


def test_parse_tolerates_json_fence():
    raw = "```json\n" + _json({"index": 0, "category": "technology", "confidence": 0.6}) + "\n```"
    parsed = _parse(raw)
    assert parsed[0].category is Theme.TECHNOLOGY


def test_parse_raises_on_invalid_json():
    with pytest.raises(LLMError):
        _parse("pas du json")


# --- Classification de bout en bout ------------------------------------------


def test_classify_items_assigns_theme_and_confidence():
    items = [_item(title="Etude scientifique"), _item(title="Tender", url="https://e.org/b")]
    raw = _json(
        {"index": 0, "category": "research", "confidence": 0.95},
        {"index": 1, "category": "opportunity", "confidence": 0.88},
    )
    result = classify_items(items, client=_fake_client(raw))
    assert result[0].theme is Theme.RESEARCH
    assert result[0].metadata["theme_confidence"] == pytest.approx(0.95)
    assert result[1].theme is Theme.OPPORTUNITY


def test_classify_items_missing_entry_is_unknown():
    items = [_item(url="https://e.org/1"), _item(url="https://e.org/2")]
    raw = _json({"index": 0, "category": "legal", "confidence": 0.9})
    result = classify_items(items, client=_fake_client(raw))
    assert result[0].theme is Theme.LEGAL
    assert result[1].theme is Theme.UNKNOWN
    assert result[1].metadata["theme_confidence"] == 0.0


def test_classify_items_batches_calls():
    items = [_item(url=f"https://e.org/{i}") for i in range(5)]
    responses = [
        _json({"index": 0, "category": "research", "confidence": 0.5},
              {"index": 1, "category": "research", "confidence": 0.5}),
        _json({"index": 0, "category": "legal", "confidence": 0.5},
              {"index": 1, "category": "legal", "confidence": 0.5}),
        _json({"index": 0, "category": "technology", "confidence": 0.5}),
    ]
    client = _fake_client(responses)
    classify_items(items, client=client, batch_size=2)
    assert client.generate.call_count == 3


def test_classify_items_empty():
    client = _fake_client(_json())
    assert classify_items([], client=client) == []
    client.generate.assert_not_called()


def test_build_batch_prompt_includes_indexes():
    items = [_item(title="Alpha"), _item(title="Beta", url="https://e.org/b")]
    prompt = build_batch_prompt(items)
    assert "[0]" in prompt and "Alpha" in prompt
    assert "[1]" in prompt and "Beta" in prompt
