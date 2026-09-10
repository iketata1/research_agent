"""Tests du client Telegram (chunking + gestion des erreurs)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from research_agent.delivery.telegram_client import (
    TELEGRAM_MAX_LEN,
    TelegramClient,
    split_message,
)
from research_agent.exceptions import DeliveryError


def _client(token="tok", chat_id="123"):
    return TelegramClient(token=token, chat_id=chat_id)


def _ok_response():
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"ok": True, "result": {"message_id": 1}}
    return resp


# --- Chunking ----------------------------------------------------------------


def test_short_text_single_chunk():
    assert split_message("bonjour") == ["bonjour"]


def test_empty_text_no_chunk():
    assert split_message("") == []


def test_long_text_split_under_limit():
    text = "\n".join("ligne " + str(i) for i in range(2000))
    chunks = split_message(text, limit=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)


def test_split_prefers_newline_boundaries():
    text = "aaaa\nbbbb\ncccc"
    chunks = split_message(text, limit=9)  # "aaaa\nbbbb" = 9 ; cccc separe
    assert chunks == ["aaaa\nbbbb", "cccc"]


def test_oversized_single_line_hard_split():
    text = "x" * 25
    chunks = split_message(text, limit=10)
    assert chunks == ["x" * 10, "x" * 10, "x" * 5]


def test_chunks_respect_telegram_limit():
    text = "y" * 10000
    chunks = split_message(text)
    assert all(len(c) <= TELEGRAM_MAX_LEN for c in chunks)


# --- Envoi -------------------------------------------------------------------


def test_send_short_message_one_post():
    client = _client()
    with patch.object(client.client, "post", return_value=_ok_response()) as post:
        count = client.send_message("court")
    assert count == 1
    assert post.call_count == 1


def test_send_long_message_multiple_posts():
    client = _client()
    long_text = "\n".join("ligne " + str(i) for i in range(3000))
    with patch.object(client.client, "post", return_value=_ok_response()) as post:
        count = client.send_message(long_text)
    assert count > 1
    assert post.call_count == count


def test_send_passes_chat_id_and_parse_mode():
    client = _client(chat_id="999")
    captured = {}

    def capture(url, json=None):
        captured.update(json)
        return _ok_response()

    with patch.object(client.client, "post", side_effect=capture):
        client.send_message("hello", parse_mode="HTML")
    assert captured["chat_id"] == "999"
    assert captured["parse_mode"] == "HTML"


# --- Erreurs -----------------------------------------------------------------


def test_missing_token_raises():
    client = TelegramClient(token=None, chat_id="123")
    with pytest.raises(DeliveryError):
        client.send_message("x")


def test_missing_chat_id_raises():
    client = TelegramClient(token="tok", chat_id=None)
    with pytest.raises(DeliveryError):
        client.send_message("x")


def test_network_error_raises_delivery_error():
    client = _client()
    with patch.object(client.client, "post", side_effect=httpx.ConnectError("boom")):
        with pytest.raises(DeliveryError):
            client.send_message("x")


def test_api_rejection_raises_delivery_error():
    """Reponse ok:false (ex. token invalide) -> DeliveryError."""
    client = _client()
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"ok": False, "description": "Unauthorized"}
    with patch.object(client.client, "post", return_value=resp):
        with pytest.raises(DeliveryError):
            client.send_message("x")
