"""Tests de la deep research (extraction page + analyse LLM)."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from research_agent.deep_research import deep_analyze, fetch_page_text
from research_agent.exceptions import ConnectorError, LLMError


def _fake_llm(response_text):
    client = MagicMock()
    client.generate.return_value = response_text
    return client


def _analysis_json(**over):
    base = {
        "titre_analyse": "Plaintes moisissure chez un bailleur",
        "organisation": "Woningcorporatie X",
        "contexte_resume": "Un bailleur fait face a des plaintes de moisissure.",
        "besoin_ou_opportunite": "Inspection humidite / prospect a contacter",
        "niveau": "HIGH",
        "action": "Proposer une inspection",
    }
    base.update(over)
    return json.dumps(base)


# --- Extraction de page ------------------------------------------------------


def test_fetch_page_text_extracts_readable_text():
    html = "<html><body><script>ignore()</script><p>Vocht en schimmel probleem</p></body></html>"
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.text = html
    with patch("research_agent.deep_research.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = resp
        text = fetch_page_text("https://example.org/a")
    assert "Vocht en schimmel probleem" in text
    assert "ignore" not in text  # le script est retire


def test_fetch_page_text_raises_on_http_error():
    with patch("research_agent.deep_research.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError("x")
        with pytest.raises(ConnectorError):
            fetch_page_text("https://example.org/a")


# --- Analyse approfondie -----------------------------------------------------


def test_deep_analyze_returns_structured_fields():
    with patch("research_agent.deep_research.fetch_page_text", return_value="contenu reel"):
        result = deep_analyze("https://e.org/a", title="Schimmel", client=_fake_llm(_analysis_json()))
    assert result["organisation"] == "Woningcorporatie X"
    assert result["niveau"] == "HIGH"
    assert result["titre_analyse"]
    assert result["besoin_ou_opportunite"]
    assert result["action"]


def test_deep_analyze_niveau_normalized():
    # Un niveau hors {HIGH, ACT} retombe sur ACT.
    with patch("research_agent.deep_research.fetch_page_text", return_value="contenu"):
        result = deep_analyze("https://e.org/a", client=_fake_llm(_analysis_json(niveau="chaud")))
    assert result["niveau"] == "ACT"


def test_deep_analyze_tolerates_json_fence():
    fenced = "```json\n" + _analysis_json(niveau="ACT") + "\n```"
    with patch("research_agent.deep_research.fetch_page_text", return_value="contenu"):
        result = deep_analyze("https://e.org/a", client=_fake_llm(fenced))
    assert result["niveau"] == "ACT"


def test_deep_analyze_raises_on_bad_json():
    with patch("research_agent.deep_research.fetch_page_text", return_value="contenu"):
        with pytest.raises(LLMError):
            deep_analyze("https://e.org/a", client=_fake_llm("pas du json"))


def test_deep_analyze_raises_on_empty_page():
    with patch("research_agent.deep_research.fetch_page_text", return_value=""):
        with pytest.raises(ConnectorError):
            deep_analyze("https://e.org/a", client=_fake_llm(_analysis_json()))
