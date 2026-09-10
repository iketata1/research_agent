"""Tests du scoring de pertinence LLM par lot."""

import json
from unittest.mock import MagicMock

import pytest

from research_agent.exceptions import LLMError
from research_agent.filtering.llm_scorer import (
    ScoreEntry,
    build_batch_prompt,
    score_items,
    _parse_scores,
)
from research_agent.models import RawItem


def _item(title="Titre", raw_text="", url="https://example.org/a"):
    return RawItem(source="test", title=title, url=url, raw_text=raw_text)


def _fake_client(response_text):
    """Client LLM factice dont generate() renvoie un texte fixe (ou une sequence)."""
    client = MagicMock()
    if isinstance(response_text, list):
        client.generate.side_effect = response_text
    else:
        client.generate.return_value = response_text
    return client


def _json_scores(*entries):
    return json.dumps({"scores": list(entries)})


# --- Parsing -----------------------------------------------------------------


def test_score_entry_clamps_out_of_range():
    assert ScoreEntry(index=0, score=150).score == 100
    assert ScoreEntry(index=0, score=-20).score == 0


def test_parse_scores_maps_by_index():
    raw = _json_scores(
        {"index": 0, "score": 90, "justification": "tres pertinent"},
        {"index": 1, "score": 10, "justification": "hors sujet"},
    )
    scores = _parse_scores(raw)
    assert scores[0].score == 90
    assert scores[1].justification == "hors sujet"


def test_parse_scores_tolerates_json_fence():
    raw = "```json\n" + _json_scores({"index": 0, "score": 50}) + "\n```"
    scores = _parse_scores(raw)
    assert scores[0].score == 50


def test_parse_scores_raises_on_invalid_json():
    with pytest.raises(LLMError):
        _parse_scores("pas du json")


def test_parse_scores_raises_on_wrong_structure():
    with pytest.raises(LLMError):
        _parse_scores(json.dumps({"scores": [{"index": "x", "score": "y"}]}))


# --- Scoring de bout en bout -------------------------------------------------


def test_score_items_annotates_relevance_and_metadata():
    items = [_item(title="Schimmel"), _item(title="Ventilatie", url="https://e.org/b")]
    raw = _json_scores(
        {"index": 0, "score": 80, "justification": "moisissure : central"},
        {"index": 1, "score": 65, "justification": "ventilation : pertinent"},
    )
    client = _fake_client(raw)
    result = score_items(items, client=client)
    assert result[0].relevance == pytest.approx(0.80)
    assert result[0].metadata["llm_score"] == 80
    assert result[0].metadata["llm_justification"] == "moisissure : central"
    assert result[1].relevance == pytest.approx(0.65)


def test_score_items_missing_entry_gets_safety_zero():
    items = [_item(url="https://e.org/1"), _item(url="https://e.org/2")]
    # Le LLM n'a note que l'index 0.
    raw = _json_scores({"index": 0, "score": 70})
    client = _fake_client(raw)
    result = score_items(items, client=client)
    assert result[0].metadata["llm_score"] == 70
    assert result[1].metadata["llm_score"] == 0
    assert result[1].relevance == 0.0


def test_score_items_batches_calls():
    items = [_item(url=f"https://e.org/{i}") for i in range(5)]
    # batch_size=2 -> 3 lots -> 3 appels. Chaque lot note ses index locaux.
    responses = [
        _json_scores({"index": 0, "score": 50}, {"index": 1, "score": 50}),
        _json_scores({"index": 0, "score": 50}, {"index": 1, "score": 50}),
        _json_scores({"index": 0, "score": 50}),
    ]
    client = _fake_client(responses)
    score_items(items, client=client, batch_size=2)
    assert client.generate.call_count == 3


def test_score_items_empty_list():
    client = _fake_client(_json_scores())
    assert score_items([], client=client) == []
    client.generate.assert_not_called()


def test_build_batch_prompt_includes_indexes_and_titles():
    items = [_item(title="Alpha"), _item(title="Beta", url="https://e.org/b")]
    prompt = build_batch_prompt(items)
    assert "[0]" in prompt
    assert "Alpha" in prompt
    assert "[1]" in prompt
    assert "Beta" in prompt
