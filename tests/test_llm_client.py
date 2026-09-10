"""Tests du client LLM (couts, rate limit, retry, erreurs)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from research_agent.config import LLMConfig
from research_agent.exceptions import LLMError
from research_agent.llm.client import LLMClient


def _config(**kw):
    base = dict(
        model="test-model",
        max_retries=3,
        price_per_1m_input=1.0,
        price_per_1m_output=2.0,
    )
    base.update(kw)
    return LLMConfig(**base)


def _client(**kw):
    return LLMClient(config=_config(**kw), api_key="test-key")


def _ok_response(text="ok", prompt_tokens=1000, completion_tokens=500, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{"message": {"content": text}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }
    return resp


def _status_response(status):
    resp = MagicMock()
    resp.status_code = status
    return resp


# --- Cout et suivi des tokens ------------------------------------------------


def test_estimate_cost():
    client = _client()
    # 1M input @1.0 + 1M output @2.0 = 3.0
    assert client.estimate_cost(1_000_000, 1_000_000) == pytest.approx(3.0)


def test_complete_tracks_usage_and_cost():
    client = _client()
    with patch.object(client.client, "post", return_value=_ok_response()):
        resp = client.complete("bonjour")
    assert resp.text == "ok"
    assert resp.input_tokens == 1000
    assert resp.output_tokens == 500
    # cout = 1000/1e6*1.0 + 500/1e6*2.0 = 0.001 + 0.001 = 0.002
    assert resp.cost == pytest.approx(0.002)
    assert client.usage.requests == 1
    assert client.usage.cost == pytest.approx(0.002)


def test_usage_accumulates_over_calls():
    client = _client()
    with patch.object(client.client, "post", return_value=_ok_response()):
        client.complete("a")
        client.complete("b")
    assert client.usage.requests == 2
    assert client.usage.input_tokens == 2000
    assert client.usage.cost == pytest.approx(0.004)


# --- Rate limit + retry ------------------------------------------------------


def test_retries_on_429_then_succeeds():
    client = _client()
    responses = [_status_response(429), _status_response(429), _ok_response()]
    with patch.object(client.client, "post", side_effect=responses), \
         patch("research_agent.llm.client.time.sleep"):  # pas d'attente reelle
        resp = client.complete("x")
    assert resp.text == "ok"


def test_persistent_429_raises_llmerror():
    client = _client(max_retries=2)
    with patch.object(
        client.client, "post", return_value=_status_response(429)
    ), patch("research_agent.llm.client.time.sleep"):
        with pytest.raises(LLMError):
            client.complete("x")


def test_backoff_uses_sleep_between_retries():
    client = _client(max_retries=2)
    with patch.object(
        client.client, "post", return_value=_status_response(503)
    ), patch("research_agent.llm.client.time.sleep") as sleep_mock:
        with pytest.raises(LLMError):
            client.complete("x")
    # 2 reessais -> 2 pauses.
    assert sleep_mock.call_count == 2


# --- Erreurs reseau et parsing -----------------------------------------------


def test_network_error_retried_then_raises():
    client = _client(max_retries=1)
    with patch.object(
        client.client, "post", side_effect=httpx.ConnectError("boom")
    ), patch("research_agent.llm.client.time.sleep"):
        with pytest.raises(LLMError):
            client.complete("x")


def test_non_retryable_http_error_fails_fast():
    client = _client()
    resp = MagicMock()
    resp.status_code = 400
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "bad request", request=MagicMock(), response=MagicMock()
    )
    with patch.object(client.client, "post", return_value=resp), \
         patch("research_agent.llm.client.time.sleep") as sleep_mock:
        with pytest.raises(LLMError):
            client.complete("x")
    # 400 n'est pas reessaye : aucune pause.
    sleep_mock.assert_not_called()


def test_malformed_response_raises():
    client = _client()
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"unexpected": "shape"}
    with patch.object(client.client, "post", return_value=resp):
        with pytest.raises(LLMError):
            client.complete("x")


def test_missing_api_key_raises():
    client = LLMClient(config=_config(), api_key=None)
    with pytest.raises(LLMError):
        client.complete("x")


# --- Facade generate() -------------------------------------------------------


def test_generate_returns_text_only():
    client = _client()
    with patch.object(client.client, "post", return_value=_ok_response(text="reponse")):
        result = client.generate("bonjour", system_prompt="tu es utile")
    assert result == "reponse"
    # Le suivi des couts reste mis a jour meme via la facade.
    assert client.usage.requests == 1


def test_generate_missing_api_key_raises():
    client = LLMClient(config=_config(), api_key=None)
    with pytest.raises(LLMError):
        client.generate("x")


def test_per_call_temperature_and_max_tokens_override():
    client = _client()
    captured = {}

    def capture(url, json=None, headers=None):
        captured.update(json)
        return _ok_response()

    with patch.object(client.client, "post", side_effect=capture):
        client.generate("x", temperature=0.9, max_tokens=42)
    assert captured["temperature"] == 0.9
    assert captured["max_tokens"] == 42


def test_defaults_used_when_no_override():
    client = _client()
    captured = {}

    def capture(url, json=None, headers=None):
        captured.update(json)
        return _ok_response()

    with patch.object(client.client, "post", side_effect=capture):
        client.complete("x")
    # Valeurs par defaut issues de la config.
    assert captured["temperature"] == client.config.temperature
    assert captured["max_tokens"] == client.config.max_tokens
