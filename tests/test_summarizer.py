"""Tests du resume LLM en 3 lignes (angle metier Intra-Air)."""

import json
from unittest.mock import MagicMock

import pytest

from research_agent.exceptions import LLMError
from research_agent.reporting.summarizer import (
    SUMMARY_LINES,
    Summary,
    summarize_item,
    summarize_items,
    _parse_summary,
    SYSTEM_PROMPT,
)
from research_agent.models import RawItem


def _item(title="Titre", raw_text="contenu", url="https://example.org/a"):
    return RawItem(source="test", title=title, url=url, raw_text=raw_text)


def _fake_client(response_text):
    client = MagicMock()
    if isinstance(response_text, list):
        client.generate.side_effect = response_text
    else:
        client.generate.return_value = response_text
    return client


def _json_lines(*lines):
    return json.dumps({"lines": list(lines)})


# --- Contrainte des 3 lignes -------------------------------------------------


def test_exactly_three_lines_accepted():
    s = Summary(lines=["a", "b", "c"])
    assert len(s.lines) == SUMMARY_LINES


def test_too_many_lines_truncated_to_three():
    s = Summary(lines=["a", "b", "c", "d", "e"])
    assert s.lines == ["a", "b", "c"]


def test_too_few_lines_padded_to_three():
    s = Summary(lines=["a", "b"])
    assert len(s.lines) == 3
    assert s.lines[:2] == ["a", "b"]


def test_empty_lines_removed():
    s = Summary(lines=["a", "", "  ", "b", "c"])
    assert s.lines == ["a", "b", "c"]


def test_string_input_split_into_lines():
    s = Summary(lines="ligne1\nligne2\nligne3")
    assert s.lines == ["ligne1", "ligne2", "ligne3"]


def test_all_empty_raises():
    with pytest.raises(Exception):
        Summary(lines=["", "  ", ""])


# --- Parsing -----------------------------------------------------------------


def test_parse_summary_valid():
    summary = _parse_summary(_json_lines("impact 1", "impact 2", "impact 3"))
    assert summary.lines == ["impact 1", "impact 2", "impact 3"]


def test_parse_summary_tolerates_fence():
    raw = "```json\n" + _json_lines("a", "b", "c") + "\n```"
    assert _parse_summary(raw).lines == ["a", "b", "c"]


def test_parse_summary_raises_on_invalid_json():
    with pytest.raises(LLMError):
        _parse_summary("pas du json")


def test_parse_summary_raises_on_empty():
    with pytest.raises(LLMError):
        _parse_summary(_json_lines())


# --- Resume de bout en bout --------------------------------------------------


def test_summarize_item_attaches_metadata():
    item = _item()
    client = _fake_client(_json_lines("l1", "l2", "l3"))
    summary = summarize_item(item, client=client)
    assert summary.lines == ["l1", "l2", "l3"]
    assert item.metadata["summary"] == ["l1", "l2", "l3"]
    assert item.metadata["summary_text"] == "l1\nl2\nl3"


def test_summarize_items_isolates_failures():
    items = [_item(url="https://e.org/1"), _item(url="https://e.org/2")]
    # Premier item : resume valide ; second : JSON casse (echec isole).
    client = _fake_client([_json_lines("a", "b", "c"), "pas du json"])
    result = summarize_items(items, client=client)
    assert result[0].metadata["summary"] == ["a", "b", "c"]
    assert "summary" not in result[1].metadata  # echec isole, non annote


def test_summarize_items_empty():
    client = _fake_client(_json_lines("a", "b", "c"))
    assert summarize_items([], client=client) == []
    client.generate.assert_not_called()


# --- Angle metier -------------------------------------------------------------


def test_system_prompt_targets_business_value():
    lowered = SYSTEM_PROMPT.lower()
    assert "intra-air" in lowered
    assert "3 lignes" in lowered
    # Ancrage sur les axes metier.
    assert any(k in lowered for k in ("ventilation", "moisissure", "capteur"))
